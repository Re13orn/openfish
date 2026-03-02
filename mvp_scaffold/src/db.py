"""Lightweight SQLite database wrapper with schema bootstrap."""

from pathlib import Path
import re
import sqlite3


MIGRATION_FILE_PATTERN = re.compile(r"^(\d+)_([a-zA-Z0-9_]+)\.sql$")


class Database:
    """Owns the SQLite connection lifecycle for the local process."""

    def __init__(self, path: Path, schema_path: Path, migrations_dir: Path | None = None) -> None:
        self.path = path
        self.schema_path = schema_path
        self.migrations_dir = migrations_dir
        self.connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON;")
        return self.connection

    def initialize_schema(self) -> None:
        """Apply the repository SQL schema if needed."""

        sql = self.schema_path.read_text(encoding="utf-8")
        connection = self.get_connection()
        connection.executescript(sql)
        self._ensure_baseline_migration(connection)
        self._apply_pending_migrations(connection)
        connection.commit()

    def get_connection(self) -> sqlite3.Connection:
        if self.connection is None:
            return self.connect()
        return self.connection

    def _ensure_baseline_migration(self, connection: sqlite3.Connection) -> None:
        row = connection.execute("SELECT COUNT(1) AS count FROM schema_migrations").fetchone()
        if row is None:
            return
        if int(row["count"]) > 0:
            return
        connection.execute(
            """
            INSERT INTO schema_migrations (version, name)
            VALUES (?, ?)
            """,
            (1, "baseline_schema"),
        )

    def _apply_pending_migrations(self, connection: sqlite3.Connection) -> None:
        if self.migrations_dir is None or not self.migrations_dir.exists():
            return

        applied_rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
        applied_versions = {int(row["version"]) for row in applied_rows}

        for path in sorted(self.migrations_dir.glob("*.sql")):
            match = MIGRATION_FILE_PATTERN.match(path.name)
            if not match:
                continue
            version = int(match.group(1))
            name = match.group(2)
            if version in applied_versions:
                continue

            sql = path.read_text(encoding="utf-8")
            connection.executescript(sql)
            connection.execute(
                """
                INSERT INTO schema_migrations (version, name)
                VALUES (?, ?)
                """,
                (version, name),
            )
            applied_versions.add(version)
