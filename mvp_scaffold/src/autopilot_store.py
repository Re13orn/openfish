"""Persistence helpers for autopilot supervisor-worker runs."""

from dataclasses import dataclass
import json
from typing import Any

from src.db import Database


@dataclass(slots=True)
class AutopilotRunRecord:
    id: int
    project_id: int
    chat_id: str
    created_by_user_id: int
    goal: str
    status: str
    supervisor_session_id: str | None
    worker_session_id: str | None
    current_phase: str
    cycle_count: int
    max_cycles: int
    no_progress_cycles: int
    same_instruction_cycles: int
    last_instruction_fingerprint: str | None
    last_decision: str | None
    last_worker_summary: str | None
    last_supervisor_summary: str | None
    paused_reason: str | None
    stopped_by_user_id: int | None


@dataclass(slots=True)
class AutopilotEventRecord:
    id: int
    run_id: int
    cycle_no: int
    actor: str
    event_type: str
    summary: str | None
    payload: dict[str, Any] | None


@dataclass(slots=True)
class AutopilotStreamChunkRecord:
    id: int
    run_id: int
    cycle_no: int
    actor: str
    channel: str
    content: str


class AutopilotStore:
    """Encapsulates persistence for autopilot runs and their event logs."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def create_run(
        self,
        *,
        project_id: int,
        chat_id: str,
        created_by_user_id: int,
        goal: str,
        status: str = "created",
        current_phase: str = "idle",
        max_cycles: int = 100,
    ) -> int:
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO autopilot_runs (
                project_id, chat_id, created_by_user_id, goal, status, current_phase, max_cycles
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, chat_id, created_by_user_id, goal, status, current_phase, max_cycles),
        )
        connection.commit()
        return int(cursor.lastrowid)

    def get_run(self, *, run_id: int) -> AutopilotRunRecord | None:
        row = self.db.get_connection().execute(
            """
            SELECT
                id,
                project_id,
                chat_id,
                created_by_user_id,
                goal,
                status,
                supervisor_session_id,
                worker_session_id,
                current_phase,
                cycle_count,
                max_cycles,
                no_progress_cycles,
                same_instruction_cycles,
                last_instruction_fingerprint,
                last_decision,
                last_worker_summary,
                last_supervisor_summary,
                paused_reason,
                stopped_by_user_id
            FROM autopilot_runs
            WHERE id = ?
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    def list_runs_for_project(self, *, project_id: int, limit: int = 20) -> list[AutopilotRunRecord]:
        rows = self.db.get_connection().execute(
            """
            SELECT
                id,
                project_id,
                chat_id,
                created_by_user_id,
                goal,
                status,
                supervisor_session_id,
                worker_session_id,
                current_phase,
                cycle_count,
                max_cycles,
                no_progress_cycles,
                same_instruction_cycles,
                last_instruction_fingerprint,
                last_decision,
                last_worker_summary,
                last_supervisor_summary,
                paused_reason,
                stopped_by_user_id
            FROM autopilot_runs
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (project_id, limit),
        ).fetchall()
        return [self._row_to_run(row) for row in rows]

    def update_run(
        self,
        *,
        run_id: int,
        status: str | None = None,
        supervisor_session_id: str | None = None,
        worker_session_id: str | None = None,
        current_phase: str | None = None,
        cycle_count: int | None = None,
        max_cycles: int | None = None,
        no_progress_cycles: int | None = None,
        same_instruction_cycles: int | None = None,
        last_instruction_fingerprint: str | None = None,
        last_decision: str | None = None,
        last_worker_summary: str | None = None,
        last_supervisor_summary: str | None = None,
        paused_reason: str | None = None,
        stopped_by_user_id: int | None = None,
    ) -> bool:
        assignments: list[str] = []
        values: list[Any] = []

        def add(field: str, value: Any) -> None:
            assignments.append(f"{field} = ?")
            values.append(value)

        if status is not None:
            add("status", status)
        if supervisor_session_id is not None:
            add("supervisor_session_id", supervisor_session_id)
        if worker_session_id is not None:
            add("worker_session_id", worker_session_id)
        if current_phase is not None:
            add("current_phase", current_phase)
        if cycle_count is not None:
            add("cycle_count", cycle_count)
        if max_cycles is not None:
            add("max_cycles", max_cycles)
        if no_progress_cycles is not None:
            add("no_progress_cycles", no_progress_cycles)
        if same_instruction_cycles is not None:
            add("same_instruction_cycles", same_instruction_cycles)
        if last_instruction_fingerprint is not None:
            add("last_instruction_fingerprint", last_instruction_fingerprint)
        if last_decision is not None:
            add("last_decision", last_decision)
        if last_worker_summary is not None:
            add("last_worker_summary", last_worker_summary)
        if last_supervisor_summary is not None:
            add("last_supervisor_summary", last_supervisor_summary)
        if paused_reason is not None:
            add("paused_reason", paused_reason)
        if stopped_by_user_id is not None:
            add("stopped_by_user_id", stopped_by_user_id)

        if not assignments:
            return False

        assignments.append("updated_at = CURRENT_TIMESTAMP")
        values.append(run_id)
        connection = self.db.get_connection()
        cursor = connection.execute(
            f"""
            UPDATE autopilot_runs
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            values,
        )
        connection.commit()
        return cursor.rowcount > 0

    def append_event(
        self,
        *,
        run_id: int,
        cycle_no: int,
        actor: str,
        event_type: str,
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO autopilot_events (
                run_id, cycle_no, actor, event_type, summary, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                cycle_no,
                actor,
                event_type,
                summary,
                json.dumps(payload, ensure_ascii=True) if payload is not None else None,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)

    def list_events(self, *, run_id: int, limit: int = 100) -> list[AutopilotEventRecord]:
        rows = self.db.get_connection().execute(
            """
            SELECT id, run_id, cycle_no, actor, event_type, summary, payload_json
            FROM autopilot_events
            WHERE run_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        records: list[AutopilotEventRecord] = []
        for row in rows:
            payload = None
            if row["payload_json"]:
                decoded = json.loads(str(row["payload_json"]))
                payload = decoded if isinstance(decoded, dict) else None
            records.append(
                AutopilotEventRecord(
                    id=int(row["id"]),
                    run_id=int(row["run_id"]),
                    cycle_no=int(row["cycle_no"]),
                    actor=str(row["actor"]),
                    event_type=str(row["event_type"]),
                    summary=str(row["summary"]) if row["summary"] else None,
                    payload=payload,
                )
            )
        return records

    def append_stream_chunk(
        self,
        *,
        run_id: int,
        cycle_no: int,
        actor: str,
        channel: str,
        content: str,
    ) -> int:
        connection = self.db.get_connection()
        cursor = connection.execute(
            """
            INSERT INTO autopilot_stream_chunks (
                run_id, cycle_no, actor, channel, content
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, cycle_no, actor, channel, content),
        )
        connection.commit()
        return int(cursor.lastrowid)

    def list_stream_chunks(self, *, run_id: int, limit: int = 200) -> list[AutopilotStreamChunkRecord]:
        rows = self.db.get_connection().execute(
            """
            SELECT id, run_id, cycle_no, actor, channel, content
            FROM autopilot_stream_chunks
            WHERE run_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (run_id, limit),
        ).fetchall()
        return list(reversed([
            AutopilotStreamChunkRecord(
                id=int(row["id"]),
                run_id=int(row["run_id"]),
                cycle_no=int(row["cycle_no"]),
                actor=str(row["actor"]),
                channel=str(row["channel"]),
                content=str(row["content"]),
            )
            for row in rows
        ]))

    def _row_to_run(self, row) -> AutopilotRunRecord:  # noqa: ANN001
        return AutopilotRunRecord(
            id=int(row["id"]),
            project_id=int(row["project_id"]),
            chat_id=str(row["chat_id"]),
            created_by_user_id=int(row["created_by_user_id"]),
            goal=str(row["goal"]),
            status=str(row["status"]),
            supervisor_session_id=str(row["supervisor_session_id"]) if row["supervisor_session_id"] else None,
            worker_session_id=str(row["worker_session_id"]) if row["worker_session_id"] else None,
            current_phase=str(row["current_phase"]),
            cycle_count=int(row["cycle_count"]),
            max_cycles=int(row["max_cycles"]),
            no_progress_cycles=int(row["no_progress_cycles"]),
            same_instruction_cycles=int(row["same_instruction_cycles"]),
            last_instruction_fingerprint=(
                str(row["last_instruction_fingerprint"]) if row["last_instruction_fingerprint"] else None
            ),
            last_decision=str(row["last_decision"]) if row["last_decision"] else None,
            last_worker_summary=str(row["last_worker_summary"]) if row["last_worker_summary"] else None,
            last_supervisor_summary=(
                str(row["last_supervisor_summary"]) if row["last_supervisor_summary"] else None
            ),
            paused_reason=str(row["paused_reason"]) if row["paused_reason"] else None,
            stopped_by_user_id=int(row["stopped_by_user_id"]) if row["stopped_by_user_id"] else None,
        )
