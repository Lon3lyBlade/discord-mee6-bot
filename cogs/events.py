import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

from database import db

logger = logging.getLogger("bot.events")

# Eingaben (Slash-Command wie Dashboard) werden als Europe/Berlin-Ortszeit interpretiert
# und für die Speicherung/Discord-Timestamps nach UTC konvertiert.
EVENT_TZ = ZoneInfo("Europe/Berlin")
DATE_FORMATS = ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M")

RSVP_STATUSES = ("yes", "no", "maybe")
RSVP_LABELS = {"yes": "✅ Zusagen", "no": "❌ Absagen", "maybe": "❓ Vielleicht"}


def parse_event_datetime(raw: str) -> datetime | None:
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=EVENT_TZ)
        except ValueError:
            continue
    return None


def to_utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def build_event_embed(event: dict, rsvps: dict[str, list[int]]) -> discord.Embed:
    event_time = datetime.fromisoformat(event["event_time"])
    ts = int(event_time.timestamp())
    cancelled = event["status"] == "cancelled"

    embed = discord.Embed(
        title=("🚫 " if cancelled else "🗓️ ") + event["title"],
        description=event.get("description") or None,
        color=discord.Color.red() if cancelled else discord.Color.blurple(),
    )
    embed.add_field(name="Termin", value=f"<t:{ts}:F> (<t:{ts}:R>)", inline=False)
    if event.get("target_role_id"):
        embed.add_field(name="Nur sichtbar für", value=f"<@&{event['target_role_id']}>", inline=False)

    for status in RSVP_STATUSES:
        members = rsvps.get(status, [])
        value = "\n".join(f"<@{uid}>" for uid in members) if members else "—"
        if len(value) > 1024:
            value = value[:1000] + "\n…"
        embed.add_field(name=f"{RSVP_LABELS[status]} ({len(members)})", value=value, inline=True)

    embed.set_footer(text="Dieses Event wurde abgesagt." if cancelled else f"Event-ID {event['id']}")
    return embed


async def _grouped_rsvps(event_id: int) -> dict[str, list[int]]:
    grouped: dict[str, list[int]] = {status: [] for status in RSVP_STATUSES}
    for row in await db.get_rsvps(event_id):
        grouped.setdefault(row["status"], []).append(row["user_id"])
    return grouped


class RSVPButton(discord.ui.Button):
    def __init__(self, event_id: int, status: str, label: str, style: discord.ButtonStyle) -> None:
        # event_id steckt im custom_id, damit jeder Button nach einem Bot-Neustart
        # (persistente Views, siehe Events.cog_load) dem richtigen Event zugeordnet wird.
        super().__init__(label=label, style=style, custom_id=f"event_rsvp:{event_id}:{status}")
        self.event_id = event_id
        self.status = status

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_rsvp(interaction, self.event_id, self.status)


class EventRSVPView(discord.ui.View):
    def __init__(self, event_id: int) -> None:
        super().__init__(timeout=None)
        self.add_item(RSVPButton(event_id, "yes", "✅ Zusage", discord.ButtonStyle.success))
        self.add_item(RSVPButton(event_id, "no", "❌ Absage", discord.ButtonStyle.danger))
        self.add_item(RSVPButton(event_id, "maybe", "❓ Vielleicht", discord.ButtonStyle.secondary))


async def handle_rsvp(interaction: discord.Interaction, event_id: int, status: str) -> None:
    event = await db.get_event(event_id)
    if event is None or event["status"] == "cancelled":
        await interaction.response.send_message(
            "Dieses Event existiert nicht mehr oder wurde abgesagt.", ephemeral=True
        )
        return

    if event["target_role_id"] is not None:
        member = interaction.user
        has_role = isinstance(member, discord.Member) and any(
            role.id == event["target_role_id"] for role in member.roles
        )
        if not has_role:
            await interaction.response.send_message(
                "Du hast für dieses Event keine Berechtigung.", ephemeral=True
            )
            return

    await db.set_rsvp(event_id, interaction.user.id, status)
    rsvps = await _grouped_rsvps(event_id)
    await interaction.response.edit_message(embed=build_event_embed(event, rsvps))


async def apply_role_restriction(channel: discord.TextChannel, role: discord.Role) -> None:
    """Beschränkt die Sichtbarkeit des Channels auf die angegebene Rolle.

    Bestehende Overwrites für andere Rollen/Mitglieder bleiben unangetastet - es werden
    gezielt nur @everyone und die Ziel-Rolle gesetzt.
    """
    try:
        await channel.set_permissions(
            channel.guild.default_role, view_channel=False, reason="Event-Rollenbeschränkung"
        )
        await channel.set_permissions(role, view_channel=True, reason="Event-Rollenbeschränkung")
    except discord.Forbidden:
        pass


async def post_event_message(bot: commands.Bot, event: dict) -> int | None:
    channel = bot.get_channel(event["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return None

    if event.get("target_role_id"):
        role = channel.guild.get_role(event["target_role_id"])
        if role is not None:
            await apply_role_restriction(channel, role)

    embed = build_event_embed(event, {})
    view = EventRSVPView(event["id"])
    try:
        message = await channel.send(embed=embed, view=view)
    except discord.Forbidden:
        return None

    await db.set_event_message(event["id"], message.id)
    return message.id


async def refresh_event_message(bot: commands.Bot, event_id: int) -> bool:
    """Aktualisiert die bereits gepostete Event-Nachricht nach einer Bearbeitung im Dashboard."""
    event = await db.get_event(event_id)
    if event is None or event["message_id"] is None:
        return False

    channel = bot.get_channel(event["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        message = await channel.fetch_message(event["message_id"])
    except (discord.NotFound, discord.Forbidden):
        return False

    rsvps = await _grouped_rsvps(event_id)
    embed = build_event_embed(event, rsvps)
    view = None if event["status"] == "cancelled" else EventRSVPView(event_id)
    try:
        await message.edit(embed=embed, view=view)
    except discord.HTTPException:
        return False
    return True


async def delete_event_message(bot: commands.Bot, event_id: int) -> None:
    event = await db.get_event(event_id)
    if event is None or event["message_id"] is None:
        return

    channel = bot.get_channel(event["channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        message = await channel.fetch_message(event["message_id"])
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Persistente Views nach einem Bot-Neustart wiederherstellen, damit alte
        # Event-Nachrichten weiterhin auf Button-Klicks reagieren.
        for event in await db.get_scheduled_events_with_message():
            self.bot.add_view(EventRSVPView(event["id"]), message_id=event["message_id"])

    event_group = app_commands.Group(name="event", description="Event- und Terminverwaltung")

    @event_group.command(name="create", description="Erstellt ein neues Event mit RSVP-Buttons.")
    @app_commands.describe(
        titel="Titel des Events.",
        datum_uhrzeit="Datum und Uhrzeit, z. B. 25.12.2026 18:00 (Europe/Berlin).",
        channel="Channel, in dem das Event gepostet wird.",
        ziel_rolle="Optional: Nur Mitglieder dieser Rolle sehen das Event und können teilnehmen.",
        beschreibung="Optionale Beschreibung des Events.",
    )
    @app_commands.checks.has_permissions(manage_events=True)
    @app_commands.guild_only()
    async def create(
        self,
        interaction: discord.Interaction,
        titel: str,
        datum_uhrzeit: str,
        channel: discord.TextChannel,
        ziel_rolle: discord.Role | None = None,
        beschreibung: str = "",
    ) -> None:
        event_time = parse_event_datetime(datum_uhrzeit)
        if event_time is None:
            await interaction.response.send_message(
                "Ungültiges Datum/Uhrzeit-Format. Beispiel: `25.12.2026 18:00`.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        event_id = await db.create_event(
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            title=titel,
            description=beschreibung,
            event_time=to_utc_iso(event_time),
            target_role_id=ziel_rolle.id if ziel_rolle else None,
            created_by=interaction.user.id,
        )
        event = await db.get_event(event_id)
        message_id = await post_event_message(self.bot, event)
        if message_id is None:
            await db.delete_event(event_id)
            await interaction.followup.send(
                "Konnte das Event nicht im gewählten Channel posten (fehlende Berechtigung?).", ephemeral=True
            )
            return

        await interaction.followup.send(f"✅ Event **{titel}** wurde in {channel.mention} erstellt.", ephemeral=True)

    @create.error
    async def on_create_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("Dafür fehlt dir die benötigte Berechtigung.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Events(bot))
