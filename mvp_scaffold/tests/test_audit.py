import json
from pathlib import Path

from src.audit import AuditLogger
from src import audit_events
from src.db import Database


def _setup_db(tmp_path: Path) -> Database:
    schema_path = tmp_path / "schema.sql"
    db_path = tmp_path / "app.db"
    schema_path.write_text(
        """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    project_id INTEGER,
    task_id INTEGER,
    severity TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""".strip(),
        encoding="utf-8",
    )
    db = Database(path=db_path, schema_path=schema_path, migrations_dir=None)
    db.connect()
    db.initialize_schema()
    return db


def test_audit_logger_normalizes_unknown_action_and_severity(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    logger = AuditLogger(db)

    logger.log(
        action="invalid.action.code",
        severity="LOUD",
        message="token=abc123",
        details={"payload": "password=hello"},
    )

    row = db.get_connection().execute(
        "SELECT action, severity, message, details_json FROM audit_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["action"] == audit_events.UNKNOWN_EVENT
    assert row["severity"] == "info"
    assert "[REDACTED]" in row["message"]
    details = json.loads(row["details_json"])
    assert details["_invalid_action"] == "invalid.action.code"
    assert details["_invalid_severity"] == "LOUD"
    assert details["payload"] == "password=[REDACTED]"


def test_audit_logger_accepts_known_event(tmp_path: Path) -> None:
    db = _setup_db(tmp_path)
    logger = AuditLogger(db)

    logger.log(
        action=audit_events.TASK_CREATED,
        severity="warning",
        message="创建任务",
        details={"request": "fix bug"},
    )

    row = db.get_connection().execute(
        "SELECT action, severity, message, details_json FROM audit_logs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None
    assert row["action"] == audit_events.TASK_CREATED
    assert row["severity"] == "warning"
    assert row["message"] == "创建任务"
