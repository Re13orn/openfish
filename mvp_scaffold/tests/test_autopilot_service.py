from pathlib import Path
import time
from types import SimpleNamespace

from src.autopilot_service import AutopilotService
from src.codex_runner import CodexRunResult
from src.db import Database
from src.models import ProjectConfig
from src.task_store import TaskStore


def _codex_result(summary: str, *, session_id: str, stdout: str | None = None) -> CodexRunResult:
    text = stdout or summary
    return CodexRunResult(
        ok=True,
        stdout=text,
        stderr="",
        exit_code=0,
        summary=summary,
        session_id=session_id,
        used_json_output=False,
        command=["codex", "exec"],
    )


class CodexStub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_worker_instruction: str | None = None

    def run(self, project, prompt: str, *, model=None, progress_callback=None, process_callback=None):  # noqa: ANN001, ANN201
        _ = project
        self.last_worker_instruction = prompt
        _ = model
        _ = progress_callback
        if process_callback is not None:
            process_callback(SimpleNamespace(pid=1001))
            process_callback(None)
        self.calls.append("run")
        return _codex_result(
            "worker round 1",
            session_id="sess-worker-1",
            stdout='{"completed_work":"done","current_state":"worker round 1","remaining_work":"tests","blockers":"none","recommended_next_step":"run tests","progress_made":true,"task_complete":false}',
        )

    def resume_session(self, project, session_id: str, instruction: str, *, model=None, progress_callback=None, process_callback=None):  # noqa: ANN001, ANN201
        _ = project
        _ = session_id
        self.last_worker_instruction = instruction
        _ = model
        _ = progress_callback
        if process_callback is not None:
            process_callback(SimpleNamespace(pid=1002))
            process_callback(None)
        self.calls.append("resume_session")
        return _codex_result(
            "worker round resumed",
            session_id="sess-worker-1",
            stdout='{"completed_work":"more","current_state":"worker round resumed","remaining_work":"verify","blockers":"none","recommended_next_step":"verify","progress_made":true,"task_complete":false}',
        )

    def ask(self, project, question: str, *, model=None, progress_callback=None, process_callback=None):  # noqa: ANN001, ANN201
        _ = project
        _ = question
        _ = model
        _ = progress_callback
        if process_callback is not None:
            process_callback(SimpleNamespace(pid=2001))
            process_callback(None)
        self.calls.append("ask")
        return _codex_result(
            "supervisor continue",
            session_id="sess-supervisor-1",
            stdout='{"decision":"continue","reason":"still work left","progress_summary":"good progress","progress_made":true,"confidence":"medium","next_instruction_for_b":"run tests next"}',
        )

    def ask_in_session(self, project, session_id: str, question: str, *, model=None, progress_callback=None, process_callback=None):  # noqa: ANN001, ANN201
        _ = project
        _ = session_id
        _ = question
        _ = model
        _ = progress_callback
        if process_callback is not None:
            process_callback(SimpleNamespace(pid=2002))
            process_callback(None)
        self.calls.append("ask_in_session")
        return _codex_result(
            "supervisor followup",
            session_id="sess-supervisor-1",
            stdout='{"decision":"continue","reason":"still work left","progress_summary":"good progress","progress_made":true,"confidence":"medium","next_instruction_for_b":"run tests next"}',
        )


class CompletingCodexStub(CodexStub):
    def ask(self, project, question: str, *, model=None, progress_callback=None, process_callback=None):  # noqa: ANN001, ANN201
        _ = project
        _ = question
        _ = model
        _ = progress_callback
        _ = process_callback
        self.calls.append("ask")
        return _codex_result(
            "supervisor complete",
            session_id="sess-supervisor-1",
            stdout='{"decision":"complete","reason":"done","progress_summary":"task complete","progress_made":true,"confidence":"high","next_instruction_for_b":""}',
        )


class SlowCompletingCodexStub(CompletingCodexStub):
    def run(self, project, prompt: str, *, model=None, progress_callback=None, process_callback=None):  # noqa: ANN001, ANN201
        _ = project
        _ = prompt
        _ = model
        _ = progress_callback
        if process_callback is not None:
            process_callback(SimpleNamespace(pid=1001))
        time.sleep(0.2)
        if process_callback is not None:
            process_callback(None)
        self.calls.append("run")
        return _codex_result(
            "worker round 1",
            session_id="sess-worker-1",
            stdout='{"completed_work":"done","current_state":"worker round 1","remaining_work":"tests","blockers":"none","recommended_next_step":"run tests","progress_made":true,"task_complete":false}',
        )


def _setup_service(tmp_path: Path) -> tuple[TaskStore, AutopilotService]:
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
    connection.commit()

    tasks = TaskStore(db)
    service = AutopilotService(tasks=tasks, codex=CodexStub())
    return tasks, service


def test_create_run_defaults_to_100_cycles(tmp_path: Path) -> None:
    _, service = _setup_service(tmp_path)

    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )

    assert run.max_cycles == 100
    assert run.status == "created"


def test_step_run_executes_worker_then_supervisor(tmp_path: Path) -> None:
    tasks, service = _setup_service(tmp_path)
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )

    result = service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    )

    assert result.run.status == "running_worker"
    assert result.run.current_phase == "worker"
    assert result.run.cycle_count == 1
    assert result.run.worker_session_id == "sess-worker-1"
    assert result.run.supervisor_session_id == "sess-supervisor-1"
    events = tasks.autopilot.list_events(run_id=run.id)
    assert [event.event_type for event in events][-4:] == [
        "stage_started",
        "stage_completed",
        "decision_started",
        "decision_made",
    ]
    assert events[-4].payload == {"pid": 1001}
    assert events[-2].payload == {"pid": 2001}


def test_pause_resume_and_stop_run(tmp_path: Path) -> None:
    _, service = _setup_service(tmp_path)
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )
    running = service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    ).run

    paused = service.pause_run(run_id=running.id, reason="manual pause")
    assert paused.status == "paused"
    assert paused.paused_reason == "manual pause"

    resumed = service.resume_run(run_id=paused.id)
    assert resumed.status == "running_worker"
    assert resumed.current_phase == "worker"

    stopped = service.stop_run(run_id=resumed.id, stopped_by_user_id=1, reason="manual stop")
    assert stopped.status == "stopped"
    assert stopped.stopped_by_user_id == 1


def test_takeover_run_injects_human_instruction_and_resets_counters(tmp_path: Path) -> None:
    tasks, service = _setup_service(tmp_path)
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )
    running = service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    ).run

    taken = service.takeover_run(
        run_id=running.id,
        instruction="不要再分析，直接修 auth tests 并跑定向 pytest",
        taken_by_user_id=1,
    )

    assert taken.status == "running_worker"
    assert taken.current_phase == "worker"
    assert taken.no_progress_cycles == 0
    assert taken.same_instruction_cycles == 0
    events = tasks.autopilot.list_events(run_id=run.id)
    assert events[-1].actor == "human"
    assert events[-1].event_type == "takeover"
    assert events[-1].payload["instruction"] == "不要再分析，直接修 auth tests 并跑定向 pytest"


def test_step_run_prefers_latest_human_takeover_instruction(tmp_path: Path) -> None:
    tasks, service = _setup_service(tmp_path)
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )
    first = service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    ).run
    service.takeover_run(
        run_id=first.id,
        instruction="不要再分析，直接修 auth tests 并跑定向 pytest",
        taken_by_user_id=1,
    )

    service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=first.id,
    )

    assert "不要再分析，直接修 auth tests 并跑定向 pytest" in service.codex.last_worker_instruction


def test_step_run_allows_single_step_from_paused_and_returns_to_paused(tmp_path: Path) -> None:
    _, service = _setup_service(tmp_path)
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )
    running = service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    ).run
    paused = service.pause_run(run_id=running.id, reason="manual pause")

    stepped = service.step_run(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=paused.id,
    ).run

    assert stepped.status == "paused"
    assert stepped.current_phase == "idle"
    assert stepped.paused_reason == "单步执行后暂停"
    assert stepped.cycle_count == 2


def test_start_run_loop_completes_in_background(tmp_path: Path) -> None:
    tasks, _ = _setup_service(tmp_path)
    service = AutopilotService(tasks=tasks, codex=CompletingCodexStub())
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )

    started = service.start_run_loop(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    )
    service.wait_for_run_loop(run_id=run.id, timeout=2.0)

    updated = service.get_run(run_id=run.id)
    events = tasks.autopilot.list_events(run_id=run.id)
    assert started is True
    assert updated is not None
    assert updated.status == "completed"
    assert updated.current_phase == "idle"
    assert updated.cycle_count == 1
    event_types = [event.event_type for event in events]
    assert "loop_started" in event_types
    assert "cycle_started" in event_types


def test_start_run_loop_does_not_duplicate_alive_thread(tmp_path: Path) -> None:
    tasks, _ = _setup_service(tmp_path)
    service = AutopilotService(tasks=tasks, codex=SlowCompletingCodexStub())
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )

    started = service.start_run_loop(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    )
    started_again = service.start_run_loop(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    )
    service.wait_for_run_loop(run_id=run.id, timeout=2.0)

    assert started is True
    assert started_again is False


def test_start_run_loop_records_scheduler_progress_before_worker_finishes(tmp_path: Path) -> None:
    tasks, _ = _setup_service(tmp_path)
    service = AutopilotService(tasks=tasks, codex=SlowCompletingCodexStub())
    run = service.create_run(
        project_id=1,
        chat_id="chat-1",
        created_by_user_id=1,
        goal="持续推进支付修复",
    )

    started = service.start_run_loop(
        project=ProjectConfig(key="demo", name="Demo", path=Path("/tmp")),
        run_id=run.id,
    )
    time.sleep(0.05)

    events = tasks.autopilot.list_events(run_id=run.id)
    runtime = service.get_runtime_snapshot(run_id=run.id)
    event_types = [event.event_type for event in events]
    assert started is True
    assert "loop_started" in event_types
    assert "cycle_started" in event_types
    assert runtime is not None
    assert runtime.thread_alive is True
    assert runtime.actor == "worker"
    assert runtime.pid == 1001
    assert runtime.process_started_at is not None

    service.wait_for_run_loop(run_id=run.id, timeout=2.0)
