from pathlib import Path

from src.db import Database
from src.project_state_store import ProjectStateStore


def _setup_store(tmp_path: Path) -> tuple[Database, ProjectStateStore]:
    repo_root = Path(__file__).resolve().parents[2]
    schema_path = repo_root / "schema.sql"
    migrations_dir = repo_root / "mvp_scaffold" / "migrations"
    db_path = tmp_path / "app.db"

    db = Database(path=db_path, schema_path=schema_path, migrations_dir=migrations_dir)
    db.connect()
    db.initialize_schema()

    connection = db.get_connection()
    connection.execute(
        """
        INSERT INTO users (id, telegram_user_id, telegram_username, display_name)
        VALUES (1, '123', 'tester', 'Tester')
        """
    )
    connection.execute(
        """
        INSERT INTO projects (id, project_key, name, path)
        VALUES (1, 'demo', 'Demo', '/tmp')
        """
    )
    connection.execute("INSERT INTO project_state (project_id) VALUES (1)")
    connection.execute(
        """
        INSERT INTO tasks (
            id, task_uuid, user_id, project_id, telegram_chat_id, telegram_message_id,
            command_type, original_request, status, latest_summary, latest_error
        )
        VALUES (
            1, 'task-1', 1, 1, 'chat-1', 'msg-1',
            'do', 'demo request', 'failed', 'task failed', 'boom'
        )
        """
    )
    connection.commit()
    return db, ProjectStateStore(db)


def test_update_project_state_after_task(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)

    store.update_project_state_after_task(
        project_id=1,
        task_id=1,
        summary="done",
        codex_session_id="sess-1",
        pending_approval_task_id=None,
        next_step="查看状态",
    )

    row = db.get_connection().execute(
        """
        SELECT last_task_id, last_task_summary, last_codex_session_id, next_step
        FROM project_state
        WHERE project_id = 1
        """
    ).fetchone()
    assert row is not None
    assert int(row["last_task_id"]) == 1
    assert str(row["last_task_summary"]) == "done"
    assert str(row["last_codex_session_id"]) == "sess-1"
    assert str(row["next_step"]) == "查看状态"


def test_memory_snapshot_data(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    store.add_project_note(project_id=1, content="先最小改动", title="note")

    snapshot = store.get_memory_snapshot_data(project_id=1)

    assert "先最小改动" in snapshot["notes"]
    assert snapshot["recent_task_summaries"] == ["task failed"]
    assert snapshot["project_summary"] is None


def test_recent_failed_task_row(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)

    row = store.get_recent_failed_task_row(project_id=1)

    assert row is not None
    assert str(row["latest_error"]) == "boom"
