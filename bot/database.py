from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        await self.conn.execute("PRAGMA synchronous = NORMAL")
        await self.init_schema()
        log.info("database connected at %s", self.path)

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    @property
    def db(self) -> aiosqlite.Connection:
        if not self.conn:
            raise RuntimeError("database is not connected")
        return self.conn

    async def init_schema(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                command_channel_id TEXT,
                staff_role_id TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS containers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                container_name TEXT NOT NULL,
                category_id TEXT,
                terminal_channel_id TEXT,
                logs_channel_id TEXT,
                general_channel_id TEXT,
                voice_channel_id TEXT,
                status TEXT NOT NULL,
                visibility TEXT NOT NULL,
                inspection_active INTEGER DEFAULT 0,
                suspended_reason TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(guild_id, owner_id)
            );

            CREATE TABLE IF NOT EXISTS container_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                is_system INTEGER DEFAULT 0,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS container_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                invited_user_id TEXT NOT NULL,
                created_at TEXT,
                UNIQUE(guild_id, owner_id, invited_user_id)
            );
            """
        )
        await self.db.commit()

    async def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        async with self.db.execute(query, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with self.db.execute(query, params) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        await self.db.execute(query, params)
        await self.db.commit()

    async def upsert_guild_settings(self, guild_id: int, command_channel_id: int, staff_role_id: int) -> None:
        now = utcnow()
        await self.execute(
            """
            INSERT INTO guild_settings (guild_id, command_channel_id, staff_role_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                command_channel_id = excluded.command_channel_id,
                staff_role_id = excluded.staff_role_id,
                updated_at = excluded.updated_at
            """,
            (str(guild_id), str(command_channel_id), str(staff_role_id), now, now),
        )

    async def get_guild_settings(self, guild_id: int) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM guild_settings WHERE guild_id = ?", (str(guild_id),))

    async def create_container(
        self,
        guild_id: int,
        owner_id: int,
        container_name: str,
        category_id: int,
        terminal_channel_id: int,
        logs_channel_id: int,
        general_channel_id: int,
        voice_channel_id: int,
        status: str,
        visibility: str,
    ) -> None:
        now = utcnow()
        await self.execute(
            """
            INSERT INTO containers (
                guild_id, owner_id, container_name, category_id,
                terminal_channel_id, logs_channel_id, general_channel_id, voice_channel_id,
                status, visibility, inspection_active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                str(guild_id), str(owner_id), container_name, str(category_id), str(terminal_channel_id),
                str(logs_channel_id), str(general_channel_id), str(voice_channel_id), status, visibility, now, now,
            ),
        )

    async def get_container(self, guild_id: int, owner_id: int) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM containers WHERE guild_id = ? AND owner_id = ?",
            (str(guild_id), str(owner_id)),
        )

    async def get_container_by_channel(self, guild_id: int, channel_id: int) -> dict[str, Any] | None:
        channel = await self.fetchone(
            "SELECT * FROM container_channels WHERE guild_id = ? AND channel_id = ?",
            (str(guild_id), str(channel_id)),
        )
        if channel:
            return await self.get_container(guild_id, int(channel["owner_id"]))
        return None

    async def list_containers(self, guild_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM containers WHERE guild_id = ? ORDER BY created_at DESC",
            (str(guild_id),),
        )

    async def update_container(self, guild_id: int, owner_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = tuple(str(value) if value is not None else None for value in fields.values())
        await self.execute(
            f"UPDATE containers SET {assignments} WHERE guild_id = ? AND owner_id = ?",
            params + (str(guild_id), str(owner_id)),
        )

    async def delete_container_record(self, guild_id: int, owner_id: int) -> None:
        await self.db.execute("DELETE FROM container_invites WHERE guild_id = ? AND owner_id = ?", (str(guild_id), str(owner_id)))
        await self.db.execute("DELETE FROM container_channels WHERE guild_id = ? AND owner_id = ?", (str(guild_id), str(owner_id)))
        await self.db.execute("DELETE FROM containers WHERE guild_id = ? AND owner_id = ?", (str(guild_id), str(owner_id)))
        await self.db.commit()

    async def add_container_channel(self, guild_id: int, owner_id: int, channel_id: int, channel_name: str, channel_type: str, *, is_system: bool = False) -> None:
        await self.execute(
            """
            INSERT INTO container_channels (guild_id, owner_id, channel_id, channel_name, channel_type, is_system, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (str(guild_id), str(owner_id), str(channel_id), channel_name, channel_type, int(is_system), utcnow()),
        )

    async def delete_container_channel(self, guild_id: int, owner_id: int, channel_id: int) -> None:
        await self.execute(
            "DELETE FROM container_channels WHERE guild_id = ? AND owner_id = ? AND channel_id = ?",
            (str(guild_id), str(owner_id), str(channel_id)),
        )

    async def get_container_channels(self, guild_id: int, owner_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM container_channels WHERE guild_id = ? AND owner_id = ? ORDER BY is_system DESC, created_at ASC",
            (str(guild_id), str(owner_id)),
        )

    async def count_container_channels(self, guild_id: int, owner_id: int) -> int:
        row = await self.fetchone(
            "SELECT COUNT(*) AS total FROM container_channels WHERE guild_id = ? AND owner_id = ?",
            (str(guild_id), str(owner_id)),
        )
        return int(row["total"] if row else 0)

    async def get_channel_record(self, guild_id: int, owner_id: int, channel_id: int) -> dict[str, Any] | None:
        return await self.fetchone(
            "SELECT * FROM container_channels WHERE guild_id = ? AND owner_id = ? AND channel_id = ?",
            (str(guild_id), str(owner_id), str(channel_id)),
        )

    async def add_invite(self, guild_id: int, owner_id: int, invited_user_id: int) -> None:
        await self.execute(
            """
            INSERT OR IGNORE INTO container_invites (guild_id, owner_id, invited_user_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(guild_id), str(owner_id), str(invited_user_id), utcnow()),
        )

    async def remove_invite(self, guild_id: int, owner_id: int, invited_user_id: int) -> None:
        await self.execute(
            "DELETE FROM container_invites WHERE guild_id = ? AND owner_id = ? AND invited_user_id = ?",
            (str(guild_id), str(owner_id), str(invited_user_id)),
        )

    async def get_invites(self, guild_id: int, owner_id: int) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT * FROM container_invites WHERE guild_id = ? AND owner_id = ? ORDER BY created_at ASC",
            (str(guild_id), str(owner_id)),
        )
