"""Schedule command handlers mixin."""

from __future__ import annotations

import sqlite3

from src import audit_events
from src.formatters import (
    format_schedule_added,
    format_schedule_deleted,
    format_schedule_list,
    format_schedule_run_result,
    format_schedule_toggled,
)
from src.models import CommandContext, CommandResult


class _ScheduleHandler:
    """Mixin: /schedule-* commands."""

    def _handle_schedule_add(self, ctx: CommandContext, argument: str) -> CommandResult:
        parsed = self._parse_schedule_add_argument(argument)
        if parsed is None:
            return CommandResult(
                "用法: /schedule-add\n"
                "  /schedule-add <HH:MM> <ask|do|digest> [text]   — 每日定时触发\n"
                "  /schedule-add every <N>m <ask|do|digest> [text] — 每隔 N 分钟触发\n"
                "  /schedule-add every <N>h <ask|do|digest> [text] — 每隔 N 小时触发",
                metadata={"wizard": "schedule_add"},
            )

        trigger_label, minute_of_day, command_type, request_text, schedule_type, interval_minutes = parsed
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        try:
            schedule_id = self.tasks.create_scheduled_task(
                user_id=active.user.id,
                project_id=active.project_id,
                chat_id=ctx.telegram_chat_id,
                command_type=command_type,
                request_text=request_text,
                minute_of_day=minute_of_day,
                schedule_type=schedule_type,
                interval_minutes=interval_minutes,
            )
        except sqlite3.OperationalError:
            return CommandResult("当前数据库尚未完成定时任务迁移，请重启服务后重试。")
        except TypeError:
            if schedule_type != "daily" or interval_minutes is not None:
                raise
            schedule_id = self.tasks.create_scheduled_task(
                user_id=active.user.id,
                project_id=active.project_id,
                chat_id=ctx.telegram_chat_id,
                command_type=command_type,
                request_text=request_text,
                minute_of_day=minute_of_day,
            )
        self.audit.log(
            action=audit_events.SCHEDULE_CREATED,
            message=f"创建定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={
                "schedule_id": schedule_id,
                "trigger_label": trigger_label,
                "schedule_type": schedule_type,
                "interval_minutes": interval_minutes,
                "command_type": command_type,
                "request_preview": request_text[:200],
            },
        )
        return CommandResult(
            format_schedule_added(
                schedule_id=schedule_id,
                trigger_label=trigger_label,
                command_type=command_type,
                request_text=request_text,
            )
        )

    def _handle_schedule_list(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        schedules = self.tasks.list_scheduled_tasks(active.project_id)
        self.audit.log(
            action=audit_events.SCHEDULE_VIEWED,
            message="查看定期任务列表",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"count": len(schedules)},
        )
        items = [
            (
                item.id,
                self._trigger_label(item),
                item.enabled,
                item.command_type,
                item.request_text,
                item.last_run_status,
            )
            for item in schedules
        ]
        return CommandResult(format_schedule_list(items))

    def _handle_schedule_run(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        schedule_id = self._parse_schedule_id(argument)
        if schedule_id is None:
            return CommandResult("用法: /schedule-run <id>")
        schedule = self.tasks.get_scheduled_task(
            schedule_id=schedule_id,
            project_id=active.project_id,
        )
        if schedule is None:
            return CommandResult(f"未找到定期任务 #{schedule_id}。")

        self.audit.log(
            action=audit_events.SCHEDULE_MANUAL_RUN,
            message=f"手动触发定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"schedule_id": schedule_id},
        )
        result = self.run_scheduled_task(schedule)
        metadata = result.metadata or {}
        task_id_raw = metadata.get("task_id")
        task_id = task_id_raw if isinstance(task_id_raw, int) else None
        status = str(metadata.get("status") or "unknown")
        self.tasks.record_scheduled_task_run(
            schedule_id=schedule.id,
            task_id=task_id,
            status=status,
            summary=result.reply_text,
        )
        return CommandResult(format_schedule_run_result(schedule.id, result.reply_text))

    def _handle_schedule_toggle(
        self,
        ctx: CommandContext,
        argument: str,
        *,
        enabled: bool,
    ) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        schedule_id = self._parse_schedule_id(argument)
        if schedule_id is None:
            return CommandResult(f"用法: /schedule-{'enable' if enabled else 'pause'} <id>")
        updated = self.tasks.set_scheduled_task_enabled(
            schedule_id=schedule_id,
            project_id=active.project_id,
            enabled=enabled,
        )
        if not updated:
            return CommandResult(f"未找到定期任务 #{schedule_id}。")
        self.audit.log(
            action=audit_events.SCHEDULE_TOGGLED,
            message=f"{'启用' if enabled else '暂停'}定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"schedule_id": schedule_id, "enabled": enabled},
        )
        return CommandResult(format_schedule_toggled(schedule_id, enabled=enabled))

    def _handle_schedule_delete(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if not argument:
            return CommandResult("用法: /schedule-del <id>")
        try:
            schedule_id = int(argument.strip())
        except ValueError:
            return CommandResult("任务 id 必须是数字。")

        deleted = self.tasks.delete_scheduled_task(
            schedule_id=schedule_id,
            project_id=active.project_id,
        )
        if not deleted:
            return CommandResult(f"未找到定期任务 #{schedule_id}。")
        self.audit.log(
            action=audit_events.SCHEDULE_DELETED,
            message=f"删除定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"schedule_id": schedule_id},
        )
        return CommandResult(format_schedule_deleted(schedule_id))

    # ------------------------------------------------------------------ #
    #  Parse helpers                                                       #
    # ------------------------------------------------------------------ #

    def _parse_schedule_add_argument(
        self, argument: str
    ) -> tuple[str, int, str, str, str, int | None] | None:
        """Returns (trigger_label, minute_of_day, command_type, request_text, schedule_type, interval_minutes)."""
        parts = argument.strip().split()
        if len(parts) < 2:
            return None

        # Interval form: every <N>m|<N>h <ask|do|digest> [text]
        if parts[0].lower() == "every":
            if len(parts) < 3:
                return None
            interval_str = parts[1].lower()
            if interval_str.endswith("m"):
                try:
                    interval_minutes = int(interval_str[:-1])
                except ValueError:
                    return None
                label = f"每{interval_minutes}分钟"
            elif interval_str.endswith("h"):
                try:
                    hours = int(interval_str[:-1])
                except ValueError:
                    return None
                interval_minutes = hours * 60
                label = f"每{hours}小时"
            else:
                return None
            if interval_minutes < 1:
                return None
            command_type = parts[2].lstrip("/")
            if command_type not in {"ask", "do", "digest"}:
                return None
            request_text = " ".join(parts[3:]).strip()
            if command_type != "digest" and not request_text:
                return None
            return label, 0, command_type, request_text or "项目摘要推送", "interval", interval_minutes

        # Daily form: HH:MM <ask|do|digest> [text]
        hhmm = parts[0]
        command_type = parts[1].lstrip("/")
        if command_type not in {"ask", "do", "digest"}:
            return None
        request_text = " ".join(parts[2:]).strip()
        if command_type != "digest" and not request_text:
            return None
        hour_text, sep, minute_text = hhmm.partition(":")
        if sep != ":":
            return None
        try:
            hour = int(hour_text)
            minute = int(minute_text)
        except ValueError:
            return None
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        normalized_hhmm = f"{hour:02d}:{minute:02d}"
        minute_of_day = hour * 60 + minute
        return normalized_hhmm, minute_of_day, command_type, request_text or "项目摘要推送", "daily", None

    def _minute_to_hhmm(self, minute_of_day: int) -> str:
        hour = minute_of_day // 60
        minute = minute_of_day % 60
        return f"{hour:02d}:{minute:02d}"

    def _trigger_label(self, item: object) -> str:
        """Return a human-readable trigger label for a scheduled task record."""
        if getattr(item, "schedule_type", "daily") == "interval":
            mins = getattr(item, "interval_minutes", None)
            if mins is not None:
                if mins % 60 == 0:
                    return f"每{mins // 60}小时"
                return f"每{mins}分钟"
        return self._minute_to_hhmm(getattr(item, "minute_of_day", 0))

    def _parse_schedule_id(self, argument: str) -> int | None:
        if not argument:
            return None
        try:
            return int(argument.strip())
        except ValueError:
            return None
