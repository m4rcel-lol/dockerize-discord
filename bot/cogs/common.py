from __future__ import annotations

import logging
from typing import Any

import discord

from bot import permissions
from bot.checks import container_category_name, sanitize_name

log = logging.getLogger(__name__)

SYSTEM_CHANNELS = (
    ("terminal", "text"),
    ("logs", "text"),
    ("general", "text"),
    ("runtime", "voice"),
)


def get_bot(interaction: discord.Interaction):
    return interaction.client


async def invited_members(guild: discord.Guild, db, owner_id: int) -> list[discord.Member]:
    invited = await db.get_invites(guild.id, owner_id)
    members: list[discord.Member] = []
    for item in invited:
        member = guild.get_member(int(item["invited_user_id"]))
        if member:
            members.append(member)
    return members


async def owner_member(guild: discord.Guild, owner_id: int) -> discord.Member | None:
    return guild.get_member(owner_id) or await guild.fetch_member(owner_id)


async def staff_role_for_guild(guild: discord.Guild, db) -> discord.Role | None:
    settings = await db.get_guild_settings(guild.id)
    if not settings or not settings.get("staff_role_id"):
        return None
    return guild.get_role(int(settings["staff_role_id"]))


async def ensure_container_objects(guild: discord.Guild, db, container: dict[str, Any], *, emoji_categories: bool = True) -> dict[str, Any]:
    """Repair missing category/default channels and update the database.

    This intentionally avoids deleting extra channels; it only recreates the
    system objects that are required for the bot to work after manual damage.
    """
    owner_id = int(container["owner_id"])
    owner = await owner_member(guild, owner_id)
    if owner is None:
        raise RuntimeError("Container owner is no longer available in this guild.")

    staff_role = await staff_role_for_guild(guild, db)
    bot_member = guild.me
    if bot_member is None:
        raise RuntimeError("Bot member is not cached in this guild.")

    invite_members = await invited_members(guild, db, owner_id)
    if container["visibility"] == "public":
        overwrites = permissions.build_public_overwrites(guild, owner, staff_role, bot_member, invite_members)
    elif container["status"] == "suspended":
        overwrites = permissions.build_suspended_overwrites(guild, owner, staff_role, bot_member, invite_members)
    else:
        overwrites = permissions.build_private_overwrites(guild, owner, staff_role, bot_member, invite_members)

    category = guild.get_channel(int(container["category_id"])) if container.get("category_id") else None
    if not isinstance(category, discord.CategoryChannel):
        category = await guild.create_category(
            container_category_name(owner.display_name, emoji=emoji_categories),
            overwrites=overwrites,
            reason="Dockerize repairing missing container category",
        )
        await db.update_container(guild.id, owner_id, category_id=category.id)
        container["category_id"] = str(category.id)
    else:
        await category.edit(overwrites=overwrites)

    field_by_name = {
        "terminal": "terminal_channel_id",
        "logs": "logs_channel_id",
        "general": "general_channel_id",
        "runtime": "voice_channel_id",
    }
    for channel_name, channel_type in SYSTEM_CHANNELS:
        field = field_by_name[channel_name]
        channel = guild.get_channel(int(container[field])) if container.get(field) else None
        if channel is None:
            if channel_type == "voice":
                channel = await guild.create_voice_channel(channel_name, category=category, reason="Dockerize repairing missing system voice channel")
            else:
                channel = await guild.create_text_channel(channel_name, category=category, reason="Dockerize repairing missing system text channel")
            await channel.edit(sync_permissions=True)
            await db.update_container(guild.id, owner_id, **{field: channel.id})
            await db.add_container_channel(guild.id, owner_id, channel.id, channel_name, channel_type, is_system=True)
            container[field] = str(channel.id)

    return container


async def create_container_channels(guild: discord.Guild, category: discord.CategoryChannel) -> tuple[discord.TextChannel, discord.TextChannel, discord.TextChannel, discord.VoiceChannel]:
    terminal = await guild.create_text_channel("terminal", category=category, reason="Dockerize system channel")
    logs = await guild.create_text_channel("logs", category=category, reason="Dockerize system channel")
    general = await guild.create_text_channel("general", category=category, reason="Dockerize system channel")
    runtime = await guild.create_voice_channel("runtime", category=category, reason="Dockerize system voice channel")
    for channel in (terminal, logs, general, runtime):
        await channel.edit(sync_permissions=True)
    return terminal, logs, general, runtime


def mention_channel(guild: discord.Guild, channel_id: str | int | None, fallback: str) -> str:
    if not channel_id:
        return fallback
    channel = guild.get_channel(int(channel_id))
    return channel.mention if channel else f"`{fallback}` (missing)"
