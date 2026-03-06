from pathlib import Path

from src.approval_store import ApprovalStore
from src.db import Database


def _setup_store(tmp_path: Path) -> tuple[Database, ApprovalStore]:
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
            command_type, original_request, status, latest_summary, codex_session_id
        )
        VALUES (1, 'task-1', 1, 1, 'chat-1', 'msg-1', 'do', 'demo request', 'waiting_approval', '等待审批', 'sess-1')
        """
    )
    connection.commit()
    return db, ApprovalStore(db)


def test_create_and_fetch_pending_approval(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)

    approval_id = store.create_approval_request(
        task_id=1,
        requested_action="need approval",
        requested_by_user_id=1,
    )

    row = store.get_pending_approval_row(1)
    assert row is not None
    assert int(row["approval_id"]) == approval_id
    assert int(row["task_id"]) == 1
    assert str(row["requested_action"]) == "need approval"
    assert str(row["latest_summary"]) == "等待审批"

    stored = db.get_connection().execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    assert stored is not None
    assert stored["status"] == "pending"


def test_cancel_pending_for_task(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    approval_id = store.create_approval_request(
        task_id=1,
        requested_action="need approval",
        requested_by_user_id=1,
    )

    store.cancel_pending_for_task(task_id=1)

    row = db.get_connection().execute(
        "SELECT status, decision_note, decided_at FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["decision_note"] == "Cancelled by user"
    assert row["decided_at"] is not None


def test_resolve_approval_is_atomic(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    approval_id = store.create_approval_request(
        task_id=1,
        requested_action="need approval",
        requested_by_user_id=1,
    )

    first = store.resolve_approval(
        approval_id=approval_id,
        status="approved",
        decided_by_user_id=1,
        decision_note="ok",
    )
    second = store.resolve_approval(
        approval_id=approval_id,
        status="rejected",
        decided_by_user_id=1,
        decision_note="late click",
    )

    row = db.get_connection().execute(
        "SELECT status, decision_note FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    assert first is True
    assert second is False
    assert row is not None
    assert row["status"] == "approved"
    assert row["decision_note"] == "ok"
