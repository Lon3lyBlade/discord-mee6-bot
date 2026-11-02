import discord
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
        creator_channel_ids = await db.get_tempvoice_creator_channels(member.guild.id)
        if not creator_channel_ids:
            return

        if after.channel is not None and after.channel.id in creator_channel_ids:
            await self._create_temp_channel(member, after.channel)

        if (
            before.channel is not None
            and before.channel.id not in creator_channel_ids
            and len(before.channel.members) == 0
            and self._is_managed_category(member.guild, before.channel, creator_channel_ids)
        ):
            try:
                await before.channel.delete(reason="Temporärer Sprachkanal ist leer")
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

    @staticmethod
    def _is_managed_category(
        guild: discord.Guild, channel: discord.VoiceChannel, creator_channel_ids: list[int]
    ) -> bool:
        # Ein verlassener Kanal gilt nur dann als "unserer", wenn er in derselben Kategorie
        # liegt wie einer der aktuell konfigurierten Ersteller-Kanäle - sonst würden fremde,
        # leere Voice-Channels in anderen Kategorien fälschlich gelöscht.
        for creator_id in creator_channel_ids:
            creator_channel = guild.get_channel(creator_id)
            if creator_channel is not None and creator_channel.category_id == channel.category_id:
                return True
        return False

    async def _create_temp_channel(
        self, member: discord.Member, creator_channel: discord.VoiceChannel
    ) -> None:
        overwrites = {
            member: discord.PermissionOverwrite(manage_channels=True, move_members=True),
        }

        try:
            new_channel = await member.guild.create_voice_channel(
                name=f"🔊 | {member.display_name}'s Lounge",
                category=creator_channel.category,
                overwrites=overwrites,
                reason=f"Temporärer Sprachkanal für {member}",
            )
            await member.move_to(new_channel, reason="Temporärer Sprachkanal erstellt")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TempVoice(bot))
