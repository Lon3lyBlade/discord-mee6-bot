import os
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for
from requests_oauthlib import OAuth2Session

# load_dotenv() ist rein für die lokale Entwicklung gedacht und no-opt, falls keine
# .env-Datei existiert. Im Unraid-Container kommen alle Werte über echte
# Umgebungsvariablen aus dem Container-Template, siehe require_env() unten.
load_dotenv()


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Umgebungsvariable {name} ist nicht gesetzt. Bitte im Docker-/Unraid-Template eintragen."
        )
    return value


# Discord liefert den OAuth2-Redirect bei lokaler Entwicklung bzw. ohne vorgeschalteten
# HTTPS-Reverse-Proxy über http statt https aus. oauthlib lehnt das standardmäßig ab,
# daher per Umgebungsvariable steuerbar explizit erlauben (Default: erlaubt, da Unraid
# typischerweise ohne eigenes TLS direkt per http://<unraid-ip>:5000 erreicht wird).
if os.environ.get("OAUTH_INSECURE_TRANSPORT", "1") == "1":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

app = Flask(__name__)
app.secret_key = require_env("FLASK_SECRET_KEY")

DISCORD_CLIENT_ID = require_env("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = require_env("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = require_env("DISCORD_REDIRECT_URI")

API_BASE_URL = "https://discord.com/api"
AUTHORIZATION_BASE_URL = f"{API_BASE_URL}/oauth2/authorize"
TOKEN_URL = f"{API_BASE_URL}/oauth2/token"
SCOPES = ["identify", "guilds"]

ADMINISTRATOR_PERMISSION = 0x8

# Interne Bot-API, über die das Dashboard dem Bot das Senden eines Embeds signalisiert.
# Bot und Dashboard laufen im selben Container als zwei Prozesse, daher reicht "localhost".
BOT_API_URL = os.environ.get("BOT_API_URL", "http://127.0.0.1:8080")
INTERNAL_API_SECRET = require_env("INTERNAL_API_SECRET")


def make_discord_session(token=None, state=None):
    return OAuth2Session(
        client_id=DISCORD_CLIENT_ID,
        token=token,
        state=state,
        scope=SCOPES,
        redirect_uri=DISCORD_REDIRECT_URI,
    )


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "oauth_token" not in session:
            return redirect(url_for("index"))
        return view(*args, **kwargs)

    return wrapped_view


def is_admin_guild(guild: dict) -> bool:
    if guild.get("owner"):
        return True
    permissions = int(guild.get("permissions", 0))
    return (permissions & ADMINISTRATOR_PERMISSION) == ADMINISTRATOR_PERMISSION


@app.route("/")
def index():
    if "oauth_token" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/login")
def login():
    discord_session = make_discord_session()
    authorization_url, state = discord_session.authorization_url(AUTHORIZATION_BASE_URL)
    session["oauth_state"] = state
    return redirect(authorization_url)


@app.route("/callback")
def callback():
    discord_session = make_discord_session(state=session.get("oauth_state"))
    token = discord_session.fetch_token(
        TOKEN_URL,
        client_secret=DISCORD_CLIENT_SECRET,
        authorization_response=request.url,
    )
    session["oauth_token"] = token
    return redirect(url_for("dashboard"))


@app.route("/dashboard")
@login_required
def dashboard():
    discord_session = make_discord_session(token=session["oauth_token"])
    user = discord_session.get(f"{API_BASE_URL}/users/@me").json()
    guilds = discord_session.get(f"{API_BASE_URL}/users/@me/guilds").json()

    admin_guilds = [guild for guild in guilds if is_admin_guild(guild)]

    return render_template("dashboard.html", user=user, guilds=admin_guilds)


@app.route("/embed-generator", methods=["GET", "POST"])
@login_required
def embed_generator():
    message = None
    error = None
    form_data = {"channel_id": "", "title": "", "description": "", "color": "#5865F2"}

    if request.method == "POST":
        form_data["channel_id"] = request.form.get("channel_id", "").strip()
        form_data["title"] = request.form.get("title", "").strip()
        form_data["description"] = request.form.get("description", "").strip()
        form_data["color"] = request.form.get("color", "#5865F2").strip()

        if not form_data["channel_id"].isdigit():
            error = "Bitte eine gültige numerische Kanal-ID angeben."
        else:
            try:
                response = requests.post(
                    f"{BOT_API_URL}/api/send-embed",
                    json={
                        "channel_id": int(form_data["channel_id"]),
                        "title": form_data["title"],
                        "description": form_data["description"],
                        "color": form_data["color"],
                    },
                    headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
                    timeout=5,
                )
                if response.ok:
                    message = "Embed wurde erfolgreich an den Bot übergeben."
                else:
                    error = response.json().get("error", "Der Bot hat die Anfrage abgelehnt.")
            except requests.RequestException:
                error = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return render_template("embed_generator.html", message=message, error=error, form_data=form_data)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
