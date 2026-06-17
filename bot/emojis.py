from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

import discord

log = logging.getLogger(__name__)

_CUSTOM_EMOJI_MENTION_RE = re.compile(r"^<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>$")
_COLON_NAME_RE = re.compile(r"^:([A-Za-z0-9_]{2,32}):$")
_PLAIN_NAME_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")


def _emoji_name(token: str) -> str | None:
    raw = token.strip()
    match = _COLON_NAME_RE.match(raw)
    if match:
        return match.group(1)
    if _PLAIN_NAME_RE.match(raw):
        return raw
    return None


def _emoji_mention(emoji: Any) -> str:
    """Return Discord's renderable custom emoji mention format."""
    name = getattr(emoji, "name", None)
    emoji_id = getattr(emoji, "id", None)
    if not name or not emoji_id:
        return str(emoji)
    animated = bool(getattr(emoji, "animated", False))
    return f"<{'a' if animated else ''}:{name}:{emoji_id}>"


def resolve_emoji_token(token: str, emojis: Iterable[Any]) -> str:
    """Resolve ':name:' from .env into a real Discord custom emoji mention.

    Discord does not render custom emojis from raw ':name:' text in bot embeds.
    It needs '<:name:id>' or '<a:name:id>'. This helper keeps unicode emojis
    and already-correct custom emoji mentions unchanged.
    """
    raw = (token or "").strip()
    if not raw:
        return ""
    if _CUSTOM_EMOJI_MENTION_RE.match(raw):
        return raw

    wanted = _emoji_name(raw)
    if wanted is None:
        return raw

    wanted_lower = wanted.lower()
    for emoji in emojis:
        if str(getattr(emoji, "name", "")).lower() == wanted_lower:
            resolved = _emoji_mention(emoji)
            log.info("resolved emoji %s -> %s", raw, resolved)
            return resolved

    log.warning("could not resolve custom emoji %s; leaving raw fallback", raw)
    return raw


async def resolve_bot_emoji_token(bot: discord.Client, token: str) -> str:
    """Resolve an emoji token against application emojis first, then guild emojis.

    Emojis uploaded in Discord Developer Portal are application/bot emojis. They
    are not included in Client.emojis, so we fetch them explicitly when the
    installed discord.py version supports it.
    """
    raw = (token or "").strip()
    if not raw or _CUSTOM_EMOJI_MENTION_RE.match(raw) or _emoji_name(raw) is None:
        return raw

    application_emojis: list[Any] = []
    fetch_application_emojis = getattr(bot, "fetch_application_emojis", None)
    if callable(fetch_application_emojis):
        try:
            application_emojis = list(await fetch_application_emojis())
        except discord.HTTPException as exc:
            log.warning("could not fetch application emojis: %s", exc)
        except Exception as exc:  # noqa: BLE001
            log.warning("application emoji resolution failed: %s", exc)

    # Prefer bot/application emojis when names collide, because these are the
    # ones users add in the Developer Portal for the bot itself.
    resolved = resolve_emoji_token(raw, [*application_emojis, *bot.emojis])
    return resolved
