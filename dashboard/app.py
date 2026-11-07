import json
import os
from datetime import datetime
from functools import wraps
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from flask import Flask, redirect, render_template, request, session, url_for
from requests_oauthlib import OAuth2Session
from werkzeug.exceptions import RequestEntityTooLarge

# Muss mit EVENT_TZ in cogs/events.py übereinstimmen, damit Eingaben aus dem
# datetime-local-Feld und die Anzeige bestehender Events dieselbe Zeitzone verwenden.
EVENT_TZ = ZoneInfo("Europe/Berlin")

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


def get_admin_guilds(discord_session) -> list[dict]:
    guilds = discord_session.get(f"{API_BASE_URL}/users/@me/guilds").json()
    return [guild for guild in guilds if is_admin_guild(guild)]


def get_admin_guild_or_none(discord_session, guild_id: str) -> dict | None:
    for guild in get_admin_guilds(discord_session):
        if guild["id"] == guild_id:
            return guild
    return None


@app.route("/dashboard")
@login_required
def dashboard():
    discord_session = make_discord_session(token=session["oauth_token"])
    user = discord_session.get(f"{API_BASE_URL}/users/@me").json()
    admin_guilds = get_admin_guilds(discord_session)

    return render_template("dashboard.html", user=user, guilds=admin_guilds)


def fetch_guild_bot_data(guild_id: str):
    """Holt Server-Status, Channels, Rollen und Config von der internen Bot-API.

    Gibt (error, data) zurück: error ist None bei Erfolg, sonst "not_in_guild"
    oder "unreachable" (data dann None).
    """
    try:
        bot_guild = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        if bot_guild.status_code == 404:
            return "not_in_guild", None
        bot_guild.raise_for_status()
        stats = bot_guild.json()

        channels = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/channels",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        ).json()["channels"]
        voice_channels = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/voice-channels",
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
        return "unreachable", None

    return None, {
        "stats": stats,
        "channels": channels,
        "voice_channels": voice_channels,
        "roles": roles,
        "config": config,
    }


def resolve_guild_or_response(guild_id: str, template: str):
    """Login + Adminrechte-Check, gemeinsam für alle guild-bezogenen Seiten.

    Gibt entweder (discord_session, user, admin_guilds, guild) oder eine fertige
    Flask-Response (bei fehlenden Adminrechten) zurück.
    """
    discord_session = make_discord_session(token=session["oauth_token"])
    user = discord_session.get(f"{API_BASE_URL}/users/@me").json()
    admin_guilds = get_admin_guilds(discord_session)
    guild = next((g for g in admin_guilds if g["id"] == guild_id), None)
    if guild is None:
        return render_template(template, error="access"), 403
    return discord_session, user, admin_guilds, guild


@app.route("/server/<guild_id>")
@login_required
def server_detail(guild_id):
    result = resolve_guild_or_response(guild_id, "settings.html")
    if not isinstance(result, tuple) or len(result) != 4:
        return result
    _discord_session, user, admin_guilds, guild = result

    error, data = fetch_guild_bot_data(guild_id)
    if error:
        return render_template("settings.html", error=error, guild=guild, user=user, guilds=admin_guilds)

    return render_template(
        "settings.html",
        active="settings",
        guild=guild,
        user=user,
        guilds=admin_guilds,
        stats=data["stats"],
        channels=data["channels"],
        roles=data["roles"],
        config=data["config"],
        message=request.args.get("message"),
    )


@app.route("/server/<guild_id>/tempvoice")
@login_required
def tempvoice_page(guild_id):
    result = resolve_guild_or_response(guild_id, "tempvoice.html")
    if not isinstance(result, tuple) or len(result) != 4:
        return result
    _discord_session, user, admin_guilds, guild = result

    error, data = fetch_guild_bot_data(guild_id)
    if error:
        return render_template("tempvoice.html", error=error, guild=guild, user=user, guilds=admin_guilds)

    return render_template(
        "tempvoice.html",
        active="tempvoice",
        guild=guild,
        user=user,
        guilds=admin_guilds,
        voice_channels=data["voice_channels"],
        config=data["config"],
        message=request.args.get("message"),
    )


@app.route("/server/<guild_id>/embed-builder")
@login_required
def embed_builder_page(guild_id):
    result = resolve_guild_or_response(guild_id, "embed_builder.html")
    if not isinstance(result, tuple) or len(result) != 4:
        return result
    _discord_session, user, admin_guilds, guild = result

    error, data = fetch_guild_bot_data(guild_id)
    if error:
        return render_template("embed_builder.html", error=error, guild=guild, user=user, guilds=admin_guilds)

    return render_template(
        "embed_builder.html",
        active="embed",
        guild=guild,
        user=user,
        guilds=admin_guilds,
        channels=data["channels"],
        message=request.args.get("message"),
    )


@app.route("/server/<guild_id>/moderation")
@login_required
def moderation_page(guild_id):
    result = resolve_guild_or_response(guild_id, "moderation.html")
    if not isinstance(result, tuple) or len(result) != 4:
        return result
    _discord_session, user, admin_guilds, guild = result

    error, data = fetch_guild_bot_data(guild_id)
    if error:
        return render_template("moderation.html", error=error, guild=guild, user=user, guilds=admin_guilds)

    try:
        warnings_response = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/warnings",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        warnings = warnings_response.json().get("warnings", [])
    except requests.RequestException:
        return render_template("moderation.html", error="unreachable", guild=guild, user=user, guilds=admin_guilds)

    return render_template(
        "moderation.html",
        active="moderation",
        guild=guild,
        user=user,
        guilds=admin_guilds,
        channels=data["channels"],
        config=data["config"],
        warnings=warnings,
        message=request.args.get("message"),
    )


def fetch_events(guild_id: str):
    """Holt die Event-Liste von der internen Bot-API und bereitet sie fürs Template auf.

    Gibt (error, events) zurück: error ist None bei Erfolg, sonst "unreachable".
    """
    try:
        response = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/events",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        response.raise_for_status()
        events = response.json()["events"]
    except requests.RequestException:
        return "unreachable", None

    for event in events:
        event_time_utc = datetime.fromisoformat(event["event_time"])
        event_time_local = event_time_utc.astimezone(EVENT_TZ)
        event["event_time_display"] = event_time_local.strftime("%d.%m.%Y %H:%M")
        event["event_time_input"] = event_time_local.strftime("%Y-%m-%dT%H:%M")

    return None, events


@app.route("/server/<guild_id>/events")
@login_required
def events_page(guild_id):
    result = resolve_guild_or_response(guild_id, "events.html")
    if not isinstance(result, tuple) or len(result) != 4:
        return result
    _discord_session, user, admin_guilds, guild = result

    error, data = fetch_guild_bot_data(guild_id)
    if error:
        return render_template("events.html", error=error, guild=guild, user=user, guilds=admin_guilds)

    events_error, events = fetch_events(guild_id)
    if events_error:
        return render_template("events.html", error=events_error, guild=guild, user=user, guilds=admin_guilds)

    return render_template(
        "events.html",
        active="events",
        guild=guild,
        user=user,
        guilds=admin_guilds,
        channels=data["channels"],
        roles=data["roles"],
        events=events,
        message=request.args.get("message"),
    )


@app.route("/server/<guild_id>/events", methods=["POST"])
@login_required
def create_event(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("events.html", error="access"), 403

    payload = {
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(),
        "event_time": request.form.get("event_time", "").strip(),
        "channel_id": request.form.get("channel_id", "").strip(),
        "target_role_id": request.form.get("target_role_id", "").strip() or None,
    }
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/events",
            json=payload,
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=10,
        )
        message = "Event wurde erstellt." if response.ok else response.json().get("error", "Fehler beim Erstellen.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("events_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/events/<event_id>/edit", methods=["POST"])
@login_required
def edit_event(guild_id, event_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("events.html", error="access"), 403

    payload = {
        "title": request.form.get("title", "").strip(),
        "description": request.form.get("description", "").strip(),
        "event_time": request.form.get("event_time", "").strip(),
        "channel_id": request.form.get("channel_id", "").strip(),
        "target_role_id": request.form.get("target_role_id", "").strip() or None,
    }
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/events/{event_id}/edit",
            json=payload,
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=10,
        )
        message = "Event wurde aktualisiert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("events_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/events/<event_id>/delete", methods=["POST"])
@login_required
def delete_event(guild_id, event_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("events.html", error="access"), 403

    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/events/{event_id}/delete",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Event wurde abgesagt und gelöscht." if response.ok else response.json().get("error", "Fehler beim Löschen.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("events_page", guild_id=guild_id, message=message))


def fetch_levels_data(guild_id: str):
    """Holt Leaderboard + Level-Konfiguration von der internen Bot-API.

    Gibt (error, data) zurück: error ist None bei Erfolg, sonst "unreachable".
    """
    try:
        users_response = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        leaderboard = users_response.json()["users"]
        config_response = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/config",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        level_config = config_response.json()
        rewards_response = requests.get(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/rewards",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        rewards = rewards_response.json()["rewards"]
    except requests.RequestException:
        return "unreachable", None

    return None, {"leaderboard": leaderboard, "level_config": level_config, "rewards": rewards}


@app.route("/server/<guild_id>/levels")
@login_required
def levels_page(guild_id):
    result = resolve_guild_or_response(guild_id, "levels.html")
    if not isinstance(result, tuple) or len(result) != 4:
        return result
    _discord_session, user, admin_guilds, guild = result

    error, data = fetch_guild_bot_data(guild_id)
    if error:
        return render_template("levels.html", error=error, guild=guild, user=user, guilds=admin_guilds)

    levels_error, levels_data = fetch_levels_data(guild_id)
    if levels_error:
        return render_template("levels.html", error=levels_error, guild=guild, user=user, guilds=admin_guilds)

    return render_template(
        "levels.html",
        active="levels",
        guild=guild,
        user=user,
        guilds=admin_guilds,
        channels=data["channels"],
        roles=data["roles"],
        leaderboard=levels_data["leaderboard"],
        level_config=levels_data["level_config"],
        rewards=levels_data["rewards"],
        message=request.args.get("message"),
    )


@app.route("/server/<guild_id>/levels/settings", methods=["POST"])
@login_required
def set_levels_settings(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("levels.html", error="access"), 403

    payload = {
        "xp_enabled": request.form.get("xp_enabled") == "on",
        "notify_mode": request.form.get("notify_mode", "chat"),
    }
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/config",
            json=payload,
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Level-Einstellungen gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("levels_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/levels/excluded-channels", methods=["POST"])
@login_required
def set_levels_excluded_channels(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("levels.html", error="access"), 403

    channel_ids = [cid for cid in request.form.getlist("channel_ids") if cid.isdigit()]
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/excluded-channels",
            json={"channel_ids": channel_ids},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Ausgeschlossene Channels gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("levels_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/levels/excluded-roles", methods=["POST"])
@login_required
def set_levels_excluded_roles(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("levels.html", error="access"), 403

    role_ids = [rid for rid in request.form.getlist("role_ids") if rid.isdigit()]
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/excluded-roles",
            json={"role_ids": role_ids},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Ausgeschlossene Rollen gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("levels_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/levels/rewards", methods=["POST"])
@login_required
def set_level_reward(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("levels.html", error="access"), 403

    level = request.form.get("level", "").strip()
    role_id = request.form.get("role_id", "").strip()
    if not level.isdigit() or not role_id.isdigit():
        return redirect(
            url_for("levels_page", guild_id=guild_id, message="Bitte ein gültiges Level und eine Rolle auswählen.")
        )

    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/rewards",
            json={"level": level, "role_id": role_id},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Level-Belohnung gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("levels_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/levels/rewards/<level>/delete", methods=["POST"])
@login_required
def delete_level_reward(guild_id, level):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("levels.html", error="access"), 403

    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/levels/rewards/{level}/delete",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Level-Belohnung gelöscht." if response.ok else response.json().get("error", "Fehler beim Löschen.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("levels_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/mod-log-channel", methods=["POST"])
@login_required
def set_mod_log_channel(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("moderation.html", error="access"), 403

    channel_id = request.form.get("channel_id", "").strip()
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/mod-log-channel",
            json={"channel_id": channel_id},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Mod-Log-Channel gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("moderation_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/warnings/<warning_id>/delete", methods=["POST"])
@login_required
def delete_warning(guild_id, warning_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("moderation.html", error="access"), 403

    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/warnings/{warning_id}/delete",
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Verwarnung gelöscht." if response.ok else response.json().get("error", "Fehler beim Löschen.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."

    return redirect(url_for("moderation_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/welcome-channel", methods=["POST"])
@login_required
def set_welcome_channel(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("settings.html", error="access"), 403

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
        return render_template("settings.html", error="access"), 403

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


@app.route("/server/<guild_id>/tempvoice-channels", methods=["POST"])
@login_required
def set_tempvoice_channels(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("tempvoice.html", error="access"), 403

    channel_ids = [cid for cid in request.form.getlist("channel_ids") if cid.isdigit()]
    try:
        response = requests.post(
            f"{BOT_API_URL}/api/guilds/{guild_id}/tempvoice-channels",
            json={"channel_ids": channel_ids},
            headers={"Authorization": f"Bearer {INTERNAL_API_SECRET}"},
            timeout=5,
        )
        message = "Tempvoice-Kanäle gespeichert." if response.ok else response.json().get("error", "Fehler beim Speichern.")
    except requests.RequestException:
        message = "Der Bot ist über die interne API aktuell nicht erreichbar."
    except ValueError:
        message = "Der Bot hat die Anfrage abgelehnt."

    return redirect(url_for("tempvoice_page", guild_id=guild_id, message=message))


@app.route("/server/<guild_id>/send-embed", methods=["POST"])
@login_required
def send_embed(guild_id):
    discord_session = make_discord_session(token=session["oauth_token"])
    if get_admin_guild_or_none(discord_session, guild_id) is None:
        return render_template("embed_builder.html", error="access"), 403

    try:
        channel_id = request.form.get("channel_id", "").strip()
        if not channel_id.isdigit():
            return redirect(url_for("embed_builder_page", guild_id=guild_id, message="Bitte einen Channel auswählen."))

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
            url_for("embed_builder_page", guild_id=guild_id, message="Die hochgeladene Datei ist zu groß (max. 8 MB).")
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

    return redirect(url_for("embed_builder_page", guild_id=guild_id, message=message))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
