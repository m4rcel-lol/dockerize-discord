from __future__ import annotations

import math

import discord
from discord import app_commands
from discord.ext import commands

from bot import permissions
from bot.animations import play_terminal_animation
from bot.checks import require_staff, send_dm_safe, short_container_id
from bot.embeds import console_block, docker_embed, failure_embed, success_embed, suspended_embed, warning_embed
from bot.views import PaginationView
from .common import ensure_container_objects, invited_members, owner_member, staff_role_for_guild


class AdminCog(commands.Cog):
    admin = app_commands.Group(name="admin", description="Dockerize staff commands.")
    container = app_commands.Group(name="container", description="Admin container controls.", parent=admin)

    def __init__(self, bot) -> None:
        self.bot = bot

    async def _get_container_for_user(self, guild: discord.Guild, user: discord.User | discord.Member) -> dict | None:
        return await self.bot.db.get_container(guild.id, user.id)

    async def _restore(self, guild: discord.Guild, container: dict) -> None:
        owner = await owner_member(guild, int(container["owner_id"]))
        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        if owner is None or guild.me is None or not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("Owner, bot member, or category is missing.")
        staff_role = await staff_role_for_guild(guild, self.bot.db)
        invites = await invited_members(guild, self.bot.db, int(container["owner_id"]))
        await permissions.restore_container_permissions(category, guild, owner, staff_role, guild.me, invites, visibility=container["visibility"])

    @container.command(name="check", description="Start staff inspection mode for a user container.")
    async def check(self, interaction: discord.Interaction, user: discord.User, reason: str) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self._get_container_for_user(guild, user)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Inspection failed", "That user does not have a Dockerize container."), ephemeral=True)
            return
        await self.bot.db.update_container(guild.id, user.id, inspection_active=1)
        embed = warning_embed(
            self.bot.config.emoji_warning,
            "Container inspection started",
            console_block(f"$ docker inspect {container['container_name']}\n[!] Staff inspection mode enabled\n[!] Owner notified") + f"\nReason: {reason}",
        )
        await interaction.response.send_message(embed=embed)
        await send_dm_safe(
            user,
            warning_embed(
                self.bot.config.emoji_warning,
                "Your Dockerize container is being checked",
                f"A server staff member has started checking your container.\n\nReason: {reason}\nThis does not mean you are suspended. It means staff is reviewing the container.",
            ),
        )

    @container.command(name="suspend", description="Suspend a user container and lock normal users out.")
    async def suspend(self, interaction: discord.Interaction, user: discord.User, reason: str) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self._get_container_for_user(guild, user)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Suspension failed", "That user does not have a Dockerize container."), ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        container = await ensure_container_objects(guild, self.bot.db, container)
        owner = await owner_member(guild, user.id)
        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        if owner and guild.me and isinstance(category, discord.CategoryChannel):
            staff_role = await staff_role_for_guild(guild, self.bot.db)
            invites = await invited_members(guild, self.bot.db, user.id)
            await permissions.apply_container_suspended(category, guild, owner, staff_role, guild.me, invites)
            await category.edit(name=f"suspended-{owner.display_name}"[:100], reason=f"Dockerize suspension by {interaction.user}")
        await self.bot.db.update_container(guild.id, user.id, status="suspended", suspended_reason=reason)
        frames = [
            f"$ docker container pause {container['container_name']}\n[!] Freezing permissions",
            f"$ docker container pause {container['container_name']}\n[!] Freezing permissions\n[!] Locking owner access",
            f"$ docker container pause {container['container_name']}\n[!] Freezing permissions\n[!] Locking owner access\n[!] Staff inspection mode enabled",
        ]
        final = suspended_embed(self.bot.config.emoji_failure, "Container suspended", f"{user.mention}'s Dockerize runtime was suspended.\n\nReason: {reason}")
        await play_terminal_animation(interaction, self.bot.config.emoji_warning, "docker container pause", frames, final, delay=0.65)
        await send_dm_safe(user, failure_embed(self.bot.config.emoji_failure, "Your Dockerize container was suspended", f"Reason: {reason}\n\nYou can no longer access the container until staff unsuspends it."))

    @container.command(name="unsuspend", description="Unsuspend a user container and restore access.")
    async def unsuspend(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self._get_container_for_user(guild, user)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Unsuspend failed", "That user does not have a Dockerize container."), ephemeral=True)
            return
        container = await ensure_container_objects(guild, self.bot.db, container)
        await self.bot.db.update_container(guild.id, user.id, status="up", suspended_reason=None)
        container["status"] = "up"
        await self._restore(guild, container)
        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        if isinstance(category, discord.CategoryChannel):
            owner = await owner_member(guild, user.id)
            if owner:
                await category.edit(name=f"🐳 container-{owner.display_name}"[:100], reason=f"Dockerize unsuspension by {interaction.user}")
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "Container unsuspended", f"{user.mention}'s container is online again."))
        await send_dm_safe(user, success_embed(self.bot.config.emoji_success, "Your Dockerize container was unsuspended", "Your container access has been restored and the status is now `up`."))

    @container.command(name="delete", description="Force-delete another user's Dockerize container.")
    async def admin_delete(self, interaction: discord.Interaction, user: discord.User, reason: str) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self._get_container_for_user(guild, user)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Delete failed", "That user does not have a Dockerize container."), ephemeral=True)
            return
        await interaction.response.defer(thinking=True, ephemeral=True)
        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        if isinstance(category, discord.CategoryChannel):
            for channel in list(category.channels):
                try:
                    await channel.delete(reason=f"Dockerize admin delete by {interaction.user}: {reason}")
                except discord.HTTPException:
                    pass
            try:
                await category.delete(reason=f"Dockerize admin delete by {interaction.user}: {reason}")
            except discord.HTTPException:
                pass
        await self.bot.db.delete_container_record(guild.id, user.id)
        await interaction.edit_original_response(embed=failure_embed(self.bot.config.emoji_failure, "Container force-deleted", f"{user.mention}'s container was deleted.\n\nReason: {reason}"))
        await send_dm_safe(user, failure_embed(self.bot.config.emoji_failure, "Your Dockerize container was deleted", f"A staff member deleted your container.\n\nReason: {reason}"))

    @container.command(name="list", description="List all Dockerize containers in this server.")
    async def list_containers(self, interaction: discord.Interaction) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        containers = await self.bot.db.list_containers(interaction.guild.id)
        if not containers:
            await interaction.response.send_message(embed=docker_embed(self.bot.config.emoji_docker, "docker ps -a", "No Dockerize containers exist in this server."), ephemeral=True)
            return
        per_page = 8
        pages: list[discord.Embed] = []
        total_pages = math.ceil(len(containers) / per_page)
        for idx in range(0, len(containers), per_page):
            chunk = containers[idx : idx + per_page]
            rows = ["CONTAINER ID   OWNER           STATUS       VISIBILITY"]
            for item in chunk:
                rows.append(f"{short_container_id(item):<14} <@{item['owner_id']}> {item['status']:<12} {item['visibility']}")
            page_no = idx // per_page + 1
            embed = docker_embed(self.bot.config.emoji_docker, f"docker ps -a • page {page_no}/{total_pages}", console_block("\n".join(rows)))
            pages.append(embed)
        view = PaginationView(pages, author_id=interaction.user.id) if len(pages) > 1 else None
        await interaction.response.send_message(embed=pages[0], view=view, ephemeral=True)

    @container.command(name="info", description="Show detailed info for a user's Dockerize container.")
    async def info(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        item = await self._get_container_for_user(guild, user)
        if not item:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Info failed", "That user does not have a Dockerize container."), ephemeral=True)
            return
        channels = await self.bot.db.get_container_channels(guild.id, user.id)
        invites = await self.bot.db.get_invites(guild.id, user.id)
        category = guild.get_channel(int(item["category_id"])) if item.get("category_id") else None
        text = (
            f"ID: `{short_container_id(item)}`\n"
            f"Owner: {user.mention}\n"
            f"Name: `{item['container_name']}`\n"
            f"Category: {category.mention if isinstance(category, discord.CategoryChannel) else '`missing`'}\n"
            f"Status: `{item['status']}`\n"
            f"Visibility: `{item['visibility']}`\n"
            f"Channels: `{len(channels)}`\n"
            f"Invited users: `{len(invites)}`\n"
            f"Inspection active: `{'yes' if int(item['inspection_active']) else 'no'}`\n"
            f"Suspended reason: `{item.get('suspended_reason') or 'none'}`\n"
            f"Created: `{item['created_at']}`\n"
            f"Updated: `{item['updated_at']}`"
        )
        await interaction.response.send_message(embed=docker_embed(self.bot.config.emoji_docker, "Container info", text), ephemeral=True)

    @container.command(name="force-private", description="Force a user container into private mode.")
    async def force_private(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._force_visibility(interaction, user, "private")

    @container.command(name="force-public", description="Force a user container into public mode.")
    async def force_public(self, interaction: discord.Interaction, user: discord.User) -> None:
        await self._force_visibility(interaction, user, "public")

    async def _force_visibility(self, interaction: discord.Interaction, user: discord.User, visibility: str) -> None:
        if not await require_staff(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self._get_container_for_user(guild, user)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Visibility update failed", "That user does not have a Dockerize container."), ephemeral=True)
            return
        if container["status"] == "suspended":
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Visibility update failed", "Suspended containers cannot be made public/private until unsuspended."), ephemeral=True)
            return
        container = await ensure_container_objects(guild, self.bot.db, container)
        container["visibility"] = visibility
        await self.bot.db.update_container(guild.id, user.id, visibility=visibility)
        await self._restore(guild, container)
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, f"Container forced {visibility}", f"{user.mention}'s container visibility is now `{visibility}`."), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(AdminCog(bot))
