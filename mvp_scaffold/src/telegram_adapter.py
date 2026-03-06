"""Telegram long-polling adapter."""

import asyncio
import logging
import random
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from src import telegram_messages
from src.formatters import format_upload_received
from src.models import CommandContext, CommandResult
from src.progress_reporter import ProgressReporter
from src.telegram_sink import TelegramMessageSink, TelegramSendSpec
from src.task_templates import BUILTIN_TEMPLATES
from src.telegram_views import TelegramReplySpec, TelegramViewFactory


logger = logging.getLogger(__name__)


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

    _CALLBACK_COMMANDS = {
        "start": "/start",
        "help": "/help",
        "status": "/status",
        "projects": "/projects",
        "templates": "/templates",
        "skills": "/skills",
        "mcp": "/mcp",
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
        "project_disable_current": "/project-disable",
        "project_archive_current": "/project-archive",
        "ui_show": "/ui",
        "ui_summary": "/ui summary",
        "ui_verbose": "/ui verbose",
    }

    _WIZARD_TOKENS = {"project_add", "schedule_add", "run", "approve_note", "reject_note"}
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
        "use": "/use",
        "project_disable": "/project-disable",
        "project_archive": "/project-archive",
        "approve": "/approve",
        "reject": "/reject",
        "mcp": "/mcp",
    }

    _PROMPT_HINTS = {
        "ask": "请输入问题。下一条消息将按 /ask 执行。",
        "do": "请输入任务描述。下一条消息将按 /do 执行。",
        "note": "请输入笔记内容。下一条消息将按 /note 保存。",
        "retry": "请输入补充说明。下一条消息将按 /retry 执行（可留空直接用 /retry）。",
        "resume": "请输入恢复指令（示例: 12 继续修复测试）。下一条消息将按 /resume 执行。",
        "skill_install": "请输入 skill 来源。下一条消息将按 /skill-install 执行。",
        "project_root": "请输入默认项目根目录（示例: /Users/you/workspace/projects）。下一条消息将按 /project-root 执行。",
        "use": "请输入项目 key。下一条消息将按 /use 执行。",
        "project_disable": "请输入要停用的项目 key。下一条消息将按 /project-disable 执行。",
        "project_archive": "请输入要归档的项目 key。下一条消息将按 /project-archive 执行。",
        "approve": "请输入审批备注。下一条消息将按 /approve 执行。",
        "reject": "请输入拒绝原因。下一条消息将按 /reject 执行。",
        "mcp": "请输入 MCP 名称（留空则查看列表）。下一条消息将按 /mcp 执行。",
    }
    _TYPING_COMMANDS = {
        "/ask",
        "/approve",
        "/do",
        "/mcp",
        "/reject",
        "/resume",
        "/retry",
        "/run",
        "/schedule-run",
        "/skill-install",
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
        self._pending_command_by_chat: dict[str, str] = {}

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
        app.add_handler(CallbackQueryHandler(self._on_callback_query))
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
            if raw_text.startswith("/"):
                self._clear_input_modes(command_context.telegram_chat_id)
            else:
                wizard_state = self._get_wizard_state(command_context.telegram_chat_id)
                if wizard_state is not None:
                    handled = await self._handle_wizard_input(
                        message,
                        command_context,
                        wizard_state,
                        raw_text,
                    )
                    if handled:
                        return
                pending_command = self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                if pending_command:
                    raw_text = f"{pending_command} {raw_text}"

            command = raw_text.split(" ", 1)[0].split("@", 1)[0]
            ack_text = self.progress.ack_text(command)
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
            result = self.router.handle(command_context)
            if await self._maybe_start_wizard_from_result(message, command_context, result):
                return
            await self._send_command_result(message, command, command_context, result)
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
            if data.startswith("use:"):
                project_key = data.split(":", 1)[1]
                await self._execute_command(
                    query.message,
                    base_ctx,
                    f"/use {project_key}",
                )
                return
            if data == "status:resume":
                await self._execute_command(query.message, base_ctx, "/resume")
                return
            if data == "status:diff":
                await self._execute_command(query.message, base_ctx, "/diff")
                return
            if data == "status:ask":
                await self._activate_prompt(query.message, base_ctx, "ask")
                return
            if data == "status:do":
                await self._activate_prompt(query.message, base_ctx, "do")
                return
            if data == "status:projects":
                await self._send_projects_panel(query.message, base_ctx)
                return
            if data == "status:approval":
                await self._send_approval_panel(query.message, base_ctx)
                return
            if data == "status:schedule":
                await self._send_schedule_panel(query.message, base_ctx)
                return
            if data == "status:more":
                await self._send_more_panel(query.message)
                return
            if data == "approval:approve":
                await self._execute_command(query.message, base_ctx, "/approve")
                await self._clear_inline_keyboard(query.message)
                return
            if data == "approval:reject":
                await self._execute_command(query.message, base_ctx, "/reject")
                await self._clear_inline_keyboard(query.message)
                return
            if data.startswith("approval:approve:"):
                approval_id = data.rsplit(":", 1)[1]
                if not self._is_active_approval_callback(base_ctx, query.message, approval_id):
                    await self._clear_inline_keyboard(query.message)
                    await self._send_text(
                        query.message,
                        "这个审批按钮已过期，请重新打开当前审批卡片。",
                        context="sending expired approval callback",
                        reply_markup=self._main_menu_markup(),
                    )
                    return
                await self._execute_command(query.message, base_ctx, f"/approve {approval_id}")
                await self._clear_inline_keyboard(query.message)
                return
            if data.startswith("approval:reject:"):
                approval_id = data.rsplit(":", 1)[1]
                if not self._is_active_approval_callback(base_ctx, query.message, approval_id):
                    await self._clear_inline_keyboard(query.message)
                    await self._send_text(
                        query.message,
                        "这个审批按钮已过期，请重新打开当前审批卡片。",
                        context="sending expired approval callback",
                        reply_markup=self._main_menu_markup(),
                    )
                    return
                await self._execute_command(query.message, base_ctx, f"/reject {approval_id}")
                await self._clear_inline_keyboard(query.message)
                return
            if data == "approval:status":
                await self._execute_command(query.message, base_ctx, "/status")
                return
            if data == "schedule:refresh":
                await self._send_schedule_panel(query.message, base_ctx)
                return
            if data.startswith("schedule:run:"):
                schedule_id = data.rsplit(":", 1)[1]
                await self._execute_command(query.message, base_ctx, f"/schedule-run {schedule_id}")
                return
            if data.startswith("schedule:pause:"):
                schedule_id = data.rsplit(":", 1)[1]
                await self._execute_command(query.message, base_ctx, f"/schedule-pause {schedule_id}")
                return
            if data.startswith("schedule:enable:"):
                schedule_id = data.rsplit(":", 1)[1]
                await self._execute_command(query.message, base_ctx, f"/schedule-enable {schedule_id}")
                return
            if data.startswith("schedule:del:"):
                schedule_id = data.rsplit(":", 1)[1]
                await self._execute_command(query.message, base_ctx, f"/schedule-del {schedule_id}")
                return
            if data in {"taskmode:ask", "taskmode:do"}:
                token = "ask" if data.endswith(":ask") else "do"
                await self._activate_prompt(query.message, base_ctx, token)
                return
            if data.startswith("cmd:"):
                token = data.split(":", 1)[1]
                command = self._resolve_callback_command(token)
                if command is not None:
                    await self._execute_command(query.message, base_ctx, command)
                    return
            if data.startswith("prompt:"):
                token = data.split(":", 1)[1]
                await self._activate_prompt(query.message, base_ctx, token)
                return
            if data.startswith("wizard:"):
                await self._handle_wizard_callback(query.message, base_ctx, data)
                return
            if data.startswith("panel:"):
                panel = data.split(":", 1)[1]
                if panel == "projects":
                    await self._send_projects_panel(query.message, base_ctx)
                    return
                if panel == "schedule":
                    await self._send_schedule_panel(query.message, base_ctx)
                    return
                if panel == "approval":
                    await self._send_approval_panel(query.message, base_ctx)
                    return
                if panel == "more":
                    await self._send_more_panel(query.message)
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
        self._pending_command_by_chat[chat_id] = command
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
        if self._should_send_typing(command):
            await self.sink.send_typing(message, context=f"executing {command}")
        command_context = CommandContext(
            telegram_user_id=base_ctx.telegram_user_id,
            telegram_chat_id=base_ctx.telegram_chat_id,
            telegram_message_id=base_ctx.telegram_message_id,
            text=text,
            telegram_username=base_ctx.telegram_username,
            telegram_display_name=base_ctx.telegram_display_name,
        )
        result = self.router.handle(command_context)
        if await self._maybe_start_wizard_from_result(message, command_context, result):
            return
        await self._send_command_result(message, command, command_context, result)

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
        reply_markup=None,  # noqa: ANN001
    ) -> bool:
        return await self._send_view_spec(
            message,
            TelegramReplySpec(text=text, reply_markup=reply_markup),
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
        return await self._send_view_spec(
            message,
            TelegramReplySpec(
                text=result.reply_text,
                reply_markup=self._reply_markup_for_result(command, ctx, result),
            ),
            context=context,
            command=command,
        )

    def _main_menu_markup(self) -> ReplyKeyboardMarkup:
        return self.views.main_menu_markup()

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
        }
        return mapping.get(text)

    def _clear_input_modes(self, chat_id: str) -> None:
        self._pending_command_by_chat.pop(chat_id, None)
        if hasattr(self.router, "tasks"):
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
        if token in {"run", "schedule_add"}:
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
            state = {"kind": token, "step": "template", "data": {}}
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
            )

        if kind == "schedule_add":
            return telegram_messages.schedule_add_prompt(step=step, data=data)
        if kind == "approve_note":
            return telegram_messages.approval_note_prompt(step=step, data=data, action="approve")
        if kind == "reject_note":
            return telegram_messages.approval_note_prompt(step=step, data=data, action="reject")

        template_keys = ", ".join(sorted(BUILTIN_TEMPLATES.keys()))
        return telegram_messages.run_template_prompt(step=step, data=data, template_keys=template_keys)

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

        if kind == "run":
            if step == "template":
                rows: list[list[InlineKeyboardButton]] = []
                current_row: list[InlineKeyboardButton] = []
                for key in sorted(BUILTIN_TEMPLATES.keys()):
                    current_row.append(
                        InlineKeyboardButton(text=key, callback_data=f"wizard:template:{key}")
                    )
                    if len(current_row) == 2:
                        rows.append(current_row)
                        current_row = []
                if current_row:
                    rows.append(current_row)
                rows.append([InlineKeyboardButton(text="取消", callback_data="wizard:cancel")])
                return InlineKeyboardMarkup(rows)
            if step == "extra":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="跳过附加说明", callback_data="wizard:skip"),
                        InlineKeyboardButton(text="取消", callback_data="wizard:cancel"),
                    ]]
                )
            if step == "confirm":
                return InlineKeyboardMarkup(
                    [[
                        InlineKeyboardButton(text="确认执行", callback_data="wizard:confirm"),
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
        text = raw_text.strip()
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
                    return {"kind": kind, "step": "name", "data": data}
                if text.startswith("/") or text.startswith("~"):
                    data["path"] = text
                    return {"kind": kind, "step": "name", "data": data}
                return None
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

        if step == "template":
            template_key = text.strip()
            if template_key not in BUILTIN_TEMPLATES:
                return None
            data["template"] = template_key
            return {"kind": kind, "step": "extra", "data": data}
        if step == "extra":
            data["extra"] = "" if lowered in {item.lower() for item in self._WIZARD_SKIP_TOKENS} else text
            return {"kind": kind, "step": "confirm", "data": data}
        return None

    def _wizard_command(self, state: dict) -> str:
        kind = str(state.get("kind") or "")
        data = state.get("data") or {}
        if kind == "project_add":
            parts = ["/project-add", str(data["key"])]
            if data.get("path"):
                parts.append(str(data["path"]))
            if data.get("name"):
                parts.append(str(data["name"]))
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
        extra = str(data.get("extra") or "").strip()
        if extra:
            return f"/run {data['template']} {extra}"
        return f"/run {data['template']}"

    def _project_shortcuts_markup(self, recent_projects: list[str] | None) -> InlineKeyboardMarkup | ReplyKeyboardMarkup:
        return self.views.project_shortcuts_markup(recent_projects)

    def _reply_markup_for_result(
        self,
        command: str,
        ctx: CommandContext,
        result: CommandResult,
    ):
        if command == "/status":
            user = self.router.tasks.ensure_user(ctx)
            snapshot = self.router.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
            return self.views.status_result_markup(
                snapshot=snapshot,
                recent_projects=(result.metadata or {}).get("recent_projects"),
            )
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
        recent_projects = (result.metadata or {}).get("recent_projects")
        return self.views.default_result_markup(recent_projects)
