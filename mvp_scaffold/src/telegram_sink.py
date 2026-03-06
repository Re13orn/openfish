"""Telegram outbound message delivery helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Callable

from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from src.formatters import truncate_for_telegram
from src.redaction import redact_text


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramSendSpec:
    text: str
    context: str
    reply_markup: Any | None = None


class TelegramMessageSink:
    """Delivers outbound Telegram messages with retries and default markup fallback."""

    def __init__(self, config, *, default_reply_markup_factory: Callable[[], Any]) -> None:  # noqa: ANN001
        self.config = config
        self.default_reply_markup_factory = default_reply_markup_factory

    async def send(self, message, spec: TelegramSendSpec) -> bool:  # noqa: ANN001
        payload = truncate_for_telegram(
            redact_text(spec.text),
            limit=self.config.max_telegram_message_length,
        )
        max_attempts = 3
        final_reply_markup = spec.reply_markup or self.default_reply_markup_factory()
        for attempt in range(1, max_attempts + 1):
            try:
                await message.reply_text(payload, reply_markup=final_reply_markup)
                return True
            except RetryAfter as exc:
                retry_after = (
                    exc.retry_after.total_seconds()
                    if hasattr(exc.retry_after, "total_seconds")
                    else float(exc.retry_after)
                )
                wait_seconds = max(1.0, min(retry_after, 10.0))
                logger.warning(
                    "Telegram rate limit while %s, retry in %.1fs (attempt %s/%s).",
                    spec.context,
                    wait_seconds,
                    attempt,
                    max_attempts,
                )
                if attempt >= max_attempts:
                    return False
                await asyncio.sleep(wait_seconds)
            except (TimedOut, NetworkError) as exc:
                if attempt >= max_attempts:
                    logger.warning("Telegram network error while %s: %s", spec.context, exc)
                    return False
                wait_seconds = float(attempt)
                logger.warning(
                    "Telegram transient error while %s (attempt %s/%s): %s",
                    spec.context,
                    attempt,
                    max_attempts,
                    exc,
                )
                await asyncio.sleep(wait_seconds)
            except TelegramError as exc:
                logger.warning("Telegram API error while %s: %s", spec.context, exc)
                return False
            except Exception:  # pragma: no cover - external callback safety guard
                logger.exception("Unexpected error while %s.", spec.context)
                return False
        return False
