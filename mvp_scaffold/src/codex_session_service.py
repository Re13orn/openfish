"""Unified view of OpenFish-tracked and local native Codex sessions."""

from dataclasses import dataclass
import json
from pathlib import Path

from src.db import Database


@dataclass(slots=True)
class CodexSessionRecord:
    session_id: str
    source: str
    title: str | None
    updated_at: str | None
    cwd: str | None
    project_key: str | None
    project_name: str | None
    project_path: str | None
    task_id: int | None
    task_status: str | None
    task_summary: str | None
    command_type: str | None
    session_file_path: str | None
    importable: bool


@dataclass(slots=True)
class CodexSessionListResult:
    sessions: list[CodexSessionRecord]
    page: int
    page_size: int
    total_count: int
    total_pages: int
    openfish_count: int
    native_count: int


class CodexSessionService:
    """Reads native Codex sessions and OpenFish task-linked sessions."""

    def __init__(self, *, db: Database, codex_home: Path) -> None:
        self.db = db
        self.codex_home = codex_home
        self.session_index_path = codex_home / "session_index.jsonl"
        self.sessions_root = codex_home / "sessions"

    def list_sessions(self, *, page: int = 1, page_size: int = 10) -> CodexSessionListResult:
        native_index = self._load_native_index()
        openfish_sessions = self._load_openfish_sessions(native_index=native_index)
        openfish_ids = {item.session_id for item in openfish_sessions}
        native_only = [
            self._native_index_to_record(item)
            for item in native_index.values()
            if item["session_id"] not in openfish_ids
        ]
        merged = sorted(
            [*openfish_sessions, *native_only],
            key=lambda item: item.updated_at or "",
            reverse=True,
        )
        normalized_page_size = max(1, int(page_size))
        total_count = len(merged)
        total_pages = max(1, (total_count + normalized_page_size - 1) // normalized_page_size)
        normalized_page = min(max(1, int(page)), total_pages)
        offset = (normalized_page - 1) * normalized_page_size
        return CodexSessionListResult(
            sessions=merged[offset : offset + normalized_page_size],
            page=normalized_page,
            page_size=normalized_page_size,
            total_count=total_count,
            total_pages=total_pages,
            openfish_count=len(openfish_sessions),
            native_count=len(native_only),
        )

    def get_session(self, session_id: str) -> CodexSessionRecord | None:
        native_index = self._load_native_index()
        openfish = self._load_openfish_session_by_id(session_id=session_id, native_index=native_index)
        if openfish is not None:
            return openfish
        native = native_index.get(session_id)
        if native is None:
            return None
        return self._native_index_to_record(native, include_meta=True)

    def _load_openfish_sessions(
        self,
        *,
        native_index: dict[str, dict[str, str | None]],
    ) -> list[CodexSessionRecord]:
        connection = self.db.get_connection()
        rows = connection.execute(
            """
            SELECT
                t.id AS task_id,
                t.codex_session_id,
                t.command_type,
                t.original_request,
                t.status,
                t.latest_summary,
                t.updated_at,
                p.project_key,
                p.name AS project_name,
                p.path AS project_path
            FROM tasks t
            JOIN projects p
                ON p.id = t.project_id
            WHERE t.codex_session_id IS NOT NULL
            ORDER BY t.id DESC
            """
        ).fetchall()
        sessions: list[CodexSessionRecord] = []
        seen: set[str] = set()
        for row in rows:
            session_id = str(row["codex_session_id"])
            if session_id in seen:
                continue
            seen.add(session_id)
            sessions.append(self._build_openfish_record(row=row, native_index=native_index))
        return sessions

    def _load_openfish_session_by_id(
        self,
        *,
        session_id: str,
        native_index: dict[str, dict[str, str | None]],
    ) -> CodexSessionRecord | None:
        connection = self.db.get_connection()
        row = connection.execute(
            """
            SELECT
                t.id AS task_id,
                t.codex_session_id,
                t.command_type,
                t.original_request,
                t.status,
                t.latest_summary,
                t.updated_at,
                p.project_key,
                p.name AS project_name,
                p.path AS project_path
            FROM tasks t
            JOIN projects p
                ON p.id = t.project_id
            WHERE t.codex_session_id = ?
            ORDER BY t.id DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return self._build_openfish_record(row=row, native_index=native_index)

    def _build_openfish_record(
        self,
        *,
        row,
        native_index: dict[str, dict[str, str | None]],
    ) -> CodexSessionRecord:
        session_id = str(row["codex_session_id"])
        native = native_index.get(session_id)
        title = str(row["original_request"]) if row["original_request"] else None
        cwd = str(row["project_path"]) if row["project_path"] else None
        updated_at = str(row["updated_at"]) if row["updated_at"] else None
        session_file_path = None
        if native is not None:
            title = native.get("thread_name") or title
            cwd = native.get("cwd") or cwd
            updated_at = native.get("updated_at") or updated_at
            session_file_path = native.get("session_file_path")
        return CodexSessionRecord(
            session_id=session_id,
            source="openfish",
            title=title,
            updated_at=updated_at,
            cwd=cwd,
            project_key=str(row["project_key"]) if row["project_key"] else None,
            project_name=str(row["project_name"]) if row["project_name"] else None,
            project_path=str(row["project_path"]) if row["project_path"] else None,
            task_id=int(row["task_id"]),
            task_status=str(row["status"]) if row["status"] else None,
            task_summary=str(row["latest_summary"]) if row["latest_summary"] else None,
            command_type=str(row["command_type"]) if row["command_type"] else None,
            session_file_path=session_file_path,
            importable=False,
        )

    def _load_native_index(self) -> dict[str, dict[str, str | None]]:
        if not self.session_index_path.exists():
            return {}
        sessions: dict[str, dict[str, str | None]] = {}
        with self.session_index_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = self._clean_text(payload.get("id"))
                if not session_id:
                    continue
                record = {
                    "session_id": session_id,
                    "thread_name": self._clean_text(payload.get("thread_name")),
                    "updated_at": self._clean_text(payload.get("updated_at")),
                    "cwd": None,
                    "session_file_path": None,
                }
                meta = self._read_native_session_meta(session_id)
                if meta is not None:
                    record["cwd"] = self._clean_text(meta.get("cwd"))
                    record["session_file_path"] = self._clean_text(meta.get("session_file_path"))
                    if not record["updated_at"]:
                        record["updated_at"] = self._clean_text(meta.get("timestamp"))
                sessions[session_id] = record
        return sessions

    def _native_index_to_record(
        self,
        item: dict[str, str | None],
        *,
        include_meta: bool = False,
    ) -> CodexSessionRecord:
        cwd = item.get("cwd")
        session_file_path = item.get("session_file_path")
        if include_meta and (cwd is None or session_file_path is None):
            meta = self._read_native_session_meta(str(item["session_id"]))
            if meta is not None:
                cwd = self._clean_text(meta.get("cwd"))
                session_file_path = self._clean_text(meta.get("session_file_path"))
        return CodexSessionRecord(
            session_id=str(item["session_id"]),
            source="native",
            title=item.get("thread_name"),
            updated_at=item.get("updated_at"),
            cwd=cwd,
            project_key=None,
            project_name=None,
            project_path=None,
            task_id=None,
            task_status=None,
            task_summary=None,
            command_type=None,
            session_file_path=session_file_path,
            importable=True,
        )

    def _read_native_session_meta(self, session_id: str) -> dict[str, str] | None:
        session_file = self._find_session_file(session_id)
        if session_file is None:
            return None
        try:
            with session_file.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if payload.get("type") != "session_meta":
                        continue
                    meta = payload.get("payload") or {}
                    return {
                        "cwd": str(meta.get("cwd") or ""),
                        "timestamp": str(meta.get("timestamp") or ""),
                        "session_file_path": str(session_file),
                    }
        except OSError:
            return None
        return None

    def _find_session_file(self, session_id: str) -> Path | None:
        if not self.sessions_root.exists():
            return None
        matches = sorted(self.sessions_root.rglob(f"*{session_id}.jsonl"))
        if not matches:
            return None
        return matches[-1]

    def _clean_text(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
