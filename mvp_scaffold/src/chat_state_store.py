"""Persistence helpers for chat-scoped UI and project state."""

import json
import logging
import sqlite3
from typing import Any, Callable

from src.db import Database


logger = logging.getLogger(__name__)


class ChatStateStore:
    """Encapsulates chat-scoped SQLite state."""

    def __init__(
        self,
        db: Database,
        *,
        record_project_use: Callable[..., None] | None = None,
    ) -> None:
        self.db = db
        self._record_project_use = record_project_use

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
        if self._record_project_use is not None:
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

    def get_chat_ui_mode(self, *, chat_id: str) -> str | None:
        connection = self.db.get_connection()
        try:
            row = connection.execute(
                """
                SELECT ui_mode
                FROM chat_context
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.debug("ui_mode column not available when loading chat UI mode.")
            return None
        if row is None or not row["ui_mode"]:
            return None
        value = str(row["ui_mode"]).strip().lower()
        if value not in {"summary", "verbose"}:
            return None
        return value

    def set_chat_ui_mode(self, *, chat_id: str, user_id: int, mode: str) -> None:
        normalized = mode.strip().lower()
        if normalized not in {"summary", "verbose"}:
            raise ValueError(f"Unsupported ui mode: {mode}")
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                INSERT INTO chat_context (telegram_chat_id, user_id, ui_mode)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    ui_mode = excluded.ui_mode,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, user_id, normalized),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("ui_mode column not available when storing chat UI mode.")
