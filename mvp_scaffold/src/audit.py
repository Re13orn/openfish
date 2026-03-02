"""Audit logging helpers."""

import json
import logging
from typing import Any

from src import audit_events
from src.db import Database
from src.redaction import redact_object, redact_text


logger = logging.getLogger(__name__)
ALLOWED_SEVERITIES = {"debug", "info", "warning", "error", "critical"}


class AuditLogger:
    """Writes durable audit events into SQLite."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def log(
        self,
        *,
        action: str,
        message: str,
        severity: str = "info",
        user_id: int | None = None,
        project_id: int | None = None,
        task_id: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        normalized_action, normalized_severity, normalized_details = self._normalize_inputs(
            action=action,
            severity=severity,
            details=details,
        )
        safe_message = redact_text(message)
        safe_details = redact_object(normalized_details) if normalized_details else None
        payload = json.dumps(safe_details) if safe_details else None
        connection = self.db.get_connection()
        connection.execute(
            """
            INSERT INTO audit_logs (user_id, project_id, task_id, severity, action, message, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                project_id,
                task_id,
                normalized_severity,
                normalized_action,
                safe_message,
                payload,
            ),
        )
        connection.commit()

    def _normalize_inputs(
        self,
        *,
        action: str,
        severity: str,
        details: dict[str, Any] | None,
    ) -> tuple[str, str, dict[str, Any] | None]:
        normalized_details = dict(details) if details else {}
        normalized_action = action
        if action not in audit_events.ALL_EVENTS:
            logger.warning("Unknown audit action: %s", action)
            normalized_action = audit_events.UNKNOWN_EVENT
            normalized_details["_invalid_action"] = action

        normalized_severity = severity.lower().strip()
        if normalized_severity not in ALLOWED_SEVERITIES:
            logger.warning("Invalid audit severity: %s", severity)
            normalized_details["_invalid_severity"] = severity
            normalized_severity = "info"

        if not normalized_details:
            return normalized_action, normalized_severity, None
        return normalized_action, normalized_severity, normalized_details
