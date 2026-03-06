"""Persistence helpers for task lifecycle, events, and artifacts."""

import json
import logging
import sqlite3
from uuid import uuid4

from src import audit_events
from src.db import Database


logger = logging.getLogger(__name__)


class TaskRuntimeStore:
    """Encapsulates tasks, task_events, and task_artifacts operations."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
        self.insert_task_event(connection, task_id, audit_events.TASK_CREATED, f"已创建任务（/{command_type}）")
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
        self.insert_task_event(connection, task_id, audit_events.TASK_STARTED, "任务开始执行")
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
        self.insert_task_event(connection, task_id, event_type, summary[:250])
        connection.commit()

    def recover_interrupted_tasks(self) -> list[int]:
        connection = self.db.get_connection()
        rows = connection.execute(
            """
            SELECT id, codex_session_id
            FROM tasks
            WHERE status IN ('created', 'running')
            ORDER BY id ASC
            """
        ).fetchall()
        recovered_task_ids: list[int] = []
        if not rows:
            return recovered_task_ids

        summary = "任务已中断：OpenFish 服务重启或执行挂起，请使用 /retry 重试。"
        error = "Task interrupted before completion."
        for row in rows:
            task_id = int(row["id"])
            self.finalize_task(
                task_id=task_id,
                status="failed",
                summary=summary,
                error=error,
                codex_session_id=str(row["codex_session_id"]) if row["codex_session_id"] else None,
            )
            recovered_task_ids.append(task_id)
        return recovered_task_ids

    def add_task_artifact(
        self,
        task_id: int,
        artifact_type: str,
        *,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO task_artifacts (task_id, artifact_type, content, metadata_json)
            VALUES (?, ?, ?, ?)
            """,
            (task_id, artifact_type, content, json.dumps(metadata) if metadata else None),
        )
        connection.commit()

    def get_latest_task_row(self, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE project_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (project_id,),
        ).fetchone()

    def get_latest_resumable_task_row(self, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
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

    def get_latest_active_task_row(self, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
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

    def get_task_row(self, task_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()

    def get_task_for_project_row(self, *, task_id: int, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
            """
            SELECT id, command_type, original_request, status, codex_session_id, latest_summary
            FROM tasks
            WHERE id = ?
              AND project_id = ?
            """,
            (task_id, project_id),
        ).fetchone()

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
        self.insert_task_event(connection, task_id, audit_events.TASK_APPROVAL_RESUMED, "审批通过后继续执行")
        connection.commit()

    def insert_task_event(
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
