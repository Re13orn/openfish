from pathlib import Path

from src.db import Database
from src.schedule_store import ScheduleStore


def _setup_store(tmp_path: Path) -> tuple[Database, ScheduleStore]:
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
    return db, ScheduleStore(db)


def test_enable_disable_round_trip(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    schedule_id = store.create_scheduled_task(
        user_id=1,
        project_id=1,
        chat_id="chat-1",
        command_type="ask",
        request_text="daily",
        minute_of_day=9 * 60,
    )

    assert store.set_scheduled_task_enabled(schedule_id=schedule_id, project_id=1, enabled=False) is True
    item = store.get_scheduled_task(schedule_id=schedule_id, project_id=1)
    assert item is not None
    assert item.enabled is False

    assert store.set_scheduled_task_enabled(schedule_id=schedule_id, project_id=1, enabled=True) is True
    item = store.get_scheduled_task(schedule_id=schedule_id, project_id=1)
    assert item is not None
    assert item.enabled is True


def test_claim_due_skips_disabled_schedule(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)
    schedule_id = store.create_scheduled_task(
        user_id=1,
        project_id=1,
        chat_id="chat-1",
        command_type="do",
        request_text="daily",
        minute_of_day=8 * 60,
    )
    store.set_scheduled_task_enabled(schedule_id=schedule_id, project_id=1, enabled=False)

    due = store.claim_due_scheduled_tasks(minute_of_day=8 * 60, trigger_date="2026-03-06")
    assert due == []
