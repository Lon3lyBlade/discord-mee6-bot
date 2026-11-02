from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from database import db

MAX_TIMEOUT_DAYS = 28
DURATION_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(raw: str) -> int | None:
    """Parst "10m", "2h", "1d" etc. in Sekunden. Reine Zahlen gelten als Minuten."""
    raw = raw.strip().lower()
    if not raw:
        return None

    if raw.isdigit():
        return int(raw) * 60

    unit = raw[-1]
    amount = raw[:-1]
    if unit not in DURATION_UNIT_SECONDS or not amount.isdigit():
        return None

    return int(amount) * DURATION_UNIT_SECONDS[unit]


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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

    @staticmethod
    async def _dm(member: discord.Member, content: str) -> None:
        try:
            await member.send(content)
        except discord.Forbidden:
            pass

    @app_commands.command(name="kick", description="Kickt ein Mitglied vom Server.")
    @app_commands.describe(member="Das zu kickende Mitglied.", reason="Grund für den Kick.")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self, interaction: discord.Interaction, member: discord.Member, reason: str = "Kein Grund angegeben"
    ) -> None:
        await self._dm(member, f"👋 Du wurdest von **{interaction.guild.name}** gekickt.\nGrund: {reason}")

        try:
            await member.kick(reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ich habe keine Berechtigung, dieses Mitglied zu kicken (Rollenhierarchie?).", ephemeral=True
            )
            return

        await interaction.response.send_message(f"👋 {member.mention} wurde gekickt.", ephemeral=True)
        await self._log(
            interaction.guild,
            f"👋 {member.mention} wurde von {interaction.user.mention} gekickt. Grund: {reason}",
        )

    @app_commands.command(name="ban", description="Bannt ein Mitglied vom Server.")
    @app_commands.describe(
        member="Das zu bannende Mitglied.",
        reason="Grund für den Bann.",
        delete_message_days="Nachrichten der letzten X Tage löschen (0-7).",
    )
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "Kein Grund angegeben",
        delete_message_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        await self._dm(member, f"🔨 Du wurdest von **{interaction.guild.name}** gebannt.\nGrund: {reason}")

        try:
            await member.ban(
                reason=f"{interaction.user} ({interaction.user.id}): {reason}",
                delete_message_seconds=delete_message_days * 86400,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ich habe keine Berechtigung, dieses Mitglied zu bannen (Rollenhierarchie?).", ephemeral=True
            )
            return

        await interaction.response.send_message(f"🔨 {member.mention} wurde gebannt.", ephemeral=True)
        await self._log(
            interaction.guild,
            f"🔨 {member.mention} wurde von {interaction.user.mention} gebannt. Grund: {reason}",
        )

    @app_commands.command(name="unban", description="Hebt den Bann eines Nutzers auf.")
    @app_commands.describe(user_id="Die User-ID des zu entbannenden Nutzers.")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, user_id: str) -> None:
        if not user_id.isdigit():
            await interaction.response.send_message("Bitte eine gültige User-ID angeben.", ephemeral=True)
            return

        user = discord.Object(id=int(user_id))
        try:
            await interaction.guild.unban(user, reason=f"Entbannt von {interaction.user} ({interaction.user.id})")
        except discord.NotFound:
            await interaction.response.send_message("Dieser Nutzer ist nicht gebannt.", ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message("Ich habe keine Berechtigung, Bans aufzuheben.", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Nutzer `{user_id}` wurde entbannt.", ephemeral=True)
        await self._log(
            interaction.guild, f"✅ Nutzer `{user_id}` wurde von {interaction.user.mention} entbannt."
        )

    @app_commands.command(name="timeout", description="Setzt ein Mitglied für eine bestimmte Dauer auf Stumm.")
    @app_commands.describe(
        member="Das Mitglied.",
        duration="Dauer, z. B. 10m, 2h, 1d (max. 28 Tage).",
        reason="Grund für den Timeout.",
    )
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: str = "Kein Grund angegeben",
    ) -> None:
        seconds = parse_duration(duration)
        if seconds is None or seconds <= 0:
            await interaction.response.send_message(
                "Ungültige Dauer. Beispiele: `10m`, `2h`, `1d`.", ephemeral=True
            )
            return
        if seconds > MAX_TIMEOUT_DAYS * 86400:
            await interaction.response.send_message(
                f"Die Dauer darf maximal {MAX_TIMEOUT_DAYS} Tage betragen.", ephemeral=True
            )
            return

        until = discord.utils.utcnow() + timedelta(seconds=seconds)
        try:
            await member.timeout(until, reason=f"{interaction.user} ({interaction.user.id}): {reason}")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ich habe keine Berechtigung, dieses Mitglied stummzuschalten (Rollenhierarchie?).", ephemeral=True
            )
            return

        await self._dm(
            member,
            f"🔇 Du wurdest auf **{interaction.guild.name}** für {duration} stummgeschaltet.\nGrund: {reason}",
        )
        await interaction.response.send_message(f"🔇 {member.mention} wurde für {duration} stummgeschaltet.", ephemeral=True)
        await self._log(
            interaction.guild,
            f"🔇 {member.mention} wurde von {interaction.user.mention} für {duration} stummgeschaltet. Grund: {reason}",
        )

    @app_commands.command(name="untimeout", description="Hebt den Timeout eines Mitglieds vorzeitig auf.")
    @app_commands.describe(member="Das Mitglied.")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member) -> None:
        try:
            await member.timeout(None, reason=f"Timeout aufgehoben von {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ich habe keine Berechtigung, den Timeout aufzuheben.", ephemeral=True
            )
            return

        await interaction.response.send_message(f"🔊 Timeout von {member.mention} wurde aufgehoben.", ephemeral=True)
        await self._log(
            interaction.guild, f"🔊 Timeout von {member.mention} wurde von {interaction.user.mention} aufgehoben."
        )

    @app_commands.command(name="warn", description="Verwarnt ein Mitglied und speichert die Verwarnung.")
    @app_commands.describe(member="Das zu verwarnende Mitglied.", reason="Grund für die Verwarnung.")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        await db.add_warning(interaction.guild_id, member.id, interaction.user.id, reason)
        await self._dm(member, f"⚠️ Du wurdest auf **{interaction.guild.name}** verwarnt.\nGrund: {reason}")

        await interaction.response.send_message(f"⚠️ {member.mention} wurde verwarnt.", ephemeral=True)
        await self._log(
            interaction.guild,
            f"⚠️ {member.mention} wurde von {interaction.user.mention} verwarnt. Grund: {reason}",
        )

    @app_commands.command(name="warnings", description="Zeigt alle Verwarnungen eines Mitglieds an.")
    @app_commands.describe(member="Das Mitglied.")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        entries = await db.get_warnings(interaction.guild_id, member.id)
        if not entries:
            await interaction.response.send_message(f"{member.mention} hat keine Verwarnungen.", ephemeral=True)
            return

        lines = [
            f"**#{entry['id']}** · {entry['created_at']} UTC · von <@{entry['moderator_id']}>: {entry['reason']}"
            for entry in entries
        ]
        await interaction.response.send_message(
            f"**Verwarnungen von {member.mention} ({len(entries)}):**\n" + "\n".join(lines), ephemeral=True
        )

    @app_commands.command(name="clearwarnings", description="Löscht alle Verwarnungen eines Mitglieds.")
    @app_commands.describe(member="Das Mitglied.")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        removed = await db.clear_warnings(interaction.guild_id, member.id)
        await interaction.response.send_message(
            f"🗑️ {removed} Verwarnung(en) von {member.mention} wurden gelöscht.", ephemeral=True
        )
        await self._log(
            interaction.guild,
            f"🗑️ {interaction.user.mention} hat alle Verwarnungen ({removed}) von {member.mention} gelöscht.",
        )

    @app_commands.command(name="clear", description="Löscht die letzten Nachrichten in diesem Channel.")
    @app_commands.describe(amount="Anzahl der zu löschenden Nachrichten (1-100).")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def clear(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message("Dieser Befehl funktioniert nur in Textchannels.", ephemeral=True)
            return

        deleted = await interaction.channel.purge(limit=amount)
        await interaction.response.send_message(f"🧹 {len(deleted)} Nachricht(en) gelöscht.", ephemeral=True)
        await self._log(
            interaction.guild,
            f"🧹 {interaction.user.mention} hat {len(deleted)} Nachricht(en) in {interaction.channel.mention} gelöscht.",
        )

    @kick.error
    @ban.error
    @unban.error
    @timeout.error
    @untimeout.error
    @warn.error
    @warnings.error
    @clearwarnings.error
    @clear.error
    async def on_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Dafür fehlt dir die benötigte Berechtigung.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
