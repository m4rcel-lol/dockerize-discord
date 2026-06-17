from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.checks import is_staff_member, require_configured_channel, sanitize_name
from bot.embeds import docker_embed, failure_embed, success_embed
from .common import ensure_container_objects, staff_role_for_guild


class ChannelsCog(commands.Cog):
    group = app_commands.Group(name="channel", description="Manage channels inside your Dockerize container.")

    def __init__(self, bot) -> None:
        self.bot = bot

    async def _is_staff(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return False
        settings = await self.bot.db.get_guild_settings(interaction.guild.id)
        staff_role_id = int(settings["staff_role_id"]) if settings and settings.get("staff_role_id") else None
        return is_staff_member(interaction.user, staff_role_id)

    @group.command(name="create", description="Mount a text or voice channel into your container.")
    @app_commands.describe(name="Channel name", type="Channel type")
    async def create(self, interaction: discord.Interaction, name: str, type: Literal["text", "voice"]) -> None:  # noqa: A002 - slash option name requested by spec
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel allocation failed", "Create a container first with `/container create`."), ephemeral=True)
            return
        if container["status"] != "up":
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel allocation failed", f"Container status is `{container['status']}`. Start it with `/container up`."), ephemeral=True)
            return

        count = await self.bot.db.count_container_channels(guild.id, interaction.user.id)
        if count >= self.bot.config.max_channels_per_container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel allocation failed", "You reached the maximum channel limit for this container."), ephemeral=True)
            return

        container = await ensure_container_objects(guild, self.bot.db, container)
        category = guild.get_channel(int(container["category_id"]))
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel allocation failed", "Container category is missing."), ephemeral=True)
            return

        channel_name = sanitize_name(name, default="channel", max_length=32)
        if type == "voice":
            channel = await guild.create_voice_channel(channel_name, category=category, reason=f"Dockerize channel mounted by {interaction.user}")
        else:
            channel = await guild.create_text_channel(channel_name, category=category, reason=f"Dockerize channel mounted by {interaction.user}")
        await channel.edit(sync_permissions=True)
        await self.bot.db.add_container_channel(guild.id, interaction.user.id, channel.id, channel.name, type, is_system=False)
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "Channel mounted", f"The channel {channel.mention} was created inside your container."))

    @group.command(name="delete", description="Unmount a channel from your container.")
    @app_commands.describe(channel="The channel to delete", confirm="Must be true to delete", force="Staff-only override for system channels")
    async def delete(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel, confirm: bool, force: bool = False) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        acting_as_staff = await self._is_staff(interaction)

        if not container and acting_as_staff and channel.category_id:
            # Staff can force-delete a managed channel by locating its category owner.
            all_containers = await self.bot.db.list_containers(guild.id)
            container = next((c for c in all_containers if c.get("category_id") == str(channel.category_id)), None)

        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel unmount failed", "No matching container was found."), ephemeral=True)
            return
        if channel.category_id != int(container["category_id"]):
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel unmount failed", "That channel is not inside your Dockerize container."), ephemeral=True)
            return
        if not confirm:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Channel unmount not confirmed", "Run the command again with `confirm:true`."), ephemeral=True)
            return

        record = await self.bot.db.get_channel_record(guild.id, int(container["owner_id"]), channel.id)
        if record and int(record["is_system"]) and not (force and acting_as_staff):
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "System volume protected", "Required system channels cannot be deleted unless staff uses `force:true`."), ephemeral=True)
            return

        await channel.delete(reason=f"Dockerize channel unmounted by {interaction.user}")
        await self.bot.db.delete_container_channel(guild.id, int(container["owner_id"]), channel.id)
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "Channel unmounted", f"`#{channel.name}` was removed from the container."), ephemeral=True)

    @group.command(name="list", description="List mounted channels inside your container.")
    async def list_channels(self, interaction: discord.Interaction) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        channels = await self.bot.db.get_container_channels(guild.id, interaction.user.id)
        if not channels:
            output = "No mounted channels found."
        else:
            rows = ["NAME                 TYPE    SYSTEM"]
            for item in channels:
                name = item["channel_name"][:20]
                rows.append(f"{name:<20} {item['channel_type']:<7} {'yes' if int(item['is_system']) else 'no'}")
            output = "```console\n" + "\n".join(rows) + "\n```"
        await interaction.response.send_message(embed=docker_embed(self.bot.config.emoji_docker, "Mounted channels", output), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(ChannelsCog(bot))
