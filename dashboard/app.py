import json
import os
from functools import wraps

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for
from requests_oauthlib import OAuth2Session
from werkzeug.exceptions import RequestEntityTooLarge

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
# Etwas großzügiger als das 8-MB-Bildlimit in cogs/internal_api.py, damit Multipart-Overhead
# (Boundaries, Header, restliche Formularfelder) nicht knapp abgeschnitten wird.
app.config["MAX_CONTENT_LENGTH"] = 9 * 1024 * 1024

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


def get_admin_guild_or_none(discord_session, guild_id: str) -> dict | None:
    guilds = discord_session.get(f"{API_BASE_URL}/users/@me/guilds").json()
    for guild in guilds:
        if guild["id"] == guild_id and is_admin_guild(guild):
            return guild
    return None


@app.route("/server/<guild_id>")
@login_required
def server_detail(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    guild = get_admin_guild_or_none(discord_session, guild_id)
    if guild is None:
        return render_template("server.html", error="access"), 403

    try:
        bot_guild = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        if bot_guild.status_code == 404:
            return render_template("server.html", error="not_in_guild", guild=guild)
        bot_guild.raise_for_status()

        channels = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/channels",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        ).json()["channels"]
        roles = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/roles",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        ).json()["roles"]
        config = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/config",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        ).json()
    except requests.RequestException:
        return render_template("server.html", error="unreachable", guild=guild)

    return render_template(
        "server.html",
        guild=guild,
        channels=channels,
        roles=roles,
        config=config,
        message=request.args.get("message"),
    )


@app.route("/server/<guild_id>/welcome-channel", methods=["POST"])
@login_required
def set_welcome_channel(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("server.html", error="access"), 403

    channel_id = request.form.get("channel_id", "").strip()
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/welcome-channel",
            json={"channel_id": channel_id},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Willkommens-Channel gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("server_detail", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/autorole", methods=["POST"])
@login_required
def set_autorole(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("server.html", error="access"), 403

    role_id = request.form.get("role_id", "").strip()
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/autorole",
            json={"role_id": role_id},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "AutoRole gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("server_detail", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/send-embed", methods=["POST"])
@login_required
def send_embed(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("server.html", error="access"), 403

    try:
        channel_id = request.form.get("channel_id", "").strip()
        if not channel_id.isdigit():
            return redirect(url_for("server_detail", guild_id=guild_id, message="Bitte einen Channel auswählen."))

        field_names = request.form.getlist("field_name[]")
        field_values = request.form.getlist("field_value[]")
        field_inlines = request.form.getlist("field_inline[]")

        fields = []
        for name, value, inline in zip(field_names, field_values, field_inlines):
            name, value = name.strip(), value.strip()
            if not name or not value:
                continue  # leere Zeilen (z. B. hinzugefügt, aber nie befüllt) überspringen
            fields.append({"name": name, "value": value, "inline": inline == "true"})

        payload = {
            "channel_id": int(channel_id),
            "title": request.form.get("title", "").strip(),
            "description": request.form.get("description", "").strip(),
            "url": request.form.get("url", "").strip(),
            "color": request.form.get("color", "#5865F2").strip(),
            "timestamp": request.form.get("timestamp") == "on",
            "author": {
                "name": request.form.get("author_name", "").strip(),
                "url": request.form.get("author_url", "").strip(),
                "icon_url": request.form.get("author_icon_url", "").strip(),
            },
            "footer": {
                "text": request.form.get("footer_text", "").strip(),
                "icon_url": request.form.get("footer_icon_url", "").strip(),
            },
            "thumbnail_url": request.form.get("thumbnail_url", "").strip(),
            "image_url": request.form.get("image_url", "").strip(),
            "fields": fields,
        }

        # fields_json wird bewusst über files= statt data= übergeben (Tupel mit filename=None),
        # damit requests den Request IMMER als multipart/form-data kodiert - auch ohne
        # Bild-Upload. So gibt es auf Bot-Seite nur einen Parsing-Pfad. Bewusst OHNE expliziten
        # Content-Type: aiohttp liefert einen Multipart-Textpart nur dann als str zurück, wenn
        # kein (oder ein text/*) Content-Type gesetzt ist - mit "application/json" kommt auf
        # Bot-Seite stattdessen ein bytearray an.
        multipart_files = {"fields_json": (None, json.dumps(payload))}

        image_file = request.files.get("image_file")
        if image_file and image_file.filename:
            multipart_files["image_file"] = (
                image_file.filename,
                image_file.stream,
                image_file.mimetype or "application/octet-stream",
            )
    except RequestEntityTooLarge:
        return redirect(
            url_for("server_detail", guild_id=guild_id, message="Die hochgeladene Datei ist zu groß (max. 8 MB).")
        )

    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/send-embed",
            files=multipart_files,
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=10,
        )
        message = "Embed wurde gesendet." if response.ok else response.json().get("error", "Der Bot hat die Anfrage abgelehnt.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."
    except ValueError:
        message = "Der Bot hat die Anfrage abgelehnt."

    return redirect(url_for("server_detail", guild_id=guild_id, message=message))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
