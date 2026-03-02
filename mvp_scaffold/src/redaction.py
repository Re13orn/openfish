"""Best-effort redaction helpers for logs and chat-safe summaries."""

from __future__ import annotations

import re
from typing import Any


TOKEN_PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{20,}\b"),  # Telegram-like tokens
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+=*"),
]
KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|token|password|secret|authorization)\b\s*[:=]\s*([^\s,;]+)"
)
ENV_EXPORT_PATTERN = re.compile(
    r"(?i)\b(export\s+)?(api[_-]?key|token|password|secret|authorization)\b\s*=\s*([^\s]+)"
)


def redact_text(text: str) -> str:
    """Redact sensitive-looking tokens from plain text."""

    if not text:
        return text

    redacted = text
    for pattern in TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)

    redacted = KEY_VALUE_PATTERN.sub(r"\1=[REDACTED]", redacted)
    redacted = ENV_EXPORT_PATTERN.sub(r"\1\2=[REDACTED]", redacted)
    return redacted


def redact_object(value: Any) -> Any:
    """Recursively redact strings in nested structures."""

    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact_object(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_object(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_object(v) for v in value)
    return value
