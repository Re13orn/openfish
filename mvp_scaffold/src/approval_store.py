"""Persistence helpers for approval state."""

import sqlite3
from typing import Callable

from src.db import Database


class ApprovalStore:
    """Encapsulates approvals table operations."""

    def __init__(
        self,
        db: Database,
        *,
        insert_task_event: Callable[[sqlite3.Connection, int, str, str], None] | None = None,
    ) -> None:
        self.db = db
        self._insert_task_event = insert_task_event

    def get_pending_approval_row(self, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
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

    def create_approval_request(
        self,
        *,
        task_id: int,
        requested_action: str,
        requested_by_user_id: int,
        approval_kind: str = "codex_action",
        event_type: str | None = None,
    ) -> int:
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO approvals (task_id, approval_kind, requested_action, requested_by_user_id, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (task_id, approval_kind, requested_action, requested_by_user_id),
        )
        if self._insert_task_event is not None and event_type is not None:
            self._insert_task_event(connection, task_id, event_type, requested_action[:250])
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

    def cancel_pending_for_task(self, *, task_id: int, decision_note: str = "Cancelled by user") -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE approvals
            SET status = 'cancelled',
                decision_note = ?,
                decided_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
              AND status = 'pending'
            """,
            (decision_note, task_id),
        )
        connection.commit()
