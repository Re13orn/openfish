"""Persistence helpers for scheduled tasks."""

from dataclasses import dataclass
import sqlite3

from src.db import Database


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


class ScheduleStore:
    """Encapsulates scheduled_tasks table operations."""

    def __init__(self, db: Database) -> None:
        self.db = db

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
