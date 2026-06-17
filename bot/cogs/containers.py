from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot import permissions
from bot.animations import play_terminal_animation
from bot.checks import (
    container_category_name,
    ensure_not_suspended,
    has_manage_channel_permissions,
    require_configured_channel,
    sanitize_name,
    send_dm_safe,
    short_container_id,
)
from bot.embeds import console_block, docker_embed, failure_embed, success_embed, warning_embed
from .common import create_container_channels, ensure_container_objects, invited_members, mention_channel, owner_member, staff_role_for_guild

log = logging.getLogger(__name__)


class ContainersCog(commands.Cog):
    group = app_commands.Group(name="container", description="Manage your Dockerize container.")

    def __init__(self, bot) -> None:
        self.bot = bot

    async def _staff_role(self, guild: discord.Guild) -> discord.Role | None:
        return await staff_role_for_guild(guild, self.bot.db)

    async def _apply_visibility(self, guild: discord.Guild, container: dict, visibility: str) -> None:
        owner = await owner_member(guild, int(container["owner_id"]))
        if owner is None or guild.me is None:
            raise RuntimeError("Missing owner or bot member.")
        category = guild.get_channel(int(container["category_id"]))
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("Container category is missing.")
        staff_role = await self._staff_role(guild)
        invites = await invited_members(guild, self.bot.db, int(container["owner_id"]))
        if visibility == "public":
            await permissions.apply_container_public(category, guild, owner, staff_role, guild.me, invites)
        else:
            await permissions.apply_container_private(category, guild, owner, staff_role, guild.me, invites)

    @group.command(name="create", description="Create your private Dockerize container.")
    @app_commands.describe(name="Container name, for display and database tracking")
    async def create(self, interaction: discord.Interaction, name: str) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        if not has_manage_channel_permissions(guild):
            await interaction.response.send_message(
                embed=failure_embed(self.bot.config.emoji_failure, "Daemon permission denied", "I need `Manage Channels` to create container namespaces."),
                ephemeral=True,
            )
            return

        existing = await self.bot.db.get_container(guild.id, interaction.user.id)
        if existing and existing.get("status") != "deleted":
            await interaction.response.send_message(
                embed=failure_embed(
                    self.bot.config.emoji_failure,
                    "Container failed to start",
                    console_block("Error: container already exists for this user") + "\nYou already have a Dockerize container in this server.",
                ),
                ephemeral=True,
            )
            return

        container_name = sanitize_name(name, default=f"container-{interaction.user.name}", max_length=32)
        staff_role = await self._staff_role(guild)
        owner = interaction.user if isinstance(interaction.user, discord.Member) else guild.get_member(interaction.user.id)
        if owner is None or guild.me is None:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container failed to start", "Owner or bot member could not be resolved."), ephemeral=True)
            return

        visibility = self.bot.config.default_container_visibility
        if visibility == "public":
            overwrites = permissions.build_public_overwrites(guild, owner, staff_role, guild.me)
        else:
            overwrites = permissions.build_private_overwrites(guild, owner, staff_role, guild.me)

        frames = [
            "$ docker compose up -d\n[+] Building Dockerize container...",
            "$ docker compose up -d\n[+] Creating private namespace...\n[+] Mounting channels...",
            "$ docker compose up -d\n[+] Creating private namespace...\n[+] Mounting channels...\n[+] Applying permission layers...",
            "$ docker compose up -d\n[+] Creating private namespace...\n[+] Mounting channels...\n[+] Applying permission layers...\n[+] Container started successfully.",
        ]

        await interaction.response.defer(thinking=True)
        try:
            category = await guild.create_category(
                container_category_name(interaction.user.display_name),
                overwrites=overwrites,
                reason=f"Dockerize container created by {interaction.user}",
            )
            terminal, logs_ch, general, runtime = await create_container_channels(guild, category)
            await self.bot.db.create_container(
                guild.id,
                interaction.user.id,
                container_name,
                category.id,
                terminal.id,
                logs_ch.id,
                general.id,
                runtime.id,
                "up",
                visibility,
            )
            for ch, ch_type in ((terminal, "text"), (logs_ch, "text"), (general, "text"), (runtime, "voice")):
                await self.bot.db.add_container_channel(guild.id, interaction.user.id, ch.id, ch.name, ch_type, is_system=True)
        except discord.Forbidden:
            await interaction.edit_original_response(embed=failure_embed(self.bot.config.emoji_failure, "Container failed to start", "Discord rejected the permission layer. Move my role above managed roles and give me `Manage Channels`."))
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("container creation failed")
            await interaction.edit_original_response(embed=failure_embed(self.bot.config.emoji_failure, "Container failed to start", console_block(f"Error: {exc}")))
            return

        final = success_embed(
            self.bot.config.emoji_success,
            "Container started",
            (
                "Your Dockerize container is now online.\n\n"
                f"Container: `{container_name}`\n"
                "Status: `up`\n"
                f"Visibility: `{visibility}`\n"
                "Channels:\n"
                f"{terminal.mention}\n{logs_ch.mention}\n{general.mention}\n🔊 {runtime.mention}"
            ),
        )
        await play_terminal_animation(interaction, self.bot.config.emoji_docker, "docker compose up -d", frames, final, delay=0.6)
        await send_dm_safe(
            interaction.user,
            docker_embed(self.bot.config.emoji_docker, "Your Dockerize container is online", "Your private container has been created and started."),
        )
        log.info("container created guild=%s owner=%s category=%s", guild.id, interaction.user.id, category.id)

    @group.command(name="up", description="Start or repair your existing Dockerize container.")
    async def up(self, interaction: discord.Interaction) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        if not ensure_not_suspended(container):
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Runtime suspended", "Staff must unsuspend your container before it can be started."), ephemeral=True)
            return
        try:
            container = await ensure_container_objects(guild, self.bot.db, container)
            await self._apply_visibility(guild, container, container["visibility"])
            await self.bot.db.update_container(guild.id, interaction.user.id, status="up")
        except Exception as exc:  # noqa: BLE001
            log.exception("container up failed")
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container failed to start", console_block(f"Error: {exc}")), ephemeral=True)
            return

        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "Container started", console_block("$ docker start user-container\n[+] Permission layer restored\n[+] Container is online")))
        await send_dm_safe(interaction.user, docker_embed(self.bot.config.emoji_docker, "Your Dockerize container is online", "Your container has been put up."))

    @group.command(name="down", description="Stop your Dockerize container without deleting its data.")
    async def down(self, interaction: discord.Interaction) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        if container["status"] == "suspended":
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Runtime suspended", "A suspended container cannot be stopped by the owner."), ephemeral=True)
            return
        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        owner = await owner_member(guild, interaction.user.id)
        if isinstance(category, discord.CategoryChannel) and owner and guild.me:
            staff_role = await self._staff_role(guild)
            invites = await invited_members(guild, self.bot.db, interaction.user.id)
            await permissions.apply_container_suspended(category, guild, owner, staff_role, guild.me, invites)
        await self.bot.db.update_container(guild.id, interaction.user.id, status="down")
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "Container stopped", console_block("$ docker stop user-container\n[+] Runtime stopped\n[+] Owner access archived\n[+] Staff inspection access kept")))
        await send_dm_safe(interaction.user, docker_embed(self.bot.config.emoji_docker, "Your Dockerize container was taken down", "Your container is stopped. Use `/container up` to start it again."))

    @group.command(name="delete", description="Delete your Dockerize container permanently.")
    @app_commands.describe(confirm="Must be true to delete the container")
    async def delete(self, interaction: discord.Interaction, confirm: bool) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        if not confirm:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Deletion not confirmed", "Run `/container delete confirm:true` to delete your container."), ephemeral=True)
            return
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "There is no container to delete."), ephemeral=True)
            return

        frames = [
            "$ docker compose down --volumes\n[+] Stopping container",
            "$ docker compose down --volumes\n[+] Stopping container\n[+] Unmounting channels",
            "$ docker compose down --volumes\n[+] Stopping container\n[+] Unmounting channels\n[+] Removing category",
            "$ docker compose down --volumes\n[+] Stopping container\n[+] Unmounting channels\n[+] Removing category\n[+] Cleaning database record",
        ]
        await interaction.response.defer(thinking=True)
        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        if isinstance(category, discord.CategoryChannel):
            for channel in list(category.channels):
                try:
                    await channel.delete(reason=f"Dockerize container deleted by {interaction.user}")
                except discord.HTTPException:
                    pass
            try:
                await category.delete(reason=f"Dockerize container deleted by {interaction.user}")
            except discord.HTTPException:
                pass
        await self.bot.db.delete_container_record(guild.id, interaction.user.id)
        final = failure_embed(self.bot.config.emoji_failure, "Container deleted", "Your Dockerize container and its mounted channels were removed.")
        await play_terminal_animation(interaction, self.bot.config.emoji_failure, "docker compose down --volumes", frames, final, delay=0.55)
        await send_dm_safe(interaction.user, failure_embed(self.bot.config.emoji_failure, "Your Dockerize container was deleted", "Your container was manually deleted."))
        log.info("container deleted guild=%s owner=%s", guild.id, interaction.user.id)

    @group.command(name="status", description="Show your container status like docker ps.")
    async def status(self, interaction: discord.Interaction) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        channels = await self.bot.db.get_container_channels(guild.id, interaction.user.id)
        invites = await self.bot.db.get_invites(guild.id, interaction.user.id)
        output = (
            "CONTAINER ID   OWNER       STATUS       VISIBILITY   CHANNELS\n"
            f"{short_container_id(container):<14} @{interaction.user.name:<10.10} {container['status']:<12} {container['visibility']:<12} {len(channels)}"
        )
        embed = docker_embed(
            self.bot.config.emoji_docker,
            "docker ps",
            console_block(output)
            + f"\nContainer: `{container['container_name']}`\n"
            + f"Created: `{container['created_at']}`\n"
            + f"Invited users: `{len(invites)}`\n"
            + f"Inspection active: `{'yes' if int(container['inspection_active']) else 'no'}`\n"
            + f"Suspended: `{'yes' if container['status'] == 'suspended' else 'no'}`",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @group.command(name="public", description="Make your container public to the server.")
    async def make_public(self, interaction: discord.Interaction) -> None:
        await self._set_visibility(interaction, "public")

    @group.command(name="private", description="Make your container private again, keeping invited users.")
    async def make_private(self, interaction: discord.Interaction) -> None:
        await self._set_visibility(interaction, "private")

    async def _set_visibility(self, interaction: discord.Interaction, visibility: str) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        if container["status"] == "suspended":
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Runtime suspended", "Suspended containers cannot change visibility."), ephemeral=True)
            return
        container = await ensure_container_objects(guild, self.bot.db, container)
        await self._apply_visibility(guild, container, visibility)
        await self.bot.db.update_container(guild.id, interaction.user.id, visibility=visibility)
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, f"Container set {visibility}", f"Visibility is now `{visibility}`."))

    @group.command(name="invite", description="Invite a user to your private container.")
    async def invite(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        if user.bot and not self.bot.config.allow_bot_invites:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Invite rejected", "Bot invites are disabled by configuration."), ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Invite rejected", "You already own this container."), ephemeral=True)
            return
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        if container["status"] == "suspended":
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Runtime suspended", "Suspended containers cannot accept invites."), ephemeral=True)
            return
        await self.bot.db.add_invite(guild.id, interaction.user.id, user.id)
        container = await ensure_container_objects(guild, self.bot.db, container)
        await self._apply_visibility(guild, container, container["visibility"])
        await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "Container invite added", f"{user.mention} can now access `{container['container_name']}`."), ephemeral=True)
        await send_dm_safe(
            user,
            docker_embed(
                self.bot.config.emoji_docker,
                "You were invited to a Dockerize container",
                f"{interaction.user.mention} invited you to access their container in **{guild.name}**.\n\nContainer: `{container['container_name']}`\nStatus: `{container['status']}`\nVisibility: `{container['visibility']}`",
            ),
        )

    @group.command(name="uninvite", description="Remove a user's access to your private container.")
    async def uninvite(self, interaction: discord.Interaction, user: discord.User) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        guild = interaction.guild
        container = await self.bot.db.get_container(guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        await self.bot.db.remove_invite(guild.id, interaction.user.id, user.id)
        container = await ensure_container_objects(guild, self.bot.db, container)
        await self._apply_visibility(guild, container, container["visibility"])
        await interaction.response.send_message(embed=warning_embed(self.bot.config.emoji_warning, "Container access removed", f"{user.mention} was removed from `{container['container_name']}`."), ephemeral=True)
        await send_dm_safe(user, warning_embed(self.bot.config.emoji_warning, "Container access removed", f"Your access to {interaction.user.mention}'s Dockerize container was removed."))

    @group.command(name="invites", description="List users invited to your container.")
    async def invites(self, interaction: discord.Interaction) -> None:
        if not await require_configured_channel(interaction, self.bot.db, self.bot.config.emoji_failure, allow_inside_container=True):
            return
        assert interaction.guild is not None
        container = await self.bot.db.get_container(interaction.guild.id, interaction.user.id)
        if not container:
            await interaction.response.send_message(embed=failure_embed(self.bot.config.emoji_failure, "Container not found", "Create one first with `/container create`."), ephemeral=True)
            return
        invites = await self.bot.db.get_invites(interaction.guild.id, interaction.user.id)
        if not invites:
            text = "No users are currently mounted into this namespace."
        else:
            text = "\n".join(f"<@{item['invited_user_id']}>" for item in invites)
        await interaction.response.send_message(embed=docker_embed(self.bot.config.emoji_docker, "Container invites", text), ephemeral=True)


async def setup(bot) -> None:
    await bot.add_cog(ContainersCog(bot))
