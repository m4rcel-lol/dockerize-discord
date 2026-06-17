from __future__ import annotations

import asyncio
import logging
import sys

import discord
from discord.ext import commands

from .config import Config
from .database import Database
from .embeds import failure_embed
from .emojis import resolve_bot_emoji_token

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("dockerize")

EXTENSIONS = (
    "bot.cogs.setup",
    "bot.cogs.containers",
    "bot.cogs.channels",
    "bot.cogs.invites",
    "bot.cogs.admin",
)


class DockerizeBot(commands.Bot):
    def __init__(self, config: Config) -> None:
        intents = discord.Intents.default()
        intents.message_content = False
        super().__init__(command_prefix=commands.when_mentioned, intents=intents, help_command=None)
        self.config = config
        self.db = Database(config.database_path)
        self._emojis_resolved = False

    async def setup_hook(self) -> None:
        await self.db.connect()
        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.info("loaded extension %s", extension)
        if self.config.sync_commands:
            synced = await self.tree.sync()
            log.info("synced %s slash commands", len(synced))

    async def resolve_config_emojis(self) -> None:
        """Resolve .env emoji names into real guild or application emoji mentions."""
        if self._emojis_resolved:
            return

        self.config.emoji_docker = await resolve_bot_emoji_token(self, self.config.emoji_docker)
        self.config.emoji_success = await resolve_bot_emoji_token(self, self.config.emoji_success)
        self.config.emoji_failure = await resolve_bot_emoji_token(self, self.config.emoji_failure)
        self.config.emoji_warning = await resolve_bot_emoji_token(self, self.config.emoji_warning)
        self._emojis_resolved = True

    async def on_ready(self) -> None:
        await self.resolve_config_emojis()
        log.info("Dockerize online as %s (%s)", self.user, self.user.id if self.user else "unknown")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="docker compose up -d"))

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError) -> None:
    bot = interaction.client
    emoji = getattr(getattr(bot, "config", None), "emoji_failure", ":failure:")
    log.exception("app command error: %s", error)
    embed = failure_embed(emoji, "Runtime command failed", f"```console\nError: {error}\n```")
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


async def run() -> None:
    config = Config.from_env()
    bot = DockerizeBot(config)
    bot.tree.on_error = on_app_command_error
    await bot.start(config.discord_token)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("shutdown requested")
    except Exception as exc:  # noqa: BLE001
        log.critical("fatal startup error: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
