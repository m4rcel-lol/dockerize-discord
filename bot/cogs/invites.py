from __future__ import annotations

from discord.ext import commands


class InvitesCog(commands.Cog):
    """Invite commands live under /container in ContainersCog.

    This file exists to keep the requested project structure stable while keeping
    the user-facing slash command tree clean: /container invite, /container
    uninvite, and /container invites.
    """

    def __init__(self, bot) -> None:
        self.bot = bot


async def setup(bot) -> None:
    await bot.add_cog(InvitesCog(bot))
