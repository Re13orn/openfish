"""Shared application models for command routing and storage."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ProjectConfig:
    """Project settings loaded from the YAML registry."""

    key: str
    name: str
    path: Path
    default_branch: str | None = None
    test_command: str | None = None
    dev_command: str | None = None
    description: str | None = None
    allowed_directories: list[Path] | None = None
    memory_seed_summary: str | None = None
    seed_notes: list[str] | None = None
    is_active: bool = True


@dataclass(slots=True)
class CommandContext:
    """Telegram message metadata passed into the command router."""

    telegram_user_id: str
    telegram_chat_id: str
    telegram_message_id: str | None
    text: str
    telegram_username: str | None = None
    telegram_display_name: str | None = None


@dataclass(slots=True)
class CommandResult:
    """Router response payload to be sent back to Telegram."""

    reply_text: str
    metadata: dict[str, Any] | None = None


@dataclass(slots=True)
class UserRecord:
    """Internal representation of an authorized Telegram user."""

    id: int
    telegram_user_id: str
