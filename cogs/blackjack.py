import random

import discord
from discord import app_commands
from discord.ext import commands

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
    def __init__(self, player: discord.abc.User) -> None:
        super().__init__(timeout=120)
        self.player = player
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
        embed.set_footer(text=f"Spieler: {self.player.display_name}")
        return embed

    def settle(self) -> str:
        p, d = hand_value(self.hand), hand_value(self.dealer)
        if p > 21:
            return "💥 Überkauft – du verlierst."
        if self.is_blackjack(self.hand) and not self.is_blackjack(self.dealer):
            return "🎉 Blackjack! Du gewinnst."
        if d > 21:
            return "🎉 Dealer überkauft – du gewinnst!"
        if p > d:
            return "🎉 Du gewinnst!"
        if p < d:
            return "😢 Der Dealer gewinnt."
        return "🤝 Unentschieden (Push)."

    async def finish(self, interaction: discord.Interaction, dealer_plays: bool = True) -> None:
        self.finished = True
        if dealer_plays and hand_value(self.hand) <= 21:
            while hand_value(self.dealer) < 17:
                self.dealer.append(self.deck.pop())
        for child in self.children:
            child.disabled = True
        self.stop()
        await interaction.response.edit_message(embed=self.build_embed(self.settle()), view=self)

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
        # Eine Karte ziehen, dann automatisch Stand.
        self.hand.append(self.deck.pop())
        await self.finish(interaction)

    async def on_timeout(self) -> None:
        if self.finished or self.message is None:
            return
        for child in self.children:
            child.disabled = True
        embed = self.build_embed()
        embed.description = "⏰ Zeit abgelaufen – Partie beendet."
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass


class Blackjack(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="blackjack", description="Spiele eine Runde Blackjack gegen den Bot.")
    async def blackjack(self, interaction: discord.Interaction) -> None:
        view = BlackjackView(interaction.user)
        if view.is_blackjack(view.hand) or view.is_blackjack(view.dealer):
            view.finished = True
            for child in view.children:
                child.disabled = True
            await interaction.response.send_message(embed=view.build_embed(view.settle()), view=view)
            return
        await interaction.response.send_message(embed=view.build_embed(), view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Blackjack(bot))
