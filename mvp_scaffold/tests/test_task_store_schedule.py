from pathlib import Path

from src.db import Database
from src.models import CommandContext
from src.task_store import TaskStore


def _setup_store(tmp_path: Path) -> tuple[Database, TaskStore, int, int]:
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
    row = conn.execute("SELECT id FROM projects WHERE project_key = 'p1'").fetchone()
    assert row is not None
    project_id = int(row["id"])
    conn.execute("INSERT INTO project_state (project_id) VALUES (?)", (project_id,))
    conn.commit()

    store = TaskStore(db)
    user = store.ensure_user(
        CommandContext(
            telegram_user_id="123",
            telegram_chat_id="chat-1",
            telegram_message_id="1",
            text="/start",
        )
    )
    return db, store, user.id, project_id


def test_schedule_create_list_delete(tmp_path: Path) -> None:
    _, store, user_id, project_id = _setup_store(tmp_path)

    schedule_id = store.create_scheduled_task(
        user_id=user_id,
        project_id=project_id,
        chat_id="chat-1",
        command_type="ask",
        request_text="每日报告",
        minute_of_day=9 * 60 + 30,
    )
    items = store.list_scheduled_tasks(project_id)

    assert len(items) == 1
    assert items[0].id == schedule_id
    assert items[0].minute_of_day == 9 * 60 + 30
    assert items[0].command_type == "ask"

    deleted = store.delete_scheduled_task(schedule_id=schedule_id, project_id=project_id)
    assert deleted is True
    assert store.list_scheduled_tasks(project_id) == []


def test_schedule_claim_due_once_per_day(tmp_path: Path) -> None:
    _, store, user_id, project_id = _setup_store(tmp_path)
    store.create_scheduled_task(
        user_id=user_id,
        project_id=project_id,
        chat_id="chat-1",
        command_type="do",
        request_text="每日执行",
        minute_of_day=8 * 60,
    )

    due_first = store.claim_due_scheduled_tasks(
        minute_of_day=8 * 60,
        trigger_date="2026-03-04",
    )
    due_second = store.claim_due_scheduled_tasks(
        minute_of_day=8 * 60,
        trigger_date="2026-03-04",
    )
    due_next_day = store.claim_due_scheduled_tasks(
        minute_of_day=8 * 60,
        trigger_date="2026-03-05",
    )

    assert len(due_first) == 1
    assert len(due_second) == 0
    assert len(due_next_day) == 1


def test_schedule_record_run_result(tmp_path: Path) -> None:
    db, store, user_id, project_id = _setup_store(tmp_path)
    schedule_id = store.create_scheduled_task(
        user_id=user_id,
        project_id=project_id,
        chat_id="chat-1",
        command_type="ask",
        request_text="日报",
        minute_of_day=12 * 60,
    )
    task_id = store.create_task(
        user_id=user_id,
        project_id=project_id,
        chat_id="chat-1",
        message_id="2",
        command_type="ask",
        original_request="日报",
    )

    store.record_scheduled_task_run(
        schedule_id=schedule_id,
        task_id=task_id,
        status="completed",
        summary="定期任务执行成功",
    )

    row = db.get_connection().execute(
        """
        SELECT last_task_id, last_run_status, last_run_summary
        FROM scheduled_tasks
        WHERE id = ?
        """,
        (schedule_id,),
    ).fetchone()
    assert row is not None
    assert int(row["last_task_id"]) == task_id
    assert str(row["last_run_status"]) == "completed"
    assert "执行成功" in str(row["last_run_summary"])
