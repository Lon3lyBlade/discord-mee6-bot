import discord
from discord import app_commands
from discord.ext import commands

from database import db


class TempVoice(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        creator_channel_id, category_id = await db.get_tempvoice_config(member.guild.id)
        if creator_channel_id is None:
            return

        if after.channel is not None and after.channel.id == creator_channel_id:
            await self._create_temp_channel(member, after.channel, category_id)

        if (
            before.channel is not None
            and before.channel.id != creator_channel_id
            and before.channel.category_id == category_id
            and len(before.channel.members) == 0
        ):
            try:
                await before.channel.delete(reason="Temporärer Sprachkanal ist leer")
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

    async def _create_temp_channel(
        self,
        member: discord.Member,
        creator_channel: discord.VoiceChannel,
        category_id: int | None,
    ) -> None:
        category = member.guild.get_channel(category_id) if category_id else creator_channel.category
        if not isinstance(category, discord.CategoryChannel):
            category = creator_channel.category

        overwrites = {
            member: discord.PermissionOverwrite(manage_channels=True, move_members=True),
        }

        try:
            new_channel = await member.guild.create_voice_channel(
                name=f"🔊 | {member.display_name}'s Lounge",
                category=category,
                overwrites=overwrites,
                reason=f"Temporärer Sprachkanal für {member}",
            )
            await member.move_to(new_channel, reason="Temporärer Sprachkanal erstellt")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @app_commands.command(
        name="set-tempvoice",
        description="Richtet temporäre Sprachkanäle ein: Ersteller-Kanal und Ziel-Kategorie.",
    )
    @app_commands.describe(
        creator_channel="Der Sprachkanal, dem Nutzer beitreten, um einen eigenen Kanal zu erstellen.",
        category="Die Kategorie, in der die temporären Kanäle erstellt werden.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_tempvoice(
        self,
        interaction: discord.Interaction,
        creator_channel: discord.VoiceChannel,
        category: discord.CategoryChannel,
    ) -> None:
        await db.set_tempvoice_config(interaction.guild_id, creator_channel.id, category.id)
        await interaction.response.send_message(
            f"Temporäre Sprachkanäle eingerichtet: Beitritt zu {creator_channel.mention} erstellt "
            f"automatisch einen neuen Kanal in der Kategorie **{category.name}**.",
            ephemeral=True,
        )

    @set_tempvoice.error
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
    await bot.add_cog(TempVoice(bot))
