from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from .embeds import failure_embed

log = logging.getLogger(__name__)

_ALLOWED = re.compile(r"[^a-z0-9_-]+")


def sanitize_name(value: str, *, default: str = "container", max_length: int = 32) -> str:
    cleaned = value.strip().lower().replace(" ", "-")
    cleaned = _ALLOWED.sub("", cleaned)
    cleaned = cleaned.strip("-_")
    if not cleaned:
        cleaned = default
    return cleaned[:max_length].strip("-_") or default


def container_category_name(username: str, *, emoji: bool = True) -> str:
    clean = sanitize_name(username, default="user", max_length=40)
    return f"🐳 container-{clean}" if emoji else f"container-{clean}"


def short_container_id(container: dict[str, Any]) -> str:
    raw = f"{container.get('id', '0'):>04}{container.get('owner_id', '')}"[-4:]
    return f"dkz-{raw}"


async def send_dm_safe(user: discord.abc.User, embed: discord.Embed) -> bool:
    try:
        await user.send(embed=embed)
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        log.warning("could not DM user %s: %s", getattr(user, "id", "unknown"), exc)
        return False


def is_staff_member(member: discord.Member, staff_role_id: int | None) -> bool:
    if member.guild_permissions.administrator:
        return True
    if staff_role_id is None:
        return False
    return any(role.id == staff_role_id for role in member.roles)


async def get_staff_role(interaction: discord.Interaction, db) -> discord.Role | None:
    if not interaction.guild:
        return None
    settings = await db.get_guild_settings(interaction.guild.id)
    if not settings or not settings.get("staff_role_id"):
        return None
    return interaction.guild.get_role(int(settings["staff_role_id"]))


async def require_guild(interaction: discord.Interaction, emoji_failure: str) -> bool:
    if interaction.guild is not None:
        return True
    await interaction.response.send_message(
        embed=failure_embed(emoji_failure, "Guild-only command", "Dockerize commands can only be used inside a Discord server."),
        ephemeral=True,
    )
    return False


async def require_staff(interaction: discord.Interaction, db, emoji_failure: str) -> bool:
    if not await require_guild(interaction, emoji_failure):
        return False
    assert interaction.guild is not None
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    settings = await db.get_guild_settings(interaction.guild.id)
    staff_role_id = int(settings["staff_role_id"]) if settings and settings.get("staff_role_id") else None
    if member and is_staff_member(member, staff_role_id):
        return True
    await interaction.response.send_message(
        embed=failure_embed(emoji_failure, "Permission layer rejected", "You need Administrator or the configured staff role to use this command."),
        ephemeral=True,
    )
    return False


async def require_configured_channel(interaction: discord.Interaction, db, emoji_failure: str, *, allow_inside_container: bool = False) -> bool:
    if not await require_guild(interaction, emoji_failure):
        return False
    assert interaction.guild is not None
    settings = await db.get_guild_settings(interaction.guild.id)
    if not settings:
        await interaction.response.send_message(
            embed=failure_embed(emoji_failure, "Dockerize daemon offline", "An administrator must run `/setup` before users can create or control containers."),
            ephemeral=True,
        )
        return False

    if interaction.channel and int(settings["command_channel_id"]) == interaction.channel.id:
        return True

    if allow_inside_container and interaction.channel:
        container = await db.get_container_by_channel(interaction.guild.id, interaction.channel.id)
        if container and int(container["owner_id"]) == interaction.user.id:
            return True

    channel_mention = f"<#{settings['command_channel_id']}>"
    await interaction.response.send_message(
        embed=failure_embed(emoji_failure, "Wrong command namespace", f"Use Dockerize commands in {channel_mention} or inside your own container terminal."),
        ephemeral=True,
    )
    return False


def has_manage_channel_permissions(guild: discord.Guild) -> bool:
    me = guild.me
    return bool(me and me.guild_permissions.manage_channels)


def ensure_not_suspended(container: dict[str, Any]) -> bool:
    return str(container.get("status", "")).lower() != "suspended"


class DockerizeCheckFailure(app_commands.CheckFailure):
    pass
