import logging
import os

import discord
from aiohttp import web
from discord.ext import commands

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InternalAPI(bot))
