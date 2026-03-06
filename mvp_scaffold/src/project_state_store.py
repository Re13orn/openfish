"""Persistence helpers for project runtime state and project memory."""

import sqlite3
from typing import Any

from src.db import Database
from src.models import ProjectConfig


class ProjectStateStore:
    """Encapsulates project_state and project_memory table operations."""

    def __init__(self, db: Database) -> None:
        self.db = db

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

    def get_project_status_row(self, *, active_project_key: str) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
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

    def get_next_schedule_row(self, *, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
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

    def get_recent_failed_task_row(self, *, project_id: int) -> sqlite3.Row | None:
        return self.db.get_connection().execute(
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

    def get_memory_snapshot_data(
        self,
        *,
        project_id: int,
        note_limit: int = 5,
        task_limit: int = 3,
    ) -> dict[str, Any]:
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
        return {
            "notes": [str(row["content"]) for row in note_rows],
            "recent_task_summaries": [
                str(row["latest_summary"]) for row in task_rows if row["latest_summary"]
            ],
            "project_summary": str(summary_row["content"]) if summary_row else None,
        }

    def seed_project_memory(
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
