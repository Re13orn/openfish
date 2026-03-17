"""Telegram long-polling adapter."""

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
import logging
from pathlib import Path
import random
import shlex
from threading import Lock
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from src import telegram_messages
from src.formatters import format_autopilot_status, format_current_task, format_home, format_upload_received
from src.models import CommandContext, CommandResult, ProjectTemplatePreset
from src.progress_reporter import ProgressReporter
from src.telegram_sink import TelegramMessageSink, TelegramSendSpec
from src.autopilot_store import AutopilotRunRecord
from src.task_store import TaskRecord
from src.telegram_views import TelegramReplySpec, TelegramViewFactory


logger = logging.getLogger(__name__)

_QUOTE_PAIRS = {
    '"': '"',
    "'": "'",
    "“": "”",
    "‘": "’",
    "「": "」",
    "『": "』",
}


@dataclass(slots=True)
class _StreamProgressState:
    command: str
    phases: list[str]
    phase_delay_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    _output_lines: deque[str] = field(default_factory=lambda: deque(maxlen=5))
    _output_version: int = 0
    _lock: Lock = field(default_factory=Lock)

    def add_output(self, text: str) -> None:
        normalized = text.strip()
        if not normalized:
            return
        if len(normalized) > 180:
            normalized = normalized[:177] + "..."
        with self._lock:
            if self._output_lines and self._output_lines[-1] == normalized:
                return
            self._output_lines.append(normalized)
            self._output_version += 1

    def snapshot(self) -> tuple[list[str], int]:
        with self._lock:
            return list(self._output_lines), self._output_version


@dataclass(slots=True)
class _ChatTarget:
    chat_id: str
    bot: object

    def get_bot(self):  # noqa: ANN201
        return self.bot

    async def reply_text(self, text: str, **kwargs):  # noqa: ANN003, ANN201
        return await self.bot.send_message(chat_id=self.chat_id, text=text, **kwargs)


class TelegramBotService:
    """Runs Telegram long polling and forwards text messages to the router."""

    _MENU_PROJECTS = "项目"
    _MENU_ASK = "提问"
    _MENU_DO = "执行"
    _MENU_STATUS = "状态"
    _MENU_RESUME = "继续"
    _MENU_DIFF = "变更"
    _MENU_SCHEDULE = "定时"
    _MENU_MORE = "更多"
    _MENU_HELP = "帮助"
    _MENU_CURRENT_TASK = "当前任务"
    _MENU_CANCEL_TASK = "取消任务"
    _MENU_APPROVE_ACTION = "批准"
    _MENU_REJECT_ACTION = "拒绝"

    _CALLBACK_COMMANDS = {
        "start": "/start",
        "home": "/home",
        "context": "/context",
        "help": "/help",
        "status": "/status",
        "projects": "/projects",
        "skills": "/skills",
        "mcp": "/mcp",
        "sessions": "/sessions",
        "model": "/model",
        "health": "/health",
        "version": "/version",
        "update_check": "/update-check",
        "update": "/update",
        "restart": "/restart",
        "logs": "/logs",
        "logs_clear": "/logs-clear",
        "task_current": "/task-current",
        "autopilots": "/autopilots",
        "autopilot_status": "/autopilot-status",
        "autopilot_context": "/autopilot-context",
        "autopilot_log": "/autopilot-log",
        "autopilot_step": "/autopilot-step",
        "autopilot_pause": "/autopilot-pause",
        "autopilot_resume": "/autopilot-resume",
        "autopilot_stop": "/autopilot-stop",
        "tasks": "/tasks",
        "tasks_clear": "/tasks-clear",
        "schedule_list": "/schedule-list",
        "last": "/last",
        "memory": "/memory",
        "diff": "/diff",
        "upload_policy": "/upload_policy",
        "cancel": "/cancel",
        "approve": "/approve",
        "reject": "/reject",
        "resume": "/resume",
        "project_root_show": "/project-root",
        "project_templates": "/project-templates",
        "project_disable_current": "/project-disable",
        "project_archive_current": "/project-archive",
        "ui_show": "/ui",
        "ui_summary": "/ui summary",
        "ui_stream": "/ui stream",
        "ui_verbose": "/ui verbose",
    }

    _WIZARD_TOKENS = {"project_add", "schedule_add", "approve_note", "reject_note"}
    _WIZARD_CANCEL_TOKENS = {"取消", "cancel", "/cancel", "退出", "exit"}
    _WIZARD_SKIP_TOKENS = {"跳过", "skip", "-", "无"}
    _WIZARD_CONFIRM_TOKENS = {"确认", "确认执行", "confirm", "ok", "yes"}

    _PROMPT_COMMANDS = {
        "ask": "/ask",
        "do": "/do",
        "note": "/note",
        "retry": "/retry",
        "resume": "/resume",
        "skill_install": "/skill-install",
        "project_root": "/project-root",
        "project_template_root": "/project-template-root",
        "use": "/use",
        "project_disable": "/project-disable",
        "project_archive": "/project-archive",
        "approve": "/approve",
        "reject": "/reject",
        "mcp": "/mcp",
        "model": "/model",
        "autopilot": "/autopilot",
        "autopilot_takeover": "/autopilot-takeover",
        "send_file": "/download-file",
        "github_clone": "/github-clone",
    }

    _PROMPT_HINTS = {
        "ask": "请输入问题。下一条消息将按 /ask 执行。",
        "do": "请输入任务描述。下一条消息将按 /do 执行。",
        "note": "请输入笔记内容。下一条消息将按 /note 保存。",
        "retry": "请输入补充说明。下一条消息将按 /retry 执行（可留空直接用 /retry）。",
        "resume": "请输入恢复指令（示例: 12 继续修复测试）。下一条消息将按 /resume 执行。",
        "skill_install": "请输入 skill 来源。下一条消息将按 /skill-install 执行。",
        "project_root": "请输入默认项目根目录（示例: /Users/you/workspace/projects）。下一条消息将按 /project-root 执行。",
        "project_template_root": "请输入项目模板根目录（示例: /Users/you/workspace/project_templates）。下一条消息将按 /project-template-root 执行。",
        "use": "请输入项目 key。下一条消息将按 /use 执行。",
        "project_disable": "请输入要停用的项目 key。下一条消息将按 /project-disable 执行。",
        "project_archive": "请输入要归档的项目 key。下一条消息将按 /project-archive 执行。",
        "approve": "请输入审批备注。下一条消息将按 /approve 执行。",
        "reject": "请输入拒绝原因。下一条消息将按 /reject 执行。",
        "mcp": "请输入 MCP 名称（留空则查看列表）。下一条消息将按 /mcp 执行。",
        "model": "请输入模型名称。下一条消息将按 /model set 执行。",
        "autopilot": "请输入长期任务目标。下一条消息将按 /autopilot 执行。",
        "autopilot_takeover": "请输入新的高层指令。下一条消息将按 /autopilot-takeover 执行。",
        "send_file": "请输入本机文件绝对路径，或使用 ~ 开头。下一条消息将按 /download-file 执行。",
        "github_clone": "请输入公开 GitHub 仓库 URL 或 owner/repo，可选再跟一个相对目录名。下一条消息将按 /github-clone 执行。",
    }
    _TYPING_COMMANDS = {
        "/ask",
        "/approve",
        "/do",
        "/mcp",
        "/reject",
        "/resume",
        "/retry",
        "/schedule-run",
        "/skill-install",
        "/logs",
        "/update",
        "/update-check",
        "/restart",
        "/github-clone",
    }
    _CURRENT_TASK_CARD_COMMANDS = {
        "/ask",
        "/approve",
        "/do",
        "/reject",
        "/resume",
        "/retry",
        "/schedule-run",
    }

    def __init__(self, config, router) -> None:
        self.config = config
        self.router = router
        self.progress = ProgressReporter()
        self.views = TelegramViewFactory()
        delivery_tracker = getattr(getattr(router, "tasks", None), "chat_state", None)
        self.sink = TelegramMessageSink(
            config,
            default_reply_markup_factory=self._main_menu_markup,
            delivery_tracker=delivery_tracker,
        )
        self._app: Application | None = None
        self._app_loop: asyncio.AbstractEventLoop | None = None
        self._pending_command_by_chat: dict[str, str] = {}
        self._autopilot_stream_tasks: dict[int, asyncio.Task[None]] = {}
        self._home_dashboard_signatures: dict[str, tuple[object, ...]] = {}
        self._notification_poll_task: asyncio.Task[None] | None = None
        autopilot = getattr(router, "autopilot", None)
        if autopilot is not None and hasattr(autopilot, "set_raw_output_observer"):
            autopilot.set_raw_output_observer(self._on_autopilot_raw_output)

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
                    bootstrap_retries=-1,
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
        request = HTTPXRequest(
            connection_pool_size=int(getattr(self.config, "telegram_connection_pool_size", 64)),
            connect_timeout=20.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=float(getattr(self.config, "telegram_pool_timeout_seconds", 15.0)),
        )
        get_updates_request = HTTPXRequest(
            connection_pool_size=int(
                getattr(self.config, "telegram_get_updates_connection_pool_size", 8)
            ),
            connect_timeout=20.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=float(
                getattr(self.config, "telegram_get_updates_pool_timeout_seconds", 30.0)
            ),
        )
        builder = (
            ApplicationBuilder()
            .token(self.config.telegram_bot_token)
            .request(request)
            .get_updates_request(get_updates_request)
            .concurrent_updates(int(getattr(self.config, "telegram_concurrent_updates", 32)))
            .post_init(self._on_post_init)
        )
        if hasattr(builder, "post_shutdown"):
            builder = builder.post_shutdown(self._on_post_shutdown)
        return builder.build()

    def _register_handlers(self, app: Application) -> None:
        app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, self._on_text_message))
        app.add_handler(MessageHandler(filters.Document.ALL & filters.ChatType.PRIVATE, self._on_document_message))
        app.add_handler(CallbackQueryHandler(self._on_callback_query))
        app.add_error_handler(self._on_application_error)

    async def _on_post_init(self, app: Application) -> None:
        self._app_loop = asyncio.get_running_loop()
        await self._deliver_pending_system_notifications(app)
        if self._notification_poll_task is not None:
            self._notification_poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._notification_poll_task
        self._notification_poll_task = asyncio.create_task(self._notification_poll_loop(app))

    async def _on_post_shutdown(self, app: Application) -> None:
        _ = app
        task = self._notification_poll_task
        self._notification_poll_task = None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _notification_poll_loop(self, app: Application) -> None:
        """Periodically deliver queued system notifications (e.g. health alerts)."""
        try:
            while True:
                await asyncio.sleep(30)
                try:
                    await self._deliver_pending_system_notifications(app)
                except Exception:
                    logger.warning("Periodic notification delivery failed.", exc_info=True)
        except asyncio.CancelledError:
            raise

    async def _deliver_pending_system_notifications(self, app: Application) -> None:
        tasks = getattr(self.router, "tasks", None)
        if tasks is None or not hasattr(tasks, "list_pending_system_notifications"):
            return
        pending = tasks.list_pending_system_notifications(limit=32)
        if not pending:
            return
        for item in pending:
            text = self._system_notification_text(
                item.notification_kind,
                payload=getattr(item, "payload", None),
            )
            if text is None:
                tasks.delete_system_notification(notification_id=item.id)
                continue
            try:
                await app.bot.send_message(
                    chat_id=item.telegram_chat_id,
                    text=text,
                    reply_markup=self._main_menu_markup(),
                )
            except Exception:
                logger.warning(
                    "Failed to deliver pending system notification kind=%s chat_id=%s",
                    item.notification_kind,
                    item.telegram_chat_id,
                    exc_info=True,
                )
                continue
            tasks.delete_system_notification(notification_id=item.id)

    def _system_notification_text(self, kind: str, payload: dict | None = None) -> str | None:
        version_text = ""
        update_service = getattr(self.router, "update_service", None)
        if update_service is not None:
            try:
                info = update_service.get_current_version()
            except Exception:
                info = None
            if info is not None:
                version_text = f"\n当前版本: {info.version} ({info.commit})"
        if kind == "restart_completed":
            return f"OpenFish 已重启完成。{version_text}".rstrip()
        if kind == "update_completed":
            return f"OpenFish 已更新并重启完成。{version_text}".rstrip()
        if kind == "scheduler_restarted":
            msg = (payload or {}).get("message") or "调度器意外重启"
            return f"[健康告警] {msg}"
        if kind == "health_alert":
            msg = (payload or {}).get("message") or "系统健康异常"
            return f"[健康告警] {msg}"
        if kind == "scheduled_task_result":
            data = payload or {}
            schedule_id = data.get("schedule_id")
            project_key = data.get("project_key") or "未知项目"
            command_type = data.get("command_type") or "ask"
            status = data.get("status") or "unknown"
            task_id = data.get("task_id")
            summary = str(data.get("summary") or "").strip()
            if len(summary) > 280:
                summary = summary[:277] + "..."
            lines = [
                "[定时任务结果]",
                f"项目: {project_key}",
                f"定时: #{schedule_id}" if schedule_id is not None else "定时: 未知",
                f"类型: /{command_type}",
                f"状态: {status}",
            ]
            if task_id is not None:
                lines.append(f"任务: #{task_id}")
            if summary:
                lines.append(f"摘要: {summary}")
            return "\n".join(lines)
        return None

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
            mapped = self._map_menu_to_command(message.text.strip())
            if mapped == "__projects__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._send_projects_panel(message, command_context)
                return
            if mapped == "__ask__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._activate_prompt(message, command_context, "ask")
                return
            if mapped == "__do__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._activate_prompt(message, command_context, "do")
                return
            if mapped == "__resume__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._execute_command(message, command_context, "/resume")
                return
            if mapped == "__diff__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._execute_command(message, command_context, "/diff")
                return
            if mapped == "__schedule__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._send_schedule_panel(message, command_context)
                return
            if mapped == "__more__":
                self._clear_input_modes(command_context.telegram_chat_id)
                await self._send_more_panel(message)
                return

            raw_text = mapped or message.text.strip()
            wizard_state = self._get_wizard_state(command_context.telegram_chat_id)
            if wizard_state is not None and (
                not raw_text.startswith("/")
                or (
                    str(wizard_state.get("kind")) == "project_add"
                    and str(wizard_state.get("step")) == "path"
                )
            ):
                handled = await self._handle_wizard_input(
                    message,
                    command_context,
                    wizard_state,
                    raw_text,
                )
                if handled:
                    return

            if raw_text.startswith("/"):
                self._clear_input_modes(command_context.telegram_chat_id)
            else:
                pending_command = self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                if pending_command is None and hasattr(self.router, "tasks"):
                    pending_command = self.router.tasks.get_chat_pending_command(
                        chat_id=command_context.telegram_chat_id
                    )
                if pending_command and hasattr(self.router, "tasks"):
                    self.router.tasks.clear_chat_pending_command(chat_id=command_context.telegram_chat_id)
                if pending_command:
                    raw_text = f"{pending_command} {raw_text}"

            command = raw_text.split(" ", 1)[0].split("@", 1)[0]
            effective_command = command if raw_text.startswith("/") else "/ask"
            ack_text = self.progress.ack_text(effective_command)
            if ack_text:
                await self._send_text(
                    message,
                    ack_text,
                    context="sending ack",
                    reply_markup=self._main_menu_markup(),
                )

            command_context = CommandContext(
                telegram_user_id=command_context.telegram_user_id,
                telegram_chat_id=command_context.telegram_chat_id,
                telegram_message_id=command_context.telegram_message_id,
                text=raw_text,
                telegram_username=command_context.telegram_username,
                telegram_display_name=command_context.telegram_display_name,
            )
            result = await self._dispatch_router_command(
                message,
                command_context,
                command=effective_command,
            )
            if await self._maybe_start_wizard_from_result(message, command_context, result):
                return
            await self._send_command_result(message, effective_command, command_context, result)
        except Exception:  # pragma: no cover - defensive logging around external API callbacks
            logger.exception("Unhandled exception while processing Telegram message.")
            await self._send_text(
                message,
                telegram_messages.internal_request_error(),
                context="sending error",
                reply_markup=self._main_menu_markup(),
            )

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
            await self._send_text(
                message,
                telegram_messages.upload_processing_ack(),
                context="sending upload ack",
                reply_markup=self._main_menu_markup(),
            )
            await self.sink.send_typing(message, context="processing upload")

            plan_or_error = self.router.prepare_document_upload(
                command_context,
                original_name=document.file_name or "upload.bin",
                size_bytes=int(document.file_size or 0),
            )
            if isinstance(plan_or_error, CommandResult):
                await self._send_text(
                    message,
                    plan_or_error.reply_text,
                    context="sending upload rejection",
                    reply_markup=self._main_menu_markup(),
                )
                return

            telegram_file = await document.get_file()
            await telegram_file.download_to_drive(custom_path=str(plan_or_error.local_path))
            await self._send_text(
                message,
                format_upload_received(
                    file_name=plan_or_error.original_name,
                    size_bytes=plan_or_error.size_bytes,
                    local_path=self._display_upload_path(plan_or_error),
                ),
                context="sending upload accepted",
                reply_markup=self._main_menu_markup(),
            )

            result = self.router.handle_uploaded_document(
                ctx=command_context,
                plan=plan_or_error,
                caption=message.caption,
            )
            await self._send_view_spec(
                message,
                TelegramReplySpec(text=result.reply_text, reply_markup=self._main_menu_markup()),
                context="sending upload result",
            )
        except BadRequest as exc:
            error_text = str(exc).strip() or "BadRequest"
            if "file is too big" in error_text.lower():
                logger.info(
                    "Telegram rejected oversized document download: name=%s size=%s",
                    document.file_name,
                    document.file_size,
                )
                await self._send_text(
                    message,
                    telegram_messages.upload_oversized_hint(),
                    context="sending oversized upload hint",
                    reply_markup=self._main_menu_markup(),
                )
                return
            logger.warning("Telegram bad request while processing document: %s", error_text)
            await self._send_text(
                message,
                telegram_messages.upload_bad_request(error_text),
                context="sending upload bad request",
                reply_markup=self._main_menu_markup(),
            )
        except Exception:  # pragma: no cover - defensive logging around external API callbacks
            logger.exception("Unhandled exception while processing Telegram document.")
            await self._send_text(
                message,
                telegram_messages.upload_internal_error(),
                context="sending upload error",
                reply_markup=self._main_menu_markup(),
            )

    async def _on_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        _ = context
        query = update.callback_query
        if query is None or query.message is None:
            return
        user = update.effective_user
        chat = update.effective_chat
        if user is None or chat is None:
            return
        await query.answer()
        data = query.data or ""
        base_ctx = CommandContext(
            telegram_user_id=str(user.id),
            telegram_chat_id=str(chat.id),
            telegram_message_id=str(query.message.message_id),
            text="",
            telegram_username=user.username,
            telegram_display_name=user.full_name,
        )
        try:
            if await self._dispatch_callback_data(query.message, base_ctx, data):
                return
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Unhandled callback query error: %s", data)
            await self._send_text(
                query.message,
                telegram_messages.callback_error(),
                context="sending callback error",
                reply_markup=self._main_menu_markup(),
            )
            return

        await self._send_text(
            query.message,
            telegram_messages.unknown_callback(),
            context="sending unknown callback",
            reply_markup=self._main_menu_markup(),
        )

    async def _dispatch_callback_data(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        handlers = (
            self._handle_use_callback,
            self._handle_status_callback,
            self._handle_pagination_callback,
            self._handle_approval_callback,
            self._handle_schedule_callback,
            self._handle_task_callback,
            self._handle_taskmode_callback,
            self._handle_cmd_callback,
            self._handle_session_callback,
            self._handle_mcp_callback,
            self._handle_prompt_callback,
            self._handle_wizard_callback_data,
            self._handle_panel_callback,
            self._handle_model_callback,
        )
        for handler in handlers:
            if await handler(message, base_ctx, data):
                return True
        return False

    async def _handle_use_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if not data.startswith("use:"):
            return False
        project_key = data.split(":", 1)[1]
        await self._execute_command(message, base_ctx, f"/use {project_key}")
        return True

    async def _handle_status_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        mapping = {
            "status:resume": lambda: self._execute_command(message, base_ctx, "/resume"),
            "status:diff": lambda: self._execute_command(message, base_ctx, "/diff"),
            "status:ask": lambda: self._activate_prompt(message, base_ctx, "ask"),
            "status:do": lambda: self._activate_prompt(message, base_ctx, "do"),
            "status:projects": lambda: self._send_projects_panel(message, base_ctx),
            "status:approval": lambda: self._send_approval_panel(message, base_ctx),
            "status:schedule": lambda: self._send_schedule_panel(message, base_ctx),
            "status:more": lambda: self._send_more_panel(message),
        }
        action = mapping.get(data)
        if action is None:
            return False
        await action()
        return True

    async def _handle_pagination_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        for prefix, command in (
            ("memory:page:", "/memory "),
            ("sessions:page:", "/sessions "),
            ("tasks:page:", "/tasks "),
        ):
            if data.startswith(prefix):
                page = data.rsplit(":", 1)[1]
                await self._execute_command(message, base_ctx, f"{command}{page}")
                return True
        return False

    async def _handle_approval_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if data == "approval:approve":
            await self._execute_command(message, base_ctx, "/approve")
            await self._clear_inline_keyboard(message)
            return True
        if data == "approval:reject":
            await self._execute_command(message, base_ctx, "/reject")
            await self._clear_inline_keyboard(message)
            return True
        if data == "approval:status":
            await self._execute_command(message, base_ctx, "/status")
            return True
        if data == "approval:more":
            await self._execute_command(message, base_ctx, "/task-current")
            return True
        for prefix, command in (("approval:approve:", "/approve "), ("approval:reject:", "/reject ")):
            if data.startswith(prefix):
                approval_id = data.rsplit(":", 1)[1]
                if not self._is_active_approval_callback(base_ctx, message, approval_id):
                    await self._clear_inline_keyboard(message)
                    await self._send_text(
                        message,
                        "这个审批按钮已过期，请重新打开当前审批卡片。",
                        context="sending expired approval callback",
                        reply_markup=self._main_menu_markup(),
                    )
                    return True
                await self._execute_command(message, base_ctx, f"{command}{approval_id}")
                await self._clear_inline_keyboard(message)
                return True
        return False

    async def _handle_schedule_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if data == "schedule:refresh":
            await self._send_schedule_panel(message, base_ctx)
            return True
        for prefix, command in (
            ("schedule:run:", "/schedule-run "),
            ("schedule:pause:", "/schedule-pause "),
            ("schedule:enable:", "/schedule-enable "),
            ("schedule:del:", "/schedule-del "),
        ):
            if data.startswith(prefix):
                schedule_id = data.rsplit(":", 1)[1]
                await self._execute_command(message, base_ctx, f"{command}{schedule_id}")
                return True
        return False

    async def _handle_task_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        for prefix, command in (
            ("task:cancel:", "/task-cancel "),
            ("task:delete:", "/task-delete "),
            ("task:output:", "/task-output "),
        ):
            if data.startswith(prefix):
                task_id = data.rsplit(":", 1)[1]
                await self._execute_command(message, base_ctx, f"{command}{task_id}")
                return True
        return False

    async def _handle_taskmode_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if data not in {"taskmode:ask", "taskmode:do"}:
            return False
        token = "ask" if data.endswith(":ask") else "do"
        await self._activate_prompt(message, base_ctx, token)
        return True

    async def _handle_cmd_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if not data.startswith("cmd:"):
            return False
        token = data.split(":", 1)[1]
        for prefix, command in (
            ("autopilot_status:", "/autopilot-status "),
            ("autopilot_context:", "/autopilot-context "),
            ("autopilot_log:", "/autopilot-log "),
            ("autopilot_step:", "/autopilot-step "),
            ("autopilot_pause:", "/autopilot-pause "),
            ("autopilot_resume:", "/autopilot-resume "),
            ("autopilot_stop:", "/autopilot-stop "),
        ):
            if token.startswith(prefix):
                run_id = token.split(":", 1)[1]
                await self._execute_command(message, base_ctx, f"{command}{run_id}")
                return True
        if token.startswith("mcp_detail:"):
            name = token.split(":", 1)[1]
            await self._execute_command(message, base_ctx, f"/mcp {name}")
            return True
        if token.startswith("session_detail:"):
            session_id = token.split(":", 1)[1]
            await self._execute_command(message, base_ctx, f"/session {session_id}")
            return True
        command = self._resolve_callback_command(token)
        if command is None:
            return False
        await self._execute_command(message, base_ctx, command)
        return True

    async def _handle_session_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if not data.startswith("session:import:"):
            return False
        session_id = data.split(":", 2)[2]
        await self._execute_command(message, base_ctx, f"/session-import {session_id}")
        return True

    async def _handle_mcp_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        for prefix, command in (("mcp:enable:", "/mcp-enable "), ("mcp:disable:", "/mcp-disable ")):
            if data.startswith(prefix):
                name = data.split(":", 2)[2]
                await self._execute_command(message, base_ctx, f"{command}{name}")
                return True
        return False

    async def _handle_prompt_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if not data.startswith("prompt:"):
            return False
        token = data.split(":", 1)[1]
        await self._activate_prompt(message, base_ctx, token)
        return True

    async def _handle_wizard_callback_data(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if not data.startswith("wizard:"):
            return False
        await self._handle_wizard_callback(message, base_ctx, data)
        return True

    async def _handle_panel_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if not data.startswith("panel:"):
            return False
        panel = data.split(":", 1)[1]
        mapping = {
            "projects": lambda: self._send_projects_panel(message, base_ctx),
            "schedule": lambda: self._send_schedule_panel(message, base_ctx),
            "approval": lambda: self._send_approval_panel(message, base_ctx),
            "more": lambda: self._send_more_panel(message),
            "service": lambda: self._send_service_panel(message),
            "autopilot": lambda: self._send_autopilot_panel(message, base_ctx),
            "model": lambda: self._send_model_panel(message, base_ctx),
        }
        action = mapping.get(panel)
        if action is None:
            return False
        await action()
        return True

    async def _handle_model_callback(self, message, base_ctx: CommandContext, data: str) -> bool:  # noqa: ANN001
        if data == "model:reset":
            await self._execute_command(message, base_ctx, "/model reset")
            return True
        if not data.startswith("model:set:"):
            return False
        model = data.split(":", 2)[2]
        await self._execute_command(message, base_ctx, f"/model set {model}")
        return True

    def _resolve_callback_command(self, token: str) -> str | None:
        return self._CALLBACK_COMMANDS.get(token)

    async def _activate_prompt(self, message, ctx: CommandContext, token: str) -> None:  # noqa: ANN001
        chat_id = ctx.telegram_chat_id
        if token == "clear":
            self._clear_input_modes(chat_id)
            await self._send_text(
                message,
                telegram_messages.prompt_mode_cleared(),
                context="clearing prompt mode",
                reply_markup=self._main_menu_markup(),
            )
            return

        if token == "approve":
            await self._start_wizard(message, ctx=ctx, token="approve_note")
            return
        if token == "reject":
            await self._start_wizard(message, ctx=ctx, token="reject_note")
            return
        if token.startswith("approve:"):
            approval_id = token.split(":", 1)[1]
            await self._start_approval_note_wizard(message, ctx=ctx, action="approve", approval_id_text=approval_id)
            return
        if token.startswith("reject:"):
            approval_id = token.split(":", 1)[1]
            await self._start_approval_note_wizard(message, ctx=ctx, action="reject", approval_id_text=approval_id)
            return

        if token in self._WIZARD_TOKENS:
            await self._start_wizard(message, ctx=ctx, token=token)
            return

        command = self._PROMPT_COMMANDS.get(token)
        if command is None:
            await self._send_text(
                message,
                telegram_messages.unknown_prompt_mode(),
                context="sending prompt mode error",
                reply_markup=self._main_menu_markup(),
            )
            return

        self.router.tasks.clear_chat_wizard_state(chat_id=chat_id)
        user = self.router.tasks.ensure_user(ctx)
        self._pending_command_by_chat[chat_id] = command
        self.router.tasks.set_chat_pending_command(chat_id=chat_id, user_id=user.id, command=command)
        hint = self._PROMPT_HINTS.get(token, f"请输入参数。下一条消息将按 {command} 执行。")
        await self._send_text(
            message,
            telegram_messages.prompt_mode_hint(hint),
            context="sending prompt mode hint",
            reply_markup=self._main_menu_markup(),
        )

    async def _start_approval_note_wizard(
        self,
        message,
        *,
        ctx: CommandContext,
        action: str,
        approval_id_text: str,
    ) -> None:  # noqa: ANN001
        if not approval_id_text.isdigit():
            await self._send_text(
                message,
                "审批参数无效，请重新打开审批卡片。",
                context="sending invalid approval wizard request",
                reply_markup=self._main_menu_markup(),
            )
            return
        pending = self._get_pending_approval(ctx)
        if pending is None or int(pending.approval_id) != int(approval_id_text):
            await self._send_text(
                message,
                f"审批 #{approval_id_text} 不存在、已处理或不属于当前项目。",
                context="sending missing approval wizard request",
                reply_markup=self._main_menu_markup(),
            )
            return
        user = self.router.tasks.ensure_user(ctx)
        self._pending_command_by_chat.pop(ctx.telegram_chat_id, None)
        self.router.tasks.clear_chat_pending_command(chat_id=ctx.telegram_chat_id)
        state = {
            "kind": "approve_note" if action == "approve" else "reject_note",
            "step": "note",
            "data": {
                "approval_id": pending.approval_id,
                "task_summary": pending.task_summary or "待审批任务",
            },
        }
        self.router.tasks.set_chat_wizard_state(
            chat_id=ctx.telegram_chat_id,
            user_id=user.id,
            state=state,
        )
        await self._send_wizard_prompt(
            message,
            state,
            context="sending wizard prompt",
        )

    async def _clear_inline_keyboard(self, message) -> None:  # noqa: ANN001
        if not hasattr(message, "edit_reply_markup"):
            return
        try:
            await message.edit_reply_markup(reply_markup=None)
        except Exception:
            logger.debug("Failed to clear inline keyboard.", exc_info=True)

    async def _execute_command(self, message, base_ctx: CommandContext, text: str) -> None:  # noqa: ANN001
        command = text.strip().split(" ", 1)[0].split("@", 1)[0]
        self._clear_input_modes(base_ctx.telegram_chat_id)
        ack_text = self.progress.ack_text(command)
        if ack_text:
            await self._send_text(
                message,
                ack_text,
                context="sending ack",
                reply_markup=self._main_menu_markup(),
            )
        command_context = CommandContext(
            telegram_user_id=base_ctx.telegram_user_id,
            telegram_chat_id=base_ctx.telegram_chat_id,
            telegram_message_id=base_ctx.telegram_message_id,
            text=text,
            telegram_username=base_ctx.telegram_username,
            telegram_display_name=base_ctx.telegram_display_name,
        )
        result = await self._dispatch_router_command(
            message,
            command_context,
            command=command,
        )
        if await self._maybe_start_wizard_from_result(message, command_context, result):
            return
        await self._send_command_result(message, command, command_context, result)

    async def _dispatch_router_command(
        self,
        message,  # noqa: ANN001
        command_context: CommandContext,
        *,
        command: str,
    ):
        if not self._should_send_typing(command):
            return self.router.handle(command_context)

        typing_context = f"executing {command}"
        typing_task: asyncio.Task[None] | None = None
        progress_task: asyncio.Task[None] | None = None
        progress_context: str | None = None
        current_task_card_task: asyncio.Task[None] | None = None
        stream_state: _StreamProgressState | None = None
        try:
            await self.sink.send_typing(message, context=typing_context)
            if self._should_refresh_current_task_card(command):
                current_task_card_task = asyncio.create_task(
                    self._track_current_task_card(message, command_context)
                )
            if self._should_stream_progress(command_context.telegram_chat_id, command):
                progress_context = self._stream_progress_context(command_context, command)
                stream_state = _StreamProgressState(
                    command=command,
                    phases=self.progress.phases(command),
                    phase_delay_seconds=max(
                        0.5,
                        float(getattr(self.config, "telegram_stream_phase_delay_seconds", 1.5)),
                    ),
                )
                command_context.progress_callback = self._make_progress_callback(stream_state)
                progress_task = asyncio.create_task(
                    self._stream_progress_updates(
                        message,
                        state=stream_state,
                        progress_context=progress_context,
                    )
                )
            typing_task = asyncio.create_task(
                self._typing_heartbeat(
                    message,
                    context=typing_context,
                )
            )
            return await asyncio.to_thread(self.router.handle, command_context)
        finally:
            if current_task_card_task is not None:
                current_task_card_task.cancel()
                with suppress(asyncio.CancelledError):
                    await current_task_card_task
                await self._refresh_current_task_card(message, command_context, force=True)
            if progress_task is not None:
                progress_task.cancel()
                with suppress(asyncio.CancelledError):
                    await progress_task
            if progress_context is not None:
                await self.sink.delete_recent_message_by_context(
                    message,
                    context=progress_context,
                    max_age_seconds=float(getattr(self.config, "telegram_stream_edit_window_seconds", 3600.0)),
                )
            if typing_task is not None:
                typing_task.cancel()
                with suppress(asyncio.CancelledError):
                    await typing_task

    async def _typing_heartbeat(
        self,
        message,  # noqa: ANN001
        *,
        context: str,
    ) -> None:
        interval_seconds = float(getattr(self.config, "telegram_typing_heartbeat_seconds", 4.0))
        interval_seconds = max(1.0, interval_seconds)
        while True:
            await asyncio.sleep(interval_seconds)
            await self.sink.send_typing(message, context=context)

    def _should_stream_progress(self, chat_id: str, command: str) -> bool:
        if not self._should_send_typing(command):
            return False
        return self._chat_uses_stream_mode(chat_id)

    def _chat_uses_stream_mode(self, chat_id: str) -> bool:
        if not hasattr(self.router, "tasks"):
            return False
        return self.router.tasks.get_chat_ui_mode(chat_id=chat_id) == "stream"

    async def _stream_progress_updates(
        self,
        message,  # noqa: ANN001
        *,
        state: _StreamProgressState,
        progress_context: str,
    ) -> None:
        heartbeat_delay = max(2.0, float(getattr(self.config, "telegram_stream_heartbeat_seconds", 5.0)))
        poll_interval = min(0.8, max(0.3, state.phase_delay_seconds / 2))
        last_render_key: tuple[int, int, int] | None = None
        total = max(1, len(state.phases))
        while True:
            elapsed_seconds = max(1, int(time.monotonic() - state.started_at))
            outputs, output_version = state.snapshot()
            phase_index = min(total - 1, int((time.monotonic() - state.started_at) // state.phase_delay_seconds))
            phase = state.phases[phase_index] if state.phases else "运行中"
            heartbeat_bucket = max(1, int(elapsed_seconds // heartbeat_delay))
            render_key = (phase_index, output_version, heartbeat_bucket)
            if render_key != last_render_key:
                await self._send_stream_progress_text(
                    message,
                    text=self.progress.stream_status_text(
                        state.command,
                        phase=phase,
                        index=phase_index + 1,
                        total=total,
                        elapsed_seconds=elapsed_seconds,
                        output_lines=outputs,
                    ),
                    progress_context=progress_context,
                )
                last_render_key = render_key
            await asyncio.sleep(poll_interval)

    def _make_progress_callback(self, state: _StreamProgressState):
        def _callback(channel: str, text: str) -> None:
            _ = channel
            state.add_output(text)

        return _callback

    def _stream_progress_context(self, command_context: CommandContext, command: str) -> str:
        token = command_context.telegram_message_id or str(time.time_ns())
        return f"stream progress {command_context.telegram_chat_id}:{command}:{token}"

    def _should_refresh_current_task_card(self, command: str) -> bool:
        return command in self._CURRENT_TASK_CARD_COMMANDS

    async def _track_current_task_card(self, message, command_context: CommandContext) -> None:  # noqa: ANN001
        last_signature: tuple[object, ...] | None = None
        last_home_signature: tuple[object, ...] | None = None
        while True:
            last_signature = await self._refresh_current_task_card(
                message,
                command_context,
                previous_signature=last_signature,
            )
            user = self.router.tasks.ensure_user(command_context)
            last_home_signature = await self._refresh_home_dashboard(
                message,
                user_id=user.id,
                chat_id=command_context.telegram_chat_id,
                previous_signature=last_home_signature,
            )
            await asyncio.sleep(1.0)

    async def _refresh_current_task_card(
        self,
        message,  # noqa: ANN001
        command_context: CommandContext,
        *,
        previous_signature: tuple[object, ...] | None = None,
        force: bool = False,
    ) -> tuple[object, ...] | None:
        tasks = getattr(self.router, "tasks", None)
        if tasks is None:
            return previous_signature
        user = tasks.ensure_user(command_context)
        active_key = tasks.get_active_project_key(user.id, command_context.telegram_chat_id)
        if not active_key:
            return previous_signature
        try:
            project_id = tasks.get_project_id(active_key)
        except KeyError:
            return previous_signature
        task = tasks.get_latest_active_task(project_id) or tasks.get_latest_task(project_id)
        signature = (
            active_key,
            getattr(task, "id", None),
            getattr(task, "status", None),
            getattr(task, "latest_summary", None),
            getattr(task, "codex_session_id", None),
        )
        if not force and signature == previous_signature:
            return signature
        await self.sink.send(
            message,
            TelegramSendSpec(
                text=format_current_task(project_key=active_key, task=task),
                context="sending current task card",
                reply_markup=self.views.current_task_markup(task),
                edit_context="sending current task card",
                edit_window_seconds=float(
                    getattr(self.config, "telegram_current_task_edit_window_seconds", 300.0)
                ),
            ),
        )
        return signature

    async def _refresh_home_dashboard(
        self,
        target,  # noqa: ANN001
        *,
        user_id: int,
        chat_id: str,
        previous_signature: tuple[object, ...] | None = None,
        force: bool = False,
    ) -> tuple[object, ...] | None:
        tasks = getattr(self.router, "tasks", None)
        if tasks is None:
            return previous_signature
        snapshot = tasks.get_status_snapshot(user_id, chat_id)
        recent_keys = tasks.list_recent_project_keys(user_id=user_id)
        current_model = tasks.get_chat_codex_model(chat_id=chat_id)
        signature = (
            snapshot.active_project_key,
            getattr(snapshot.active_task, "id", None),
            getattr(snapshot.active_task, "status", None),
            snapshot.pending_approval,
            snapshot.pending_approval_id,
            snapshot.last_codex_session_id,
            snapshot.next_schedule_id,
            snapshot.next_schedule_hhmm,
            snapshot.next_step,
            snapshot.most_recent_task_summary,
            current_model,
            tuple(recent_keys[:4]),
        )
        if previous_signature is None:
            previous_signature = self._home_dashboard_signatures.get(chat_id)
        if not force and signature == previous_signature:
            return signature
        context = f"live home dashboard {chat_id}"
        await self.sink.send(
            target,
            TelegramSendSpec(
                text=format_home(
                    snapshot=snapshot,
                    current_model=current_model,
                    recent_project_keys=recent_keys,
                ),
                context=context,
                reply_markup=self.views.home_markup(snapshot=snapshot, recent_projects=recent_keys),
                edit_context=context,
                edit_window_seconds=float(
                    getattr(self.config, "telegram_home_edit_window_seconds", 300.0)
                ),
            ),
        )
        self._home_dashboard_signatures[chat_id] = signature
        return signature

    async def _send_stream_progress_text(
        self,
        message,  # noqa: ANN001
        *,
        text: str,
        progress_context: str,
    ) -> bool:
        if await self.sink.send_message_draft(
            message,
            draft_id=self.sink.build_draft_id(context=progress_context),
            text=text,
            context=progress_context,
        ):
            return True
        return await self.sink.send(
            message,
            TelegramSendSpec(
                text=text,
                context=progress_context,
                edit_context=progress_context,
                edit_window_seconds=float(getattr(self.config, "telegram_stream_edit_window_seconds", 3600.0)),
            ),
        )

    def _on_autopilot_raw_output(self, run_id: int) -> None:
        if self._app is None or self._app_loop is None:
            return
        run = getattr(self.router, "autopilot", None)
        if run is None:
            return
        record = run.get_run(run_id=run_id)
        if record is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._schedule_autopilot_stream_update(run_id=run_id, chat_id=record.chat_id),
                self._app_loop,
            )
        except RuntimeError:
            return

    async def _schedule_autopilot_stream_update(self, *, run_id: int, chat_id: str) -> None:
        existing = self._autopilot_stream_tasks.get(run_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._flush_autopilot_stream_update(run_id=run_id, chat_id=chat_id))
        self._autopilot_stream_tasks[run_id] = task

    async def _flush_autopilot_stream_update(self, *, run_id: int, chat_id: str) -> None:
        try:
            await asyncio.sleep(0.8)
            if not self._chat_uses_stream_mode(chat_id):
                return
            autopilot = getattr(self.router, "autopilot", None)
            if autopilot is None or self._app is None:
                return
            run = autopilot.get_run(run_id=run_id)
            if run is None:
                return
            runtime = autopilot.get_runtime_snapshot(run_id=run_id)
            events = autopilot.list_events(run_id=run_id, limit=10)
            raw_output_lines = autopilot.get_recent_output(run_id=run_id, limit=10)
            if not raw_output_lines:
                return
            target = _ChatTarget(chat_id=chat_id, bot=self._app.bot)
            status_context = f"autopilot live {chat_id}:{run_id}"
            await self.sink.send(
                target,
                TelegramSendSpec(
                    text=format_autopilot_status(
                        run=run,
                        events=events,
                        runtime=runtime,
                        raw_output_lines=raw_output_lines[-6:],
                    ),
                    context=status_context,
                    reply_markup=self.views.autopilot_run_markup(run),
                    edit_context=status_context,
                    edit_window_seconds=float(
                        getattr(self.config, "telegram_autopilot_edit_window_seconds", 300.0)
                    ),
                ),
            )
            await self._refresh_home_dashboard(
                target,
                user_id=run.created_by_user_id,
                chat_id=chat_id,
            )
            context = f"autopilot raw {chat_id}:{run_id}"
            lines = [
                "Autopilot 原始流",
                f"Run: #{run.id}",
                f"状态: {run.status}",
            ]
            if runtime is not None and runtime.actor:
                lines.append(f"当前执行者: {runtime.actor}")
            lines.extend(raw_output_lines[-8:])
            text = "\n".join(lines)
            if await self.sink.send_message_draft(
                target,
                draft_id=self.sink.build_draft_id(context=context),
                text=text,
                context=context,
            ):
                return
            await self.sink.send(
                target,
                TelegramSendSpec(
                    text=text,
                    context=context,
                    edit_context=context,
                    edit_window_seconds=float(
                        getattr(self.config, "telegram_autopilot_edit_window_seconds", 300.0)
                    ),
                ),
            )
        finally:
            self._autopilot_stream_tasks.pop(run_id, None)

    async def _send_projects_panel(self, message, ctx: CommandContext) -> None:  # noqa: ANN001
        user = self.router.tasks.ensure_user(ctx)
        active_key = self.router.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        recent_keys = self.router.tasks.list_recent_project_keys(user_id=user.id)
        keys = self.router.projects.list_keys()
        ordered_keys: list[str] = []
        for key in [active_key, *recent_keys, *keys]:
            if key and key not in ordered_keys and key in keys:
                ordered_keys.append(key)
        spec = self.views.projects_panel(
            active_key=active_key,
            recent_keys=recent_keys,
            ordered_keys=ordered_keys,
        )
        await self._send_view_spec(
            message,
            spec,
            context="sending projects panel",
            edit_context="sending projects panel",
            edit_window_seconds=float(getattr(self.config, "telegram_projects_edit_window_seconds", 300.0)),
        )

    async def _send_schedule_panel(self, message, ctx: CommandContext) -> None:  # noqa: ANN001
        await self._execute_command(message, ctx, "/schedule-list")

    async def _send_approval_panel(self, message, ctx: CommandContext | None = None) -> None:  # noqa: ANN001
        approval_id = self._get_pending_approval_id(ctx) if ctx is not None else None
        spec = self.views.approval_panel(approval_id=approval_id)
        await self._send_view_spec(
            message,
            spec,
            context="sending approval panel",
            edit_context="sending approval panel",
            edit_window_seconds=float(getattr(self.config, "telegram_approval_edit_window_seconds", 300.0)),
        )

    async def _send_more_panel(self, message) -> None:  # noqa: ANN001
        spec = self.views.more_panel()
        await self._send_view_spec(
            message,
            spec,
            context="sending more panel",
            edit_context="sending more panel",
            edit_window_seconds=float(getattr(self.config, "telegram_more_edit_window_seconds", 300.0)),
        )

    async def _send_service_panel(self, message) -> None:  # noqa: ANN001
        spec = self.views.service_panel()
        await self._send_view_spec(
            message,
            spec,
            context="sending service panel",
            edit_context="sending service panel",
            edit_window_seconds=float(getattr(self.config, "telegram_service_edit_window_seconds", 300.0)),
        )

    async def _send_autopilot_panel(self, message, ctx: CommandContext) -> None:  # noqa: ANN001
        run = None
        runs: list[AutopilotRunRecord] = []
        if getattr(self.router, "autopilot", None) is not None:
            user = self.router.tasks.ensure_user(ctx)
            active_key = self.router.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
            if active_key:
                try:
                    project_id = self.router.tasks.get_project_id(active_key)
                except KeyError:
                    project_id = None
                if project_id is not None:
                    runs = self.router.autopilot.list_runs_for_project(project_id=project_id, limit=6)
                    run = runs[0] if runs else None
        spec = self.views.autopilot_panel(run, recent_runs=runs)
        await self._send_view_spec(
            message,
            spec,
            context="sending autopilot panel",
            edit_context="sending autopilot panel",
            edit_window_seconds=float(getattr(self.config, "telegram_autopilot_edit_window_seconds", 300.0)),
        )

    async def _send_model_panel(self, message, ctx: CommandContext) -> None:  # noqa: ANN001
        current_model = self.router.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        spec = self.views.model_panel(
            current_model=current_model,
            model_choices=list(getattr(self.config, "codex_model_choices", ()) or ()),
        )
        await self._send_view_spec(
            message,
            spec,
            context="sending model panel",
            edit_context="sending model panel",
            edit_window_seconds=float(getattr(self.config, "telegram_model_edit_window_seconds", 300.0)),
        )

    def _should_send_typing(self, command: str) -> bool:
        return command in self._TYPING_COMMANDS

    def _get_pending_approval_id(self, ctx: CommandContext) -> int | None:
        pending = self._get_pending_approval(ctx)
        return pending.approval_id if pending is not None else None

    def _get_pending_approval(self, ctx: CommandContext):
        user = self.router.tasks.ensure_user(ctx)
        active_key = self.router.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if not active_key:
            return None
        try:
            project_id = self.router.tasks.get_project_id(active_key)
        except KeyError:
            return None
        return self.router.tasks.get_pending_approval(project_id)

    def _is_active_approval_callback(self, ctx: CommandContext, message, approval_id_text: str) -> bool:  # noqa: ANN001
        if not approval_id_text.isdigit():
            return False
        pending = self._get_pending_approval(ctx)
        if pending is None or int(pending.approval_id) != int(approval_id_text):
            return False
        message_id = getattr(message, "message_id", None)
        if message_id is None or not hasattr(self.router.tasks, "get_recent_outbound_message_id_by_context"):
            return True
        recent_status = self.router.tasks.get_recent_outbound_message_id_by_context(
            chat_id=ctx.telegram_chat_id,
            context="sending status result",
            max_age_seconds=float(getattr(self.config, "telegram_status_edit_window_seconds", 300.0)),
        )
        recent_approval = self.router.tasks.get_recent_outbound_message_id_by_context(
            chat_id=ctx.telegram_chat_id,
            context="sending approval panel",
            max_age_seconds=3600.0,
        )
        return str(message_id) in {value for value in {recent_status, recent_approval} if value}

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

    async def _safe_reply_text(
        self,
        message,
        text: str,
        *,
        context: str,
        reply_markup=None,  # noqa: ANN001
    ) -> bool:
        """Compatibility wrapper retained for tests and existing call sites."""

        return await self.sink.send(
            message,
            TelegramSendSpec(text=text, context=context, reply_markup=reply_markup),
        )

    async def _send_text(
        self,
        message,
        text: str,
        *,
        context: str,
        ctx: CommandContext | None = None,
        reply_markup=None,  # noqa: ANN001
    ) -> bool:
        return await self._send_view_spec(
            message,
            TelegramReplySpec(
                text=text,
                reply_markup=reply_markup if reply_markup is not None else self._contextual_main_menu_markup(ctx),
            ),
            context=context,
        )

    async def _send_view_spec(
        self,
        message,
        spec: TelegramReplySpec,
        *,
        context: str,
        command: str | None = None,
        edit_context: str | None = None,
        edit_window_seconds: float | None = None,
    ) -> bool:  # noqa: ANN001
        send_spec = TelegramSendSpec(
            text=spec.text,
            context=context,
            reply_markup=spec.reply_markup,
        )
        if command == "/status":
            send_spec.context = "sending status result"
            send_spec.edit_context = "sending status result"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_status_edit_window_seconds", 300.0)
            )
        elif command == "/projects":
            send_spec.context = "sending projects panel"
            send_spec.edit_context = "sending projects panel"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_projects_edit_window_seconds", 300.0)
            )
        elif command == "/schedule-list":
            send_spec.context = "sending schedule panel"
            send_spec.edit_context = "sending schedule panel"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_schedule_edit_window_seconds", 300.0)
            )
        elif command == "/tasks":
            send_spec.context = "sending tasks panel"
            send_spec.edit_context = "sending tasks panel"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_tasks_edit_window_seconds", 300.0)
            )
        elif command == "/task-current":
            send_spec.context = "sending current task card"
            send_spec.edit_context = "sending current task card"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_current_task_edit_window_seconds", 300.0)
            )
        elif command in {
            "/autopilot",
            "/autopilot-status",
            "/autopilot-context",
            "/autopilot-takeover",
            "/autopilot-step",
            "/autopilot-pause",
            "/autopilot-resume",
            "/autopilot-stop",
        }:
            send_spec.context = "sending autopilot card"
            send_spec.edit_context = "sending autopilot card"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_autopilot_edit_window_seconds", 300.0)
            )
        elif command == "/memory":
            send_spec.context = "sending memory panel"
            send_spec.edit_context = "sending memory panel"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_memory_edit_window_seconds", 300.0)
            )
        elif command == "/sessions":
            send_spec.context = "sending sessions panel"
            send_spec.edit_context = "sending sessions panel"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_sessions_edit_window_seconds", 300.0)
            )
        elif command == "/session":
            send_spec.context = "sending session detail"
            send_spec.edit_context = "sending session detail"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_session_detail_edit_window_seconds", 300.0)
            )
        elif command in {"/health", "/version", "/update-check", "/logs", "/logs-clear"}:
            send_spec.context = "sending service panel"
            send_spec.edit_context = "sending service panel"
            send_spec.edit_window_seconds = float(
                getattr(self.config, "telegram_service_edit_window_seconds", 300.0)
            )
        elif edit_context is not None:
            send_spec.edit_context = edit_context
            send_spec.edit_window_seconds = edit_window_seconds
        return await self.sink.send(
            message,
            send_spec,
        )

    async def _send_command_result(
        self,
        message,
        command: str,
        ctx: CommandContext,
        result: CommandResult,
        *,
        context: str = "sending command result",
    ) -> bool:  # noqa: ANN001
        if await self._send_local_file_result(message, result):
            return True
        reply_markup = self._reply_markup_for_result(command, ctx, result)
        if reply_markup is None:
            reply_markup = self._contextual_main_menu_markup(ctx)
        parts = self._split_long_text(result.reply_text)
        if len(parts) == 1:
            return await self._send_view_spec(
                message,
                TelegramReplySpec(
                    text=result.reply_text,
                    reply_markup=reply_markup,
                ),
                context=context,
                command=command,
            )

        first_ok = await self._send_view_spec(
            message,
            TelegramReplySpec(
                text=parts[0],
                reply_markup=reply_markup,
            ),
            context=context,
            command=command,
        )
        if not first_ok:
            return False
        for index, part in enumerate(parts[1:], start=2):
            ok = await self._send_text(
                message,
                f"续 {index}/{len(parts)}\n{part}",
                context=f"{context} continuation",
                reply_markup=None,
            )
            if not ok:
                return False
        return True

    async def _send_local_file_result(
        self,
        message,
        result: CommandResult,
    ) -> bool:  # noqa: ANN001
        metadata = result.metadata or {}
        send_local_file = metadata.get("send_local_file")
        if not isinstance(send_local_file, dict):
            return False
        path_value = send_local_file.get("path")
        if not isinstance(path_value, str) or not path_value:
            return False

        file_path = Path(path_value)
        caption = result.reply_text.strip()
        if len(caption) > 900:
            caption = caption[:897] + "..."
        try:
            with file_path.open("rb") as handle:
                if hasattr(message, "reply_document"):
                    await message.reply_document(document=handle, filename=file_path.name, caption=caption)
                    return True
                chat_id = getattr(message, "chat_id", None)
                get_bot = getattr(message, "get_bot", None)
                if chat_id is not None and callable(get_bot):
                    bot = get_bot()
                    await bot.send_document(chat_id=chat_id, document=handle, filename=file_path.name, caption=caption)
                    return True
                raise RuntimeError("当前消息对象不支持文件发送。")
        except (OSError, BadRequest, NetworkError, TelegramError, RuntimeError) as exc:
            await self._send_text(
                message,
                telegram_messages.local_file_send_failed(str(exc)),
                context="sending local file failure",
                reply_markup=self._main_menu_markup(),
            )
            return True

    def _split_long_text(self, text: str) -> list[str]:
        limit = int(getattr(self.config, "max_telegram_message_length", 3500))
        if len(text) <= limit:
            return [text]

        parts: list[str] = []
        remaining = text
        soft_limit = max(80, limit - 32)
        while len(remaining) > limit:
            split_at = remaining.rfind("\n", 0, soft_limit)
            if split_at < soft_limit // 2:
                split_at = soft_limit
            parts.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
        if remaining:
            parts.append(remaining)
        return parts

    def _main_menu_markup(self) -> ReplyKeyboardMarkup:
        return self.views.main_menu_markup()

    def _contextual_main_menu_markup(self, ctx: CommandContext | None) -> ReplyKeyboardMarkup:
        if ctx is None or not hasattr(self.router, "tasks"):
            return self._main_menu_markup()
        try:
            user = self.router.tasks.ensure_user(ctx)
            snapshot = self.router.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
        except Exception:
            return self._main_menu_markup()
        return self.views.main_menu_markup(snapshot=snapshot)

    def _map_menu_to_command(self, text: str) -> str | None:
        mapping = {
            self._MENU_STATUS: "/status",
            self._MENU_HELP: "/help",
            self._MENU_PROJECTS: "__projects__",
            self._MENU_ASK: "__ask__",
            self._MENU_DO: "__do__",
            self._MENU_RESUME: "__resume__",
            self._MENU_DIFF: "__diff__",
            self._MENU_SCHEDULE: "__schedule__",
            self._MENU_MORE: "__more__",
            self._MENU_CURRENT_TASK: "/task-current",
            self._MENU_CANCEL_TASK: "/cancel",
            self._MENU_APPROVE_ACTION: "/approve",
            self._MENU_REJECT_ACTION: "/reject",
        }
        return mapping.get(text)

    def _clear_input_modes(self, chat_id: str) -> None:
        self._pending_command_by_chat.pop(chat_id, None)
        if hasattr(self.router, "tasks"):
            self.router.tasks.clear_chat_pending_command(chat_id=chat_id)
            self.router.tasks.clear_chat_wizard_state(chat_id=chat_id)

    def _get_wizard_state(self, chat_id: str) -> dict | None:
        if not hasattr(self.router, "tasks"):
            return None
        return self.router.tasks.get_chat_wizard_state(chat_id=chat_id)

    async def _handle_wizard_callback(
        self,
        message,
        ctx: CommandContext,
        data: str,
    ) -> None:  # noqa: ANN001
        state = self._get_wizard_state(ctx.telegram_chat_id)
        if state is None:
            await self._clear_inline_keyboard(message)
            await self._send_text(
                message,
                telegram_messages.wizard_missing_state(),
                context="sending missing wizard state",
                reply_markup=self._main_menu_markup(),
            )
            return

        if data == "wizard:cancel":
            self.router.tasks.clear_chat_wizard_state(chat_id=ctx.telegram_chat_id)
            await self._clear_inline_keyboard(message)
            await self._send_text(
                message,
                telegram_messages.wizard_cancelled(),
                context="sending wizard cancel",
                reply_markup=self._main_menu_markup(),
            )
            return
        if data == "wizard:confirm":
            await self._handle_wizard_input(message, ctx, state, "确认")
            await self._clear_inline_keyboard(message)
            return
        if data == "wizard:default":
            await self._handle_wizard_input(message, ctx, state, "默认")
            await self._clear_inline_keyboard(message)
            return
        if data == "wizard:skip":
            await self._handle_wizard_input(message, ctx, state, "跳过")
            await self._clear_inline_keyboard(message)
            return
        if data.startswith("wizard:mode:"):
            await self._handle_wizard_input(message, ctx, state, data.rsplit(":", 1)[1])
            await self._clear_inline_keyboard(message)
            return
        if data.startswith("wizard:template:"):
            await self._handle_wizard_input(message, ctx, state, data.split(":", 2)[2])
            await self._clear_inline_keyboard(message)
            return
        if data.startswith("wizard:preset:"):
            await self._handle_wizard_input(message, ctx, state, data.split(":", 2)[2])
            await self._clear_inline_keyboard(message)
            return

        await self._clear_inline_keyboard(message)
        await self._send_text(
            message,
            telegram_messages.wizard_unknown_button(),
            context="sending unknown wizard callback",
            reply_markup=self._main_menu_markup(),
        )

    async def _maybe_start_wizard_from_result(
        self,
        message,
        ctx: CommandContext,
        result: CommandResult,
    ) -> bool:  # noqa: ANN001
        metadata = result.metadata or {}
        token = metadata.get("wizard")
        if token not in self._WIZARD_TOKENS:
            return False
        await self._start_wizard(message, ctx=ctx, token=str(token), preface=result.reply_text)
        return True

    async def _start_wizard(
        self,
        message,
        *,
        ctx: CommandContext,
        token: str,
        preface: str | None = None,
    ) -> None:  # noqa: ANN001
        user = self.router.tasks.ensure_user(ctx)
        if token == "schedule_add":
            active_key = self.router.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
            if not active_key:
                await self._send_text(
                    message,
                    telegram_messages.wizard_project_requirement(),
                    context="sending wizard project requirement",
                    reply_markup=self._project_shortcuts_markup(
                        self.router.tasks.list_recent_project_keys(user_id=user.id)
                    ),
                )
                return
        pending = None
        if token in {"approve_note", "reject_note"}:
            pending = self._get_pending_approval(ctx)
            if pending is None:
                await self._send_text(
                    message,
                    "当前没有待审批任务。",
                    context="sending approval wizard requirement",
                    reply_markup=self._main_menu_markup(),
                )
                return

        self._pending_command_by_chat.pop(ctx.telegram_chat_id, None)
        self.router.tasks.clear_chat_pending_command(chat_id=ctx.telegram_chat_id)
        if token == "project_add":
            state = {"kind": token, "step": "key", "data": {}}
        elif token == "schedule_add":
            state = {"kind": token, "step": "time", "data": {}}
        elif token in {"approve_note", "reject_note"} and pending is not None:
            state = {
                "kind": token,
                "step": "note",
                "data": {
                    "approval_id": pending.approval_id,
                    "task_summary": pending.task_summary or "待审批任务",
                },
            }
        else:
            state = {"kind": token, "step": "key", "data": {}}
        self.router.tasks.set_chat_wizard_state(
            chat_id=ctx.telegram_chat_id,
            user_id=user.id,
            state=state,
        )
        await self._send_wizard_prompt(
            message,
            state,
            preface=preface,
            context="sending wizard prompt",
        )

    def _wizard_prompt(self, state: dict) -> str:
        kind = str(state.get("kind") or "")
        step = str(state.get("step") or "")
        data = state.get("data") or {}

        if kind == "project_add":
            return telegram_messages.project_add_prompt(
                step=step,
                data=data,
                default_root=getattr(self.router.projects, "default_project_root", None),
                templates=self._project_template_presets(),
            )

        if kind == "schedule_add":
            return telegram_messages.schedule_add_prompt(step=step, data=data)
        if kind == "approve_note":
            return telegram_messages.approval_note_prompt(step=step, data=data, action="approve")
        if kind == "reject_note":
            return telegram_messages.approval_note_prompt(step=step, data=data, action="reject")

        return "未知向导。"

    def _wizard_markup(self, state: dict) -> InlineKeyboardMarkup:
        kind = str(state.get("kind") or "")
        step = str(state.get("step") or "")

        if kind == "project_add":
            if step == "path" and getattr(self.router.projects, "default_project_root", None) is not None:
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="默认目录", callback_data="wizard:default"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )
            if step == "template":
                templates = self._project_template_presets()
                rows: list[list[InlineKeyboardButton]] = []
                row: list[InlineKeyboardButton] = []
                for preset in templates[:6]:
                    row.append(
                        InlineKeyboardButton(
                            text=preset.key,
                            callback_data=f"wizard:template:{preset.key}",
                        )
                    )
                    if len(row) == 2:
                        rows.append(row)
                        row = []
                if row:
                    rows.append(row)
                rows.append(
                    [
                        InlineKeyboardButton(text="跳过模板", callback_data="wizard:skip"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]
                )
                return InlineKeyboardMarkup(rows)
            if step == "mode":
                return InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(text="normal", callback_data="wizard:mode:normal"),
                            InlineKeyboardButton(text="autopilot", callback_data="wizard:mode:autopilot"),
                        ],
                        [InlineKeyboardButton(text="取消", callback_data="wizard:cancel")],
                    ]
                )
            if step == "name":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="跳过命名", callback_data="wizard:skip"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )
            if step == "confirm":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="确认创建", callback_data="wizard:confirm"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )

        if kind == "schedule_add":
            if step == "mode":
                return InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(text="ask", callback_data="wizard:mode:ask"),
                            InlineKeyboardButton(text="do", callback_data="wizard:mode:do"),
                        ],
                        [InlineKeyboardButton(text="取消", callback_data="wizard:cancel")],
                    ]
                )
            if step == "confirm":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="确认创建", callback_data="wizard:confirm"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )

        if kind == "approve_note":
            if step == "note":
                return InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(text="无备注", callback_data="wizard:skip"),
                            InlineKeyboardButton(text="继续执行", callback_data="wizard:preset:继续执行"),
                        ],
                        [InlineKeyboardButton(text="取消", callback_data="wizard:cancel")],
                    ]
                )
            if step == "confirm":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="确认批准", callback_data="wizard:confirm"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )

        if kind == "reject_note":
            if step == "note":
                return InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(text="默认原因", callback_data="wizard:default"),
                            InlineKeyboardButton(text="风险太高", callback_data="wizard:preset:风险太高"),
                        ],
                        [
                            InlineKeyboardButton(text="暂不执行", callback_data="wizard:preset:暂不执行"),
                            InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                        ],
                    ]
                )
            if step == "confirm":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="确认拒绝", callback_data="wizard:confirm"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )

        return InlineKeyboardMarkup([[InlineKeyboardButton(text="取消", callback_data="wizard:cancel")]])

    async def _send_wizard_prompt(
        self,
        message,
        state: dict,
        *,
        preface: str | None = None,
        context: str,
    ) -> None:  # noqa: ANN001
        prompt = self._wizard_prompt(state)
        if preface:
            prompt = f"{preface}\n\n{prompt}"
        await self._send_view_spec(
            message,
            TelegramReplySpec(text=prompt, reply_markup=self._wizard_markup(state)),
            context=context,
        )

    async def _handle_wizard_input(
        self,
        message,
        ctx: CommandContext,
        state: dict,
        raw_text: str,
    ) -> bool:  # noqa: ANN001
        text = self._normalize_wizard_input(raw_text)
        lowered = text.lower()
        if lowered in {item.lower() for item in self._WIZARD_CANCEL_TOKENS}:
            self.router.tasks.clear_chat_wizard_state(chat_id=ctx.telegram_chat_id)
            await self._send_text(
                message,
                telegram_messages.wizard_cancelled(),
                context="sending wizard cancel",
                reply_markup=self._main_menu_markup(),
            )
            return True

        if str(state.get("step")) == "confirm":
            if lowered in {item.lower() for item in self._WIZARD_CONFIRM_TOKENS}:
                command_text = self._wizard_command(state)
                self.router.tasks.clear_chat_wizard_state(chat_id=ctx.telegram_chat_id)
                await self._execute_command(message, ctx, command_text)
                return True
            await self._send_wizard_prompt(
                message,
                state,
                context="sending wizard confirm hint",
            )
            return True

        next_state = self._advance_wizard_state(state, text)
        if next_state is None:
            await self._send_wizard_prompt(
                message,
                state,
                context="sending wizard retry prompt",
            )
            return True

        user = self.router.tasks.ensure_user(ctx)
        self.router.tasks.set_chat_wizard_state(
            chat_id=ctx.telegram_chat_id,
            user_id=user.id,
            state=next_state,
        )
        await self._send_wizard_prompt(
            message,
            next_state,
            context="sending wizard next prompt",
        )
        return True

    def _normalize_wizard_input(self, raw_text: str) -> str:
        text = raw_text.strip()
        if len(text) >= 2:
            closing = _QUOTE_PAIRS.get(text[0])
            if closing is not None and text[-1] == closing:
                text = text[1:-1].strip()
        return text

    def _advance_wizard_state(self, state: dict, text: str) -> dict | None:
        kind = str(state.get("kind") or "")
        step = str(state.get("step") or "")
        data = dict(state.get("data") or {})
        lowered = text.lower()

        if kind == "project_add":
            if step == "key":
                key = text.strip()
                if not key or " " in key:
                    return None
                data["key"] = key
                return {"kind": kind, "step": "path", "data": data}
            if step == "path":
                if lowered in {"默认", "default"}:
                    if getattr(self.router.projects, "default_project_root", None) is None:
                        return None
                    data["path"] = ""
                    return {"kind": kind, "step": "template", "data": data}
                if text.startswith("/") or text.startswith("~"):
                    data["path"] = text
                    return {"kind": kind, "step": "template", "data": data}
                return None
            if step == "template":
                if lowered in {item.lower() for item in self._WIZARD_SKIP_TOKENS}:
                    data["template_name"] = ""
                    return {"kind": kind, "step": "mode", "data": data}
                preset = self._find_project_template(text)
                if preset is None:
                    return None
                data["template_name"] = preset.key
                if preset.default_autopilot_goal and not data.get("autopilot_goal"):
                    data["autopilot_goal"] = preset.default_autopilot_goal
                return {"kind": kind, "step": "mode", "data": data}
            if step == "mode":
                if lowered not in {"normal", "autopilot"}:
                    return None
                data["default_run_mode"] = lowered
                if lowered == "autopilot" and not data.get("autopilot_goal"):
                    return {"kind": kind, "step": "goal", "data": data}
                return {"kind": kind, "step": "name", "data": data}
            if step == "goal":
                if not text.strip():
                    return None
                data["autopilot_goal"] = text.strip()
                return {"kind": kind, "step": "name", "data": data}
            if step == "name":
                data["name"] = "" if lowered in {item.lower() for item in self._WIZARD_SKIP_TOKENS} else text
                return {"kind": kind, "step": "confirm", "data": data}
            return None

        if kind == "schedule_add":
            if step == "time":
                hour_text, sep, minute_text = text.partition(":")
                if sep != ":":
                    return None
                try:
                    hour = int(hour_text)
                    minute = int(minute_text)
                except ValueError:
                    return None
                if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                    return None
                data["hhmm"] = f"{hour:02d}:{minute:02d}"
                return {"kind": kind, "step": "mode", "data": data}
            if step == "mode":
                if lowered not in {"ask", "do", "/ask", "/do"}:
                    return None
                data["mode"] = lowered.lstrip("/")
                return {"kind": kind, "step": "text", "data": data}
            if step == "text":
                if not text:
                    return None
                data["text"] = text
                return {"kind": kind, "step": "confirm", "data": data}
            return None

        if kind == "approve_note":
            if step == "note":
                data["note"] = "" if lowered in {item.lower() for item in self._WIZARD_SKIP_TOKENS} else text
                return {"kind": kind, "step": "confirm", "data": data}
            return None

        if kind == "reject_note":
            if step == "note":
                data["note"] = "用户拒绝" if lowered in {"默认", "default"} else (text or "用户拒绝")
                return {"kind": kind, "step": "confirm", "data": data}
            return None

        return None

    def _wizard_command(self, state: dict) -> str:
        kind = str(state.get("kind") or "")
        data = state.get("data") or {}
        if kind == "project_add":
            parts = ["/project-add", str(data["key"])]
            if data.get("path"):
                parts.append(shlex.quote(str(data["path"])))
            if data.get("template_name"):
                parts.append("--template")
                parts.append(shlex.quote(str(data["template_name"])))
            if data.get("default_run_mode"):
                parts.append("--mode")
                parts.append(str(data["default_run_mode"]))
            if data.get("autopilot_goal"):
                parts.append("--autopilot-goal")
                parts.append(shlex.quote(str(data["autopilot_goal"])))
            if data.get("name"):
                parts.append(shlex.quote(str(data["name"])))
            return " ".join(parts)
        if kind == "schedule_add":
            return f"/schedule-add {data['hhmm']} {data['mode']} {data['text']}"
        if kind == "approve_note":
            note = str(data.get("note") or "").strip()
            if note:
                return f"/approve {data['approval_id']} {note}"
            return f"/approve {data['approval_id']}"
        if kind == "reject_note":
            note = str(data.get("note") or "用户拒绝").strip() or "用户拒绝"
            return f"/reject {data['approval_id']} {note}"
        return ""

    def _project_template_presets(self) -> list[ProjectTemplatePreset]:
        projects = getattr(self.router, "projects", None)
        if projects is None or not hasattr(projects, "list_project_templates"):
            return []
        return list(projects.list_project_templates())

    def _find_project_template(self, text: str) -> ProjectTemplatePreset | None:
        normalized = text.strip()
        if not normalized:
            return None
        for preset in self._project_template_presets():
            if preset.key == normalized or preset.name == normalized:
                return preset
        return None

    def _project_shortcuts_markup(self, recent_projects: list[str] | None) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        return self.views.project_shortcuts_markup(recent_projects)

    def _reply_markup_for_result(
        self,
        command: str,
        ctx: CommandContext,
        result: CommandResult,
    ):
        if command in {"/start", "/home"}:
            snapshot = (result.metadata or {}).get("home_snapshot")
            if snapshot is not None:
                return self.views.home_markup(
                    snapshot=snapshot,
                    recent_projects=(result.metadata or {}).get("recent_projects"),
                )
        if command == "/context":
            snapshot = (result.metadata or {}).get("context_snapshot")
            if snapshot is not None:
                return self.views.context_markup(snapshot=snapshot)
        if command == "/status":
            user = self.router.tasks.ensure_user(ctx)
            snapshot = self.router.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
            return self.views.status_result_markup(
                snapshot=snapshot,
                recent_projects=(result.metadata or {}).get("recent_projects"),
            )
        if command == "/use" and (result.metadata or {}).get("projects_panel"):
            metadata = result.metadata or {}
            ordered_keys = metadata.get("projects_ordered_keys")
            active_key = metadata.get("projects_active_key")
            recent_keys = metadata.get("recent_projects")
            if isinstance(ordered_keys, list):
                return self.views.projects_panel(
                    active_key=active_key if isinstance(active_key, str) else None,
                    recent_keys=[item for item in (recent_keys or []) if isinstance(item, str)],
                    ordered_keys=[item for item in ordered_keys if isinstance(item, str)],
                ).reply_markup
        if command in {"/health", "/version", "/update-check", "/logs", "/logs-clear"}:
            return self.views.service_panel().reply_markup
        if command == "/schedule-list":
            active_key = self.router.tasks.get_active_project_key(
                self.router.tasks.ensure_user(ctx).id,
                ctx.telegram_chat_id,
            )
            if not active_key:
                return self._main_menu_markup()
            project_id = self.router.tasks.get_project_id(active_key)
            schedules = self.router.tasks.list_scheduled_tasks(project_id)
            return self.views.schedule_list_markup(schedules)
        if command == "/tasks":
            metadata = result.metadata or {}
            items = metadata.get("tasks_items")
            page = metadata.get("tasks_page")
            total_pages = metadata.get("tasks_total_pages")
            if isinstance(items, list) and isinstance(page, int) and isinstance(total_pages, int):
                return self.views.tasks_list_markup(items, page=page, total_pages=total_pages)
        if command == "/task-current":
            task = (result.metadata or {}).get("current_task")
            return self.views.current_task_markup(task if isinstance(task, TaskRecord) else None)
        if command in {"/ask", "/do", "/resume", "/approve", "/reject", "/task-output"}:
            task_id = (result.metadata or {}).get("task_id")
            status = (result.metadata or {}).get("status")
            return self.views.task_result_markup(
                task_id=task_id if isinstance(task_id, int) else None,
                status=str(status) if status is not None else None,
            )
        if command in {
            "/autopilots",
            "/autopilot",
            "/autopilot-status",
            "/autopilot-context",
            "/autopilot-takeover",
            "/autopilot-step",
            "/autopilot-pause",
            "/autopilot-resume",
            "/autopilot-stop",
        }:
            if command == "/autopilots":
                runs = (result.metadata or {}).get("autopilot_runs")
                if isinstance(runs, list):
                    return self.views.autopilot_runs_markup(
                        [run for run in runs if isinstance(run, AutopilotRunRecord)]
                    )
            run = (result.metadata or {}).get("autopilot_run")
            return self.views.autopilot_run_markup(run if isinstance(run, AutopilotRunRecord) else None)
        if command == "/memory":
            metadata = result.metadata or {}
            page = metadata.get("memory_page")
            total_pages = metadata.get("memory_total_pages")
            if isinstance(page, int) and isinstance(total_pages, int):
                return self.views.memory_pagination_markup(page=page, total_pages=total_pages)
        if command == "/sessions":
            metadata = result.metadata or {}
            sessions = metadata.get("sessions_items")
            page = metadata.get("sessions_page")
            total_pages = metadata.get("sessions_total_pages")
            if isinstance(sessions, list) and isinstance(page, int) and isinstance(total_pages, int):
                return self.views.sessions_list_markup(
                    sessions=sessions,
                    page=page,
                    total_pages=total_pages,
                )
        if command == "/session":
            record = (result.metadata or {}).get("session_record")
            if record is not None:
                return self.views.session_detail_markup(record=record)
        if command in {"/mcp", "/mcp-enable", "/mcp-disable"}:
            metadata = result.metadata or {}
            name = metadata.get("mcp_name")
            enabled = metadata.get("mcp_enabled")
            if isinstance(name, str) and isinstance(enabled, bool):
                return self.views.mcp_detail_markup(name=name, enabled=enabled)
        recent_projects = (result.metadata or {}).get("recent_projects")
        return self.views.default_result_markup(recent_projects)
