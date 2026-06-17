from __future__ import annotations

import discord

FOOTER_TEXT = "Dockerize Runtime • container-isolated Discord environment"

GENERAL_COLOR = discord.Color.from_str("#0091e2")
SUCCESS_COLOR = discord.Color.from_str("#00e277")
FAILURE_COLOR = discord.Color.from_str("#e20001")
WARNING_COLOR = discord.Color.from_str("#b9b229")
SUSPENDED_COLOR = discord.Color.from_str("#e20001")


def _base_embed(title: str, description: str | None, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, description=description or discord.Embed.Empty, color=color)
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def console_block(text: str) -> str:
    return f"```console\n{text.strip()}\n```"


def docker_embed(emoji: str, title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(f"{emoji} {title}", description, GENERAL_COLOR)


def success_embed(emoji: str, title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(f"{emoji} {title}", description, SUCCESS_COLOR)


def failure_embed(emoji: str, title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(f"{emoji} {title}", description, FAILURE_COLOR)


def warning_embed(emoji: str, title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(f"{emoji} {title}", description, WARNING_COLOR)


def suspended_embed(emoji: str, title: str, description: str | None = None) -> discord.Embed:
    return _base_embed(f"{emoji} {title}", description, SUSPENDED_COLOR)


def terminal_embed(emoji: str, title: str, terminal_output: str, extra: str | None = None, *, color: discord.Color = GENERAL_COLOR) -> discord.Embed:
    description = console_block(terminal_output)
    if extra:
        description += f"\n{extra}"
    return _base_embed(f"{emoji} {title}", description, color)
