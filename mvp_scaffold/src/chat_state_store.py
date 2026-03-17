"""Persistence helpers for chat-scoped UI and project state."""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
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
        if value not in {"summary", "verbose", "stream"}:
            return None
        return value

    def set_chat_ui_mode(self, *, chat_id: str, user_id: int, mode: str) -> None:
        normalized = mode.strip().lower()
        if normalized not in {"summary", "verbose", "stream"}:
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

    def clear_chat_ui_mode(self, *, chat_id: str) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                UPDATE chat_context
                SET ui_mode = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("ui_mode column not available when clearing chat UI mode.")

    def get_chat_codex_model(self, *, chat_id: str) -> str | None:
        connection = self.db.get_connection()
        try:
            row = connection.execute(
                """
                SELECT codex_model
                FROM chat_context
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.debug("codex_model column not available when loading chat model.")
            return None
        if row is None or not row["codex_model"]:
            return None
        value = str(row["codex_model"]).strip()
        return value or None

    def get_chat_pending_command(self, *, chat_id: str) -> str | None:
        connection = self.db.get_connection()
        try:
            row = connection.execute(
                """
                SELECT pending_command
                FROM chat_context
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.debug("pending_command column not available when loading pending command.")
            return None
        if row is None or not row["pending_command"]:
            return None
        value = str(row["pending_command"]).strip()
        return value or None

    def set_chat_pending_command(self, *, chat_id: str, user_id: int, command: str) -> None:
        normalized = command.strip()
        if not normalized:
            raise ValueError("Pending command must not be empty.")
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                INSERT INTO chat_context (telegram_chat_id, user_id, pending_command)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    pending_command = excluded.pending_command,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, user_id, normalized),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("pending_command column not available when storing pending command.")

    def clear_chat_pending_command(self, *, chat_id: str) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                UPDATE chat_context
                SET pending_command = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("pending_command column not available when clearing pending command.")

    def set_chat_codex_model(self, *, chat_id: str, user_id: int, model: str) -> None:
        normalized = model.strip()
        if not normalized:
            raise ValueError("Model must not be empty.")
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                INSERT INTO chat_context (telegram_chat_id, user_id, codex_model)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    codex_model = excluded.codex_model,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (chat_id, user_id, normalized),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("codex_model column not available when storing chat model.")

    def clear_chat_codex_model(self, *, chat_id: str) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                UPDATE chat_context
                SET codex_model = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("codex_model column not available when clearing chat model.")

    def get_recent_outbound_message_id(
        self,
        *,
        chat_id: str,
        dedup_key: str,
        max_age_seconds: float,
    ) -> str | None:
        connection = self.db.get_connection()
        try:
            row = connection.execute(
                """
                SELECT last_outbound_message_id, last_outbound_dedup_key, last_outbound_sent_at
                FROM chat_context
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.debug("chat delivery columns not available when loading outbound delivery state.")
            return None
        if row is None:
            return None
        if row["last_outbound_dedup_key"] != dedup_key or not row["last_outbound_sent_at"]:
            return None
        sent_at = self._parse_sqlite_timestamp(str(row["last_outbound_sent_at"]))
        if sent_at is None:
            return None
        now = datetime.now(timezone.utc)
        if now - sent_at > timedelta(seconds=max_age_seconds):
            return None
        return str(row["last_outbound_message_id"]) if row["last_outbound_message_id"] else None

    def remember_outbound_message(
        self,
        *,
        chat_id: str,
        dedup_key: str,
        context: str,
        message_id: str | None,
    ) -> None:
        connection = self.db.get_connection()
        try:
            connection.execute(
                """
                UPDATE chat_context
                SET last_outbound_message_id = ?,
                    last_outbound_dedup_key = ?,
                    last_outbound_context = ?,
                    last_outbound_sent_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_chat_id = ?
                """,
                (message_id, dedup_key, context, chat_id),
            )
            connection.commit()
        except sqlite3.OperationalError:
            logger.debug("chat delivery columns not available when storing outbound delivery state.")

    def get_recent_outbound_message_id_by_context(
        self,
        *,
        chat_id: str,
        context: str,
        max_age_seconds: float,
    ) -> str | None:
        connection = self.db.get_connection()
        try:
            row = connection.execute(
                """
                SELECT last_outbound_message_id, last_outbound_context, last_outbound_sent_at
                FROM chat_context
                WHERE telegram_chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            logger.debug("chat delivery columns not available when loading outbound context state.")
            return None
        if row is None:
            return None
        if row["last_outbound_context"] != context or not row["last_outbound_sent_at"]:
            return None
        sent_at = self._parse_sqlite_timestamp(str(row["last_outbound_sent_at"]))
        if sent_at is None:
            return None
        now = datetime.now(timezone.utc)
        if now - sent_at > timedelta(seconds=max_age_seconds):
            return None
        return str(row["last_outbound_message_id"]) if row["last_outbound_message_id"] else None

    def list_all_telegram_chat_ids(self) -> list[str]:
        """Return all known Telegram chat IDs (used for broadcasting system alerts)."""
        try:
            rows = self.db.get_connection().execute(
                "SELECT DISTINCT telegram_chat_id FROM chat_context"
            ).fetchall()
            return [str(row["telegram_chat_id"]) for row in rows]
        except sqlite3.OperationalError:
            return []

    def _parse_sqlite_timestamp(self, value: str) -> datetime | None:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
