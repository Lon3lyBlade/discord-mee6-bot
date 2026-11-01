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

        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_post("/api/send-embed", self._handle_send_embed)
        app.router.add_get("/api/guilds/{guild_id}", self._handle_get_guild)
        app.router.add_get("/api/guilds/{guild_id}/channels", self._handle_get_channels)
        app.router.add_get("/api/guilds/{guild_id}/roles", self._handle_get_roles)
        app.router.add_get("/api/guilds/{guild_id}/config", self._handle_get_config)
        app.router.add_post("/api/guilds/{guild_id}/welcome-channel", self._handle_set_welcome_channel)
        app.router.add_post("/api/guilds/{guild_id}/autorole", self._handle_set_autorole)

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

    async def _handle_send_embed(self, request: web.Request) -> web.Response:
        try:
            payload = await request.json()
            channel_id = int(payload["channel_id"])
        except (ValueError, KeyError, TypeError):
            return web.json_response({"error": "channel_id fehlt oder ist ungültig"}, status=400)

        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return web.json_response({"error": "Channel nicht gefunden oder kein Textchannel"}, status=404)

        title = (payload.get("title") or "").strip()
        description = (payload.get("description") or "").strip()
        color = parse_color(payload.get("color", ""))

        embed = discord.Embed(
            title=title or None,
            description=description or None,
            color=color,
        )

        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            return web.json_response({"error": "Keine Berechtigung in diesem Channel"}, status=403)

        return web.json_response({"status": "ok"})

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
        return web.json_response({"id": str(guild.id), "name": guild.name})

    async def _handle_get_channels(self, request: web.Request) -> web.Response:
        guild = self._get_guild(request)
        if guild is None:
            return web.json_response({"error": "Bot ist auf diesem Server nicht vorhanden"}, status=404)
        channels = [{"id": str(c.id), "name": c.name} for c in guild.text_channels]
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
        return web.json_response(
            {
                "welcome_channel_id": str(welcome_channel_id) if welcome_channel_id else None,
                "auto_role_id": str(auto_role_id) if auto_role_id else None,
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InternalAPI(bot))
