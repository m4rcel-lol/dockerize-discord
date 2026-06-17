from __future__ import annotations

from collections.abc import Iterable

import discord


def _owner_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_channels=True,
        connect=True,
        speak=True,
    )


def _guest_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        connect=True,
        speak=True,
    )


def _staff_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_channels=True,
        manage_messages=True,
        connect=True,
        speak=True,
        move_members=True,
    )


def _bot_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        manage_channels=True,
        manage_messages=True,
        connect=True,
        speak=True,
    )


def _denied_overwrite() -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(view_channel=False, send_messages=False, connect=False)


def build_private_overwrites(
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: _denied_overwrite(),
        owner: _owner_overwrite(),
        bot_member: _bot_overwrite(),
    }
    if staff_role:
        overwrites[staff_role] = _staff_overwrite()
    for member in invited_members:
        overwrites[member] = _guest_overwrite()
    return overwrites


def build_public_overwrites(
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites = build_private_overwrites(guild, owner, staff_role, bot_member, invited_members)
    overwrites[guild.default_role] = _guest_overwrite()
    return overwrites


def build_suspended_overwrites(
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
) -> dict[discord.abc.Snowflake, discord.PermissionOverwrite]:
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: _denied_overwrite(),
        owner: _denied_overwrite(),
        bot_member: _bot_overwrite(),
    }
    if staff_role:
        overwrites[staff_role] = _staff_overwrite()
    for member in invited_members:
        overwrites[member] = _denied_overwrite()
    return overwrites


async def apply_overwrites_to_category_and_children(category: discord.CategoryChannel, overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite]) -> None:
    await category.edit(overwrites=overwrites)
    for channel in category.channels:
        try:
            await channel.edit(sync_permissions=True)
        except discord.HTTPException:
            # A manually damaged channel should not crash the bot.
            pass


async def apply_container_private(
    category: discord.CategoryChannel,
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
) -> None:
    await apply_overwrites_to_category_and_children(
        category,
        build_private_overwrites(guild, owner, staff_role, bot_member, invited_members),
    )


async def apply_container_public(
    category: discord.CategoryChannel,
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
) -> None:
    await apply_overwrites_to_category_and_children(
        category,
        build_public_overwrites(guild, owner, staff_role, bot_member, invited_members),
    )


async def apply_container_suspended(
    category: discord.CategoryChannel,
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
) -> None:
    await apply_overwrites_to_category_and_children(
        category,
        build_suspended_overwrites(guild, owner, staff_role, bot_member, invited_members),
    )


async def restore_container_permissions(
    category: discord.CategoryChannel,
    guild: discord.Guild,
    owner: discord.abc.Snowflake,
    staff_role: discord.Role | None,
    bot_member: discord.Member,
    invited_members: Iterable[discord.abc.Snowflake] = (),
    *,
    visibility: str,
) -> None:
    if visibility == "public":
        await apply_container_public(category, guild, owner, staff_role, bot_member, invited_members)
    else:
        await apply_container_private(category, guild, owner, staff_role, bot_member, invited_members)
