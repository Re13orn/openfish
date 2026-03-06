"""Persistence helpers for project continuity and task lifecycle."""

from dataclasses import dataclass
import json
import logging
import sqlite3
from typing import Any
from uuid import uuid4

from src import audit_events
from src.db import Database
from src.models import CommandContext, ProjectConfig, UserRecord
from src.project_registry import ProjectRegistry


logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class TaskRecord:
    id: int
    command_type: str
    original_request: str
    status: str
    codex_session_id: str | None
    latest_summary: str | None


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


@dataclass(slots=True)
class ScheduledTaskRecord:
    id: int
    user_id: int
    project_id: int
    telegram_chat_id: str
    command_type: str
    request_text: str
    minute_of_day: int
    enabled: bool
    last_triggered_on: str | None
    last_task_id: int | None
    last_run_status: str | None
    last_run_summary: str | None


class TaskStore:
    """Encapsulates SQLite operations for users, projects, and tasks."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
            self._seed_project_memory(connection, project_id=project_id, project=project)
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

    def set_active_project(self, user_id: int, project_key: str, chat_id: str | None = None) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO user_preferences (user_id, default_project_key)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                default_project_key = excluded.default_project_key,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, project_key),
        )
        if chat_id:
            try:
                connection.execute(
                    """
                    INSERT INTO chat_context (telegram_chat_id, user_id, active_project_key)
                    VALUES (?, ?, ?)
                    ON CONFLICT(telegram_chat_id) DO UPDATE SET
                        user_id = excluded.user_id,
                        active_project_key = excluded.active_project_key,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (chat_id, user_id, project_key),
                )
            except sqlite3.OperationalError:
                logger.debug("chat_context table not available; fallback to user_preferences only.")
        self._record_project_use(connection, user_id=user_id, project_key=project_key)
        connection.commit()

    def get_active_project_key(self, user_id: int, chat_id: str | None = None) -> str | None:
        connection = self.db.get_connection()
        if chat_id:
            try:
                row = connection.execute(
                    """
                    SELECT active_project_key
                    FROM chat_context
                    WHERE user_id = ? AND telegram_chat_id = ?
                    """,
                    (user_id, chat_id),
                ).fetchone()
                if row and row["active_project_key"]:
                    return str(row["active_project_key"])
            except sqlite3.OperationalError:
                logger.debug("chat_context table not available; fallback to user_preferences only.")

        row = connection.execute(
            "SELECT default_project_key FROM user_preferences WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        value = row["default_project_key"]
        return str(value) if value else None

    def clear_active_project(self, user_id: int, chat_id: str | None = None) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE user_preferences
            SET default_project_key = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (user_id,),
        )
        if chat_id:
            try:
                connection.execute(
                    """
                    UPDATE chat_context
                    SET active_project_key = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ? AND telegram_chat_id = ?
                    """,
                    (user_id, chat_id),
                )
            except sqlite3.OperationalError:
                logger.debug("chat_context table not available when clearing active project.")
        connection.commit()

    def get_chat_wizard_state(self, *, chat_id: str) -> dict[str, Any] | None:
        connection = self.db.get_connection()
        try:
            row = connection.execute(
                """
                SELECT pending_flow_json
                FROM chat_context
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.debug("pending_flow_json column not available when loading chat wizard state.")
            return None
        if row is None or not row["pending_flow_json"]:
            return None
        try:
            payload = json.loads(str(row["pending_flow_json"]))
        except json.JSONDecodeError:
            logger.warning("Invalid pending_flow_json for chat_id=%s", chat_id)
            return None
        return payload if isinstance(payload, dict) else None

    def set_chat_wizard_state(self, *, chat_id: str, user_id: int, state: dict[str, Any]) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                INSERT INTO chat_context (telegram_chat_id, user_id, pending_flow_json)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    pending_flow_json = excluded.pending_flow_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, user_id, json.dumps(state, ensure_ascii=True)),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("pending_flow_json column not available when storing chat wizard state.")

    def clear_chat_wizard_state(self, *, chat_id: str) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                UPDATE chat_context
                SET pending_flow_json = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("pending_flow_json column not available when clearing chat wizard state.")

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
        task_uuid = str(uuid4())
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO tasks (
                task_uuid, user_id, project_id, telegram_chat_id, telegram_message_id, command_type,
                original_request, normalized_request, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_uuid,
                user_id,
                project_id,
                chat_id,
                message_id,
                command_type,
                original_request,
                original_request.strip(),
                "created",
            ),
        )
        task_id = int(cursor.lastrowid)
        self._insert_task_event(
            connection,
            task_id,
            audit_events.TASK_CREATED,
            f"已创建任务（/{command_type}）",
        )
        connection.commit()
        return task_id

    def mark_task_running(self, task_id: int) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE tasks
            SET status = 'running',
                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (task_id,),
        )
        self._insert_task_event(connection, task_id, audit_events.TASK_STARTED, "任务开始执行")
        connection.commit()

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
        connection = self.db.get_connection()
        completion_time = "CURRENT_TIMESTAMP" if status in {"completed", "failed", "cancelled"} else "NULL"
        connection.execute(
            f"""
            UPDATE tasks
            SET status = ?,
                latest_summary = ?,
                latest_error = ?,
                codex_session_id = COALESCE(?, codex_session_id),
                requires_approval = ?,
                pending_approval_action = ?,
                completed_at = {completion_time},
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status,
                summary,
                error,
                codex_session_id,
                1 if requires_approval else 0,
                pending_approval_action,
                task_id,
            ),
        )
        event_type = self._status_to_event_type(status)
        self._insert_task_event(connection, task_id, event_type, summary[:250])
        connection.commit()

    def add_task_artifact(
        self,
        task_id: int,
        artifact_type: str,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO task_artifacts (task_id, artifact_type, content, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                task_id,
                artifact_type,
                content,
                json.dumps(metadata) if metadata else None,
            ),
        )
        connection.commit()

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
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE project_state
            SET last_task_id = ?,
                last_task_summary = ?,
                last_codex_session_id = COALESCE(?, last_codex_session_id),
                pending_approval_task_id = ?,
                next_step = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (task_id, summary, codex_session_id, pending_approval_task_id, next_step, project_id),
        )
        connection.commit()

    def update_repo_state(self, *, project_id: int, branch: str | None, repo_dirty: bool | None) -> None:
        connection = self.db.get_connection()
        repo_dirty_value = None if repo_dirty is None else (1 if repo_dirty else 0)
        connection.execute(
            """
            UPDATE project_state
            SET current_branch = ?,
                repo_dirty = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (branch, repo_dirty_value, project_id),
        )
        connection.commit()

    def clear_project_session_state(self, *, project_id: int) -> None:
        """Clear resumable session/runtime pointers for a project."""

        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE project_state
            SET last_codex_session_id = NULL,
                last_task_id = NULL,
                last_task_summary = NULL,
                last_test_command = NULL,
                last_test_status = NULL,
                last_test_summary = NULL,
                pending_approval_task_id = NULL,
                next_step = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (project_id,),
        )
        connection.commit()

    def get_status_snapshot(self, user_id: int, chat_id: str | None = None) -> StatusSnapshot:
        connection = self.db.get_connection()
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
                next_schedule_id=None,
                next_schedule_hhmm=None,
                next_step=None,
            )

        row = connection.execute(
            """
            SELECT
                p.id AS project_id,
                p.name AS active_project_name,
                p.path AS project_path,
                ps.current_branch,
                ps.repo_dirty,
                ps.last_codex_session_id,
                ps.last_task_summary,
                ps.pending_approval_task_id,
                ps.next_step
            FROM projects p
            LEFT JOIN project_state ps
                ON ps.project_id = p.id
            WHERE p.project_key = ?
            """,
            (active_project_key,),
        ).fetchone()

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
            next_schedule_row = connection.execute(
                """
                SELECT id, minute_of_day
                FROM scheduled_tasks
                WHERE project_id = ?
                  AND enabled = 1
                ORDER BY minute_of_day ASC, id ASC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if next_schedule_row is not None:
                next_schedule_id = int(next_schedule_row["id"])
                next_schedule_hhmm = self._minute_to_hhmm(int(next_schedule_row["minute_of_day"]))
        except sqlite3.OperationalError:
            logger.debug("scheduled_tasks table not available when loading status snapshot.")

        failed_row = connection.execute(
            """
            SELECT latest_error, latest_summary
            FROM tasks
            WHERE project_id = ?
              AND status = 'failed'
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        recent_failed_summary = None
        if failed_row is not None:
            recent_failed_summary = (
                str(failed_row["latest_error"])
                if failed_row["latest_error"]
                else (str(failed_row["latest_summary"]) if failed_row["latest_summary"] else None)
            )

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
            pending_approval=row["pending_approval_task_id"] is not None,
            next_schedule_id=next_schedule_id,
            next_schedule_hhmm=next_schedule_hhmm,
            next_step=str(row["next_step"]) if row["next_step"] else None,
        )

    def get_latest_task(self, project_id: int) -> TaskRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE project_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        return self._row_to_task(row)

    def get_latest_resumable_task(self, project_id: int) -> TaskRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE project_id = ?
              AND status NOT IN ('cancelled', 'rejected')
              AND (codex_session_id IS NOT NULL OR status IN ('created', 'running', 'failed', 'completed'))
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        return self._row_to_task(row)

    def cancel_latest_active_task(self, project_id: int) -> TaskRecord | None:
        connection = self.db.get_connection()
        row = connection.execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE project_id = ?
              AND status IN ('created', 'running', 'waiting_approval')
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        task = self._row_to_task(row)
        if task is None:
            return None

        if task.status == "waiting_approval":
            connection.execute(
                """
                UPDATE approvals
                SET status = 'cancelled',
                    decision_note = 'Cancelled by user',
                    decided_at = CURRENT_TIMESTAMP
                WHERE task_id = ?
                  AND status = 'pending'
                """,
                (task.id,),
            )

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

    def get_task(self, task_id: int) -> TaskRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        return self._row_to_task(row)

    def get_task_for_project(self, *, task_id: int, project_id: int) -> TaskRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE id = ?
              AND project_id = ?
            """,
            (task_id, project_id),
        ).fetchone()
        return self._row_to_task(row)

    def get_pending_approval(self, project_id: int) -> PendingApprovalRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT
                a.id AS approval_id,
                a.task_id AS task_id,
                a.requested_action,
                t.latest_summary,
                t.codex_session_id
            FROM approvals a
            JOIN tasks t ON t.id = a.task_id
            WHERE t.project_id = ?
              AND a.status = 'pending'
              AND t.status = 'waiting_approval'
            ORDER BY a.id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
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
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO approvals (task_id, approval_kind, requested_action, requested_by_user_id, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (task_id, approval_kind, requested_action, requested_by_user_id),
        )
        self._insert_task_event(
            connection,
            task_id,
            audit_events.APPROVAL_REQUESTED,
            requested_action[:250],
        )
        connection.commit()
        return int(cursor.lastrowid)

    def resolve_approval(
        self,
        *,
        approval_id: int,
        status: str,
        decided_by_user_id: int,
        decision_note: str | None,
    ) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE approvals
            SET status = ?, decision_note = ?, decided_by_user_id = ?, decided_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, decision_note, decided_by_user_id, approval_id),
        )
        connection.commit()

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
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE tasks
            SET status = 'running',
                requires_approval = 0,
                pending_approval_action = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (task_id,),
        )
        self._insert_task_event(
            connection,
            task_id,
            audit_events.TASK_APPROVAL_RESUMED,
            "审批通过后继续执行",
        )
        connection.commit()

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

    def add_project_note(self, *, project_id: int, content: str, title: str | None = None) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO project_memory (project_id, memory_type, title, content, source, is_pinned)
            VALUES (?, 'owner_note', ?, ?, 'telegram_note', 0)
            """,
            (project_id, title, content),
        )
        connection.commit()

    def get_memory_snapshot(self, *, project_id: int, note_limit: int = 5, task_limit: int = 3) -> MemorySnapshot:
        connection = self.db.get_connection()
        note_rows = connection.execute(
            """
            SELECT content
            FROM project_memory
            WHERE project_id = ?
              AND memory_type = 'owner_note'
            ORDER BY id DESC
            LIMIT ?
            """,
            (project_id, note_limit),
        ).fetchall()
        task_rows = connection.execute(
            """
            SELECT latest_summary
            FROM tasks
            WHERE project_id = ?
              AND latest_summary IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (project_id, task_limit),
        ).fetchall()
        summary_row = connection.execute(
            """
            SELECT content
            FROM project_memory
            WHERE project_id = ?
              AND memory_type = 'summary'
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()
        return MemorySnapshot(
            notes=[str(row["content"]) for row in note_rows],
            recent_task_summaries=[str(row["latest_summary"]) for row in task_rows if row["latest_summary"]],
            project_summary=str(summary_row["content"]) if summary_row else None,
        )

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
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO scheduled_tasks (
                user_id, project_id, telegram_chat_id, command_type, request_text, minute_of_day, enabled
            ) VALUES (?, ?, ?, ?, ?, ?, 1)
            """,
            (user_id, project_id, chat_id, command_type, request_text, minute_of_day),
        )
        connection.commit()
        return int(cursor.lastrowid)

    def list_scheduled_tasks(self, project_id: int) -> list[ScheduledTaskRecord]:
        rows = self.db.get_connection().execute(
            """
            SELECT
                id,
                user_id,
                project_id,
                telegram_chat_id,
                command_type,
                request_text,
                minute_of_day,
                enabled,
                last_triggered_on,
                last_task_id,
                last_run_status,
                last_run_summary
            FROM scheduled_tasks
            WHERE project_id = ?
            ORDER BY minute_of_day ASC, id ASC
            """,
            (project_id,),
        ).fetchall()
        return [self._row_to_scheduled_task(row) for row in rows]

    def get_scheduled_task(self, *, schedule_id: int, project_id: int) -> ScheduledTaskRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT
                id,
                user_id,
                project_id,
                telegram_chat_id,
                command_type,
                request_text,
                minute_of_day,
                enabled,
                last_triggered_on,
                last_task_id,
                last_run_status,
                last_run_summary
            FROM scheduled_tasks
            WHERE id = ? AND project_id = ?
            """,
            (schedule_id, project_id),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_scheduled_task(row)

    def set_scheduled_task_enabled(
        self,
        *,
        schedule_id: int,
        project_id: int,
        enabled: bool,
    ) -> bool:
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            UPDATE scheduled_tasks
            SET enabled = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND project_id = ?
            """,
            (1 if enabled else 0, schedule_id, project_id),
        )
        connection.commit()
        return cursor.rowcount > 0

    def delete_scheduled_task(self, *, schedule_id: int, project_id: int) -> bool:
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            DELETE FROM scheduled_tasks
            WHERE id = ? AND project_id = ?
            """,
            (schedule_id, project_id),
        )
        connection.commit()
        return cursor.rowcount > 0

    def claim_due_scheduled_tasks(
        self,
        *,
        minute_of_day: int,
        trigger_date: str,
        include_missed_before: bool = False,
        limit: int = 10,
    ) -> list[ScheduledTaskRecord]:
        connection = self.db.get_connection()
        minute_operator = "<=" if include_missed_before else "="
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"""
                SELECT
                    id,
                    user_id,
                    project_id,
                    telegram_chat_id,
                    command_type,
                    request_text,
                    minute_of_day,
                    enabled,
                    last_triggered_on,
                    last_task_id,
                    last_run_status,
                    last_run_summary
                FROM scheduled_tasks
                WHERE enabled = 1
                  AND minute_of_day {minute_operator} ?
                  AND (last_triggered_on IS NULL OR last_triggered_on < ?)
                ORDER BY id ASC
                LIMIT ?
                """,
                (minute_of_day, trigger_date, limit),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE scheduled_tasks
                    SET last_triggered_on = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (trigger_date, int(row["id"])),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return [self._row_to_scheduled_task(row) for row in rows]

    def record_scheduled_task_run(
        self,
        *,
        schedule_id: int,
        task_id: int | None,
        status: str,
        summary: str,
    ) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE scheduled_tasks
            SET last_task_id = ?,
                last_run_status = ?,
                last_run_summary = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (task_id, status, summary[:500], schedule_id),
        )
        connection.commit()

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

    def _row_to_scheduled_task(self, row: sqlite3.Row) -> ScheduledTaskRecord:
        return ScheduledTaskRecord(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            project_id=int(row["project_id"]),
            telegram_chat_id=str(row["telegram_chat_id"]),
            command_type=str(row["command_type"]),
            request_text=str(row["request_text"]),
            minute_of_day=int(row["minute_of_day"]),
            enabled=bool(int(row["enabled"])),
            last_triggered_on=str(row["last_triggered_on"]) if row["last_triggered_on"] else None,
            last_task_id=int(row["last_task_id"]) if row["last_task_id"] is not None else None,
            last_run_status=str(row["last_run_status"]) if row["last_run_status"] else None,
            last_run_summary=str(row["last_run_summary"]) if row["last_run_summary"] else None,
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
        normalized_event_type = self._normalize_task_event_type(event_type)
        normalized_summary = event_summary
        if normalized_event_type != event_type:
            normalized_summary = f"[invalid_event:{event_type}] {event_summary}"
            logger.warning("Unknown task event type: %s", event_type)

        connection.execute(
            """
            INSERT INTO task_events (task_id, event_type, event_summary)
            VALUES (?, ?, ?)
            """,
            (task_id, normalized_event_type, normalized_summary[:250]),
        )

    def _status_to_event_type(self, status: str) -> str:
        mapping = {
            "created": audit_events.TASK_CREATED,
            "running": audit_events.TASK_STARTED,
            "waiting_approval": audit_events.TASK_WAITING_APPROVAL,
            "completed": audit_events.TASK_COMPLETED,
            "failed": audit_events.TASK_FAILED,
            "cancelled": audit_events.TASK_CANCELLED,
            "rejected": audit_events.TASK_REJECTED,
        }
        return mapping.get(status, f"task.status.{status}")

    def _normalize_task_event_type(self, event_type: str) -> str:
        if event_type in audit_events.TASK_EVENT_TYPES:
            return event_type
        if event_type.startswith("task.status."):
            return event_type
        return audit_events.TASK_UNKNOWN_EVENT

    def _seed_project_memory(
        self,
        connection: sqlite3.Connection,
        *,
        project_id: int,
        project: ProjectConfig,
    ) -> None:
        if project.memory_seed_summary:
            existing = connection.execute(
                """
                SELECT 1
                FROM project_memory
                WHERE project_id = ?
                  AND memory_type = 'summary'
                  AND source = 'registry_seed'
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO project_memory (project_id, memory_type, title, content, source, is_pinned)
                    VALUES (?, 'summary', 'Project Summary', ?, 'registry_seed', 1)
                    """,
                    (project_id, project.memory_seed_summary),
                )

        for note in project.seed_notes or []:
            duplicate = connection.execute(
                """
                SELECT 1
                FROM project_memory
                WHERE project_id = ?
                  AND memory_type = 'owner_note'
                  AND content = ?
                  AND source = 'registry_note'
                LIMIT 1
                """,
                (project_id, note),
            ).fetchone()
            if duplicate is None:
                connection.execute(
                    """
                    INSERT INTO project_memory (project_id, memory_type, title, content, source, is_pinned)
                    VALUES (?, 'owner_note', 'Registry Note', ?, 'registry_note', 0)
                    """,
                    (project_id, note),
                )
