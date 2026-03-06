from pathlib import Path

from src.chat_state_store import ChatStateStore
from src.db import Database


def _setup_store(tmp_path: Path) -> tuple[Database, ChatStateStore]:
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
    connection.commit()
    return db, ChatStateStore(db)


def test_active_project_falls_back_to_user_default(tmp_path: Path) -> None:
    db, store = _setup_store(tmp_path)
    connection = db.get_connection()
    connection.execute("INSERT INTO user_preferences (user_id, default_project_key) VALUES (1, 'demo')")
    connection.commit()

    assert store.get_active_project_key(1, "chat-1") == "demo"


def test_wizard_state_round_trip(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)

    store.set_chat_wizard_state(
        chat_id="chat-1",
        user_id=1,
        state={"kind": "project_add", "step": "confirm", "data": {"key": "demo"}},
    )

    assert store.get_chat_wizard_state(chat_id="chat-1") == {
        "kind": "project_add",
        "step": "confirm",
        "data": {"key": "demo"},
    }


def test_invalid_ui_mode_raises_value_error(tmp_path: Path) -> None:
    _, store = _setup_store(tmp_path)

    try:
        store.set_chat_ui_mode(chat_id="chat-1", user_id=1, mode="compact")
    except ValueError as exc:
        assert "Unsupported ui mode" in str(exc)
    else:
        raise AssertionError("expected ValueError for unsupported ui mode")
