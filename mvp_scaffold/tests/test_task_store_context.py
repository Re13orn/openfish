from pathlib import Path

from src.db import Database
from src.models import CommandContext
from src.task_store import TaskStore


def _setup_store(tmp_path: Path) -> tuple[Database, TaskStore]:
    repo_root = Path(__file__).resolve().parents[2]
    schema_path = repo_root / "schema.sql"
    migrations_dir = repo_root / "mvp_scaffold" / "migrations"
    db_path = tmp_path / "app.db"

    db = Database(path=db_path, schema_path=schema_path, migrations_dir=migrations_dir)
    db.connect()
    db.initialize_schema()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO projects (project_key, name, path)
        VALUES ('p1', 'Project 1', '/tmp')
        """
    )
    conn.execute(
        """
        INSERT INTO projects (project_key, name, path)
        VALUES ('p2', 'Project 2', '/tmp')
        """
    )
    for key in ("p1", "p2"):
        row = conn.execute("SELECT id FROM projects WHERE project_key = ?", (key,)).fetchone()
        assert row is not None
        conn.execute("INSERT INTO project_state (project_id) VALUES (?)", (int(row["id"]),))
    conn.commit()

    return db, TaskStore(db)


def test_chat_context_active_project_overrides_user_default(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/use p1",
        )
    )

    store.set_active_project(user.id, "p1")
    db.get_connection().execute(
        """
        INSERT INTO chat_context (telegram_chat_id, user_id, active_project_key)
        VALUES ('chat-1', ?, 'p2')
        """,
        (user.id,),
    )
    db.get_connection().commit()

    assert store.get_active_project_key(user.id, "chat-1") == "p2"
    assert store.get_active_project_key(user.id, "chat-2") == "p1"


def test_cancel_waiting_approval_resolves_pending_approval(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/do something",
        )
    )
    project_id = store.get_project_id("p1")
    task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="2",
        command_type="do",
        original_request="dangerous op",
    )
    store.mark_task_waiting_approval(
        task_id=task_id,
        summary="等待审批",
        pending_action="需要审批",
        codex_session_id="sess-1",
    )
    approval_id = store.create_approval_request(
        task_id=task_id,
        requested_action="需要审批",
        requested_by_user_id=user.id,
    )

    cancelled = store.cancel_latest_active_task(project_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"

    row = db.get_connection().execute(
        "SELECT status, decision_note, decided_at FROM approvals WHERE id = ?",
        (approval_id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["decision_note"] == "Cancelled by user"
    assert row["decided_at"] is not None


def test_clear_project_session_state_resets_runtime_fields(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/do something",
        )
    )
    project_id = store.get_project_id("p1")
    task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="2",
        command_type="do",
        original_request="do",
    )
    store.update_project_state_after_task(
        project_id=project_id,
        task_id=task_id,
        summary="summary",
        codex_session_id="sess-1",
        pending_approval_task_id=task_id,
        next_step="next",
    )
    db.get_connection().execute(
        """
        UPDATE project_state
        SET last_test_command='pytest',
            last_test_status='passed',
            last_test_summary='ok'
        WHERE project_id=?
        """,
        (project_id,),
    )
    db.get_connection().commit()

    store.clear_project_session_state(project_id=project_id)

    row = db.get_connection().execute(
        """
        SELECT last_codex_session_id, last_task_id, last_task_summary,
               last_test_command, last_test_status, last_test_summary,
               pending_approval_task_id, next_step
        FROM project_state
        WHERE project_id=?
        """,
        (project_id,),
    ).fetchone()
    assert row is not None
    assert row["last_codex_session_id"] is None
    assert row["last_task_id"] is None
    assert row["last_task_summary"] is None
    assert row["last_test_command"] is None
    assert row["last_test_status"] is None
    assert row["last_test_summary"] is None
    assert row["pending_approval_task_id"] is None
    assert row["next_step"] is None


def test_recent_projects_follow_latest_use_order(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/use p1",
        )
    )

    store.set_active_project(user.id, "p1", "chat-default")
    store.set_active_project(user.id, "p2", "chat-default")
    db.get_connection().execute(
        """
        UPDATE user_project_activity
        SET last_used_at = CASE project_key
            WHEN 'p2' THEN '2026-03-06 10:00:00'
            WHEN 'p1' THEN '2026-03-06 09:00:00'
        END
        WHERE user_id = ?
        """,
        (user.id,),
    )
    db.get_connection().commit()

    assert store.list_recent_project_keys(user_id=user.id) == ["p2", "p1"]


def test_chat_wizard_state_round_trip(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/project-add",
        )
    )

    store.set_chat_wizard_state(
        chat_id="chat-default",
        user_id=user.id,
        state={"kind": "project_add", "step": "name", "data": {"key": "demo"}},
    )
    assert store.get_chat_wizard_state(chat_id="chat-default") == {
        "kind": "project_add",
        "step": "name",
        "data": {"key": "demo"},
    }

    store.clear_chat_wizard_state(chat_id="chat-default")
    assert store.get_chat_wizard_state(chat_id="chat-default") is None


def test_chat_ui_mode_round_trip(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/ui summary",
        )
    )

    store.set_chat_ui_mode(chat_id="chat-default", user_id=user.id, mode="summary")
    assert store.get_chat_ui_mode(chat_id="chat-default") == "summary"

    store.set_chat_ui_mode(chat_id="chat-default", user_id=user.id, mode="verbose")
    assert store.get_chat_ui_mode(chat_id="chat-default") == "verbose"

    store.set_chat_ui_mode(chat_id="chat-default", user_id=user.id, mode="stream")
    assert store.get_chat_ui_mode(chat_id="chat-default") == "stream"

    store.clear_chat_ui_mode(chat_id="chat-default")
    assert store.get_chat_ui_mode(chat_id="chat-default") is None


def test_chat_pending_command_round_trip(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/do something",
        )
    )

    store.set_chat_pending_command(chat_id="chat-default", user_id=user.id, command="/do")
    assert store.get_chat_pending_command(chat_id="chat-default") == "/do"

    store.clear_chat_pending_command(chat_id="chat-default")
    assert store.get_chat_pending_command(chat_id="chat-default") is None


def test_recover_interrupted_tasks_marks_created_and_running_failed(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/do something",
        )
    )
    project_id = store.get_project_id("p1")

    created_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="2",
        command_type="do",
        original_request="created task",
    )
    running_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="3",
        command_type="do",
        original_request="running task",
    )
    waiting_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="4",
        command_type="do",
        original_request="waiting task",
    )

    store.mark_task_running(running_task_id)
    store.mark_task_waiting_approval(
        task_id=waiting_task_id,
        summary="等待审批",
        pending_action="需要审批",
        codex_session_id="sess-1",
    )

    recovered_task_ids = store.recover_interrupted_tasks()

    assert recovered_task_ids == [created_task_id, running_task_id]

    rows = db.get_connection().execute(
        """
        SELECT id, status, latest_summary, latest_error
        FROM tasks
        WHERE id IN (?, ?, ?)
        ORDER BY id
        """,
        (created_task_id, running_task_id, waiting_task_id),
    ).fetchall()
    assert [row["status"] for row in rows] == ["failed", "failed", "waiting_approval"]
    assert rows[0]["latest_summary"] == "任务已中断：OpenFish 服务重启或执行挂起，请使用 /retry 重试。"
    assert rows[0]["latest_error"] == "Task interrupted before completion."

    event_rows = db.get_connection().execute(
        """
        SELECT task_id, event_type
        FROM task_events
        WHERE task_id IN (?, ?)
        ORDER BY id
        """,
        (created_task_id, running_task_id),
    ).fetchall()
    assert (created_task_id, "task.failed") in {
        (int(row["task_id"]), str(row["event_type"])) for row in event_rows
    }
    assert (running_task_id, "task.failed") in {
        (int(row["task_id"]), str(row["event_type"])) for row in event_rows
    }


def test_list_cancel_and_delete_tasks(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/do something",
        )
    )
    project_id = store.get_project_id("p1")

    running_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="2",
        command_type="do",
        original_request="running task",
    )
    finished_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="3",
        command_type="ask",
        original_request="finished task",
    )

    store.mark_task_running(running_task_id)
    store.finalize_task(
        task_id=finished_task_id,
        status="completed",
        summary="done",
        error=None,
        codex_session_id="sess-finished",
    )
    store.update_project_state_after_task(
        project_id=project_id,
        task_id=finished_task_id,
        summary="done",
        codex_session_id="sess-finished",
        pending_approval_task_id=None,
        next_step="next",
    )

    page = store.list_tasks(project_id=project_id, page=1, page_size=10)
    assert [item.id for item in page.items] == [finished_task_id, running_task_id]

    cancelled = store.cancel_task(task_id=running_task_id, project_id=project_id)
    assert cancelled is not None
    assert cancelled.status == "cancelled"

    deleted = store.delete_task(task_id=finished_task_id, project_id=project_id)
    assert deleted is not None
    assert deleted.id == finished_task_id
    assert store.get_task(finished_task_id) is None

    row = db.get_connection().execute(
        """
        SELECT last_task_id, last_task_summary
        FROM project_state
        WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    assert row is not None
    assert row["last_task_id"] is None
    assert row["last_task_summary"] is None


def test_clear_tasks_removes_terminal_tasks_and_clears_references(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-default",
            telegram_message_id="1",
            text="/do something",
        )
    )
    project_id = store.get_project_id("p1")

    first_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="2",
        command_type="do",
        original_request="done task",
    )
    second_task_id = store.create_task(
        user_id=user.id,
        project_id=project_id,
        chat_id="chat-default",
        message_id="3",
        command_type="ask",
        original_request="failed task",
    )
    store.finalize_task(
        task_id=first_task_id,
        status="completed",
        summary="done",
        error=None,
        codex_session_id="sess-done",
    )
    store.finalize_task(
        task_id=second_task_id,
        status="failed",
        summary="failed",
        error="boom",
        codex_session_id="sess-failed",
    )
    store.update_project_state_after_task(
        project_id=project_id,
        task_id=second_task_id,
        summary="failed",
        codex_session_id="sess-failed",
        pending_approval_task_id=None,
        next_step="next",
    )

    deleted_count = store.clear_tasks(project_id=project_id)
    assert deleted_count == 2
    assert store.get_task(first_task_id) is None
    assert store.get_task(second_task_id) is None

    row = db.get_connection().execute(
        "SELECT last_task_id, last_task_summary FROM project_state WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    assert row is not None
    assert row["last_task_id"] is None
    assert row["last_task_summary"] is None
