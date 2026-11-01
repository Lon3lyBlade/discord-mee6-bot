import discord
from discord import app_commands
from discord.ext import commands

from database import db


def build_welcome_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title="Willkommen!",
        description=f"Hey {member.mention}, schön dass du auf **{member.guild.name}** gelandet bist!",
        color=discord.Color.green(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"Mitglied Nr. {member.guild.member_count}")
    return embed


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        channel_id = await db.get_welcome_channel(member.guild.id)
        if channel_id is None:
            return

        channel = member.guild.get_channel(channel_id)
        if channel is None:
            return

        await channel.send(embed=build_welcome_embed(member))

    @app_commands.command(name="set-welcome", description="Legt den Channel für Willkommensnachrichten fest.")
    @app_commands.describe(channel="Der Textchannel, in dem neue Mitglieder begrüßt werden sollen.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_welcome(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await db.set_welcome_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"Willkommensnachrichten werden ab jetzt in {channel.mention} gepostet.",
            ephemeral=True,
        )

    @app_commands.command(name="test-welcome", description="Postet eine Test-Willkommensnachricht in diesem Channel.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def test_welcome(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=build_welcome_embed(interaction.user))

    @set_welcome.error
    @test_welcome.error
    async def on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "Dafür benötigst du die Berechtigung **Server verwalten**.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
