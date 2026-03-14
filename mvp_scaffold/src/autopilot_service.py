"""Autopilot supervisor-worker orchestration for long-running tasks."""

from dataclasses import dataclass
import json
import re
import signal
import subprocess
from threading import Lock, Thread
from typing import Any

from src.autopilot_store import AutopilotEventRecord, AutopilotRunRecord
from src.codex_runner import CodexRunResult
from src.models import ProjectConfig
from src.task_store import TaskStore


JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


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


class AutopilotService:
    """Owns autopilot run lifecycle and one-step supervisor-worker execution."""

    TERMINAL_STATUSES = {"completed", "blocked", "needs_human", "stopped", "failed"}

    def __init__(self, *, tasks: TaskStore, codex) -> None:  # noqa: ANN001
        self.tasks = tasks
        self.codex = codex
        self._run_state_guard = Lock()
        self._run_states: dict[int, _RunExecutionState] = {}

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

    def list_events(self, *, run_id: int, limit: int = 20) -> list[AutopilotEventRecord]:
        return self.tasks.autopilot.list_events(run_id=run_id, limit=limit)

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
        if run.status in self.TERMINAL_STATUSES:
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

        worker_instruction = self._resolve_worker_instruction(run=run)
        worker_result = self._run_worker(
            project=project,
            run=run,
            instruction=worker_instruction,
            model=model,
            progress_callback=progress_callback,
        )
        worker_payload = self._parse_worker_payload(worker_result)
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
            payload=worker_payload,
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
            worker_payload=worker_payload,
            model=model,
            progress_callback=progress_callback,
        )
        supervisor_payload = self._parse_supervisor_payload(supervisor_result)
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

    def _run_loop(
        self,
        *,
        project: ProjectConfig,
        run_id: int,
        model: str | None,
        progress_callback=None,  # noqa: ANN001
    ) -> None:
        try:
            while True:
                run = self.tasks.autopilot.get_run(run_id=run_id)
                if run is None:
                    return
                if run.status in self.TERMINAL_STATUSES or run.status == "paused":
                    return
                if self._should_stop(run_id):
                    return
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
            with self._run_state_guard:
                state = self._run_states.get(run_id)
                if state is not None:
                    state.thread = None
                    state.actor = None
                    state.process = None

    def _resolve_worker_instruction(self, *, run: AutopilotRunRecord) -> str:
        if run.cycle_count == 0:
            return self._build_initial_worker_prompt(goal=run.goal)

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
            instruction="继续推进任务，并输出本轮结构化状态。",
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
        if run.worker_session_id:
            return self.codex.resume_session(
                project,
                run.worker_session_id,
                instruction,
                model=model,
                progress_callback=progress_callback,
                process_callback=lambda proc, run_id=run.id: self._set_active_process(run_id, "worker", proc),
            )
        return self.codex.run(
            project,
            instruction,
            model=model,
            progress_callback=progress_callback,
            process_callback=lambda proc, run_id=run.id: self._set_active_process(run_id, "worker", proc),
        )

    def _run_supervisor(
        self,
        *,
        project: ProjectConfig,
        run: AutopilotRunRecord,
        worker_summary: str,
        worker_payload: dict[str, Any] | None,
        model: str | None,
        progress_callback=None,  # noqa: ANN001
    ) -> CodexRunResult:
        prompt = self._build_supervisor_prompt(
            goal=run.goal,
            worker_summary=worker_summary,
            worker_payload=worker_payload,
        )
        if run.supervisor_session_id:
            return self.codex.ask_in_session(
                project,
                run.supervisor_session_id,
                prompt,
                model=model,
                progress_callback=progress_callback,
                process_callback=lambda proc, run_id=run.id: self._set_active_process(run_id, "supervisor", proc),
            )
        return self.codex.ask(
            project,
            prompt,
            model=model,
            progress_callback=progress_callback,
            process_callback=lambda proc, run_id=run.id: self._set_active_process(run_id, "supervisor", proc),
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
            elif no_progress_cycles >= 2 or same_instruction_cycles >= 2:
                final_status = "blocked"
                current_phase = "idle"
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
            last_decision=decision,
            last_worker_summary=self._payload_summary(worker_payload) or run.last_worker_summary,
            last_supervisor_summary=self._payload_summary(supervisor_payload) or supervisor_result.summary,
            paused_reason=paused_reason,
        )
        updated = self.tasks.autopilot.get_run(run_id=run.id)
        assert updated is not None
        return updated

    def _parse_worker_payload(self, result: CodexRunResult) -> dict[str, Any]:
        parsed = self._extract_json_object(result.stdout) or self._extract_json_object(result.summary)
        if isinstance(parsed, dict):
            return parsed
        return {
            "completed_work": result.summary,
            "current_state": result.summary,
            "remaining_work": "unknown",
            "blockers": result.stderr or "none",
            "recommended_next_step": "继续推进任务，并输出下一轮结构化状态。",
            "progress_made": bool(result.ok),
            "task_complete": False,
        }

    def _parse_supervisor_payload(self, result: CodexRunResult) -> dict[str, Any]:
        parsed = self._extract_json_object(result.stdout) or self._extract_json_object(result.summary)
        if isinstance(parsed, dict):
            return parsed
        return {
            "decision": "needs_human",
            "reason": "Supervisor output was not parseable JSON.",
            "progress_summary": result.summary,
            "progress_made": False,
            "confidence": "low",
            "next_instruction_for_b": "",
        }

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
        with self._run_state_guard:
            state = self._run_states.setdefault(run_id, _RunExecutionState())
            state.process = proc
            state.actor = actor

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

    def _build_initial_worker_prompt(self, *, goal: str) -> str:
        return (
            "You are the worker in OpenFish autopilot mode.\n"
            "Your job is to push the task forward and avoid handing control back to the human too early.\n"
            "Make practical progress on the task, then end with JSON only using this schema:\n"
            "{"
            '"completed_work":"...",'
            '"current_state":"...",'
            '"remaining_work":"...",'
            '"blockers":"...",'
            '"recommended_next_step":"...",'
            '"progress_made":true,'
            '"task_complete":false'
            "}\n\n"
            f"Goal:\n{goal}"
        )

    def _build_followup_worker_prompt(self, *, goal: str, instruction: str) -> str:
        return (
            "You are the worker in OpenFish autopilot mode.\n"
            "Continue the same long-running task. Execute the instruction, keep pushing forward, and end with JSON only.\n"
            "Required JSON schema:\n"
            "{"
            '"completed_work":"...",'
            '"current_state":"...",'
            '"remaining_work":"...",'
            '"blockers":"...",'
            '"recommended_next_step":"...",'
            '"progress_made":true,'
            '"task_complete":false'
            "}\n\n"
            f"Goal:\n{goal}\n\n"
            f"Supervisor instruction:\n{instruction}"
        )

    def _build_supervisor_prompt(
        self,
        *,
        goal: str,
        worker_summary: str,
        worker_payload: dict[str, Any] | None,
    ) -> str:
        payload_text = json.dumps(worker_payload or {}, ensure_ascii=True)
        return (
            "You are the supervisor in OpenFish autopilot mode.\n"
            "Do not execute the task yourself. Judge whether the worker should continue, stop, or escalate.\n"
            "Do not output free-form prose. Output JSON only using this schema:\n"
            "{"
            '"decision":"continue",'
            '"reason":"...",'
            '"progress_summary":"...",'
            '"progress_made":true,'
            '"confidence":"medium",'
            '"next_instruction_for_b":"..."'
            "}\n"
            "Valid decisions: continue, complete, blocked, needs_human.\n"
            "If decision is not continue, next_instruction_for_b should be empty.\n\n"
            f"Goal:\n{goal}\n\n"
            f"Worker summary:\n{worker_summary}\n\n"
            f"Worker payload:\n{payload_text}"
        )
