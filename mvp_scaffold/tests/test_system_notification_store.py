from pathlib import Path

from src.db import Database
from src.system_notification_store import SystemNotificationStore


def _setup_store(tmp_path: Path) -> tuple[Database, SystemNotificationStore]:
    repo_root = Path(__file__).resolve().parents[2]
    schema_path = repo_root / "schema.sql"
    migrations_dir = repo_root / "mvp_scaffold" / "migrations"
    db_path = tmp_path / "app.db"

    db = Database(path=db_path, schema_path=schema_path, migrations_dir=migrations_dir)
    db.connect()
    db.initialize_schema()
    return db, SystemNotificationStore(db)


def test_queue_notification_collapses_same_chat_and_kind(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)

    store.queue_notification(chat_id="chat-1", kind="restart_completed")
    store.queue_notification(chat_id="chat-1", kind="restart_completed")

    pending = store.list_pending_notifications(limit=10)
    assert len(pending) == 1
    assert pending[0].telegram_chat_id == "chat-1"
    assert pending[0].notification_kind == "restart_completed"


def test_queue_notification_round_trip_with_payload(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)

    store.queue_notification(
        chat_id="chat-2",
        kind="update_completed",
        payload={"source": "update"},
    )

    pending = store.list_pending_notifications(limit=10)
    assert len(pending) == 1
    assert pending[0].payload == {"source": "update"}

    store.delete_notification(notification_id=pending[0].id)
    assert store.list_pending_notifications(limit=10) == []
