from pathlib import Path

from src import audit_events
from src.db import Database
from src.task_store import TaskStore


def _setup_db(tmp_path: Path) -> tuple[Database, TaskStore]:
    schema_path = tmp_path / "schema.sql"
    db_path = tmp_path / "app.db"
    schema_path.write_text(
        """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id TEXT NOT NULL UNIQUE,
    telegram_username TEXT,
    display_name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_uuid TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    telegram_chat_id TEXT,
    telegram_message_id TEXT,
    command_type TEXT NOT NULL,
    original_request TEXT NOT NULL,
    normalized_request TEXT,
    status TEXT NOT NULL,
    latest_summary TEXT,
    latest_error TEXT,
    codex_session_id TEXT,
    requires_approval INTEGER NOT NULL DEFAULT 0,
    pending_approval_action TEXT,
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_summary TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""".strip(),
        encoding="utf-8",
    )
    db = Database(path=db_path, schema_path=schema_path, migrations_dir=None)
    db.connect()
    db.initialize_schema()
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO users (telegram_user_id, telegram_username, display_name) VALUES ('123', 'u', 'U')"
    )
    conn.execute(
        "INSERT INTO projects (project_key, name, path) VALUES ('demo', 'Demo', '/tmp/demo')"
    )
    conn.commit()
    return db, TaskStore(db)


def test_task_events_keep_known_event_codes(tmp_path: Path) -> None:
    db, store = _setup_db(tmp_path)
    task_id = store.create_task(
        user_id=1,
        project_id=1,
        chat_id="1",
        message_id="10",
        command_type="do",
        original_request="run",
    )
    store.finalize_task(
        task_id=task_id,
        status="completed",
        summary="ok",
        error=None,
        codex_session_id=None,
    )

    row = db.get_connection().execute(
        "SELECT event_type FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert row is not None
    assert row["event_type"] == audit_events.TASK_COMPLETED


def test_task_events_fallback_unknown_event_type(tmp_path: Path) -> None:
    db, store = _setup_db(tmp_path)
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO tasks (
            task_uuid, user_id, project_id, command_type, original_request, normalized_request, status
        ) VALUES ('uuid-1', 1, 1, 'do', 'x', 'x', 'created')
        """
    )
    task_id = int(
        conn.execute("SELECT id FROM tasks WHERE task_uuid = 'uuid-1'").fetchone()["id"]
    )
    store._insert_task_event(conn, task_id, "weird.event", "summary")
    conn.commit()

    row = conn.execute(
        "SELECT event_type, event_summary FROM task_events WHERE task_id = ? ORDER BY id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    assert row is not None
    assert row["event_type"] == audit_events.TASK_UNKNOWN_EVENT
    assert "invalid_event:weird.event" in row["event_summary"]
