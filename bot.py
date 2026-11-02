import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from database.db import init_db

# load_dotenv() ist rein für die lokale Entwicklung gedacht und no-opt, falls keine
# .env-Datei existiert. Im Unraid-Container kommen alle Werte über echte
# Umgebungsvariablen aus dem Container-Template.
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


class MyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await init_db()

        for filename in os.listdir("cogs"):
            if filename.endswith(".py") and not filename.startswith("_"):
                await self.load_extension(f"cogs.{filename[:-3]}")
                logger.info("Cog geladen: %s", filename)

        synced = await self.tree.sync()
        logger.info("%d Slash-Command(s) synchronisiert.", len(synced))


bot = MyBot()


@bot.event
async def on_ready() -> None:
    logger.info("Eingeloggt als %s (ID: %s)", bot.user, bot.user.id)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError(
            "Umgebungsvariable DISCORD_TOKEN ist nicht gesetzt. Bitte im Docker-/Unraid-Template eintragen."
        )
    bot.run(token)


if __name__ == "__main__":
    main()
