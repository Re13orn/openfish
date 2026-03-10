"""Persistence helpers for one-shot system notifications delivered after restart."""

from dataclasses import dataclass
import json
import sqlite3
from typing import Any

from src.db import Database


@dataclass(slots=True)
class SystemNotificationRecord:
    id: int
    telegram_chat_id: str
    notification_kind: str
    payload: dict[str, Any] | None


class SystemNotificationStore:
    """Queues restart/update completion notifications across process restarts."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def queue_notification(
        self,
        *,
        chat_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        collapse_existing: bool = True,
    ) -> None:
        connection = self.db.get_connection()
        if collapse_existing:
            try:
                connection.execute(
                    """
                    DELETE FROM system_notifications
                    WHERE telegram_chat_id = ? AND notification_kind = ?
                    """,
                    (chat_id, kind),
                )
            except sqlite3.OperationalError:
                return
        try:
            connection.execute(
                """
                INSERT INTO system_notifications (telegram_chat_id, notification_kind, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    chat_id,
                    kind,
                    json.dumps(payload, ensure_ascii=True) if payload is not None else None,
                ),
            )
            connection.commit()
        except sqlite3.OperationalError:
            return

    def list_pending_notifications(self, *, limit: int = 32) -> list[SystemNotificationRecord]:
        connection = self.db.get_connection()
        try:
            rows = connection.execute(
                """
                SELECT id, telegram_chat_id, notification_kind, payload_json
                FROM system_notifications
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        records: list[SystemNotificationRecord] = []
        for row in rows:
            payload = None
            if row["payload_json"]:
                try:
                    loaded = json.loads(str(row["payload_json"]))
                    payload = loaded if isinstance(loaded, dict) else None
                except json.JSONDecodeError:
                    payload = None
            records.append(
                SystemNotificationRecord(
                    id=int(row["id"]),
                    telegram_chat_id=str(row["telegram_chat_id"]),
                    notification_kind=str(row["notification_kind"]),
                    payload=payload,
                )
            )
        return records

    def delete_notification(self, *, notification_id: int) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                "DELETE FROM system_notifications WHERE id = ?",
                (notification_id,),
            )
            connection.commit()
        except sqlite3.OperationalError:
            return
