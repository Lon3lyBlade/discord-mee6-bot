import asyncio
import logging
import os

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("bot.music")

LAVALINK_HOST = os.environ.get("LAVALINK_HOST", "lavalink")
LAVALINK_PORT = os.environ.get("LAVALINK_PORT", "2333")
LAVALINK_PASSWORD = os.environ.get("LAVALINK_SERVER_PASSWORD")
LAVALINK_SECURE = os.environ.get("LAVALINK_SECURE", "false").lower() == "true"

MAX_QUEUE_PREVIEW = 10


def format_duration(length_ms: int) -> str:
    seconds = length_ms // 1000
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class MusicPlayer(wavelink.Player):
    """Eigene Player-Klasse, nur um den Text-Channel für Now-Playing-Nachrichten zu merken."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.text_channel: discord.abc.Messageable | None = None


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._connect_task: asyncio.Task | None = None

    async def cog_load(self) -> None:
        if not LAVALINK_PASSWORD:
            logger.warning(
                "LAVALINK_SERVER_PASSWORD ist nicht gesetzt - das Musik-Feature bleibt deaktiviert, "
                "bis ein Lavalink-Node konfiguriert ist."
            )
            return

        scheme = "https" if LAVALINK_SECURE else "http"
        node = wavelink.Node(uri=f"{scheme}://{LAVALINK_HOST}:{LAVALINK_PORT}", password=LAVALINK_PASSWORD)
        # wavelink.Pool.connect() versucht bei nicht erreichbarem Node unbegrenzt oft (mit
        # Backoff) erneut zu verbinden und würde hier sonst den kompletten Bot-Start
        # blockieren (setup_hook wartet auf cog_load) - deshalb als Hintergrund-Task, damit
        # ein noch nicht bereiter oder dauerhaft nicht erreichbarer Lavalink-Node den Rest
        # des Bots nicht aufhält.
        self._connect_task = asyncio.create_task(self._connect_node(node))

    async def cog_unload(self) -> None:
        if self._connect_task is not None:
            self._connect_task.cancel()
        if wavelink.Pool.nodes:
            await wavelink.Pool.close()

    async def _connect_node(self, node: wavelink.Node) -> None:
        try:
            await wavelink.Pool.connect(nodes=[node], client=self.bot, cache_capacity=100)
        except Exception:
            logger.exception("Verbindung zum Lavalink-Node fehlgeschlagen.")

    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload) -> None:
        logger.info("Lavalink-Node verbunden: %s (Session-ID: %s)", payload.node.uri, payload.session_id)

    @commands.Cog.listener()
    async def on_wavelink_track_start(self, payload: wavelink.TrackStartEventPayload) -> None:
        player = payload.player
        if not isinstance(player, MusicPlayer) or player.text_channel is None:
            return

        track = payload.track
        embed = discord.Embed(
            title="🎵 Jetzt läuft",
            description=f"[{track.title}]({track.uri})" if track.uri else track.title,
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Künstler", value=track.author or "Unbekannt", inline=True)
        embed.add_field(name="Dauer", value=format_duration(track.length), inline=True)
        if track.artwork:
            embed.set_thumbnail(url=track.artwork)
        if track.source:
            embed.set_footer(text=f"Quelle: {track.source.capitalize()}")

        try:
            await player.text_channel.send(embed=embed)
        except discord.Forbidden:
            pass

    @staticmethod
    def _get_player(interaction: discord.Interaction) -> MusicPlayer | None:
        return interaction.guild.voice_client if isinstance(interaction.guild.voice_client, MusicPlayer) else None

    @app_commands.command(
        name="play",
        description="Spielt einen Song oder eine Playlist ab (YouTube, Spotify, Apple Music, SoundCloud).",
    )
    @app_commands.describe(query="Songname, Künstler oder ein Link (YouTube/Spotify/Apple Music/SoundCloud).")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if not wavelink.Pool.nodes:
            await interaction.response.send_message(
                "Das Musik-System ist nicht konfiguriert (kein Lavalink-Server erreichbar).", ephemeral=True
            )
            return

        voice_state = interaction.user.voice if isinstance(interaction.user, discord.Member) else None
        if voice_state is None or voice_state.channel is None:
            await interaction.response.send_message(
                "Du musst zuerst einem Sprachkanal beitreten.", ephemeral=True
            )
            return

        voice_channel = voice_state.channel
        await interaction.response.defer()

        player = self._get_player(interaction)
        if player is None:
            try:
                player = await voice_channel.connect(cls=MusicPlayer, self_deaf=True)
            except discord.ClientException:
                await interaction.followup.send("Ich bin bereits mit einem Sprachkanal verbunden.", ephemeral=True)
                return
            except asyncio.TimeoutError:
                await interaction.followup.send(
                    "Zeitüberschreitung beim Verbinden mit dem Sprachkanal. Habe ich die nötige Berechtigung?",
                    ephemeral=True,
                )
                return
        elif player.channel.id != voice_channel.id:
            await interaction.followup.send(f"Ich bin bereits in {player.channel.mention} aktiv.", ephemeral=True)
            return

        player.autoplay = wavelink.AutoPlayMode.partial
        player.text_channel = interaction.channel

        try:
            result = await wavelink.Playable.search(query)
        except wavelink.LavalinkLoadException:
            await interaction.followup.send("Dieser Link konnte nicht geladen werden.", ephemeral=True)
            return
        except wavelink.InvalidNodeException:
            await interaction.followup.send(
                "Das Musik-System ist nicht konfiguriert (kein Lavalink-Server erreichbar).", ephemeral=True
            )
            return
        except Exception:
            logger.exception("Fehler bei der Songsuche für Query: %s", query)
            await interaction.followup.send("Der Musik-Server ist aktuell nicht erreichbar.", ephemeral=True)
            return

        if not result:
            await interaction.followup.send("Keine Ergebnisse gefunden.", ephemeral=True)
            return

        if isinstance(result, wavelink.Playlist):
            added = await player.queue.put_wait(result)
            await interaction.followup.send(
                f"🎶 Playlist **{result.name}** mit {added} Song(s) zur Warteschlange hinzugefügt."
            )
        else:
            track = result[0]
            await player.queue.put_wait(track)
            await interaction.followup.send(f"🎶 **{track.title}** zur Warteschlange hinzugefügt.")

        if not player.playing and not player.queue.is_empty:
            await player.play(player.queue.get())

    @app_commands.command(name="skip", description="Überspringt den aktuellen Song.")
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        player = self._get_player(interaction)
        if player is None or not player.playing:
            await interaction.response.send_message("Aktuell läuft kein Song.", ephemeral=True)
            return

        track = player.current
        await player.skip(force=True)
        await interaction.response.send_message(
            f"⏭️ **{track.title if track else 'Song'}** wurde übersprungen."
        )

    @app_commands.command(name="queue", description="Zeigt die aktuelle Warteschlange an.")
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        player = self._get_player(interaction)
        if player is None or (not player.playing and player.queue.is_empty):
            await interaction.response.send_message("Die Warteschlange ist leer.", ephemeral=True)
            return

        embed = discord.Embed(title="🎶 Warteschlange", color=discord.Color.blurple())
        if player.current:
            current = player.current
            value = f"[{current.title}]({current.uri})" if current.uri else current.title
            embed.add_field(name="Aktuell", value=value, inline=False)

        upcoming = list(player.queue)[:MAX_QUEUE_PREVIEW]
        if upcoming:
            lines = [f"**{i}.** {track.title}" for i, track in enumerate(upcoming, start=1)]
            if len(player.queue) > MAX_QUEUE_PREVIEW:
                lines.append(f"… und {len(player.queue) - MAX_QUEUE_PREVIEW} weitere")
            embed.add_field(name=f"Nächste Songs ({len(player.queue)})", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Nächste Songs", value="—", inline=False)

        await interaction.response.send_message(embed=embed)

    async def _stop_and_leave(self, interaction: discord.Interaction) -> None:
        player = self._get_player(interaction)
        if player is None:
            await interaction.response.send_message("Ich bin aktuell in keinem Sprachkanal.", ephemeral=True)
            return

        player.queue.clear()
        await player.disconnect()
        await interaction.response.send_message("⏹️ Musik gestoppt, Sprachkanal verlassen.")

    @app_commands.command(name="stop", description="Stoppt die Musik und verlässt den Sprachkanal.")
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        await self._stop_and_leave(interaction)

    @app_commands.command(name="leave", description="Verlässt den Sprachkanal und stoppt die Musik.")
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        await self._stop_and_leave(interaction)

    @play.error
    @skip.error
    @queue.error
    @stop.error
    @leave.error
    async def on_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.NoPrivateMessage):
            await interaction.response.send_message(
                "Dieser Befehl funktioniert nur auf einem Server.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
