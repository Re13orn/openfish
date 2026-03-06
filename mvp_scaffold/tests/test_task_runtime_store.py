from pathlib import Path

from src.db import Database
from src.task_runtime_store import TaskRuntimeStore


def _setup_store(tmp_path: Path) -> tuple[Database, TaskRuntimeStore]:
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
    connection.commit()
    return db, TaskRuntimeStore(db)


def test_create_and_finalize_task(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    task_id = store.create_task(
        user_id=1,
        project_id=1,
        chat_id="chat-1",
        message_id="msg-1",
        command_type="do",
        original_request="run task",
    )

    store.finalize_task(
        task_id=task_id,
        status="completed",
        summary="ok",
        error=None,
        codex_session_id="sess-1",
    )

    row = db.get_connection().execute(
        "SELECT status, latest_summary, codex_session_id FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "completed"
    assert row["latest_summary"] == "ok"
    assert row["codex_session_id"] == "sess-1"


def test_mark_task_resumed_after_approval(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    task_id = store.create_task(
        user_id=1,
        project_id=1,
        chat_id="chat-1",
        message_id="msg-1",
        command_type="do",
        original_request="run task",
    )
    store.finalize_task(
        task_id=task_id,
        status="waiting_approval",
        summary="waiting",
        error=None,
        codex_session_id=None,
        requires_approval=True,
        pending_approval_action="approve me",
    )

    store.mark_task_resumed_after_approval(task_id)

    row = db.get_connection().execute(
        "SELECT status, requires_approval, pending_approval_action FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "running"
    assert int(row["requires_approval"]) == 0
    assert row["pending_approval_action"] is None
