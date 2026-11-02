import io
import json
import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

from database import db

logger = logging.getLogger("bot.internal_api")

API_SECRET = os.environ.get("INTERNAL_API_SECRET")
API_HOST = os.environ.get("BOT_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("BOT_API_PORT", "8080"))

DEFAULT_COLOR = discord.Color.blurple()

# Discords eigenes Upload-Limit liegt (ohne Server-Boost) bei 10 MB; 8 MB lassen Spielraum
# für den restlichen Multipart-Overhead innerhalb des client_max_size-Caps unten.
MAX_IMAGE_SIZE = 8 * 1024 * 1024
ALLOWED_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
EXTENSION_BY_CONTENT_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}
MAX_EMBED_FIELDS = 25


def parse_color(value: str) -> discord.Color:
    try:
        return discord.Color(int(value.lstrip("#"), 16))
    except (TypeError, ValueError):
        return DEFAULT_COLOR


class InternalAPI(commands.Cog):
    """Kleine interne HTTP-API, über die das Dashboard dem Bot Aktionen signalisiert.

    Bot und Dashboard laufen als zwei Prozesse im selben Container; die API lauscht
    daher nur auf localhost/0.0.0.0 innerhalb des Containers und ist zusätzlich per
    Bearer-Token geschützt.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.runner: web.AppRunner | None = None

    async def cog_load(self) -> None:
        if not API_SECRET:
            raise RuntimeError(
                "Umgebungsvariable INTERNAL_API_SECRET ist nicht gesetzt. "
                "Bitte im Docker-/Unraid-Template eintragen (gleicher Wert wie beim Dashboard)."
            )

        # Default von aiohttp ist 1 MiB; auf das Bild-Limit (siehe MAX_IMAGE_SIZE) plus
        # Multipart-Overhead der restlichen Formularfelder hochsetzen.
        app = web.Application(middlewares=[self._auth_middleware], client_max_size=10 * 1024 * 1024)
        app.router.add_get("/api/guilds/{guild_id}", self._handle_get_guild)
        app.router.add_get("/api/guilds/{guild_id}/channels", self._handle_get_channels)
        app.router.add_get("/api/guilds/{guild_id}/voice-channels", self._handle_get_voice_channels)
        app.router.add_get("/api/guilds/{guild_id}/roles", self._handle_get_roles)
        app.router.add_get("/api/guilds/{guild_id}/config", self._handle_get_config)
        app.router.add_post("/api/guilds/{guild_id}/welcome-channel", self._handle_set_welcome_channel)
        app.router.add_post("/api/guilds/{guild_id}/autorole", self._handle_set_autorole)
        app.router.add_post("/api/guilds/{guild_id}/tempvoice-channels", self._handle_set_tempvoice_channels)
        app.router.add_post("/api/guilds/{guild_id}/send-embed", self._handle_send_embed)
        app.router.add_post("/api/guilds/{guild_id}/mod-log-channel", self._handle_set_mod_log_channel)
        app.router.add_get("/api/guilds/{guild_id}/warnings", self._handle_get_warnings)
        app.router.add_post("/api/guilds/{guild_id}/warnings/{warning_id}/delete", self._handle_delete_warning)

        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, API_HOST, API_PORT)
        await site.start()
        logger.info("Interne API lauscht auf %s:%s", API_HOST, API_PORT)

    async def cog_unload(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler) -> web.Response:
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {API_SECRET}":
            return web.json_response({"error": "unauthorized"}, status=401)
        return await handler(request)

    def _get_guild(self, request: web.Request) -> discord.Guild | None:
        try:
            guild_id = int(request.match_info["guild_id"])
        except ValueError:
            return None
        return self.bot.get_guild(guild_id)

    @staticmethod
    def _is_assignable_role(guild: discord.Guild, role: discord.Role) -> bool:
        # Gleiche Regel wie /set-autorole: @everyone und Rollen auf/über der höchsten
        # Bot-Rolle würden zur Laufzeit mit discord.Forbidden fehlschlagen.
        return not role.is_default() and role < guild.me.top_role

    async def _handle_get_guild(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)
        return web.json_response(
            {
                "id": str(guild.id),
                "name": guild.name,
                "member_count": guild.member_count,
                "text_channel_count": len(guild.text_channels),
                "voice_channel_count": len(guild.voice_channels),
            }
        )

    async def _handle_get_channels(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)
        channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]
        return web.json_response({"channels": channels})

    async def _handle_get_voice_channels(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)
        channels = [{"id": str(c.id), "name": c.name} for c in guild.voice_channels]
        return web.json_response({"channels": channels})

    async def _handle_get_roles(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)
        roles = [
            {"id": str(r.id), "name": r.name}
            for r in guild.roles
            if self._is_assignable_role(guild, r)
        ]
        return web.json_response({"roles": roles})

    async def _handle_get_config(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)
        welcome_channel_id = await db.get_welcome_channel(guild.id)
        auto_role_id = await db.get_auto_role(guild.id)
        tempvoice_channel_ids = await db.get_tempvoice_creator_channels(guild.id)
        mod_log_channel_id = await db.get_mod_log_channel(guild.id)
        return web.json_response(
            {
                "welcome_channel_id": str(welcome_channel_id) if welcome_channel_id else None,
                "auto_role_id": str(auto_role_id) if auto_role_id else None,
                "tempvoice_channel_ids": [str(channel_id) for channel_id in tempvoice_channel_ids],
                "mod_log_channel_id": str(mod_log_channel_id) if mod_log_channel_id else None,
            }
        )

    async def _handle_set_welcome_channel(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        try:
            payload = await request.json()
            channel_id = int(payload["channel_id"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "channel_id fehlt oder ist ungültig"}, status=400)

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return web.json_response({"error": "Channel gehört nicht zu diesem Server oder ist kein Textchannel"}, status=404)

        await db.set_welcome_channel(guild.id, channel.id)
        return web.json_response({"status": "ok"})

    async def _handle_set_autorole(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        try:
            payload = await request.json()
            role_id = int(payload["role_id"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "role_id fehlt oder ist ungültig"}, status=400)

        role = guild.get_role(role_id)
        if role is None:
            return web.json_response({"error": "Rolle gehört nicht zu diesem Server"}, status=404)
        if not self._is_assignable_role(guild, role):
            return web.json_response(
                {"error": "Diese Rolle liegt über oder auf meiner höchsten Rolle und kann nicht vergeben werden"},
                status=400,
            )

        await db.set_auto_role(guild.id, role.id)
        return web.json_response({"status": "ok"})

    async def _handle_set_tempvoice_channels(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        try:
            payload = await request.json()
            raw_ids = payload["channel_ids"]
            channel_ids = [int(channel_id) for channel_id in raw_ids]
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "channel_ids fehlt oder ist ungültig"}, status=400)

        for channel_id in channel_ids:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                return web.json_response(
                    {"error": "Channel gehört nicht zu diesem Server oder ist kein Sprachkanal"}, status=404
                )

        await db.set_tempvoice_creator_channels(guild.id, channel_ids)
        return web.json_response({"status": "ok"})

    async def _handle_set_mod_log_channel(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        try:
            payload = await request.json()
            channel_id = int(payload["channel_id"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "channel_id fehlt oder ist ungültig"}, status=400)

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return web.json_response({"error": "Channel gehört nicht zu diesem Server oder ist kein Textchannel"}, status=404)

        await db.set_mod_log_channel(guild.id, channel.id)
        return web.json_response({"status": "ok"})

    def _resolve_display_name(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is not None:
            return str(member)
        user = self.bot.get_user(user_id)
        if user is not None:
            return str(user)
        return f"Unbekannter Nutzer ({user_id})"

    async def _handle_get_warnings(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        entries = await db.get_warnings(guild.id)
        warnings = [
            {
                "id": entry["id"],
                "user_id": str(entry["user_id"]),
                "user_display": self._resolve_display_name(guild, entry["user_id"]),
                "moderator_id": str(entry["moderator_id"]),
                "moderator_display": self._resolve_display_name(guild, entry["moderator_id"]),
                "reason": entry["reason"],
                "created_at": entry["created_at"],
            }
            for entry in entries
        ]
        return web.json_response({"warnings": warnings})

    async def _handle_delete_warning(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        try:
            warning_id = int(request.match_info["warning_id"])
        except ValueError:
            return web.json_response({"error": "Ungültige Warning-ID"}, status=400)

        deleted = await db.delete_warning(guild.id, warning_id)
        if not deleted:
            return web.json_response({"error": "Verwarnung nicht gefunden"}, status=404)
        return web.json_response({"status": "ok"})

    @staticmethod
    def _too_long(value, limit: int) -> bool:
        return isinstance(value, str) and len(value) > limit

    async def _handle_send_embed(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)

        # Alle Skalarwerte kommen gebündelt als ein JSON-codierter Multipart-Textpart
        # ("fields_json"), ein optionaler Bild-Upload als echter Datei-Part ("image_file").
        # So gibt es nur einen Parsing-Pfad, egal ob ein Bild mitgeschickt wird oder nicht.
        try:
            post_data = await request.post()
        except ValueError:
            return web.json_response({"error": "Ungültige Multipart-Anfrage"}, status=400)

        raw_fields = post_data.get("fields_json")
        # Normalerweise liefert aiohttp einen Multipart-Textpart als str zurück, aber falls er
        # mit einem nicht-text/*-Content-Type ankommt (z. B. "application/json"), gibt es ihn
        # stattdessen als bytearray/bytes zurück - beide Fälle akzeptieren.
        if isinstance(raw_fields, (bytes, bytearray)):
            raw_fields = raw_fields.decode("utf-8", errors="replace")
        if not isinstance(raw_fields, str):
            return web.json_response({"error": "fields_json fehlt"}, status=400)
        try:
            payload = json.loads(raw_fields)
        except json.JSONDecodeError:
            return web.json_response({"error": "fields_json ist kein gültiges JSON"}, status=400)

        try:
            channel_id = int(payload["channel_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "channel_id fehlt oder ist ungültig"}, status=400)

        # Channel wird gezielt über die Guild aufgelöst (nicht global über den Bot), damit
        # ein Dashboard-User kein Embed in einen Channel auf einem fremden Server schicken
        # kann, auf dem er keine Adminrechte hat.
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return web.json_response({"error": "Channel gehört nicht zu diesem Server oder ist kein Textchannel"}, status=404)

        title = (payload.get("title") or "").strip()
        description = (payload.get("description") or "").strip()
        url = (payload.get("url") or "").strip()
        author = payload.get("author") or {}
        footer = payload.get("footer") or {}
        fields = payload.get("fields") or []

        # Discords eigene Embed-Limits vorab prüfen, damit der Nutzer eine klare Meldung
        # bekommt statt einer rohen discord.HTTPException von Discords API.
        if len(fields) > MAX_EMBED_FIELDS:
            return web.json_response({"error": f"Ein Embed darf maximal {MAX_EMBED_FIELDS} Felder haben"}, status=400)
        if self._too_long(title, 256):
            return web.json_response({"error": "Titel darf maximal 256 Zeichen haben"}, status=400)
        if self._too_long(description, 4096):
            return web.json_response({"error": "Beschreibung darf maximal 4096 Zeichen haben"}, status=400)
        if self._too_long(author.get("name"), 256):
            return web.json_response({"error": "Autor-Name darf maximal 256 Zeichen haben"}, status=400)
        if self._too_long(footer.get("text"), 2048):
            return web.json_response({"error": "Footer-Text darf maximal 2048 Zeichen haben"}, status=400)
        for field in fields:
            name = (field.get("name") or "").strip()
            value = (field.get("value") or "").strip()
            if not name or not value:
                return web.json_response({"error": "Jedes Feld benötigt einen Namen und einen Wert"}, status=400)
            if self._too_long(name, 256) or self._too_long(value, 1024):
                return web.json_response(
                    {"error": "Feldname darf maximal 256, Feldwert maximal 1024 Zeichen haben"}, status=400
                )

        color = parse_color(payload.get("color", ""))

        embed = discord.Embed(title=title or None, description=description or None, url=url or None, color=color)

        if payload.get("timestamp"):
            embed.timestamp = discord.utils.utcnow()

        if author.get("name"):
            embed.set_author(
                name=author["name"].strip(),
                url=(author.get("url") or "").strip() or None,
                icon_url=(author.get("icon_url") or "").strip() or None,
            )

        if footer.get("text"):
            embed.set_footer(text=footer["text"].strip(), icon_url=(footer.get("icon_url") or "").strip() or None)

        thumbnail_url = (payload.get("thumbnail_url") or "").strip()
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        for field in fields:
            embed.add_field(name=field["name"].strip(), value=field["value"].strip(), inline=bool(field.get("inline")))

        # Ein hochgeladenes Bild hat Vorrang vor einer gleichzeitig eingegebenen Bild-URL.
        discord_file = None
        image_field = post_data.get("image_file")
        if isinstance(image_field, web.FileField) and image_field.filename:
            raw_bytes = image_field.file.read()
            if len(raw_bytes) > MAX_IMAGE_SIZE:
                return web.json_response(
                    {"error": f"Bild ist zu groß (max. {MAX_IMAGE_SIZE // (1024 * 1024)} MB)"}, status=400
                )
            content_type = image_field.content_type
            if content_type not in ALLOWED_IMAGE_CONTENT_TYPES:
                return web.json_response({"error": "Nur PNG-, JPEG-, GIF- oder WebP-Bilder sind erlaubt"}, status=400)
            filename = f"embed-image.{EXTENSION_BY_CONTENT_TYPE[content_type]}"
            discord_file = discord.File(io.BytesIO(raw_bytes), filename=filename)
            embed.set_image(url=f"attachment://{filename}")
        else:
            image_url = (payload.get("image_url") or "").strip()
            if image_url:
                embed.set_image(url=image_url)

        if len(embed) > 6000:
            return web.json_response({"error": "Das Embed überschreitet Discords Gesamtlimit von 6000 Zeichen"}, status=400)

        try:
            if discord_file is not None:
                await channel.send(embed=embed, file=discord_file)
            else:
                await channel.send(embed=embed)
        except discord.Forbidden:
            return web.json_response({"error": "Keine Berechtigung in diesem Channel"}, status=403)
        except discord.HTTPException as exc:
            return web.json_response({"error": f"Discord hat die Anfrage abgelehnt: {exc}"}, status=400)

        return web.json_response({"status": "ok"})


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InternalAPI(bot))
