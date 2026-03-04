"""Lightweight SQLite database wrapper with schema bootstrap."""

from pathlib import Path
import re
import sqlite3
from threading import Lock, local


MIGRATION_FILE_PATTERN = re.compile(r"^(\d+)_([a-zA-Z0-9_]+)\.sql$")


class Database:
    """Owns the SQLite connection lifecycle for the local process."""

    def __init__(self, path: Path, schema_path: Path, migrations_dir: Path | None = None) -> None:
        self.path = path
        self.schema_path = schema_path
        self.migrations_dir = migrations_dir
        self._local = local()
        self._all_connections: list[sqlite3.Connection] = []
        self._connections_lock = Lock()

    def connect(self) -> sqlite3.Connection:
        """Create (or reuse) a connection bound to the current thread."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            return existing

        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        self._local.connection = connection
        with self._connections_lock:
            self._all_connections.append(connection)
        return connection

    def initialize_schema(self) -> None:
        """Apply the repository SQL schema if needed."""

        sql = self.schema_path.read_text(encoding="utf-8")
        connection = self.get_connection()
        connection.executescript(sql)
        self._ensure_baseline_migration(connection)
        self._apply_pending_migrations(connection)
        connection.commit()

    def get_connection(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            return self.connect()
        return connection

    def close_all(self) -> None:
        """Close all thread-local connections created by this process."""

        with self._connections_lock:
            connections = list(self._all_connections)
            self._all_connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                continue
        if hasattr(self._local, "connection"):
            del self._local.connection

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
