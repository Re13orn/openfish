"""Persistence helpers for scheduled tasks."""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sqlite3

from src.db import Database


logger = logging.getLogger(__name__)


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
    schedule_type: str = "daily"          # "daily" | "interval"
    interval_minutes: int | None = None   # only used when schedule_type == "interval"
    last_triggered_at: str | None = None  # ISO datetime; used for interval gap calc


class ScheduleStore:
    """Encapsulates scheduled_tasks table operations."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def _supports_interval_columns(self, connection: sqlite3.Connection) -> bool:
        try:
            rows = connection.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
        except sqlite3.OperationalError:
            return False
        available = {str(row["name"]) for row in rows}
        return {"schedule_type", "interval_minutes", "last_triggered_at"} <= available

    def create_scheduled_task(
        self,
        *,
        user_id: int,
        project_id: int,
        chat_id: str,
        command_type: str,
        request_text: str,
        minute_of_day: int,
        schedule_type: str = "daily",
        interval_minutes: int | None = None,
    ) -> int:
        connection = self.db.get_connection()
        if self._supports_interval_columns(connection):
            cursor = connection.execute(
                """
                INSERT INTO scheduled_tasks (
                    user_id, project_id, telegram_chat_id, command_type, request_text,
                    minute_of_day, enabled, schedule_type, interval_minutes
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    user_id,
                    project_id,
                    chat_id,
                    command_type,
                    request_text,
                    minute_of_day,
                    schedule_type,
                    interval_minutes,
                ),
            )
        else:
            if schedule_type != "daily" or interval_minutes is not None:
                raise sqlite3.OperationalError(
                    "scheduled_tasks table is missing interval scheduling columns"
                )
            cursor = connection.execute(
                """
                INSERT INTO scheduled_tasks (
                    user_id, project_id, telegram_chat_id, command_type, request_text,
                    minute_of_day, enabled
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (user_id, project_id, chat_id, command_type, request_text, minute_of_day),
            )
        connection.commit()
        return int(cursor.lastrowid)

    def list_scheduled_tasks(self, project_id: int) -> list[ScheduledTaskRecord]:
        connection = self.db.get_connection()
        try:
            if self._supports_interval_columns(connection):
                rows = connection.execute(
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
                        last_run_summary,
                        schedule_type,
                        interval_minutes,
                        last_triggered_at
                    FROM scheduled_tasks
                    WHERE project_id = ?
                    ORDER BY minute_of_day ASC, id ASC
                    """,
                    (project_id,),
                ).fetchall()
            else:
                rows = connection.execute(
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
        except sqlite3.OperationalError:
            logger.debug("scheduled_tasks table not available when listing schedules.")
            return []
        return [self._row_to_scheduled_task(row) for row in rows]

    def get_scheduled_task(self, *, schedule_id: int, project_id: int) -> ScheduledTaskRecord | None:
        connection = self.db.get_connection()
        try:
            if self._supports_interval_columns(connection):
                row = connection.execute(
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
                        last_run_summary,
                        schedule_type,
                        interval_minutes,
                        last_triggered_at
                    FROM scheduled_tasks
                    WHERE id = ? AND project_id = ?
                    """,
                    (schedule_id, project_id),
                ).fetchone()
            else:
                row = connection.execute(
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
        except sqlite3.OperationalError:
            logger.debug("scheduled_tasks table not available when loading schedule.")
            return None
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
        supports_interval = self._supports_interval_columns(connection)
        cols = (
            """
            id, user_id, project_id, telegram_chat_id, command_type, request_text,
            minute_of_day, enabled, last_triggered_on, last_task_id, last_run_status,
            last_run_summary, schedule_type, interval_minutes, last_triggered_at
            """
            if supports_interval
            else """
            id, user_id, project_id, telegram_chat_id, command_type, request_text,
            minute_of_day, enabled, last_triggered_on, last_task_id, last_run_status,
            last_run_summary
            """
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            if supports_interval:
                rows = connection.execute(
                    f"""
                    SELECT {cols}
                    FROM scheduled_tasks
                    WHERE enabled = 1
                      AND schedule_type = 'daily'
                      AND minute_of_day {minute_operator} ?
                      AND (last_triggered_on IS NULL OR last_triggered_on < ?)
                    UNION ALL
                    SELECT {cols}
                    FROM scheduled_tasks
                    WHERE enabled = 1
                      AND schedule_type = 'interval'
                      AND interval_minutes IS NOT NULL
                      AND (
                          last_triggered_at IS NULL
                          OR (CAST(strftime('%s', 'now') AS INTEGER)
                              - CAST(strftime('%s', last_triggered_at) AS INTEGER))
                             >= interval_minutes * 60
                      )
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (minute_of_day, trigger_date, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT {cols}
                    FROM scheduled_tasks
                    WHERE enabled = 1
                      AND minute_of_day {minute_operator} ?
                      AND (last_triggered_on IS NULL OR last_triggered_on < ?)
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (minute_of_day, trigger_date, limit),
                ).fetchall()
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            for row in rows:
                if supports_interval and row["schedule_type"] == "interval":
                    connection.execute(
                        """
                        UPDATE scheduled_tasks
                        SET last_triggered_at = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (now_iso, int(row["id"])),
                    )
                else:
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
            schedule_type=str(row["schedule_type"]) if "schedule_type" in row.keys() and row["schedule_type"] else "daily",
            interval_minutes=(
                int(row["interval_minutes"])
                if "interval_minutes" in row.keys() and row["interval_minutes"] is not None
                else None
            ),
            last_triggered_at=(
                str(row["last_triggered_at"])
                if "last_triggered_at" in row.keys() and row["last_triggered_at"]
                else None
            ),
        )
