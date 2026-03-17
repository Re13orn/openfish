"""Autopilot supervisor-worker orchestration for long-running tasks."""

from collections import deque
from dataclasses import dataclass
import json
import re
import shlex
import signal
import subprocess
from threading import Lock, Thread
import time
from typing import Any

from src.autopilot_store import AutopilotEventRecord, AutopilotRunRecord, AutopilotStreamChunkRecord
from src.codex_runner import CodexRunResult
from src.models import ProjectConfig
from src.task_store import TaskStore


JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
COMPLETION_CLAIM_PATTERN = re.compile(
    r"\b(done|complete|completed|finished|nothing left|ready to ship|verified)\b|已完成|完成了|做完了|搞定了|全部完成|没有更多",
    re.IGNORECASE,
)


@dataclass(slots=True)
class AutopilotStepResult:
    run: AutopilotRunRecord
    worker_payload: dict[str, Any] | None
    supervisor_payload: dict[str, Any] | None
    worker_result: CodexRunResult
    supervisor_result: CodexRunResult


@dataclass(slots=True)
class _RunExecutionState:
    stop_requested: bool = False
    process: subprocess.Popen[str] | None = None
    actor: str | None = None
    thread: Thread | None = None
    process_started_at: float | None = None
    raw_output_lines: deque[str] | None = None
    output_version: int = 0
    last_output_at: float | None = None

    def __post_init__(self) -> None:
        if self.raw_output_lines is None:
            self.raw_output_lines = deque(maxlen=40)


@dataclass(slots=True)
class AutopilotRuntimeSnapshot:
    run_id: int
    actor: str | None
    pid: int | None
    process_started_at: float | None
    thread_alive: bool
    output_version: int
    last_output_at: float | None


class AutopilotService:
    """Owns autopilot run lifecycle and one-step supervisor-worker execution."""

    TERMINAL_STATUSES = {"completed", "blocked", "needs_human", "stopped", "failed"}
    WORKER_OUTPUT_EXCERPT_LIMIT = 1200

    def __init__(self, *, tasks: TaskStore, codex, config) -> None:  # noqa: ANN001
        self.tasks = tasks
        self.codex = codex
        self.config = config
        self._run_state_guard = Lock()
        self._run_states: dict[int, _RunExecutionState] = {}
        self._raw_output_observer = None

    def set_raw_output_observer(self, observer) -> None:  # noqa: ANN001
        self._raw_output_observer = observer

    def create_run(
        self,
        *,
        project_id: int,
        chat_id: str,
        created_by_user_id: int,
        goal: str,
        max_cycles: int = 100,
    ) -> AutopilotRunRecord:
        normalized_max_cycles = max(1, min(int(max_cycles), 200))
        run_id = self.tasks.autopilot.create_run(
            project_id=project_id,
            chat_id=chat_id,
            created_by_user_id=created_by_user_id,
            goal=goal,
            status="created",
            current_phase="idle",
            max_cycles=normalized_max_cycles,
        )
        self.tasks.autopilot.append_event(
            run_id=run_id,
            cycle_no=0,
            actor="system",
            event_type="run_created",
            summary="已创建 autopilot 任务。",
            payload={"goal": goal, "max_cycles": normalized_max_cycles},
        )
        record = self.tasks.autopilot.get_run(run_id=run_id)
        assert record is not None
        return record

    def get_run(self, *, run_id: int) -> AutopilotRunRecord | None:
        return self.tasks.autopilot.get_run(run_id=run_id)

    def get_latest_run_for_project(self, *, project_id: int) -> AutopilotRunRecord | None:
        runs = self.tasks.autopilot.list_runs_for_project(project_id=project_id, limit=1)
        return runs[0] if runs else None

    def list_runs_for_project(self, *, project_id: int, limit: int = 20) -> list[AutopilotRunRecord]:
        return self.tasks.autopilot.list_runs_for_project(project_id=project_id, limit=limit)

    def list_events(self, *, run_id: int, limit: int = 20) -> list[AutopilotEventRecord]:
        return self.tasks.autopilot.list_events(run_id=run_id, limit=limit)

    def list_stream_chunks(self, *, run_id: int, limit: int = 200) -> list[AutopilotStreamChunkRecord]:
        return self.tasks.autopilot.list_stream_chunks(run_id=run_id, limit=limit)

    def get_runtime_snapshot(self, *, run_id: int) -> AutopilotRuntimeSnapshot | None:
        with self._run_state_guard:
            state = self._run_states.get(run_id)
            if state is None:
                return None
            pid = state.process.pid if state.process is not None else None
            thread_alive = state.thread.is_alive() if state.thread is not None else False
            return AutopilotRuntimeSnapshot(
                run_id=run_id,
                actor=state.actor,
                pid=pid,
                process_started_at=state.process_started_at,
                thread_alive=thread_alive,
                output_version=state.output_version,
                last_output_at=state.last_output_at,
            )

    def get_recent_output(self, *, run_id: int, limit: int = 12) -> list[str]:
        with self._run_state_guard:
            state = self._run_states.get(run_id)
            if state is None or state.raw_output_lines is None:
                return []
            lines = list(state.raw_output_lines)
        return lines[-limit:] if limit > 0 else lines

    def start_run_loop(
        self,
        *,
        project: ProjectConfig,
        run_id: int,
        model: str | None = None,
        progress_callback=None,  # noqa: ANN001
    ) -> bool:
        run = self.tasks.autopilot.get_run(run_id=run_id)
        if run is None:
            raise ValueError(f"autopilot run #{run_id} 不存在。")
        if run.status == "created":
            self.tasks.autopilot.update_run(
                run_id=run_id,
                status="running_worker",
                current_phase="worker",
            )
        with self._run_state_guard:
            state = self._run_states.setdefault(run_id, _RunExecutionState())
            thread = state.thread
            if thread is not None and thread.is_alive():
                return False
            state.stop_requested = False
            loop_thread = Thread(
                target=self._run_loop,
                kwargs={
                    "project": project,
                    "run_id": run_id,
                    "model": model,
                    "progress_callback": progress_callback,
                },
                daemon=True,
                name=f"autopilot-run-{run_id}",
            )
            state.thread = loop_thread
            loop_thread.start()
            return True

    def wait_for_run_loop(self, *, run_id: int, timeout: float | None = None) -> None:
        with self._run_state_guard:
            thread = self._run_states.get(run_id).thread if run_id in self._run_states else None
        if thread is not None:
            thread.join(timeout=timeout)

    def pause_run(self, *, run_id: int, reason: str | None = None) -> AutopilotRunRecord:
        run = self.tasks.autopilot.get_run(run_id=run_id)
        if run is None:
            raise ValueError(f"autopilot run #{run_id} 不存在。")
        if run.status in self.TERMINAL_STATUSES:
            raise ValueError(f"autopilot run #{run_id} 当前状态为 {run.status}，不可暂停。")
        self._request_stop(run_id=run.id, terminate_process=True)
        self.tasks.autopilot.update_run(
            run_id=run.id,
            status="paused",
            current_phase="idle",
            paused_reason=(reason or "用户暂停"),
        )
        self.tasks.autopilot.append_event(
            run_id=run.id,
            cycle_no=run.cycle_count,
            actor="human",
            event_type="paused",
            summary=reason or "用户暂停",
            payload=None,
        )
        updated = self.tasks.autopilot.get_run(run_id=run.id)
        assert updated is not None
        return updated

    def resume_run(self, *, run_id: int) -> AutopilotRunRecord:
        run = self.tasks.autopilot.get_run(run_id=run_id)
        if run is None:
            raise ValueError(f"autopilot run #{run_id} 不存在。")
        if run.status != "paused":
            raise ValueError(f"autopilot run #{run_id} 当前状态为 {run.status}，不可恢复。")
        with self._run_state_guard:
            state = self._run_states.setdefault(run.id, _RunExecutionState())
            state.stop_requested = False
        self.tasks.autopilot.update_run(
            run_id=run.id,
            status="running_worker",
            current_phase="worker",
        )
        self.tasks.autopilot.append_event(
            run_id=run.id,
            cycle_no=run.cycle_count,
            actor="human",
            event_type="resumed",
            summary="用户恢复运行",
            payload=None,
        )
        updated = self.tasks.autopilot.get_run(run_id=run.id)
        assert updated is not None
        return updated

    def stop_run(self, *, run_id: int, stopped_by_user_id: int | None = None, reason: str | None = None) -> AutopilotRunRecord:
        run = self.tasks.autopilot.get_run(run_id=run_id)
        if run is None:
            raise ValueError(f"autopilot run #{run_id} 不存在。")
        if run.status in self.TERMINAL_STATUSES:
            return run
        self._request_stop(run_id=run.id, terminate_process=True)
        self.tasks.autopilot.update_run(
            run_id=run.id,
            status="stopped",
            current_phase="idle",
            paused_reason=reason or "用户停止",
            stopped_by_user_id=stopped_by_user_id,
        )
        self.tasks.autopilot.append_event(
            run_id=run.id,
            cycle_no=run.cycle_count,
            actor="human",
            event_type="stopped",
            summary=reason or "用户停止",
            payload={"stopped_by_user_id": stopped_by_user_id} if stopped_by_user_id is not None else None,
        )
        updated = self.tasks.autopilot.get_run(run_id=run.id)
        assert updated is not None
        return updated

    def takeover_run(
        self,
        *,
        run_id: int,
        instruction: str,
        taken_by_user_id: int | None = None,
    ) -> AutopilotRunRecord:
        run = self.tasks.autopilot.get_run(run_id=run_id)
        if run is None:
            raise ValueError(f"autopilot run #{run_id} 不存在。")
        if run.status == "completed":
            raise ValueError(f"autopilot run #{run_id} 当前状态为 {run.status}，不可接管。")
        normalized = instruction.strip()
        if not normalized:
            raise ValueError("人工接管指令不能为空。")
        self._request_stop(run_id=run.id, terminate_process=True)
        self.tasks.autopilot.append_event(
            run_id=run.id,
            cycle_no=run.cycle_count,
            actor="human",
            event_type="takeover",
            summary=normalized,
            payload={"instruction": normalized, "taken_by_user_id": taken_by_user_id},
        )
        self.tasks.autopilot.update_run(
            run_id=run.id,
            status="running_worker",
            current_phase="worker",
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="",
            last_decision="continue",
            last_supervisor_summary=normalized,
            paused_reason="",
        )
        updated = self.tasks.autopilot.get_run(run_id=run.id)
        assert updated is not None
        return updated

    def step_run(
        self,
        *,
        project: ProjectConfig,
        run_id: int,
        model: str | None = None,
        progress_callback=None,  # noqa: ANN001
    ) -> AutopilotStepResult:
        run = self.tasks.autopilot.get_run(run_id=run_id)
        if run is None:
            raise ValueError(f"autopilot run #{run_id} 不存在。")
        single_step_paused = run.status == "paused"
        if run.status in self.TERMINAL_STATUSES:
            raise ValueError(f"autopilot run #{run_id} 当前状态为 {run.status}，不可继续。")
        if single_step_paused:
            self.tasks.autopilot.update_run(
                run_id=run.id,
                status="running_worker",
                current_phase="worker",
            )
            refreshed_run = self.tasks.autopilot.get_run(run_id=run.id)
            assert refreshed_run is not None
            run = refreshed_run

        cycle_no = run.cycle_count + 1

        worker_instruction = self._resolve_worker_instruction(project=project, run=run)
        worker_result = self._run_worker(
            project=project,
            run=run,
            instruction=worker_instruction,
            model=model,
            progress_callback=progress_callback,
        )
        worker_payload = self._parse_worker_payload(worker_result)
        worker_event_payload = self._build_worker_event_payload(
            worker_result=worker_result,
            worker_payload=worker_payload,
        )
        interrupted_run = self.tasks.autopilot.get_run(run_id=run.id)
        if interrupted_run is not None and interrupted_run.status in {"paused", "stopped"}:
            return AutopilotStepResult(
                run=interrupted_run,
                worker_payload=worker_payload,
                supervisor_payload=None,
                worker_result=worker_result,
                supervisor_result=self._synthetic_result("autopilot step interrupted"),
            )
        self.tasks.autopilot.append_event(
            run_id=run.id,
            cycle_no=cycle_no,
            actor="worker",
            event_type="stage_completed" if worker_result.ok else "stage_failed",
            summary=worker_result.summary,
            payload=worker_event_payload,
        )
        self.tasks.autopilot.update_run(
            run_id=run.id,
            worker_session_id=worker_result.session_id or run.worker_session_id,
            current_phase="supervisor",
            status="running_supervisor",
            cycle_count=cycle_no,
            last_worker_summary=worker_result.summary,
        )

        refreshed = self.tasks.autopilot.get_run(run_id=run.id)
        assert refreshed is not None

        supervisor_result = self._run_supervisor(
            project=project,
            run=refreshed,
            worker_summary=worker_result.summary,
            worker_stdout=worker_result.stdout,
            worker_payload=worker_payload,
            model=model,
            progress_callback=progress_callback,
        )
        supervisor_payload = self._parse_supervisor_payload(supervisor_result)
        supervisor_payload = self._apply_supervision_policy(
            run=refreshed,
            worker_summary=worker_result.summary,
            worker_stdout=worker_result.stdout,
            worker_payload=worker_payload,
            supervisor_payload=supervisor_payload,
        )
        interrupted_run = self.tasks.autopilot.get_run(run_id=run.id)
        if interrupted_run is not None and interrupted_run.status in {"paused", "stopped"}:
            return AutopilotStepResult(
                run=interrupted_run,
                worker_payload=worker_payload,
                supervisor_payload=supervisor_payload,
                worker_result=worker_result,
                supervisor_result=supervisor_result,
            )
        self.tasks.autopilot.append_event(
            run_id=run.id,
            cycle_no=cycle_no,
            actor="supervisor",
            event_type="decision_made" if supervisor_result.ok else "decision_failed",
            summary=supervisor_result.summary,
            payload=supervisor_payload,
        )

        updated_run = self._apply_supervisor_decision(
            run=refreshed,
            supervisor_result=supervisor_result,
            supervisor_payload=supervisor_payload,
            worker_payload=worker_payload,
        )
        if single_step_paused and updated_run.status == "running_worker":
            self.tasks.autopilot.update_run(
                run_id=updated_run.id,
                status="paused",
                current_phase="idle",
                paused_reason="单步执行后暂停",
            )
            paused_again = self.tasks.autopilot.get_run(run_id=updated_run.id)
            assert paused_again is not None
            updated_run = paused_again
        return AutopilotStepResult(
            run=updated_run,
            worker_payload=worker_payload,
            supervisor_payload=supervisor_payload,
            worker_result=worker_result,
            supervisor_result=supervisor_result,
        )

    def _save_run_summary_to_memory(self, *, run_id: int) -> None:
        """Write a compact autopilot run summary to project_memory after the run ends."""
        try:
            run = self.tasks.autopilot.get_run(run_id=run_id)
            if run is None or run.cycle_count == 0:
                return
            if run.status not in {"completed", "blocked", "needs_human", "failed"}:
                return
            summary = run.last_supervisor_summary or run.last_worker_summary
            if not summary:
                return
            goal_short = run.goal[:80]
            content = (
                f"goal: {goal_short}\n"
                f"status={run.status}, cycles={run.cycle_count}\n"
                f"result: {summary[:500]}"
            )
            title = f"[autopilot] {goal_short}"
            self.tasks.add_autopilot_run_note(
                project_id=run.project_id,
                title=title,
                content=content,
                run_id=run_id,
            )
        except Exception:  # noqa: BLE001
            pass  # never let memory saving crash the run loop

    def _run_loop(
        self,
        *,
        project: ProjectConfig,
        run_id: int,
        model: str | None,
        progress_callback=None,  # noqa: ANN001
    ) -> None:
        try:
            self.tasks.autopilot.append_event(
                run_id=run_id,
                cycle_no=self.tasks.autopilot.get_run(run_id=run_id).cycle_count if self.tasks.autopilot.get_run(run_id=run_id) else 0,
                actor="system",
                event_type="loop_started",
                summary="后台自治循环已启动。",
                payload=None,
            )
            while True:
                run = self.tasks.autopilot.get_run(run_id=run_id)
                if run is None:
                    return
                if run.status in self.TERMINAL_STATUSES or run.status == "paused":
                    return
                if self._should_stop(run_id):
                    return
                self.tasks.autopilot.append_event(
                    run_id=run_id,
                    cycle_no=run.cycle_count + 1,
                    actor="system",
                    event_type="cycle_started",
                    summary=f"开始第 {run.cycle_count + 1} 轮。",
                    payload={"phase": run.current_phase or "worker"},
                )
                self.step_run(
                    project=project,
                    run_id=run_id,
                    model=model,
                    progress_callback=progress_callback,
                )
        except Exception as exc:  # noqa: BLE001
            self.tasks.autopilot.update_run(
                run_id=run_id,
                status="failed",
                current_phase="idle",
                last_supervisor_summary=str(exc),
            )
            self.tasks.autopilot.append_event(
                run_id=run_id,
                cycle_no=self.tasks.autopilot.get_run(run_id=run_id).cycle_count if self.tasks.autopilot.get_run(run_id=run_id) else 0,
                actor="system",
                event_type="loop_failed",
                summary=str(exc),
                payload=None,
            )
        finally:
            self._save_run_summary_to_memory(run_id=run_id)
            with self._run_state_guard:
                state = self._run_states.get(run_id)
                if state is not None:
                    state.thread = None
                    state.actor = None
                    state.process = None

    def _get_project_memory_context(self, *, project_id: int, max_chars: int = 500) -> str:
        """Return a compact project memory string for prompt injection, or empty string."""
        try:
            memory = self.tasks.get_memory_snapshot(project_id=project_id, page=1, page_size=3)
        except Exception:  # noqa: BLE001
            return ""
        parts: list[str] = []
        if memory.project_summary:
            parts.append(memory.project_summary[:200])
        if memory.notes:
            parts.append("Notes: " + " | ".join(n[:80] for n in memory.notes[:3]))
        if memory.recent_task_summaries:
            parts.append("Recent: " + " | ".join(s[:100] for s in memory.recent_task_summaries[:2]))
        if not parts:
            return ""
        context = "\n".join(parts)
        return context[:max_chars] + "..." if len(context) > max_chars else context

    def _resolve_worker_instruction(self, *, project: ProjectConfig, run: AutopilotRunRecord) -> str:
        if run.cycle_count == 0:
            base_prompt = self._build_initial_worker_prompt(
                goal=run.goal,
                bootstrap_instruction=project.default_autopilot_bootstrap_instruction,
            )
            memory_context = self._get_project_memory_context(project_id=run.project_id)
            if memory_context:
                return f"[Project Context]\n{memory_context}\n---\n{base_prompt}"
            return base_prompt

        events = self.tasks.autopilot.list_events(run_id=run.id, limit=100)
        for event in reversed(events):
            if event.actor == "human" and event.event_type == "takeover" and event.payload:
                takeover_instruction = event.payload.get("instruction")
                if isinstance(takeover_instruction, str) and takeover_instruction.strip():
                    return self._build_followup_worker_prompt(
                        goal=run.goal,
                        instruction=takeover_instruction.strip(),
                    )
            if event.actor == "supervisor" and event.payload:
                next_instruction = event.payload.get("next_instruction_for_b")
                if isinstance(next_instruction, str) and next_instruction.strip():
                    return self._build_followup_worker_prompt(
                        goal=run.goal,
                        instruction=next_instruction.strip(),
                    )
        return self._build_followup_worker_prompt(
            goal=run.goal,
            instruction="继续推进任务。",
        )

    def _run_worker(
        self,
        *,
        project: ProjectConfig,
        run: AutopilotRunRecord,
        instruction: str,
        model: str | None,
        progress_callback=None,  # noqa: ANN001
    ) -> CodexRunResult:
        process_callback = self._make_actor_process_callback(
            run_id=run.id,
            actor="worker",
            cycle_no=run.cycle_count + 1,
            event_type="stage_started",
            summary="B 已启动本轮执行。",
        )
        combined_progress_callback = self._combine_progress_callbacks(
            progress_callback,
            self._make_actor_output_callback(run_id=run.id, actor="worker", cycle_no=run.cycle_count + 1),
        )
        if run.worker_session_id:
            return self.codex.resume_session(
                project,
                run.worker_session_id,
                instruction,
                model=model,
                sandbox_mode=self.config.autopilot_codex_sandbox_mode,
                approval_mode=self.config.autopilot_codex_approval_mode,
                progress_callback=combined_progress_callback,
                process_callback=process_callback,
            )
        return self.codex.run(
            project,
            instruction,
            model=model,
            sandbox_mode=self.config.autopilot_codex_sandbox_mode,
            approval_mode=self.config.autopilot_codex_approval_mode,
            progress_callback=combined_progress_callback,
            process_callback=process_callback,
        )

    def _run_supervisor(
        self,
        *,
        project: ProjectConfig,
        run: AutopilotRunRecord,
        worker_summary: str,
        worker_stdout: str,
        worker_payload: dict[str, Any] | None,
        model: str | None,
        progress_callback=None,  # noqa: ANN001
    ) -> CodexRunResult:
        prompt = self._build_supervisor_prompt(
            goal=run.goal,
            project_snapshot=self._build_project_snapshot(project, run_tests=False),
            worker_summary=worker_summary,
            worker_stdout=worker_stdout,
            worker_payload=worker_payload,
            cycle_history=self._build_cycle_history_excerpt(run_id=run.id),
        )
        process_callback = self._make_actor_process_callback(
            run_id=run.id,
            actor="supervisor",
            cycle_no=run.cycle_count,
            event_type="decision_started",
            summary="A 已开始评估本轮结果。",
        )
        combined_progress_callback = self._combine_progress_callbacks(
            progress_callback,
            self._make_actor_output_callback(run_id=run.id, actor="supervisor", cycle_no=run.cycle_count),
        )
        supervisor_model = getattr(self.config, "autopilot_supervisor_model", None) or model
        if run.supervisor_session_id:
            return self.codex.ask_in_session(
                project,
                run.supervisor_session_id,
                prompt,
                model=supervisor_model,
                sandbox_mode=self.config.autopilot_codex_sandbox_mode,
                approval_mode=self.config.autopilot_codex_approval_mode,
                progress_callback=combined_progress_callback,
                process_callback=process_callback,
            )
        return self.codex.ask(
            project,
            prompt,
            model=supervisor_model,
            sandbox_mode=self.config.autopilot_codex_sandbox_mode,
            approval_mode=self.config.autopilot_codex_approval_mode,
            progress_callback=combined_progress_callback,
            process_callback=process_callback,
        )

    def _apply_supervisor_decision(
        self,
        *,
        run: AutopilotRunRecord,
        supervisor_result: CodexRunResult,
        supervisor_payload: dict[str, Any] | None,
        worker_payload: dict[str, Any] | None,
    ) -> AutopilotRunRecord:
        payload = supervisor_payload or {}
        decision = str(payload.get("decision") or "needs_human")
        progress_made = bool(payload.get("progress_made"))
        next_instruction = str(payload.get("next_instruction_for_b") or "").strip()
        fingerprint = self._instruction_fingerprint(next_instruction) if next_instruction else None

        no_progress_cycles = 0 if progress_made else run.no_progress_cycles + 1
        same_instruction_cycles = 0
        if fingerprint and fingerprint == run.last_instruction_fingerprint:
            same_instruction_cycles = run.same_instruction_cycles + 1

        final_status = decision
        current_phase = "idle"
        paused_reason = None

        if decision == "continue":
            if run.cycle_count >= run.max_cycles:
                final_status = "blocked"
                current_phase = "idle"
                paused_reason = None
            else:
                final_status = "running_worker"
                current_phase = "worker"
        elif decision == "complete":
            final_status = "completed"
        elif decision not in self.TERMINAL_STATUSES:
            final_status = "needs_human"

        self.tasks.autopilot.update_run(
            run_id=run.id,
            status=final_status,
            supervisor_session_id=supervisor_result.session_id or run.supervisor_session_id,
            current_phase=current_phase,
            no_progress_cycles=no_progress_cycles,
            same_instruction_cycles=same_instruction_cycles,
            last_instruction_fingerprint=fingerprint,
            last_decision=str(supervisor_payload.get("classification") or decision),
            last_worker_summary=self._payload_summary(worker_payload) or run.last_worker_summary,
            last_supervisor_summary=self._payload_summary(supervisor_payload) or supervisor_result.summary,
            paused_reason=paused_reason,
        )
        updated = self.tasks.autopilot.get_run(run_id=run.id)
        assert updated is not None
        return updated

    def _parse_worker_payload(self, result: CodexRunResult) -> dict[str, Any] | None:
        parsed = self._extract_json_object(result.stdout) or self._extract_json_object(result.summary)
        if isinstance(parsed, dict):
            return parsed
        return None

    def _build_worker_event_payload(
        self,
        *,
        worker_result: CodexRunResult,
        worker_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = dict(worker_payload or {})
        raw_output_excerpt = worker_result.stdout.strip()
        if raw_output_excerpt:
            payload["raw_output_excerpt"] = raw_output_excerpt[: self.WORKER_OUTPUT_EXCERPT_LIMIT]
        return payload

    def _parse_supervisor_payload(self, result: CodexRunResult) -> dict[str, Any]:
        parsed = self._extract_json_object(result.stdout) or self._extract_json_object(result.summary)
        if isinstance(parsed, dict):
            return parsed
        return {
            "classification": "blocked",
            "reason": "Supervisor output was not parseable JSON.",
            "confidence": "low",
        }

    def _apply_supervision_policy(
        self,
        *,
        run: AutopilotRunRecord,
        worker_summary: str,
        worker_stdout: str,
        worker_payload: dict[str, Any] | None,
        supervisor_payload: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = dict(supervisor_payload)
        classification = self._normalize_supervisor_classification(enriched)
        enriched["classification"] = classification

        if classification == "hesitation":
            push_level = self._count_recent_hesitations(run_id=run.id) + 1
            enriched.update(
                {
                    "decision": "continue",
                    "progress_summary": enriched.get("progress_summary") or worker_summary,
                    "progress_made": True,
                    "next_instruction_for_b": self._build_push_instruction(
                        push_level=push_level,
                        reason=str(enriched.get("reason") or "").strip(),
                    ),
                    "push_level": push_level,
                }
            )
        elif classification == "complete":
            enriched.update(
                {
                    "decision": "complete",
                    "progress_summary": enriched.get("progress_summary") or worker_summary,
                    "progress_made": True,
                    "next_instruction_for_b": "",
                }
            )
        elif classification == "blocked":
            enriched.update(
                {
                    "decision": "needs_human",
                    "progress_summary": enriched.get("progress_summary") or worker_summary,
                    "progress_made": False,
                    "next_instruction_for_b": "",
                }
            )
        else:
            enriched["decision"] = str(enriched.get("decision") or "needs_human").strip()
        return enriched

    def _normalize_supervisor_classification(self, payload: dict[str, Any]) -> str:
        raw = str(payload.get("classification") or "").strip().lower()
        if raw in {"hesitation", "blocked", "complete"}:
            return raw
        legacy_decision = str(payload.get("decision") or "").strip().lower()
        if legacy_decision == "complete":
            return "complete"
        if legacy_decision == "continue":
            return "hesitation"
        return "blocked"

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
        normalized = text.strip()
        if not normalized:
            return None
        try:
            loaded = json.loads(normalized)
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            pass

        match = JSON_BLOCK_PATTERN.search(normalized)
        if match:
            try:
                loaded = json.loads(match.group(1))
                return loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                return None
        return None

    def _payload_summary(self, payload: dict[str, Any] | None) -> str | None:
        if not payload:
            return None
        for key in ("progress_summary", "current_state", "completed_work", "reason"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _worker_claims_completion(
        self,
        *,
        summary: str,
        stdout: str,
        payload: dict[str, Any] | None,
    ) -> bool:
        if payload and payload.get("task_complete") is True:
            return True
        haystack = "\n".join(part for part in (summary, stdout) if part).strip()
        return bool(haystack and COMPLETION_CLAIM_PATTERN.search(haystack))

    def _count_worker_completion_claims(self, *, run_id: int) -> int:
        count = 0
        for event in self.tasks.autopilot.list_events(run_id=run_id, limit=200):
            if event.actor != "worker":
                continue
            payload = event.payload if isinstance(event.payload, dict) else None
            if self._worker_claims_completion(
                summary=event.summary or "",
                stdout=(payload or {}).get("raw_output_excerpt", "") if payload else "",
                payload=payload,
            ):
                count += 1
        return count

    def _count_supervisor_verification_rounds(self, *, run_id: int) -> int:
        count = 0
        for event in self.tasks.autopilot.list_events(run_id=run_id, limit=200):
            if event.actor != "supervisor" or not isinstance(event.payload, dict):
                continue
            if event.payload.get("verification_round"):
                count += 1
        return count

    def _count_recent_hesitations(self, *, run_id: int, limit: int = 12) -> int:
        count = 0
        for event in reversed(self.tasks.autopilot.list_events(run_id=run_id, limit=limit)):
            if event.actor != "supervisor" or event.event_type != "decision_made":
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if str(payload.get("classification") or "").strip().lower() == "hesitation":
                count += 1
        return count

    def _build_push_instruction(self, *, push_level: int, reason: str) -> str:
        if push_level <= 1:
            instruction = "继续推进，不要等待确认；直接完成你已经知道的下一步。"
        elif push_level == 2:
            instruction = "不要停在总结或建议层，直接完成剩余步骤；除非缺少你无法自行获取的信息，否则不要停止。"
        else:
            instruction = "不要再次停下来汇报或等待确认。直接完成所有剩余步骤；只有在缺少你无法自行获取的信息或权限时才停止。"
        if not reason:
            return instruction
        return f"{instruction}\n\n你这次停下来的原因：{reason}"

    def _determine_supervision_mode(
        self,
        *,
        completion_claim_count: int,
        verification_rounds: int,
    ) -> str:
        if completion_claim_count >= 3 or verification_rounds >= 1:
            return "hard_verify"
        if completion_claim_count >= 2:
            return "skeptical"
        return "normal"

    def _build_project_snapshot(self, project: ProjectConfig, *, run_tests: bool = False) -> str:
        project_path = project.path
        lines = [f"path={project_path}"]
        try:
            entries = sorted(project_path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower()))
            top_entries = []
            for entry in entries[:12]:
                suffix = "/" if entry.is_dir() else ""
                top_entries.append(f"{entry.name}{suffix}")
            if top_entries:
                lines.append("top_level=" + ", ".join(top_entries))
        except OSError as exc:
            lines.append(f"top_level_error={exc}")

        git_dir = project_path / ".git"
        if git_dir.exists():
            try:
                completed = subprocess.run(  # noqa: S603
                    ["git", "status", "--short"],
                    cwd=str(project_path),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                status_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
                if status_lines:
                    lines.append("git_status=" + " | ".join(status_lines[:10]))
                else:
                    lines.append("git_status=clean")
            except OSError as exc:
                lines.append(f"git_status_error={exc}")
            try:
                diff_completed = subprocess.run(  # noqa: S603
                    ["git", "diff", "--stat", "HEAD"],
                    cwd=str(project_path),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                diff_lines = [line.strip() for line in diff_completed.stdout.splitlines() if line.strip()]
                if diff_lines:
                    lines.append("git_diff_stat=" + " | ".join(diff_lines[:8]))
            except (OSError, subprocess.TimeoutExpired):
                pass

        if run_tests and project.test_command:
            lines.append(self._run_test_command(project))

        return "\n".join(lines)

    def _run_test_command(self, project: ProjectConfig) -> str:
        """Run the project test_command and return a compact result line for snapshot injection."""
        assert project.test_command  # caller guarantees this
        try:
            cmd = shlex.split(project.test_command)
        except ValueError:
            return f"test_result=parse_error({project.test_command!r})"
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                cwd=str(project.path),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return "test_result=TIMEOUT(>30s)"
        except OSError as exc:
            return f"test_result=ERROR({exc})"

        combined = (proc.stdout + "\n" + proc.stderr).strip()
        tail_lines = [ln.strip() for ln in combined.splitlines() if ln.strip()][-5:]
        verdict = "PASS" if proc.returncode == 0 else "FAIL"
        tail = " | ".join(tail_lines) if tail_lines else "(no output)"
        # Keep the line compact: trim at 300 chars
        result = f"test_result={verdict}(rc={proc.returncode}) {tail}"
        return result[:300]

    def _instruction_fingerprint(self, value: str) -> str:
        return " ".join(value.strip().lower().split())[:200]

    def _synthetic_result(self, summary: str) -> CodexRunResult:
        return CodexRunResult(
            ok=False,
            stdout=summary,
            stderr="",
            exit_code=1,
            summary=summary,
            session_id=None,
            used_json_output=False,
            command=["autopilot"],
        )

    def _set_active_process(
        self,
        run_id: int,
        actor: str,
        proc: subprocess.Popen[str] | None,
    ) -> None:
        now = time.monotonic()
        with self._run_state_guard:
            state = self._run_states.setdefault(run_id, _RunExecutionState())
            state.process = proc
            state.actor = actor
            state.process_started_at = now if proc is not None else None

    def _make_actor_process_callback(
        self,
        *,
        run_id: int,
        actor: str,
        cycle_no: int,
        event_type: str,
        summary: str,
    ):
        emitted = False

        def _callback(proc: subprocess.Popen[str] | None) -> None:
            nonlocal emitted
            self._set_active_process(run_id, actor, proc)
            if proc is None or emitted:
                return
            emitted = True
            pid = getattr(proc, "pid", None)
            payload = {"pid": pid} if pid is not None else None
            self.tasks.autopilot.append_event(
                run_id=run_id,
                cycle_no=cycle_no,
                actor=actor,
                event_type=event_type,
                summary=summary,
                payload=payload,
            )

        return _callback

    def _make_actor_output_callback(self, *, run_id: int, actor: str, cycle_no: int):
        actor_label = "A" if actor == "supervisor" else "B"

        def _callback(channel: str, text: str) -> None:
            line = text.strip()
            if not line:
                return
            rendered = f"{actor_label}>[{channel}] {line}"
            observer = None
            with self._run_state_guard:
                state = self._run_states.setdefault(run_id, _RunExecutionState())
                if state.raw_output_lines and state.raw_output_lines[-1] == rendered:
                    return
                state.raw_output_lines.append(rendered)
                state.output_version += 1
                state.last_output_at = time.monotonic()
                observer = self._raw_output_observer
            self.tasks.autopilot.append_stream_chunk(
                run_id=run_id,
                cycle_no=cycle_no,
                actor=actor,
                channel=channel,
                content=line,
            )
            if observer is not None:
                observer(run_id)

        return _callback

    def _combine_progress_callbacks(self, *callbacks):
        valid_callbacks = [callback for callback in callbacks if callback is not None]
        if not valid_callbacks:
            return None

        def _callback(channel: str, text: str) -> None:
            for callback in valid_callbacks:
                callback(channel, text)

        return _callback

    def _request_stop(self, *, run_id: int, terminate_process: bool) -> None:
        with self._run_state_guard:
            state = self._run_states.setdefault(run_id, _RunExecutionState())
            state.stop_requested = True
            proc = state.process
        if terminate_process and proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            except OSError:
                try:
                    proc.send_signal(signal.SIGTERM)
                except OSError:
                    return

    def _should_stop(self, run_id: int) -> bool:
        with self._run_state_guard:
            state = self._run_states.get(run_id)
            if state is None:
                return False
            return state.stop_requested

    def _build_initial_worker_prompt(self, *, goal: str, bootstrap_instruction: str | None) -> str:
        if bootstrap_instruction and bootstrap_instruction.strip():
            return bootstrap_instruction.strip()
        return goal.strip()

    def _build_followup_worker_prompt(self, *, goal: str, instruction: str) -> str:
        _ = goal
        return instruction.strip()

    def _build_cycle_history_excerpt(self, *, run_id: int, last_n: int = 5) -> str:
        """Return a compact summary of the last N supervisor decisions for prompt context."""
        events = self.tasks.autopilot.list_events(run_id=run_id, limit=200)
        decisions = []
        for event in events:
            if event.actor != "supervisor" or event.event_type != "decision_made":
                continue
            payload = event.payload or {}
            decision = payload.get("decision", "?")
            summary = (payload.get("progress_summary") or event.summary or "")[:120].replace("\n", " ")
            decisions.append(f"[C{event.cycle_no}] {decision}: {summary}")
        recent = decisions[-last_n:]
        return "\n".join(recent) if recent else ""

    def _build_supervisor_prompt(
        self,
        *,
        goal: str,
        project_snapshot: str,
        worker_summary: str,
        worker_stdout: str,
        worker_payload: dict[str, Any] | None,
        cycle_history: str = "",
    ) -> str:
        payload_text = json.dumps(worker_payload or {}, ensure_ascii=True)
        worker_output_excerpt = (worker_stdout.strip() or worker_summary.strip())[: self.WORKER_OUTPUT_EXCERPT_LIMIT]
        history_section = f"Recent cycle decisions:\n{cycle_history}\n\n" if cycle_history else ""
        return (
            "You are the supervisor in OpenFish autopilot mode.\n"
            "Do not execute the task yourself. Your only job is to decide whether the worker is hesitating or truly blocked.\n"
            "Classify as hesitation when the worker has already started doing real work, can describe what it did, "
            "and implies a concrete next step, but stopped because of caution.\n"
            "Classify as blocked only when the worker lacks information, permissions, or a decision that it cannot obtain by itself.\n"
            "Bias toward hesitation.\n"
            "Do not output free-form prose. Output JSON only using this schema:\n"
            "{"
            '"classification":"hesitation",'
            '"reason":"...",'
            '"confidence":"medium"'
            "}\n"
            "Valid classifications: hesitation, blocked.\n\n"
            f"Goal:\n{goal}\n\n"
            f"Project snapshot:\n{project_snapshot}\n\n"
            f"{history_section}"
            f"Worker summary:\n{worker_summary}\n\n"
            f"Worker raw output:\n{worker_output_excerpt}\n\n"
            f"Worker payload:\n{payload_text}"
        )
