"""Telegram outbound message delivery helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
from typing import Any, Callable

from telegram.error import BadRequest, Conflict, Forbidden, InvalidToken, NetworkError, RetryAfter, TelegramError, TimedOut

from src.formatters import truncate_for_telegram
from src.redaction import redact_text


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramSendSpec:
    text: str
    context: str
    reply_markup: Any | None = None
    dedup_key: str | None = None
    dedup_window_seconds: float | None = None
    edit_context: str | None = None
    edit_window_seconds: float | None = None


class TelegramMessageSink:
    """Delivers outbound Telegram messages with retries and default markup fallback."""

    def __init__(
        self,
        config,
        *,
        default_reply_markup_factory: Callable[[], Any],
        delivery_tracker=None,  # noqa: ANN001
    ) -> None:
        self.config = config
        self.default_reply_markup_factory = default_reply_markup_factory
        self._delivery_tracker = delivery_tracker

    async def send_typing(self, message, *, context: str) -> bool:  # noqa: ANN001
        try:
            if hasattr(message, "reply_chat_action"):
                await message.reply_chat_action(action="typing")
                return True
            chat_id = getattr(message, "chat_id", None)
            get_bot = getattr(message, "get_bot", None)
            if chat_id is not None and callable(get_bot):
                bot = get_bot()
                await bot.send_chat_action(chat_id=chat_id, action="typing")
                return True
        except RetryAfter as exc:
            logger.debug("Telegram rate limit while %s typing indicator: %s", context, exc)
            return False
        except (TimedOut, NetworkError, TelegramError):
            logger.debug("Telegram typing indicator failed while %s.", context, exc_info=True)
            return False
        except Exception:  # pragma: no cover - external callback safety guard
            logger.debug("Unexpected typing indicator failure while %s.", context, exc_info=True)
            return False
        return False

    async def send(self, message, spec: TelegramSendSpec) -> bool:  # noqa: ANN001
        payload = truncate_for_telegram(
            redact_text(spec.text),
            limit=self.config.max_telegram_message_length,
        )
        dedup_key = spec.dedup_key or self.build_dedup_key(
            text=payload,
            context=spec.context,
            reply_markup=spec.reply_markup,
        )
        dedup_window = spec.dedup_window_seconds
        if dedup_window is None:
            dedup_window = float(getattr(self.config, "telegram_delivery_dedup_window_seconds", 2.0))
        chat_id = self._extract_chat_id(message)
        if (
            chat_id is not None
            and dedup_key
            and dedup_window > 0
            and self._should_skip_delivery(chat_id=chat_id, dedup_key=dedup_key, max_age_seconds=dedup_window)
        ):
            logger.debug("Skip duplicate Telegram delivery while %s for chat_id=%s.", spec.context, chat_id)
            return True
        max_attempts = 3
        final_reply_markup = spec.reply_markup or self.default_reply_markup_factory()
        if (
            chat_id is not None
            and spec.edit_context
            and (spec.edit_window_seconds or 0) > 0
            and await self._try_edit_recent_message(
                message=message,
                chat_id=chat_id,
                text=payload,
                reply_markup=final_reply_markup,
                context=spec.context,
                dedup_key=dedup_key,
                edit_context=spec.edit_context,
                edit_window_seconds=float(spec.edit_window_seconds or 0),
            )
        ):
            return True
        for attempt in range(1, max_attempts + 1):
            try:
                sent_message = await message.reply_text(payload, reply_markup=final_reply_markup)
                if chat_id is not None and dedup_key:
                    self._remember_delivery(
                        chat_id=chat_id,
                        dedup_key=dedup_key,
                        context=spec.context,
                        message_id=self._extract_message_id(sent_message),
                    )
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
                category = self._classify_api_error(exc)
                if category == "server" and attempt < max_attempts:
                    wait_seconds = float(attempt)
                    logger.warning(
                        "Telegram server error while %s (attempt %s/%s): %s",
                        spec.context,
                        attempt,
                        max_attempts,
                        exc,
                    )
                    await asyncio.sleep(wait_seconds)
                    continue
                logger.warning("Telegram %s error while %s: %s", category, spec.context, exc)
                return False
            except Exception:  # pragma: no cover - external callback safety guard
                logger.exception("Unexpected error while %s.", spec.context)
                return False
        return False

    def build_dedup_key(self, *, text: str, context: str, reply_markup: Any | None) -> str:
        material = f"{context}\n{text}\n{self._serialize_reply_markup(reply_markup)}".encode("utf-8", "ignore")
        return hashlib.sha1(material).hexdigest()

    def _classify_api_error(self, exc: TelegramError) -> str:
        if isinstance(exc, (BadRequest, Forbidden, InvalidToken, Conflict)):
            return "client"

        message = str(exc).lower()
        if any(
            token in message
            for token in (
                "internal server error",
                "server error",
                "bad gateway",
                "gateway timeout",
                "service unavailable",
                "temporarily unavailable",
            )
        ):
            return "server"
        return "api"

    async def _try_edit_recent_message(
        self,
        *,
        message,  # noqa: ANN001
        chat_id: str,
        text: str,
        reply_markup: Any | None,
        context: str,
        dedup_key: str,
        edit_context: str,
        edit_window_seconds: float,
    ) -> bool:
        tracker = getattr(self, "_delivery_tracker", None)
        if tracker is None:
            return False
        target_message_id = tracker.get_recent_outbound_message_id_by_context(
            chat_id=chat_id,
            context=edit_context,
            max_age_seconds=edit_window_seconds,
        )
        if target_message_id is None:
            return False
        get_bot = getattr(message, "get_bot", None)
        if not callable(get_bot):
            return False
        try:
            bot = get_bot()
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=target_message_id,
                text=text,
                reply_markup=reply_markup,
            )
            self._remember_delivery(
                chat_id=chat_id,
                dedup_key=dedup_key,
                context=context,
                message_id=target_message_id,
            )
            return True
        except BadRequest as exc:
            logger.debug("Telegram edit skipped while %s: %s", context, exc)
            return False
        except (TimedOut, NetworkError, TelegramError):
            logger.debug("Telegram edit failed while %s.", context, exc_info=True)
            return False

    def _extract_chat_id(self, message) -> str | None:  # noqa: ANN001
        chat_id = getattr(message, "chat_id", None)
        if chat_id is not None:
            return str(chat_id)
        chat = getattr(message, "chat", None)
        chat_id = getattr(chat, "id", None)
        return str(chat_id) if chat_id is not None else None

    def _extract_message_id(self, sent_message) -> str | None:  # noqa: ANN001
        if sent_message is None:
            return None
        message_id = getattr(sent_message, "message_id", None)
        return str(message_id) if message_id is not None else None

    def _serialize_reply_markup(self, reply_markup: Any | None) -> str:
        if reply_markup is None:
            return ""
        inline_keyboard = getattr(reply_markup, "inline_keyboard", None)
        if inline_keyboard is not None:
            rows: list[str] = []
            for row in inline_keyboard:
                items = [
                    f"{getattr(button, 'text', '')}|{getattr(button, 'callback_data', '')}"
                    for button in row
                ]
                rows.append(",".join(items))
            return ";".join(rows)

        keyboard = getattr(reply_markup, "keyboard", None)
        if keyboard is not None:
            rows = []
            for row in keyboard:
                items = [str(getattr(button, "text", button)) for button in row]
                rows.append(",".join(items))
            return ";".join(rows)
        return reply_markup.__class__.__name__

    def _should_skip_delivery(self, *, chat_id: str, dedup_key: str, max_age_seconds: float) -> bool:
        tracker = getattr(self, "_delivery_tracker", None)
        if tracker is None:
            return False
        recent_message_id = tracker.get_recent_outbound_message_id(
            chat_id=chat_id,
            dedup_key=dedup_key,
            max_age_seconds=max_age_seconds,
        )
        return recent_message_id is not None

    def _remember_delivery(
        self,
        *,
        chat_id: str,
        dedup_key: str,
        context: str,
        message_id: str | None,
    ) -> None:
        tracker = getattr(self, "_delivery_tracker", None)
        if tracker is None:
            return
        tracker.remember_outbound_message(
            chat_id=chat_id,
            dedup_key=dedup_key,
            context=context,
            message_id=message_id,
        )
