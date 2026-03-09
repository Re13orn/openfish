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

    def bind_project_session(
        self,
        *,
        project_id: int,
        codex_session_id: str,
        next_step: str | None = None,
    ) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE project_state
            SET last_codex_session_id = ?,
                next_step = COALESCE(?, next_step),
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (codex_session_id, next_step, project_id),
        )
        connection.commit()

    def clear_deleted_task_references(self, *, project_id: int, task_id: int) -> None:
        connection = self.db.get_connection()
        row = connection.execute(
            """
            SELECT last_task_id, last_task_summary, pending_approval_task_id
            FROM project_state
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None:
            return
        clear_last_task = row["last_task_id"] is not None and int(row["last_task_id"]) == task_id
        clear_pending = (
            row["pending_approval_task_id"] is not None
            and int(row["pending_approval_task_id"]) == task_id
        )
        connection.execute(
            """
            UPDATE project_state
            SET last_task_id = ?,
                last_task_summary = ?,
                pending_approval_task_id = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (
                None if clear_last_task else row["last_task_id"],
                None if clear_last_task else row["last_task_summary"],
                None if clear_pending else row["pending_approval_task_id"],
                project_id,
            ),
        )
        connection.commit()

    def clear_missing_task_references(self, *, project_id: int) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE project_state
            SET last_task_id = CASE
                    WHEN last_task_id IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = project_state.last_task_id)
                    THEN NULL
                    ELSE last_task_id
                END,
                last_task_summary = CASE
                    WHEN last_task_id IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.id = project_state.last_task_id)
                    THEN NULL
                    ELSE last_task_summary
                END,
                pending_approval_task_id = CASE
                    WHEN pending_approval_task_id IS NOT NULL
                     AND NOT EXISTS (
                        SELECT 1 FROM tasks t WHERE t.id = project_state.pending_approval_task_id
                     )
                    THEN NULL
                    ELSE pending_approval_task_id
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_id = ?
            """,
            (project_id,),
        )
        connection.commit()

    def clear_task_references(self, *, project_id: int) -> None:
        connection = self.db.get_connection()
        connection.execute(
            """
            UPDATE project_state
            SET last_task_id = NULL,
                last_task_summary = NULL,
                pending_approval_task_id = NULL,
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

    def get_last_codex_session_id(self, *, project_id: int) -> str | None:
        row = self.db.get_connection().execute(
            """
            SELECT last_codex_session_id
            FROM project_state
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if row is None or not row["last_codex_session_id"]:
            return None
        return str(row["last_codex_session_id"])

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
        page: int = 1,
        page_size: int = 5,
    ) -> dict[str, Any]:
        connection = self.db.get_connection()
        requested_page = max(1, int(page))
        normalized_page_size = max(1, int(page_size))
        total_notes = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM project_memory
                WHERE project_id = ?
                  AND memory_type = 'owner_note'
                """,
                (project_id,),
            ).fetchone()[0]
        )
        total_task_summaries = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM tasks
                WHERE project_id = ?
                  AND latest_summary IS NOT NULL
                """,
                (project_id,),
            ).fetchone()[0]
        )
        total_note_pages = max(1, (total_notes + normalized_page_size - 1) // normalized_page_size)
        total_task_pages = max(
            1,
            (total_task_summaries + normalized_page_size - 1) // normalized_page_size,
        )
        normalized_page = min(requested_page, max(total_note_pages, total_task_pages))
        offset = (normalized_page - 1) * normalized_page_size
        note_rows = connection.execute(
            """
            SELECT content
            FROM project_memory
            WHERE project_id = ?
              AND memory_type = 'owner_note'
            ORDER BY id DESC
            LIMIT ?
            OFFSET ?
            """,
            (project_id, normalized_page_size, offset),
        ).fetchall()
        task_rows = connection.execute(
            """
            SELECT latest_summary
            FROM tasks
            WHERE project_id = ?
              AND latest_summary IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            OFFSET ?
            """,
            (project_id, normalized_page_size, offset),
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
            "page": normalized_page,
            "page_size": normalized_page_size,
            "total_notes": total_notes,
            "total_task_summaries": total_task_summaries,
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
