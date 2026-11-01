import discord
from discord import app_commands
from discord.ext import commands

from database import db


class AutoRole(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        role_id = await db.get_auto_role(member.guild.id)
        if role_id is None:
            return

        role = member.guild.get_role(role_id)
        if role is None:
            return

        try:
            await member.add_roles(role, reason="AutoRole")
        except discord.Forbidden:
            pass

    @app_commands.command(
        name="set-autorole",
        description="Legt die Rolle fest, die neue Mitglieder automatisch erhalten.",
    )
    @app_commands.describe(role="Die Rolle, die neuen Mitgliedern automatisch zugewiesen wird.")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def set_autorole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if role.is_default():
            await interaction.response.send_message(
                "Die @everyone-Rolle kann nicht als AutoRole verwendet werden.", ephemeral=True
            )
            return

        if role >= interaction.guild.me.top_role:
            await interaction.response.send_message(
                "Diese Rolle liegt über oder auf meiner höchsten Rolle. "
                "Ich kann sie so nicht automatisch vergeben.",
                ephemeral=True,
            )
            return

        await db.set_auto_role(interaction.guild_id, role.id)
        await interaction.response.send_message(
            f"Neue Mitglieder erhalten ab jetzt automatisch die Rolle {role.mention}.",
            ephemeral=True,
        )

    @set_autorole.error
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
    await bot.add_cog(AutoRole(bot))
