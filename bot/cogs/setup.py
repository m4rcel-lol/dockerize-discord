from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.embeds import success_embed

log = logging.getLogger(__name__)


class SetupCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command(name="setup", description="Attach the Dockerize daemon to this server.")
    @app_commands.describe(command_channel="The channel where users can run Dockerize commands", staff_role="The staff role that can inspect/manage containers")
    @app_commands.checks.has_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction, command_channel: discord.TextChannel, staff_role: discord.Role) -> None:
        if not interaction.guild:
            return
        await self.bot.db.upsert_guild_settings(interaction.guild.id, command_channel.id, staff_role.id)
        embed = success_embed(
            self.bot.config.emoji_success,
            "Dockerize daemon initialized",
            (
                "The Dockerize runtime has been attached to this server.\n\n"
                f"Command Channel: {command_channel.mention}\n"
                f"Staff Role: {staff_role.mention}\n"
                "Container Mode: `user-isolated`\n"
                "Status: `online`"
            ),
        )
        log.info("guild %s configured command_channel=%s staff_role=%s", interaction.guild.id, command_channel.id, staff_role.id)
        await interaction.response.send_message(embed=embed)

    @setup.error
    async def setup_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Administrator permission to run `/setup`.", ephemeral=True)
            return
        raise error


async def setup(bot) -> None:
    await bot.add_cog(SetupCog(bot))
