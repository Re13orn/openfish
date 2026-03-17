"""Internal types shared across CommandRouter handler mixins."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.models import ProjectConfig, UserRecord


PROJECT_ADD_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def _clip_text(text: str | None, limit: int = 120) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


@dataclass(slots=True)
class ActiveProjectContext:
    user: UserRecord
    project_key: str
    project: ProjectConfig
    project_id: int


@dataclass(slots=True)
class DocumentUploadPlan:
    active: ActiveProjectContext
    original_name: str
    safe_name: str
    size_bytes: int
    local_path: Path


@dataclass(slots=True)
class ActiveTaskExecution:
    task_id: int
    project_id: int
    process: subprocess.Popen[str] | None = None
    cancel_requested: bool = False
