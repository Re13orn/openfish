"""Telegram long-polling adapter."""

import asyncio
import logging
import random
import time

from telegram import Update
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, ApplicationBuilder, ContextTypes, MessageHandler, filters

from src.formatters import format_upload_received, truncate_for_telegram
from src.models import CommandContext, CommandResult
from src.progress_reporter import ProgressReporter
from src.redaction import redact_text


logger = logging.getLogger(__name__)


class TelegramBotService:
    """Runs Telegram long polling and forwards text messages to the router."""

    def __init__(self, config, router) -> None:
        self.config = config
        self.router = router
        self.progress = ProgressReporter()
        self._app: Application | None = None

    def run_polling(self) -> None:
        retry_delay = float(getattr(self.config, "telegram_reconnect_initial_delay_seconds", 2.0))
        max_retry_delay = float(getattr(self.config, "telegram_reconnect_max_delay_seconds", 300.0))
        jitter_seconds = float(getattr(self.config, "telegram_reconnect_jitter_seconds", 1.0))
        failure_count = 0
        while True:
            self._app = self._build_application()
            self._register_handlers(self._app)
            try:
                self._app.run_polling(
                    poll_interval=self.config.poll_interval_seconds,
                    allowed_updates=Update.ALL_TYPES,
                    close_loop=False,
                )
                return
            except (KeyboardInterrupt, SystemExit):
                raise
            except (TimedOut, NetworkError) as exc:
                failure_count += 1
                logger.warning(
                    "Telegram polling interrupted by network error (consecutive failures=%s): %s. Retry in %.1fs.",
                    failure_count,
                    exc,
                    retry_delay,
                )
            except TelegramError as exc:
                logger.error("Telegram polling stopped due to Telegram API error: %s", exc)
                raise
            except Exception:
                failure_count += 1
                logger.exception(
                    "Telegram polling crashed unexpectedly (consecutive failures=%s). Retry in %.1fs.",
                    failure_count,
                    retry_delay,
                )

            sleep_seconds = retry_delay + random.uniform(0.0, max(0.0, jitter_seconds))
            time.sleep(sleep_seconds)
            retry_delay = min(retry_delay * 2, max_retry_delay)

    def _build_application(self) -> Application:
        return (
            ApplicationBuilder()
            .token(self.config.telegram_bot_token)
            .connect_timeout(20.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .pool_timeout(30.0)
            .build()
        )

    def _register_handlers(self, app: Application) -> None:
        app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, self._on_text_message))
        app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, self._on_document_message))
        app.add_error_handler(self._on_application_error)

    async def _on_application_error(
        self,
        update: object,  # noqa: ANN401
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        _ = update
        error = context.error
        if isinstance(error, (TimedOut, NetworkError)):
            logger.warning("Telegram application transient network error: %s", error)
            return
        logger.exception("Unhandled Telegram application error: %s", error)

    async def _on_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        if message is None or user is None or chat is None or message.text is None:
            return

        command_context = CommandContext(
            telegram_user_id=str(user.id),
            telegram_chat_id=str(chat.id),
            telegram_message_id=str(message.message_id),
            text=message.text,
            telegram_username=user.username,
            telegram_display_name=user.full_name,
        )
        try:
            command = message.text.strip().split(" ", 1)[0].split("@", 1)[0]
            ack_text = self.progress.ack_text(command)
            if ack_text:
                await self._safe_reply_text(message, ack_text, context="sending ack")

            result = self.router.handle(command_context)
            await self._safe_reply_text(message, result.reply_text, context="sending command result")
        except Exception:  # pragma: no cover - defensive logging around external API callbacks
            logger.exception("Unhandled exception while processing Telegram message.")
            await self._safe_reply_text(message, "Internal error while handling request.", context="sending error")

    async def _on_document_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        document = message.document if message else None
        if message is None or user is None or chat is None or document is None:
            return

        command_context = CommandContext(
            telegram_user_id=str(user.id),
            telegram_chat_id=str(chat.id),
            telegram_message_id=str(message.message_id),
            text=message.caption or "/upload",
            telegram_username=user.username,
            telegram_display_name=user.full_name,
        )
        try:
            await self._safe_reply_text(
                message,
                "已收到文件，处理中：\n- 校验项目\n- 校验文件\n- 下载并分析",
                context="sending upload ack",
            )

            plan_or_error = self.router.prepare_document_upload(
                command_context,
                original_name=document.file_name or "upload.bin",
                size_bytes=int(document.file_size or 0),
            )
            if isinstance(plan_or_error, CommandResult):
                await self._safe_reply_text(
                    message,
                    plan_or_error.reply_text,
                    context="sending upload rejection",
                )
                return

            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(custom_path=str(plan_or_error.local_path))
            await self._safe_reply_text(
                message,
                format_upload_received(
                    file_name=plan_or_error.original_name,
                    size_bytes=plan_or_error.size_bytes,
                    local_path=self._display_upload_path(plan_or_error),
                ),
                context="sending upload accepted",
            )

            result = self.router.handle_uploaded_document(
                ctx=command_context,
                plan=plan_or_error,
                caption=message.caption,
            )
            await self._safe_reply_text(message, result.reply_text, context="sending upload result")
        except Exception:  # pragma: no cover - defensive logging around external API callbacks
            logger.exception("Unhandled exception while processing Telegram document.")
            await self._safe_reply_text(
                message,
                "Internal error while handling uploaded file.",
                context="sending upload error",
            )

    def _display_upload_path(self, plan) -> str:  # noqa: ANN001
        try:
            return str(plan.local_path.resolve().relative_to(plan.active.project.path.resolve()))
        except ValueError:
            return plan.local_path.name

    def dispatch_text(self, telegram_user_id: str, chat_id: str, text: str, message_id: str | None = None):
        """Helper for tests and local dispatch without Telegram transport."""

        ctx = CommandContext(
            telegram_user_id=telegram_user_id,
            telegram_chat_id=chat_id,
            telegram_message_id=message_id,
            text=text,
        )
        return self.router.handle(ctx)

    async def _safe_reply_text(self, message, text: str, *, context: str) -> bool:  # noqa: ANN001
        """Send Telegram reply with conservative retries for transient network failures."""

        payload = truncate_for_telegram(
            redact_text(text),
            limit=self.config.max_telegram_message_length,
        )
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await message.reply_text(payload)
                return True
            except RetryAfter as exc:
                retry_after = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else float(exc.retry_after)
                wait_seconds = max(1.0, min(retry_after, 10.0))
                logger.warning(
                    "Telegram rate limit while %s, retry in %.1fs (attempt %s/%s).",
                    context,
                    wait_seconds,
                    attempt,
                    max_attempts,
                )
                if attempt >= max_attempts:
                    return False
                await asyncio.sleep(wait_seconds)
            except (TimedOut, NetworkError) as exc:
                if attempt >= max_attempts:
                    logger.warning("Telegram network error while %s: %s", context, exc)
                    return False
                wait_seconds = float(attempt)
                logger.warning(
                    "Telegram transient error while %s (attempt %s/%s): %s",
                    context,
                    attempt,
                    max_attempts,
                    exc,
                )
                await asyncio.sleep(wait_seconds)
            except TelegramError as exc:
                logger.warning("Telegram API error while %s: %s", context, exc)
                return False
            except Exception:  # pragma: no cover - defensive guard around external API callbacks
                logger.exception("Unexpected error while %s.", context)
                return False
        return False
