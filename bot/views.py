from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import TypeAlias

import discord

InviteResult: TypeAlias = tuple[discord.Embed, discord.Embed]
InviteCallback: TypeAlias = Callable[[discord.Interaction], Awaitable[InviteResult]]


class PaginationView(discord.ui.View):
    def __init__(self, pages: list[discord.Embed], *, author_id: int, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.pages = pages
        self.author_id = author_id
        self.index = 0
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous_page.disabled = self.index <= 0
        self.next_page.disabled = self.index >= len(self.pages) - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This paginator belongs to another runtime session.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = max(0, self.index - 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True


class ContainerInviteRequestView(discord.ui.View):
    def __init__(
        self,
        *,
        invited_user_id: int,
        owner_message: discord.InteractionMessage | None,
        pending_invitee_embed: discord.Embed,
        timeout_invitee_embed: discord.Embed,
        timeout_owner_embed: discord.Embed,
        accept_callback: InviteCallback,
        decline_callback: InviteCallback,
        timeout: float = 900.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.invited_user_id = invited_user_id
        self.owner_message = owner_message
        self.invite_message: discord.Message | None = None
        self.pending_invitee_embed = pending_invitee_embed
        self.timeout_invitee_embed = timeout_invitee_embed
        self.timeout_owner_embed = timeout_owner_embed
        self.accept_callback = accept_callback
        self.decline_callback = decline_callback
        self.finished = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invited_user_id:
            await interaction.response.send_message("This invite request belongs to another user.", ephemeral=True)
            return False
        return True

    def _disable_buttons(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def _edit_owner_message(self, embed: discord.Embed) -> None:
        if self.owner_message is None:
            return
        with contextlib.suppress(discord.HTTPException, discord.NotFound, discord.Forbidden):
            await self.owner_message.edit(embed=embed, view=None)

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.finished = True
        self._disable_buttons()
        invitee_embed, owner_embed = await self.accept_callback(interaction)
        await interaction.response.edit_message(embed=invitee_embed, view=self)
        await self._edit_owner_message(owner_embed)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.finished = True
        self._disable_buttons()
        invitee_embed, owner_embed = await self.decline_callback(interaction)
        await interaction.response.edit_message(embed=invitee_embed, view=self)
        await self._edit_owner_message(owner_embed)
        self.stop()

    async def on_timeout(self) -> None:
        if self.finished:
            return
        self._disable_buttons()
        if self.invite_message is not None:
            with contextlib.suppress(discord.HTTPException, discord.NotFound, discord.Forbidden):
                await self.invite_message.edit(embed=self.timeout_invitee_embed, view=self)
        await self._edit_owner_message(self.timeout_owner_embed)
