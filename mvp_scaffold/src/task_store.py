"""Persistence helpers for project continuity and task lifecycle."""

from dataclasses import dataclass
import logging
import sqlite3
from typing import Any

from src import audit_events
from src.autopilot_store import AutopilotStore
from src.approval_store import ApprovalStore
from src.chat_state_store import ChatStateStore
from src.db import Database
from src.models import CommandContext, UserRecord
from src.project_registry import ProjectRegistry
from src.project_state_store import ProjectStateStore
from src.schedule_store import ScheduleStore, ScheduledTaskRecord
from src.system_notification_store import SystemNotificationRecord, SystemNotificationStore
from src.task_runtime_store import TaskRuntimeStore


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TaskRecord:
    id: int
    command_type: str
    original_request: str
    status: str
    codex_session_id: str | None
    latest_summary: str | None


@dataclass(slots=True)
class StatusSnapshot:
    active_project_key: str | None
    active_project_name: str | None
    project_path: str | None
    current_branch: str | None
    repo_dirty: bool | None
    last_codex_session_id: str | None
    most_recent_task_summary: str | None
    recent_failed_summary: str | None
    pending_approval: bool
    next_schedule_id: int | None
    next_schedule_hhmm: str | None
    next_step: str | None
    pending_approval_id: int | None = None
    active_task: TaskRecord | None = None


@dataclass(slots=True)
class TaskPage:
    items: list[TaskRecord]
    page: int
    page_size: int
    total_count: int
    total_pages: int


@dataclass(slots=True)
class PendingApprovalRecord:
    task_id: int
    approval_id: int
    requested_action: str
    task_summary: str | None
    codex_session_id: str | None


@dataclass(slots=True)
class MemorySnapshot:
    notes: list[str]
    recent_task_summaries: list[str]
    project_summary: str | None
    page: int = 1
    page_size: int = 5
    total_notes: int = 0
    total_task_summaries: int = 0

    @property
    def total_pages(self) -> int:
        note_pages = max(1, (self.total_notes + self.page_size - 1) // self.page_size)
        task_pages = max(1, (self.total_task_summaries + self.page_size - 1) // self.page_size)
        return max(note_pages, task_pages)


class TaskStore:
    """Encapsulates SQLite operations for users, projects, and tasks."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.chat_state = ChatStateStore(db, record_project_use=self._record_project_use)
        self.autopilot = AutopilotStore(db)
        self.approvals = ApprovalStore(db, insert_task_event=self._insert_task_event)
        self.schedules = ScheduleStore(db)
        self.project_state = ProjectStateStore(db)
        self.runtime = TaskRuntimeStore(db)
        self.system_notifications = SystemNotificationStore(db)

    def sync_projects_from_registry(self, projects: ProjectRegistry) -> None:
        """Mirror YAML registry entries into SQLite project tables."""

        connection = self.db.get_connection()
        for project in projects.projects.values():
            connection.execute(
                """
                INSERT INTO projects (
                    project_key, name, path, default_branch, test_command, dev_command, description, stack_summary, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_key) DO UPDATE SET
                    name = excluded.name,
                    path = excluded.path,
                    default_branch = excluded.default_branch,
                    test_command = excluded.test_command,
                    dev_command = excluded.dev_command,
                    description = excluded.description,
                    stack_summary = excluded.stack_summary,
                    is_active = excluded.is_active,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    project.key,
                    project.name,
                    str(project.path),
                    project.default_branch,
                    project.test_command,
                    project.dev_command,
                    project.description,
                    None,
                    1 if project.is_active else 0,
                ),
            )
            project_id = self.get_project_id(project.key)
            connection.execute("DELETE FROM project_allowed_directories WHERE project_id = ?", (project_id,))
            for allowed_directory in project.allowed_directories or [project.path]:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO project_allowed_directories (project_id, directory_path)
                    VALUES (?, ?)
                    """,
                    (project_id, str(allowed_directory)),
                )
            connection.execute(
                "INSERT OR IGNORE INTO project_state (project_id) VALUES (?)",
                (project_id,),
            )
            self.project_state.seed_project_memory(connection, project_id=project_id, project=project)
        connection.commit()

    def ensure_user(self, ctx: CommandContext) -> UserRecord:
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO users (telegram_user_id, telegram_username, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                telegram_username = excluded.telegram_username,
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (ctx.telegram_user_id, ctx.telegram_username, ctx.telegram_display_name),
        )
        row = connection.execute(
            "SELECT id, telegram_user_id FROM users WHERE telegram_user_id = ?",
            (ctx.telegram_user_id,),
        ).fetchone()
        connection.commit()
        if row is None:
            raise RuntimeError("Failed to load user row after upsert.")
        return UserRecord(id=int(row["id"]), telegram_user_id=str(row["telegram_user_id"]))

    # Chat-scoped state facade.
    def set_active_project(self, user_id: int, project_key: str, chat_id: str | None = None) -> None:
        self.chat_state.set_active_project(user_id, project_key, chat_id)

    def get_active_project_key(self, user_id: int, chat_id: str | None = None) -> str | None:
        return self.chat_state.get_active_project_key(user_id, chat_id)

    def clear_active_project(self, user_id: int, chat_id: str | None = None) -> None:
        self.chat_state.clear_active_project(user_id, chat_id)

    def get_chat_wizard_state(self, *, chat_id: str) -> dict[str, Any] | None:
        return self.chat_state.get_chat_wizard_state(chat_id=chat_id)

    def set_chat_wizard_state(self, *, chat_id: str, user_id: int, state: dict[str, Any]) -> None:
        self.chat_state.set_chat_wizard_state(chat_id=chat_id, user_id=user_id, state=state)

    def clear_chat_wizard_state(self, *, chat_id: str) -> None:
        self.chat_state.clear_chat_wizard_state(chat_id=chat_id)

    def get_chat_ui_mode(self, *, chat_id: str) -> str | None:
        return self.chat_state.get_chat_ui_mode(chat_id=chat_id)

    def set_chat_ui_mode(self, *, chat_id: str, user_id: int, mode: str) -> None:
        self.chat_state.set_chat_ui_mode(chat_id=chat_id, user_id=user_id, mode=mode)

    def clear_chat_ui_mode(self, *, chat_id: str) -> None:
        self.chat_state.clear_chat_ui_mode(chat_id=chat_id)

    def get_chat_codex_model(self, *, chat_id: str) -> str | None:
        return self.chat_state.get_chat_codex_model(chat_id=chat_id)

    def set_chat_codex_model(self, *, chat_id: str, user_id: int, model: str) -> None:
        self.chat_state.set_chat_codex_model(chat_id=chat_id, user_id=user_id, model=model)

    def clear_chat_codex_model(self, *, chat_id: str) -> None:
        self.chat_state.clear_chat_codex_model(chat_id=chat_id)

    def get_recent_outbound_message_id(
        self,
        *,
        chat_id: str,
        dedup_key: str,
        max_age_seconds: float,
    ) -> str | None:
        return self.chat_state.get_recent_outbound_message_id(
            chat_id=chat_id,
            dedup_key=dedup_key,
            max_age_seconds=max_age_seconds,
        )

    def remember_outbound_message(
        self,
        *,
        chat_id: str,
        dedup_key: str,
        context: str,
        message_id: str | None,
    ) -> None:
        self.chat_state.remember_outbound_message(
            chat_id=chat_id,
            dedup_key=dedup_key,
            context=context,
            message_id=message_id,
        )

    def get_recent_outbound_message_id_by_context(
        self,
        *,
        chat_id: str,
        context: str,
        max_age_seconds: float,
    ) -> str | None:
        return self.chat_state.get_recent_outbound_message_id_by_context(
            chat_id=chat_id,
            context=context,
            max_age_seconds=max_age_seconds,
        )

    def queue_system_notification(
        self,
        *,
        chat_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        collapse_existing: bool = True,
    ) -> None:
        self.system_notifications.queue_notification(
            chat_id=chat_id,
            kind=kind,
            payload=payload,
            collapse_existing=collapse_existing,
        )

    def list_pending_system_notifications(self, *, limit: int = 32) -> list[SystemNotificationRecord]:
        return self.system_notifications.list_pending_notifications(limit=limit)

    def delete_system_notification(self, *, notification_id: int) -> None:
        self.system_notifications.delete_notification(notification_id=notification_id)

    # User/project registry helpers.
    def list_recent_project_keys(self, *, user_id: int, limit: int = 6) -> list[str]:
        connection = self.db.get_connection()
        try:
            rows = connection.execute(
                """
                SELECT upa.project_key
                FROM user_project_activity upa
                JOIN projects p
                  ON p.project_key = upa.project_key
                WHERE upa.user_id = ?
                  AND p.is_active = 1
                ORDER BY upa.is_pinned DESC, upa.last_used_at DESC, upa.updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            logger.debug("user_project_activity table not available when listing recent projects.")
            return []
        return [str(row["project_key"]) for row in rows if row["project_key"]]

    def get_project_id(self, project_key: str) -> int:
        row = self.db.get_connection().execute(
            "SELECT id FROM projects WHERE project_key = ?",
            (project_key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Project not found in database: {project_key}")
        return int(row["id"])

    def get_project_key_by_id(self, project_id: int) -> str | None:
        row = self.db.get_connection().execute(
            "SELECT project_key FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            return None
        value = row["project_key"]
        return str(value) if value else None

    # Task lifecycle facade.
    def create_task(
        self,
        *,
        user_id: int,
        project_id: int,
        chat_id: str,
        message_id: str | None,
        command_type: str,
        original_request: str,
    ) -> int:
        return self.runtime.create_task(
            user_id=user_id,
            project_id=project_id,
            chat_id=chat_id,
            message_id=message_id,
            command_type=command_type,
            original_request=original_request,
        )

    def mark_task_running(self, task_id: int) -> None:
        self.runtime.mark_task_running(task_id)

    def finalize_task(
        self,
        *,
        task_id: int,
        status: str,
        summary: str,
        error: str | None,
        codex_session_id: str | None,
        requires_approval: bool = False,
        pending_approval_action: str | None = None,
    ) -> None:
        self.runtime.finalize_task(
            task_id=task_id,
            status=status,
            summary=summary,
            error=error,
            codex_session_id=codex_session_id,
            requires_approval=requires_approval,
            pending_approval_action=pending_approval_action,
        )

    def recover_interrupted_tasks(self) -> list[int]:
        """Fail stale in-flight tasks after a service restart."""

        return self.runtime.recover_interrupted_tasks()

    def add_task_artifact(
        self,
        task_id: int,
        artifact_type: str,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.runtime.add_task_artifact(
            task_id,
            artifact_type,
            content=content,
            metadata=metadata,
        )

    # Project runtime/memory facade.
    def update_project_state_after_task(
        self,
        *,
        project_id: int,
        task_id: int,
        summary: str,
        codex_session_id: str | None,
        pending_approval_task_id: int | None,
        next_step: str | None = None,
    ) -> None:
        self.project_state.update_project_state_after_task(
            project_id=project_id,
            task_id=task_id,
            summary=summary,
            codex_session_id=codex_session_id,
            pending_approval_task_id=pending_approval_task_id,
            next_step=next_step,
        )

    def update_repo_state(self, *, project_id: int, branch: str | None, repo_dirty: bool | None) -> None:
        self.project_state.update_repo_state(project_id=project_id, branch=branch, repo_dirty=repo_dirty)

    def clear_project_session_state(self, *, project_id: int) -> None:
        """Clear resumable session/runtime pointers for a project."""

        self.project_state.clear_project_session_state(project_id=project_id)

    def get_project_last_codex_session_id(self, *, project_id: int) -> str | None:
        return self.project_state.get_last_codex_session_id(project_id=project_id)

    def bind_project_session(
        self,
        *,
        project_id: int,
        codex_session_id: str,
        next_step: str | None = None,
    ) -> None:
        self.project_state.bind_project_session(
            project_id=project_id,
            codex_session_id=codex_session_id,
            next_step=next_step,
        )

    def get_status_snapshot(self, user_id: int, chat_id: str | None = None) -> StatusSnapshot:
        active_project_key = self.get_active_project_key(user_id=user_id, chat_id=chat_id)
        if active_project_key is None:
            return StatusSnapshot(
                active_project_key=None,
                active_project_name=None,
                project_path=None,
                current_branch=None,
                repo_dirty=None,
                last_codex_session_id=None,
                most_recent_task_summary=None,
                recent_failed_summary=None,
                pending_approval=False,
                pending_approval_id=None,
                next_schedule_id=None,
                next_schedule_hhmm=None,
                next_step=None,
            )

        row = self.project_state.get_project_status_row(active_project_key=active_project_key)

        if row is None:
            return StatusSnapshot(
                active_project_key=None,
                active_project_name=None,
                project_path=None,
                current_branch=None,
                repo_dirty=None,
                last_codex_session_id=None,
                most_recent_task_summary=None,
                recent_failed_summary=None,
                pending_approval=False,
                pending_approval_id=None,
                next_schedule_id=None,
                next_schedule_hhmm=None,
                next_step=None,
            )

        repo_dirty_raw = row["repo_dirty"]
        repo_dirty = None if repo_dirty_raw is None else bool(int(repo_dirty_raw))
        project_id = int(row["project_id"])

        next_schedule_id: int | None = None
        next_schedule_hhmm: str | None = None
        try:
            next_schedule_row = self.project_state.get_next_schedule_row(project_id=project_id)
            if next_schedule_row is not None:
                next_schedule_id = int(next_schedule_row["id"])
                next_schedule_hhmm = self._minute_to_hhmm(int(next_schedule_row["minute_of_day"]))
        except sqlite3.OperationalError:
            logger.debug("scheduled_tasks table not available when loading status snapshot.")

        failed_row = self.project_state.get_recent_failed_task_row(project_id=project_id)
        recent_failed_summary = None
        if failed_row is not None:
            recent_failed_summary = (
                str(failed_row["latest_error"])
                if failed_row["latest_error"]
                else (str(failed_row["latest_summary"]) if failed_row["latest_summary"] else None)
            )
        pending_approval = self.get_pending_approval(project_id)
        active_task = self.get_latest_active_task(project_id)

        return StatusSnapshot(
            active_project_key=active_project_key,
            active_project_name=str(row["active_project_name"]) if row["active_project_name"] else None,
            project_path=str(row["project_path"]) if row["project_path"] else None,
            current_branch=str(row["current_branch"]) if row["current_branch"] else None,
            repo_dirty=repo_dirty,
            last_codex_session_id=(
                str(row["last_codex_session_id"]) if row["last_codex_session_id"] else None
            ),
            most_recent_task_summary=(
                str(row["last_task_summary"]) if row["last_task_summary"] else None
            ),
            recent_failed_summary=recent_failed_summary,
            pending_approval=pending_approval is not None or row["pending_approval_task_id"] is not None,
            pending_approval_id=pending_approval.approval_id if pending_approval is not None else None,
            next_schedule_id=next_schedule_id,
            next_schedule_hhmm=next_schedule_hhmm,
            next_step=str(row["next_step"]) if row["next_step"] else None,
            active_task=active_task,
        )

    # Task lookup and approval orchestration facade.
    def get_latest_task(self, project_id: int) -> TaskRecord | None:
        row = self.runtime.get_latest_task_row(project_id)
        return self._row_to_task(row)

    def list_tasks(self, *, project_id: int, page: int = 1, page_size: int = 10) -> TaskPage:
        normalized_page_size = max(1, int(page_size))
        total_count = self.runtime.count_tasks(project_id)
        total_pages = max(1, (total_count + normalized_page_size - 1) // normalized_page_size)
        normalized_page = min(max(1, int(page)), total_pages)
        offset = (normalized_page - 1) * normalized_page_size
        rows = self.runtime.list_task_rows(
            project_id=project_id,
            limit=normalized_page_size,
            offset=offset,
        )
        return TaskPage(
            items=[item for item in (self._row_to_task(row) for row in rows) if item is not None],
            page=normalized_page,
            page_size=normalized_page_size,
            total_count=total_count,
            total_pages=total_pages,
        )

    def get_latest_resumable_task(self, project_id: int) -> TaskRecord | None:
        row = self.runtime.get_latest_resumable_task_row(project_id)
        return self._row_to_task(row)

    def get_latest_active_task(self, project_id: int) -> TaskRecord | None:
        row = self.runtime.get_latest_active_task_row(project_id)
        return self._row_to_task(row)

    def cancel_latest_active_task(self, project_id: int) -> TaskRecord | None:
        row = self.runtime.get_latest_active_task_row(project_id)
        task = self._row_to_task(row)
        if task is None:
            return None
        return self.cancel_task(task_id=task.id, project_id=project_id)

    def cancel_task(self, *, task_id: int, project_id: int) -> TaskRecord | None:
        task = self.get_task_for_project(task_id=task_id, project_id=project_id)
        if task is None or task.status not in {"created", "running", "waiting_approval"}:
            return None

        if task.status == "waiting_approval":
            self.approvals.cancel_pending_for_task(task_id=task.id)

        cancel_summary = "任务已取消。"
        self.finalize_task(
            task_id=task.id,
            status="cancelled",
            summary=cancel_summary,
            error=None,
            codex_session_id=task.codex_session_id,
        )
        return TaskRecord(
            id=task.id,
            command_type=task.command_type,
            original_request=task.original_request,
            status="cancelled",
            codex_session_id=task.codex_session_id,
            latest_summary=cancel_summary,
        )

    def delete_task(self, *, task_id: int, project_id: int) -> TaskRecord | None:
        task = self.get_task_for_project(task_id=task_id, project_id=project_id)
        if task is None or task.status in {"created", "running", "waiting_approval"}:
            return None
        self.project_state.clear_deleted_task_references(project_id=project_id, task_id=task_id)
        if not self.runtime.delete_task(task_id=task_id, project_id=project_id):
            return None
        return task

    def clear_tasks(self, *, project_id: int) -> int:
        if self.get_latest_active_task(project_id) is None:
            self.project_state.clear_task_references(project_id=project_id)
        deleted_count = self.runtime.delete_terminal_tasks(project_id=project_id)
        self.project_state.clear_missing_task_references(project_id=project_id)
        return deleted_count

    def get_task(self, task_id: int) -> TaskRecord | None:
        row = self.runtime.get_task_row(task_id)
        return self._row_to_task(row)

    def get_task_for_project(self, *, task_id: int, project_id: int) -> TaskRecord | None:
        row = self.runtime.get_task_for_project_row(task_id=task_id, project_id=project_id)
        return self._row_to_task(row)

    def get_pending_approval(self, project_id: int, approval_id: int | None = None) -> PendingApprovalRecord | None:
        row = self.approvals.get_pending_approval_row(project_id, approval_id=approval_id)
        if row is None:
            return None
        return PendingApprovalRecord(
            task_id=int(row["task_id"]),
            approval_id=int(row["approval_id"]),
            requested_action=str(row["requested_action"]),
            task_summary=str(row["latest_summary"]) if row["latest_summary"] else None,
            codex_session_id=str(row["codex_session_id"]) if row["codex_session_id"] else None,
        )

    def create_approval_request(
        self,
        *,
        task_id: int,
        requested_action: str,
        requested_by_user_id: int,
        approval_kind: str = "codex_action",
    ) -> int:
        return self.approvals.create_approval_request(
            task_id=task_id,
            requested_action=requested_action,
            requested_by_user_id=requested_by_user_id,
            approval_kind=approval_kind,
            event_type=audit_events.APPROVAL_REQUESTED,
        )

    def resolve_approval(
        self,
        *,
        approval_id: int,
        status: str,
        decided_by_user_id: int,
        decision_note: str | None,
    ) -> bool:
        return self.approvals.resolve_approval(
            approval_id=approval_id,
            status=status,
            decided_by_user_id=decided_by_user_id,
            decision_note=decision_note,
        )

    def mark_task_waiting_approval(
        self,
        *,
        task_id: int,
        summary: str,
        pending_action: str,
        codex_session_id: str | None,
    ) -> None:
        self.finalize_task(
            task_id=task_id,
            status="waiting_approval",
            summary=summary,
            error=None,
            codex_session_id=codex_session_id,
            requires_approval=True,
            pending_approval_action=pending_action,
        )

    def mark_task_resumed_after_approval(self, task_id: int) -> None:
        self.runtime.mark_task_resumed_after_approval(task_id)

    def reject_task(self, *, task_id: int, summary: str) -> None:
        self.finalize_task(
            task_id=task_id,
            status="rejected",
            summary=summary,
            error=None,
            codex_session_id=None,
            requires_approval=False,
            pending_approval_action=None,
        )

    # Project memory facade.
    def add_project_note(self, *, project_id: int, content: str, title: str | None = None) -> None:
        self.project_state.add_project_note(project_id=project_id, content=content, title=title)

    def get_memory_snapshot(
        self,
        *,
        project_id: int,
        page: int = 1,
        page_size: int = 5,
    ) -> MemorySnapshot:
        payload = self.project_state.get_memory_snapshot_data(
            project_id=project_id,
            page=page,
            page_size=page_size,
        )
        return MemorySnapshot(
            notes=list(payload["notes"]),
            recent_task_summaries=list(payload["recent_task_summaries"]),
            project_summary=payload["project_summary"],
            page=int(payload["page"]),
            page_size=int(payload["page_size"]),
            total_notes=int(payload["total_notes"]),
            total_task_summaries=int(payload["total_task_summaries"]),
        )

    # Schedule facade.
    def create_scheduled_task(
        self,
        *,
        user_id: int,
        project_id: int,
        chat_id: str,
        command_type: str,
        request_text: str,
        minute_of_day: int,
    ) -> int:
        return self.schedules.create_scheduled_task(
            user_id=user_id,
            project_id=project_id,
            chat_id=chat_id,
            command_type=command_type,
            request_text=request_text,
            minute_of_day=minute_of_day,
        )

    def list_scheduled_tasks(self, project_id: int) -> list[ScheduledTaskRecord]:
        return self.schedules.list_scheduled_tasks(project_id)

    def get_scheduled_task(self, *, schedule_id: int, project_id: int) -> ScheduledTaskRecord | None:
        return self.schedules.get_scheduled_task(schedule_id=schedule_id, project_id=project_id)

    def set_scheduled_task_enabled(
        self,
        *,
        schedule_id: int,
        project_id: int,
        enabled: bool,
    ) -> bool:
        return self.schedules.set_scheduled_task_enabled(
            schedule_id=schedule_id,
            project_id=project_id,
            enabled=enabled,
        )

    def delete_scheduled_task(self, *, schedule_id: int, project_id: int) -> bool:
        return self.schedules.delete_scheduled_task(schedule_id=schedule_id, project_id=project_id)

    def claim_due_scheduled_tasks(
        self,
        *,
        minute_of_day: int,
        trigger_date: str,
        include_missed_before: bool = False,
        limit: int = 10,
    ) -> list[ScheduledTaskRecord]:
        return self.schedules.claim_due_scheduled_tasks(
            minute_of_day=minute_of_day,
            trigger_date=trigger_date,
            include_missed_before=include_missed_before,
            limit=limit,
        )

    def record_scheduled_task_run(
        self,
        *,
        schedule_id: int,
        task_id: int | None,
        status: str,
        summary: str,
    ) -> None:
        self.schedules.record_scheduled_task_run(
            schedule_id=schedule_id,
            task_id=task_id,
            status=status,
            summary=summary,
        )

    # Shared local helpers for facade composition.
    def _row_to_task(self, row: sqlite3.Row | None) -> TaskRecord | None:
        if row is None:
            return None
        return TaskRecord(
            id=int(row["id"]),
            command_type=str(row["command_type"]),
            original_request=str(row["original_request"]),
            status=str(row["status"]),
            codex_session_id=str(row["codex_session_id"]) if row["codex_session_id"] else None,
            latest_summary=str(row["latest_summary"]) if row["latest_summary"] else None,
        )

    def _minute_to_hhmm(self, minute_of_day: int) -> str:
        hour = minute_of_day // 60
        minute = minute_of_day % 60
        return f"{hour:02d}:{minute:02d}"

    def _record_project_use(self, connection: sqlite3.Connection, *, user_id: int, project_key: str) -> None:
        try:
            connection.execute(
                """
                INSERT INTO user_project_activity (user_id, project_key, use_count, last_used_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, project_key) DO UPDATE SET
                    use_count = user_project_activity.use_count + 1,
                    last_used_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, project_key),
            )
        except sqlite3.OperationalError:
            logger.debug("user_project_activity table not available when recording project use.")

    def _insert_task_event(
        self,
        connection: sqlite3.Connection,
        task_id: int,
        event_type: str,
        event_summary: str,
    ) -> None:
        self.runtime.insert_task_event(connection, task_id, event_type, event_summary)

    def _status_to_event_type(self, status: str) -> str:
        return self.runtime._status_to_event_type(status)

    def _normalize_task_event_type(self, event_type: str) -> str:
        return self.runtime._normalize_task_event_type(event_type)
