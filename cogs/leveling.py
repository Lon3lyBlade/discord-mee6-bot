import random
import time

import discord
from discord import app_commands
from discord.ext import commands

from database import db

MIN_XP_PER_MESSAGE = 15
MAX_XP_PER_MESSAGE = 25
XP_COOLDOWN_SECONDS = 60
PROGRESS_BAR_LENGTH = 20


def build_levelup_embed(member: discord.Member, level: int) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 Level Up!",
        description=f"Glückwunsch {member.mention}, du hast **Level {level}** erreicht!",
        color=discord.Color.gold(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed


def build_progress_bar(xp: int, level: int) -> tuple[str, int, int]:
    """Gibt (Balken, aktuelle Levelschwelle, nächste Levelschwelle) zurück."""
    current_threshold = db.xp_for_level(level)
    next_threshold = db.xp_for_level(level + 1)
    span = next_threshold - current_threshold
    progress = (xp - current_threshold) / span if span else 0
    progress = max(0.0, min(1.0, progress))

    filled = round(progress * PROGRESS_BAR_LENGTH)
    bar = "█" * filled + "░" * (PROGRESS_BAR_LENGTH - filled)
    return bar, current_threshold, next_threshold


class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_xp_at: dict[tuple[int, int], float] = {}

    def _on_cooldown(self, guild_id: int, user_id: int) -> bool:
        key = (guild_id, user_id)
        now = time.monotonic()
        last = self._last_xp_at.get(key)
        if last is not None and now - last < XP_COOLDOWN_SECONDS:
            return True
        self._last_xp_at[key] = now
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.author, discord.Member):
            return

        guild_id = message.guild.id
        if not await db.get_xp_enabled(guild_id):
            return

        excluded_channels = await db.get_level_excluded_channels(guild_id)
        if message.channel.id in excluded_channels:
            return

        excluded_roles = await db.get_level_excluded_roles(guild_id)
        if any(role.id in excluded_roles for role in message.author.roles):
            return

        if self._on_cooldown(guild_id, message.author.id):
            return

        gained = random.randint(MIN_XP_PER_MESSAGE, MAX_XP_PER_MESSAGE)
        result = await db.add_xp(guild_id, message.author.id, gained)

        if result["level"] > result["old_level"]:
            await self._announce_levelup(message, result["level"])

    async def _announce_levelup(self, message: discord.Message, level: int) -> None:
        mode = await db.get_levelup_notify_mode(message.guild.id)
        if mode == "disabled":
            return

        embed = build_levelup_embed(message.author, level)
        if mode == "dm":
            try:
                await message.author.send(embed=embed)
            except discord.Forbidden:
                pass
            return

        try:
            await message.channel.send(embed=embed)
        except discord.Forbidden:
            pass

    @app_commands.command(name="rank", description="Zeigt Level und XP eines Mitglieds an.")
    @app_commands.describe(user="Das Mitglied (Standard: du selbst).")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        member = user or interaction.user
        data = await db.get_user_level(interaction.guild_id, member.id) or {"xp": 0, "level": 0}
        xp, level = data["xp"], data["level"]
        bar, current_threshold, next_threshold = build_progress_bar(xp, level)

        embed = discord.Embed(title=f"Rang von {member.display_name}", color=discord.Color.blurple())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Level", value=str(level), inline=True)
        embed.add_field(name="XP", value=f"{xp} / {next_threshold}", inline=True)
        embed.add_field(name="Fortschritt zum nächsten Level", value=f"`{bar}`", inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Zeigt die Top 10 Nutzer dieses Servers.")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        entries = await db.get_leaderboard(interaction.guild_id, limit=10)
        if not entries:
            await interaction.response.send_message(
                "Es gibt noch keine XP-Einträge auf diesem Server.", ephemeral=True
            )
            return

        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for index, entry in enumerate(entries):
            member = interaction.guild.get_member(entry["user_id"])
            name = member.display_name if member else f"Unbekannter Nutzer ({entry['user_id']})"
            prefix = medals.get(index, f"**#{index + 1}**")
            lines.append(f"{prefix} {name} — Level {entry['level']} ({entry['xp']} XP)")

        embed = discord.Embed(
            title="🏆 Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leveling(bot))
