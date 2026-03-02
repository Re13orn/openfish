from pathlib import Path

from src.db import Database


def test_initialize_schema_records_baseline_migration(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.sql"
    db_path = tmp_path / "app.db"
    schema_path.write_text(
        """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""".strip(),
        encoding="utf-8",
    )

    db = Database(path=db_path, schema_path=schema_path)
    db.connect()
    db.initialize_schema()
    db.initialize_schema()

    row = db.get_connection().execute(
        "SELECT COUNT(*) AS cnt FROM schema_migrations WHERE version = 1 AND name = 'baseline_schema'"
    ).fetchone()
    assert row is not None
    assert int(row["cnt"]) == 1


def test_apply_pending_migrations_once(tmp_path: Path) -> None:
    schema_path = tmp_path / "schema.sql"
    db_path = tmp_path / "app.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    schema_path.write_text(
        """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
""".strip(),
        encoding="utf-8",
    )
    (migrations_dir / "0002_create_demo.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo_table (id INTEGER PRIMARY KEY);",
        encoding="utf-8",
    )

    db = Database(path=db_path, schema_path=schema_path, migrations_dir=migrations_dir)
    db.connect()
    db.initialize_schema()
    db.initialize_schema()

    row = db.get_connection().execute(
        "SELECT COUNT(*) AS cnt FROM schema_migrations WHERE version = 2 AND name = 'create_demo'"
    ).fetchone()
    assert row is not None
    assert int(row["cnt"]) == 1
