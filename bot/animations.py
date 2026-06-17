from __future__ import annotations

import asyncio
import logging

import discord

from .embeds import terminal_embed

log = logging.getLogger(__name__)


async def play_terminal_animation(
    interaction: discord.Interaction,
    emoji: str,
    title: str,
    frames: list[str],
    final_embed: discord.Embed,
    *,
    delay: float = 0.75,
    ephemeral: bool = False,
) -> None:
    """Animate an interaction by editing the original response.

    Discord embeds do not animate. This keeps edits small and falls back to the
    final embed if Discord rate limits, permissions, or interaction state fail.
    """
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral, thinking=True)

        for frame in frames[:6]:
            await interaction.edit_original_response(embed=terminal_embed(emoji, title, frame))
            await asyncio.sleep(delay)

        await interaction.edit_original_response(embed=final_embed)
    except Exception as exc:  # noqa: BLE001 - animation must never break commands
        log.warning("terminal animation failed: %s", exc)
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=final_embed)
            else:
                await interaction.response.send_message(embed=final_embed, ephemeral=ephemeral)
        except Exception as fallback_exc:  # noqa: BLE001
            log.error("could not send final animation embed: %s", fallback_exc)
