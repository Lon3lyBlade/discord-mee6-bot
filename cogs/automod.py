import collections

import discord
from discord import app_commands
from discord.ext import commands

from database import db

SPAM_MESSAGE_LIMIT = 5
SPAM_INTERVAL_SECONDS = 3
INVITE_LINK_MARKER = "discord.gg/"


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # (guild_id, user_id) -> letzte Nachrichten des Users, für den Spam-Check.
        self._message_history: dict[tuple[int, int], collections.deque[discord.Message]] = (
            collections.defaultdict(lambda: collections.deque(maxlen=20))
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        if await self._handle_link_filter(message):
            return

        await self._handle_spam(message)

    async def _handle_link_filter(self, message: discord.Message) -> bool:
        if INVITE_LINK_MARKER not in message.content:
            return False

        if isinstance(message.author, discord.Member) and message.author.guild_permissions.administrator:
            return False

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            return False

        await self._log(
            message.guild,
            f"🔗 Nachricht von {message.author.mention} in {message.channel.mention} gelöscht "
            "(enthielt einen Discord-Einladungslink).",
        )
        return True

    async def _handle_spam(self, message: discord.Message) -> None:
        key = (message.guild.id, message.author.id)
        history = self._message_history[key]
        history.append(message)

        now = discord.utils.utcnow()
        recent = [m for m in history if (now - m.created_at).total_seconds() <= SPAM_INTERVAL_SECONDS]

        if len(recent) <= SPAM_MESSAGE_LIMIT:
            return

        # Zurücksetzen, damit nicht jede weitere Nachricht erneut auslöst.
        history.clear()

        for recent_message in recent:
            try:
                await recent_message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass

        try:
            await message.author.send(
                f"⚠️ Du wurdest auf **{message.guild.name}** verwarnt, weil du innerhalb von "
                f"{SPAM_INTERVAL_SECONDS} Sekunden mehr als {SPAM_MESSAGE_LIMIT} Nachrichten gesendet hast. "
                "Bitte poste langsamer, sonst können weitere Maßnahmen folgen."
            )
        except discord.Forbidden:
            pass

        await self._log(
            message.guild,
            f"🚨 Anti-Spam: {len(recent)} Nachrichten von {message.author.mention} in "
            f"{message.channel.mention} gelöscht, User wurde per DM verwarnt.",
        )

    async def _get_log_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        channel_id = await db.get_mod_log_channel(guild.id)
        if channel_id is not None:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel

        return discord.utils.find(lambda c: c.name == "mod-log", guild.text_channels)

    async def _log(self, guild: discord.Guild, content: str) -> None:
        channel = await self._get_log_channel(guild)
        if channel is None:
            return

        try:
            await channel.send(content)
        except discord.Forbidden:
            pass

    @app_commands.command(
        name="set-modlog",
        description="Legt den Channel fest, in dem Auto-Mod-Aktionen protokolliert werden.",
    )
    @app_commands.describe(channel="Der Textchannel für das Mod-Log.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_modlog(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await db.set_mod_log_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Auto-Mod-Aktionen werden ab jetzt in {channel.mention} protokolliert.",
            ephemeral=True,
        )

    @set_modlog.error
    async def on_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Dafür benötigst du die Berechtigung **Server verwalten**.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))
