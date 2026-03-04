"""Telegram long-polling adapter."""

import asyncio
import logging
import random
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from src.formatters import format_upload_received, truncate_for_telegram
from src.models import CommandContext, CommandResult
from src.progress_reporter import ProgressReporter
from src.redaction import redact_text


logger = logging.getLogger(__name__)


class TelegramBotService:
    """Runs Telegram long polling and forwards text messages to the router."""

    _MENU_PROJECTS = "项目"
    _MENU_TASKS = "任务"
    _MENU_STATUS = "状态"
    _MENU_SCHEDULE = "定时"
    _MENU_APPROVAL = "审批"
    _MENU_TOOLS = "工具"
    _MENU_HELP = "帮助"
    _MENU_CANCEL = "取消"

    _CALLBACK_COMMANDS = {
        "start": "/start",
        "help": "/help",
        "status": "/status",
        "projects": "/projects",
        "templates": "/templates",
        "skills": "/skills",
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
    }

    _PROMPT_COMMANDS = {
        "ask": "/ask",
        "do": "/do",
        "note": "/note",
        "retry": "/retry",
        "resume": "/resume",
        "run": "/run",
        "skill_install": "/skill-install",
        "schedule_add": "/schedule-add",
        "project_add": "/project-add",
        "project_root": "/project-root",
        "use": "/use",
        "project_disable": "/project-disable",
        "project_archive": "/project-archive",
        "approve": "/approve",
        "reject": "/reject",
    }

    _PROMPT_HINTS = {
        "ask": "请输入问题。下一条消息将按 /ask 执行。",
        "do": "请输入任务描述。下一条消息将按 /do 执行。",
        "note": "请输入笔记内容。下一条消息将按 /note 保存。",
        "retry": "请输入补充说明。下一条消息将按 /retry 执行（可留空直接用 /retry）。",
        "resume": "请输入恢复指令（示例: 12 继续修复测试）。下一条消息将按 /resume 执行。",
        "run": "请输入模板 key 与可选说明（示例: review_repo 重点看 CI）。下一条消息将按 /run 执行。",
        "skill_install": "请输入 skill 来源。下一条消息将按 /skill-install 执行。",
        "schedule_add": "请输入计划（示例: 09:30 ask 检查项目状态）。下一条消息将按 /schedule-add 执行。",
        "project_add": "请输入新增项目参数（示例: demo 项目名，或 demo /abs/path 项目名）。下一条消息将按 /project-add 执行。",
        "project_root": "请输入默认项目根目录（示例: /Users/you/workspace/projects）。下一条消息将按 /project-root 执行。",
        "use": "请输入项目 key。下一条消息将按 /use 执行。",
        "project_disable": "请输入要停用的项目 key。下一条消息将按 /project-disable 执行。",
        "project_archive": "请输入要归档的项目 key。下一条消息将按 /project-archive 执行。",
        "approve": "请输入审批备注。下一条消息将按 /approve 执行。",
        "reject": "请输入拒绝原因。下一条消息将按 /reject 执行。",
    }

    def __init__(self, config, router) -> None:
        self.config = config
        self.router = router
        self.progress = ProgressReporter()
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
                self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                await self._send_projects_panel(message, command_context)
                return
            if mapped == "__tasks__":
                self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                await self._send_task_panel(message)
                return
            if mapped == "__schedule__":
                self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                await self._send_schedule_panel(message, command_context)
                return
            if mapped == "__approval__":
                self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                await self._send_approval_panel(message)
                return
            if mapped == "__tools__":
                self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                await self._send_tools_panel(message)
                return

            raw_text = mapped or message.text.strip()
            if raw_text.startswith("/"):
                self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
            else:
                pending_command = self._pending_command_by_chat.pop(command_context.telegram_chat_id, None)
                if pending_command:
                    raw_text = f"{pending_command} {raw_text}"

            command = raw_text.split(" ", 1)[0].split("@", 1)[0]
            ack_text = self.progress.ack_text(command)
            if ack_text:
                await self._safe_reply_text(
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
            await self._safe_reply_text(
                message,
                result.reply_text,
                context="sending command result",
                reply_markup=self._reply_markup_for_command(command, command_context),
            )
        except Exception:  # pragma: no cover - defensive logging around external API callbacks
            logger.exception("Unhandled exception while processing Telegram message.")
            await self._safe_reply_text(
                message,
                "Internal error while handling request.",
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
            await self._safe_reply_text(
                message,
                "已收到文件，处理中：\n- 校验项目\n- 校验文件\n- 下载并分析",
                context="sending upload ack",
                reply_markup=self._main_menu_markup(),
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
                    reply_markup=self._main_menu_markup(),
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
                reply_markup=self._main_menu_markup(),
            )

            result = self.router.handle_uploaded_document(
                ctx=command_context,
                plan=plan_or_error,
                caption=message.caption,
            )
            await self._safe_reply_text(
                message,
                result.reply_text,
                context="sending upload result",
                reply_markup=self._main_menu_markup(),
            )
        except Exception:  # pragma: no cover - defensive logging around external API callbacks
            logger.exception("Unhandled exception while processing Telegram document.")
            await self._safe_reply_text(
                message,
                "Internal error while handling uploaded file.",
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
            if data == "status:approval":
                await self._send_approval_panel(query.message)
                return
            if data == "status:schedule":
                await self._send_schedule_panel(query.message, base_ctx)
                return
            if data == "approval:approve":
                await self._execute_command(query.message, base_ctx, "/approve")
                return
            if data == "approval:reject":
                await self._execute_command(query.message, base_ctx, "/reject")
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
                await self._activate_prompt(query.message, base_ctx.telegram_chat_id, token)
                return
            if data.startswith("cmd:"):
                token = data.split(":", 1)[1]
                command = self._resolve_callback_command(token)
                if command is not None:
                    await self._execute_command(query.message, base_ctx, command)
                    return
            if data.startswith("prompt:"):
                token = data.split(":", 1)[1]
                await self._activate_prompt(query.message, base_ctx.telegram_chat_id, token)
                return
            if data.startswith("panel:"):
                panel = data.split(":", 1)[1]
                if panel == "projects":
                    await self._send_projects_panel(query.message, base_ctx)
                    return
                if panel == "tasks":
                    await self._send_task_panel(query.message)
                    return
                if panel == "schedule":
                    await self._send_schedule_panel(query.message, base_ctx)
                    return
                if panel == "approval":
                    await self._send_approval_panel(query.message)
                    return
                if panel == "tools":
                    await self._send_tools_panel(query.message)
                    return
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Unhandled callback query error: %s", data)
            await self._safe_reply_text(
                query.message,
                "处理按钮操作时发生错误。",
                context="sending callback error",
                reply_markup=self._main_menu_markup(),
            )
            return

        await self._safe_reply_text(
            query.message,
            "未识别的按钮操作，请重试。",
            context="sending unknown callback",
            reply_markup=self._main_menu_markup(),
        )

    def _resolve_callback_command(self, token: str) -> str | None:
        return self._CALLBACK_COMMANDS.get(token)

    async def _activate_prompt(self, message, chat_id: str, token: str) -> None:  # noqa: ANN001
        if token == "clear":
            self._pending_command_by_chat.pop(chat_id, None)
            await self._safe_reply_text(
                message,
                "已清除输入引导。",
                context="clearing prompt mode",
                reply_markup=self._main_menu_markup(),
            )
            return

        command = self._PROMPT_COMMANDS.get(token)
        if command is None:
            await self._safe_reply_text(
                message,
                "未识别的输入模式。",
                context="sending prompt mode error",
                reply_markup=self._main_menu_markup(),
            )
            return

        self._pending_command_by_chat[chat_id] = command
        hint = self._PROMPT_HINTS.get(token, f"请输入参数。下一条消息将按 {command} 执行。")
        await self._safe_reply_text(
            message,
            f"{hint}\n可发送 /help 查看命令。",
            context="sending prompt mode hint",
            reply_markup=self._main_menu_markup(),
        )

    async def _execute_command(self, message, base_ctx: CommandContext, text: str) -> None:  # noqa: ANN001
        command = text.strip().split(" ", 1)[0].split("@", 1)[0]
        self._pending_command_by_chat.pop(base_ctx.telegram_chat_id, None)
        ack_text = self.progress.ack_text(command)
        if ack_text:
            await self._safe_reply_text(
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
        result = self.router.handle(command_context)
        await self._safe_reply_text(
            message,
            result.reply_text,
            context="sending command result",
            reply_markup=self._reply_markup_for_command(command, command_context),
        )

    async def _send_projects_panel(self, message, ctx: CommandContext) -> None:  # noqa: ANN001
        keys = self.router.projects.list_keys()
        rows: list[list[InlineKeyboardButton]] = []
        for key in keys[:20]:
            rows.append([InlineKeyboardButton(text=f"切换: {key}", callback_data=f"use:{key}")])
        rows.extend(
            [
                [InlineKeyboardButton(text="查看项目列表", callback_data="cmd:projects")],
                [
                    InlineKeyboardButton(text="新增项目", callback_data="prompt:project_add"),
                    InlineKeyboardButton(text="设置默认根目录", callback_data="prompt:project_root"),
                ],
                [
                    InlineKeyboardButton(text="查看默认根目录", callback_data="cmd:project_root_show"),
                    InlineKeyboardButton(text="手输切换", callback_data="prompt:use"),
                ],
                [
                    InlineKeyboardButton(text="停用当前", callback_data="cmd:project_disable_current"),
                    InlineKeyboardButton(text="归档当前", callback_data="cmd:project_archive_current"),
                ],
                [
                    InlineKeyboardButton(text="停用指定", callback_data="prompt:project_disable"),
                    InlineKeyboardButton(text="归档指定", callback_data="prompt:project_archive"),
                ],
            ]
        )
        await self._safe_reply_text(
            message,
            "项目操作：",
            context="sending projects panel",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def _send_task_panel(self, message) -> None:  # noqa: ANN001
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(text="只读分析", callback_data="prompt:ask"),
                    InlineKeyboardButton(text="执行修改", callback_data="prompt:do"),
                ],
                [
                    InlineKeyboardButton(text="最近任务", callback_data="cmd:last"),
                    InlineKeyboardButton(text="查看Diff", callback_data="cmd:diff"),
                ],
                [
                    InlineKeyboardButton(text="重试任务", callback_data="prompt:retry"),
                    InlineKeyboardButton(text="继续任务", callback_data="prompt:resume"),
                ],
                [
                    InlineKeyboardButton(text="项目记忆", callback_data="cmd:memory"),
                    InlineKeyboardButton(text="记录笔记", callback_data="prompt:note"),
                ],
                [
                    InlineKeyboardButton(text="模板列表", callback_data="cmd:templates"),
                    InlineKeyboardButton(text="执行模板", callback_data="prompt:run"),
                ],
                [
                    InlineKeyboardButton(text="技能列表", callback_data="cmd:skills"),
                    InlineKeyboardButton(text="安装技能", callback_data="prompt:skill_install"),
                ],
                [InlineKeyboardButton(text="清除输入引导", callback_data="prompt:clear")],
            ]
        )
        await self._safe_reply_text(
            message,
            "任务操作：",
            context="sending task panel",
            reply_markup=markup,
        )

    async def _send_schedule_panel(self, message, ctx: CommandContext) -> None:  # noqa: ANN001
        await self._execute_command(message, ctx, "/schedule-list")

    async def _send_approval_panel(self, message) -> None:  # noqa: ANN001
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(text="批准", callback_data="approval:approve"),
                    InlineKeyboardButton(text="拒绝", callback_data="approval:reject"),
                ],
                [
                    InlineKeyboardButton(text="批准+备注", callback_data="prompt:approve"),
                    InlineKeyboardButton(text="拒绝+原因", callback_data="prompt:reject"),
                ],
                [InlineKeyboardButton(text="查看状态", callback_data="approval:status")],
            ]
        )
        await self._safe_reply_text(
            message,
            "审批操作：",
            context="sending approval panel",
            reply_markup=markup,
        )

    async def _send_tools_panel(self, message) -> None:  # noqa: ANN001
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(text="开始", callback_data="cmd:start"),
                    InlineKeyboardButton(text="帮助", callback_data="cmd:help"),
                ],
                [
                    InlineKeyboardButton(text="上传策略", callback_data="cmd:upload_policy"),
                    InlineKeyboardButton(text="取消任务", callback_data="cmd:cancel"),
                ],
                [
                    InlineKeyboardButton(text="状态", callback_data="cmd:status"),
                    InlineKeyboardButton(text="定时列表", callback_data="cmd:schedule_list"),
                ],
            ]
        )
        await self._safe_reply_text(
            message,
            "工具操作：",
            context="sending tools panel",
            reply_markup=markup,
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

    async def _safe_reply_text(
        self,
        message,
        text: str,
        *,
        context: str,
        reply_markup=None,  # noqa: ANN001
    ) -> bool:
        """Send Telegram reply with conservative retries for transient network failures."""

        payload = truncate_for_telegram(
            redact_text(text),
            limit=self.config.max_telegram_message_length,
        )
        max_attempts = 3
        final_reply_markup = reply_markup or self._main_menu_markup()
        for attempt in range(1, max_attempts + 1):
            try:
                await message.reply_text(payload, reply_markup=final_reply_markup)
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

    def _main_menu_markup(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(
            [
                [self._MENU_PROJECTS, self._MENU_TASKS, self._MENU_STATUS],
                [self._MENU_SCHEDULE, self._MENU_APPROVAL, self._MENU_TOOLS],
                [self._MENU_HELP, self._MENU_CANCEL],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
            selective=False,
        )

    def _map_menu_to_command(self, text: str) -> str | None:
        mapping = {
            self._MENU_STATUS: "/status",
            self._MENU_CANCEL: "/cancel",
            self._MENU_HELP: "/help",
            self._MENU_PROJECTS: "__projects__",
            self._MENU_TASKS: "__tasks__",
            self._MENU_SCHEDULE: "__schedule__",
            self._MENU_APPROVAL: "__approval__",
            self._MENU_TOOLS: "__tools__",
        }
        return mapping.get(text)

    def _reply_markup_for_command(
        self,
        command: str,
        ctx: CommandContext,
    ):
        if command == "/status":
            return InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(text="继续", callback_data="status:resume"),
                        InlineKeyboardButton(text="看Diff", callback_data="status:diff"),
                    ],
                    [
                        InlineKeyboardButton(text="审批", callback_data="status:approval"),
                        InlineKeyboardButton(text="定时", callback_data="status:schedule"),
                    ],
                    [
                        InlineKeyboardButton(text="最近任务", callback_data="cmd:last"),
                        InlineKeyboardButton(text="任务面板", callback_data="panel:tasks"),
                    ],
                ]
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
            rows: list[list[InlineKeyboardButton]] = [
                [InlineKeyboardButton(text="新增定时", callback_data="prompt:schedule_add")]
            ]
            for item in schedules[:8]:
                rows.append(
                    [
                        InlineKeyboardButton(text=f"运行 #{item.id}", callback_data=f"schedule:run:{item.id}"),
                        InlineKeyboardButton(
                            text=("暂停" if item.enabled else "启用"),
                            callback_data=f"schedule:{'pause' if item.enabled else 'enable'}:{item.id}",
                        ),
                        InlineKeyboardButton(text="删除", callback_data=f"schedule:del:{item.id}"),
                    ]
                )
            rows.append([InlineKeyboardButton(text="刷新", callback_data="schedule:refresh")])
            return InlineKeyboardMarkup(rows)
        return self._main_menu_markup()
