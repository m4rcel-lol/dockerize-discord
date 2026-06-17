from __future__ import annotations

import asyncio
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
from bot.embeds import console_block, docker_embed, failure_embed, success_embed, terminal_embed, warning_embed
from bot.views import ContainerInviteRequestView
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
            "$ docker compose up -d\n[+] Pulling dockerize/runtime:latest\n[+] Preparing startup plan...",
            "$ docker compose up -d\n[+] Pulling dockerize/runtime:latest\n[+] Creating private namespace...",
            "$ docker compose up -d\n[+] Creating private namespace... done\n[+] Mounting channel volumes...",
            "$ docker compose up -d\n[+] Creating private namespace... done\n[+] Mounting channel volumes... done\n[+] Applying permission layers...",
            "$ docker compose up -d\n[+] Creating private namespace... done\n[+] Mounting channel volumes... done\n[+] Applying permission layers... done\n[+] Writing container metadata...",
            "$ docker compose up -d\n[+] Creating private namespace... done\n[+] Mounting channel volumes... done\n[+] Applying permission layers... done\n[+] Container started successfully.",
        ]

        category: discord.CategoryChannel | None = None
        terminal: discord.TextChannel | None = None
        logs_ch: discord.TextChannel | None = None
        general: discord.TextChannel | None = None
        runtime: discord.VoiceChannel | None = None

        async def edit_frame(index: int, delay: float = 0.55) -> None:
            await interaction.edit_original_response(
                embed=terminal_embed(self.bot.config.emoji_docker, "docker compose up -d", frames[index])
            )
            await asyncio.sleep(delay)

        try:
            # Send the visible terminal immediately, before creating any Discord objects.
            await interaction.response.send_message(embed=terminal_embed(self.bot.config.emoji_docker, "docker compose up -d", frames[0]))
            await asyncio.sleep(0.55)

            await edit_frame(1)
            category = await guild.create_category(
                container_category_name(container_name),
                overwrites=overwrites,
                reason=f"Dockerize container created by {interaction.user}",
            )

            await edit_frame(2)
            terminal, logs_ch, general, runtime = await create_container_channels(guild, category)

            await edit_frame(3)
            # Category overwrites are inherited by the default channels, but re-applying
            # keeps the sequence explicit and makes manual Discord weirdness harmless.
            if visibility == "public":
                await permissions.apply_container_public(category, guild, owner, staff_role, guild.me)
            else:
                await permissions.apply_container_private(category, guild, owner, staff_role, guild.me)

            await edit_frame(4)
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

            await edit_frame(5, delay=0.35)
        except discord.Forbidden:
            log.exception("container creation forbidden")
            if category is not None:
                for channel in list(category.channels):
                    try:
                        await channel.delete(reason="Dockerize cleanup after failed container creation")
                    except discord.HTTPException:
                        pass
                try:
                    await category.delete(reason="Dockerize cleanup after failed container creation")
                except discord.HTTPException:
                    pass
            failure = failure_embed(self.bot.config.emoji_failure, "Container failed to start", "Discord rejected the permission layer. Move my role above managed roles and give me `Manage Channels`.")
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=failure)
            else:
                await interaction.response.send_message(embed=failure, ephemeral=True)
            return
        except Exception as exc:  # noqa: BLE001
            log.exception("container creation failed")
            if category is not None:
                for channel in list(category.channels):
                    try:
                        await channel.delete(reason="Dockerize cleanup after failed container creation")
                    except discord.HTTPException:
                        pass
                try:
                    await category.delete(reason="Dockerize cleanup after failed container creation")
                except discord.HTTPException:
                    pass
            failure = failure_embed(self.bot.config.emoji_failure, "Container failed to start", console_block(f"Error: {exc}"))
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=failure)
            else:
                await interaction.response.send_message(embed=failure, ephemeral=True)
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
        await interaction.edit_original_response(embed=final)
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

        # Start with the Docker emoji while the runtime is being dismantled,
        # then swap to the failure emoji only on the final deletion embed.
        try:
            await interaction.edit_original_response(
                embed=terminal_embed(self.bot.config.emoji_docker, "docker compose down --volumes", frames[0])
            )
        except discord.HTTPException:
            pass

        category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
        if isinstance(category, discord.CategoryChannel):
            await asyncio.sleep(0.45)
            try:
                await interaction.edit_original_response(
                    embed=terminal_embed(self.bot.config.emoji_docker, "docker compose down --volumes", frames[1])
                )
            except discord.HTTPException:
                pass

            for channel in list(category.channels):
                try:
                    await channel.delete(reason=f"Dockerize container deleted by {interaction.user}")
                except discord.HTTPException:
                    pass

            await asyncio.sleep(0.45)
            try:
                await interaction.edit_original_response(
                    embed=terminal_embed(self.bot.config.emoji_docker, "docker compose down --volumes", frames[2])
                )
            except discord.HTTPException:
                pass

            try:
                await category.delete(reason=f"Dockerize container deleted by {interaction.user}")
            except discord.HTTPException:
                pass
        else:
            await asyncio.sleep(0.45)
            try:
                await interaction.edit_original_response(
                    embed=terminal_embed(self.bot.config.emoji_docker, "docker compose down --volumes", frames[2])
                )
            except discord.HTTPException:
                pass

        await asyncio.sleep(0.45)
        try:
            await interaction.edit_original_response(
                embed=terminal_embed(self.bot.config.emoji_docker, "docker compose down --volumes", frames[3])
            )
        except discord.HTTPException:
            pass

        await self.bot.db.delete_container_record(guild.id, interaction.user.id)
        final = failure_embed(self.bot.config.emoji_failure, "Container deleted", "Your Dockerize container and its mounted channels were removed.")
        try:
            await interaction.edit_original_response(embed=final)
        except discord.HTTPException:
            await interaction.followup.send(embed=final, ephemeral=True)
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

    @group.command(name="invite", description="Send a container invite request to a user.")
    async def invite(self, interaction: discord.Interaction, user: discord.Member) -> None:
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

        accepted_invites = await self.bot.db.get_invites(guild.id, interaction.user.id)
        if any(int(item["invited_user_id"]) == user.id for item in accepted_invites):
            await interaction.response.send_message(embed=success_embed(self.bot.config.emoji_success, "User already mounted", f"{user.mention} already has access to `{container['container_name']}`."), ephemeral=True)
            return

        owner_pending_embed = docker_embed(
            self.bot.config.emoji_docker,
            "Container invite request sent",
            (
                f"An access request was sent to {user.mention}.\n\n"
                f"Container: `{container['container_name']}`\n"
                f"Status: `{container['status']}`\n"
                "Access: `pending`\n\n"
                "They do **not** have access yet. This embed will update when they accept or decline."
            ),
        )
        await interaction.response.send_message(embed=owner_pending_embed, ephemeral=True)
        owner_message = await interaction.original_response()

        pending_invitee_embed = docker_embed(
            self.bot.config.emoji_docker,
            "Dockerize container invite request",
            (
                f"{interaction.user.mention} wants to invite you to their Dockerize container in **{guild.name}**.\n\n"
                f"Container: `{container['container_name']}`\n"
                f"Status: `{container['status']}`\n"
                f"Visibility: `{container['visibility']}`\n\n"
                "Accepting this request will mount your user permissions into the container. Declining will leave access unchanged."
            ),
        )
        timeout_invitee_embed = warning_embed(
            self.bot.config.emoji_warning,
            "Container invite request expired",
            f"The invite request for `{container['container_name']}` expired. No access was granted.",
        )
        timeout_owner_embed = warning_embed(
            self.bot.config.emoji_warning,
            "Container invite request expired",
            f"{user.mention} did not respond to the request for `{container['container_name']}` in time. No access was granted.",
        )

        async def accept_callback(button_interaction: discord.Interaction) -> tuple[discord.Embed, discord.Embed]:
            current_guild = self.bot.get_guild(guild.id)
            if current_guild is None:
                failure = failure_embed(self.bot.config.emoji_failure, "Invite failed", "I could not resolve the server for this invite request. No access was granted.")
                return failure, failure

            current_container = await self.bot.db.get_container(current_guild.id, interaction.user.id)
            if not current_container:
                failure = failure_embed(self.bot.config.emoji_failure, "Invite failed", "The container no longer exists. No access was granted.")
                return failure, failure
            if current_container["status"] == "suspended":
                failure = failure_embed(self.bot.config.emoji_failure, "Invite failed", "The container is suspended. No access was granted.")
                return failure, failure

            try:
                invited_member = current_guild.get_member(user.id) or await current_guild.fetch_member(user.id)
            except discord.HTTPException:
                failure = failure_embed(self.bot.config.emoji_failure, "Invite failed", "The invited user is no longer available in this server. No access was granted.")
                return failure, failure

            try:
                await self.bot.db.add_invite(current_guild.id, interaction.user.id, invited_member.id)
                current_container = await ensure_container_objects(current_guild, self.bot.db, current_container)
                await self._apply_visibility(current_guild, current_container, current_container["visibility"])
            except discord.Forbidden:
                await self.bot.db.remove_invite(current_guild.id, interaction.user.id, invited_member.id)
                failure = failure_embed(self.bot.config.emoji_failure, "Invite failed", "Discord rejected the permission update. No access was granted.")
                return failure, failure
            except Exception as exc:  # noqa: BLE001
                await self.bot.db.remove_invite(current_guild.id, interaction.user.id, invited_member.id)
                failure = failure_embed(self.bot.config.emoji_failure, "Invite failed", console_block(f"Error: {exc}"))
                return failure, failure

            invitee_embed = success_embed(
                self.bot.config.emoji_success,
                "Container invite accepted",
                (
                    f"You accepted {interaction.user.mention}'s Dockerize container invite.\n\n"
                    f"Container: `{current_container['container_name']}`\n"
                    "Access: `granted`"
                ),
            )
            owner_embed = success_embed(
                self.bot.config.emoji_success,
                "Container invite accepted",
                (
                    f"{invited_member.mention} accepted the request and can now access `{current_container['container_name']}`.\n\n"
                    "Access: `granted`"
                ),
            )
            return invitee_embed, owner_embed

        async def decline_callback(button_interaction: discord.Interaction) -> tuple[discord.Embed, discord.Embed]:
            invitee_embed = warning_embed(
                self.bot.config.emoji_warning,
                "Container invite declined",
                f"You declined the request for `{container['container_name']}`. No access was granted.",
            )
            owner_embed = warning_embed(
                self.bot.config.emoji_warning,
                "Container invite declined",
                f"{user.mention} declined the request for `{container['container_name']}`. No access was granted.",
            )
            return invitee_embed, owner_embed

        view = ContainerInviteRequestView(
            invited_user_id=user.id,
            owner_message=owner_message,
            pending_invitee_embed=pending_invitee_embed,
            timeout_invitee_embed=timeout_invitee_embed,
            timeout_owner_embed=timeout_owner_embed,
            accept_callback=accept_callback,
            decline_callback=decline_callback,
        )

        try:
            view.invite_message = await user.send(embed=pending_invitee_embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            failed = failure_embed(
                self.bot.config.emoji_failure,
                "Invite request failed",
                f"I could not DM {user.mention}, so no request was delivered and no access was granted.",
            )
            await interaction.edit_original_response(embed=failed)

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
