import random

import discord
from discord import app_commands
from discord.ext import commands

from database import db

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def new_deck() -> list[tuple[str, str]]:
    deck = [(r, s) for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


def hand_value(hand: list[tuple[str, str]]) -> int:
    total = 0
    aces = 0
    for rank, _ in hand:
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def fmt(hand: list[tuple[str, str]], hide_second: bool = False) -> str:
    if hide_second:
        return f"`{hand[0][0]}`{hand[0][1]} `?`"
    return " ".join(f"`{r}`{s}" for r, s in hand)


class BlackjackView(discord.ui.View):
    def __init__(self, player: discord.abc.User, guild_id: int, bet: int) -> None:
        super().__init__(timeout=120)
        self.player = player
        self.guild_id = guild_id
        self.bet = bet
        self.deck = new_deck()
        self.hand = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.message: discord.Message | None = None
        self.finished = False

    # --- Spiellogik -------------------------------------------------------
    def is_blackjack(self, hand: list[tuple[str, str]]) -> bool:
        return len(hand) == 2 and hand_value(hand) == 21

    def build_embed(self, result: str | None = None) -> discord.Embed:
        reveal = result is not None
        color = discord.Color.blurple()
        if result:
            if result.startswith("🎉"):
                color = discord.Color.green()
            elif result.startswith("🤝"):
                color = discord.Color.gold()
            else:
                color = discord.Color.red()
        embed = discord.Embed(title="🃏 Blackjack", color=color)
        embed.add_field(
            name=f"Deine Hand ({hand_value(self.hand)})", value=fmt(self.hand), inline=False
        )
        dealer_val = hand_value(self.dealer) if reveal else hand_value([self.dealer[0]])
        embed.add_field(
            name=f"Dealer ({dealer_val}{'' if reveal else '+'})",
            value=fmt(self.dealer, hide_second=not reveal),
            inline=False,
        )
        if result:
            embed.description = f"**{result}**"
        embed.add_field(name="Einsatz", value=f"{self.bet} 🪙", inline=True)
        embed.set_footer(text=f"Spieler: {self.player.display_name}")
        return embed

    def settle(self) -> tuple[str, int]:
        """Gibt (Text, Auszahlung) zurück. Der Einsatz wurde schon abgebucht, die Auszahlung enthält ihn."""
        p, d = hand_value(self.hand), hand_value(self.dealer)
        if p > 21:
            return f"💥 Überkauft – du verlierst {self.bet} 🪙.", 0
        if self.is_blackjack(self.hand) and not self.is_blackjack(self.dealer):
            payout = self.bet * 5 // 2
            return f"🎉 Blackjack! Du gewinnst {payout - self.bet} 🪙.", payout
        if d > 21:
            return f"🎉 Dealer überkauft – du gewinnst {self.bet} 🪙!", self.bet * 2
        if p > d:
            return f"🎉 Du gewinnst {self.bet} 🪙!", self.bet * 2
        if p < d:
            return f"😢 Der Dealer gewinnt – du verlierst {self.bet} 🪙.", 0
        return "🤝 Unentschieden (Push) – Einsatz zurück.", self.bet

    async def settle_and_pay(self) -> tuple[str, int]:
        text, payout = self.settle()
        balance = None
        if payout:
            balance = await db.change_balance(self.guild_id, self.player.id, payout)
        if balance is None:
            balance = (await db.get_balance(self.guild_id, self.player.id))["balance"]
        return text, balance

    async def finish(self, interaction: discord.Interaction, dealer_plays: bool = True) -> None:
        self.finished = True
        if dealer_plays and hand_value(self.hand) <= 21:
            while hand_value(self.dealer) < 17:
                self.dealer.append(self.deck.pop())
        for child in self.children:
            child.disabled = True
        self.stop()
        text, balance = await self.settle_and_pay()
        embed = self.build_embed(text)
        embed.set_footer(text=f"Spieler: {self.player.display_name} • Guthaben: {balance} 🪙")
        await interaction.response.edit_message(embed=embed, view=self)

    # --- Buttons ----------------------------------------------------------
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(
                "Das ist nicht deine Partie – starte mit `/blackjack` ein eigenes Spiel.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.hand.append(self.deck.pop())
        self.remove_item(self.double)
        if hand_value(self.hand) >= 21:
            await self.finish(interaction)
        else:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.success, emoji="✋")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.finish(interaction)

    @discord.ui.button(label="Double", style=discord.ButtonStyle.secondary, emoji="2️⃣")
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Einsatz verdoppeln, eine Karte ziehen, dann automatisch Stand.
        if await db.change_balance(self.guild_id, self.player.id, -self.bet) is None:
            await interaction.response.send_message(
                "Dein Guthaben reicht zum Verdoppeln nicht aus.", ephemeral=True
            )
            return
        self.bet *= 2
        self.hand.append(self.deck.pop())
        await self.finish(interaction)

    async def on_timeout(self) -> None:
        if self.finished or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        embed = self.build_embed()
        embed.description = f"⏰ Zeit abgelaufen – dein Einsatz von {self.bet} 🪙 ist verfallen."
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass


class Blackjack(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="blackjack", description="Spiele eine Runde Blackjack gegen den Bot.")
    @app_commands.describe(einsatz="Wie viele Coins du setzen möchtest (Standard: 100).")
    @app_commands.guild_only()
    async def blackjack(
        self, interaction: discord.Interaction, einsatz: app_commands.Range[int, 1, 1_000_000] = 100
    ) -> None:
        # Einsatz sofort abbuchen, damit man nicht parallel mehrere Partien mit demselben Guthaben spielt.
        if await db.change_balance(interaction.guild_id, interaction.user.id, -einsatz) is None:
            info = await db.get_balance(interaction.guild_id, interaction.user.id)
            await interaction.response.send_message(
                f"Dein Guthaben ({info['balance']} 🪙) reicht für {einsatz} 🪙 nicht aus. "
                f"Nächste Auffüllung auf 1000 🪙 (falls du darunter bist): <t:{info['next_refill']}:R>.",
                ephemeral=True,
            )
            return

        view = BlackjackView(interaction.user, interaction.guild_id, einsatz)
        if view.is_blackjack(view.hand) or view.is_blackjack(view.dealer):
            view.finished = True
            for child in view.children:
                child.disabled = True
            text, balance = await view.settle_and_pay()
            embed = view.build_embed(text)
            embed.set_footer(text=f"Spieler: {interaction.user.display_name} • Guthaben: {balance} 🪙")
            await interaction.response.send_message(embed=embed, view=view)
            return
        await interaction.response.send_message(embed=view.build_embed(), view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="balance", description="Zeigt dein Coin-Guthaben an.")
    @app_commands.describe(user="Das Mitglied (Standard: du selbst).")
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None) -> None:
        member = user or interaction.user
        info = await db.get_balance(interaction.guild_id, member.id)
        text = f"**{member.display_name}** hat **{info['balance']} 🪙**."
        if info["balance"] < db.WEEKLY_MIN_BALANCE:
            text += f"\nAuffüllung auf {db.WEEKLY_MIN_BALANCE} 🪙: <t:{info['next_refill']}:R>"
        await interaction.response.send_message(text)

    @app_commands.command(name="coins-leaderboard", description="Die reichsten Spieler dieses Servers.")
    @app_commands.guild_only()
    async def coins_leaderboard(self, interaction: discord.Interaction) -> None:
        entries = await db.get_balance_leaderboard(interaction.guild_id, limit=10)
        if not entries:
            await interaction.response.send_message("Noch niemand hat Coins.", ephemeral=True)
            return
        medals = {0: "🥇", 1: "🥈", 2: "🥉"}
        lines = []
        for i, e in enumerate(entries):
            m = interaction.guild.get_member(e["user_id"])
            name = m.display_name if m else f"Unbekannt ({e['user_id']})"
            lines.append(f"{medals.get(i, f'**#{i + 1}**')} {name} — {e['balance']} 🪙")
        await interaction.response.send_message(
            embed=discord.Embed(title="🪙 Coins-Rangliste", description="\n".join(lines), color=discord.Color.gold())
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Blackjack(bot))
