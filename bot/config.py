from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(slots=True)
class Config:
    discord_token: str
    database_path: str
    max_channels_per_container: int
    default_container_visibility: str
    allow_bot_invites: bool
    emoji_docker: str
    emoji_success: str
    emoji_failure: str
    emoji_warning: str
    sync_commands: bool

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token == "put-token-here":
            raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and set your bot token.")

        database_path = os.getenv("DATABASE_PATH", "/app/data/dockerize.sqlite3")
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        visibility = os.getenv("DEFAULT_CONTAINER_VISIBILITY", "private").strip().lower()
        if visibility not in {"private", "public"}:
            visibility = "private"

        return cls(
            discord_token=token,
            database_path=database_path,
            max_channels_per_container=max(4, int(os.getenv("MAX_CHANNELS_PER_CONTAINER", "10"))),
            default_container_visibility=visibility,
            allow_bot_invites=os.getenv("ALLOW_BOT_INVITES", "false").strip().lower() in {"1", "true", "yes", "on"},
            emoji_docker=os.getenv("EMOJI_DOCKER", ":docker:"),
            emoji_success=os.getenv("EMOJI_SUCCESS", ":success:"),
            emoji_failure=os.getenv("EMOJI_FAILURE", ":failure:"),
            emoji_warning=os.getenv("EMOJI_WARNING", ":warning:"),
            sync_commands=os.getenv("SYNC_COMMANDS", "true").strip().lower() in {"1", "true", "yes", "on"},
        )
