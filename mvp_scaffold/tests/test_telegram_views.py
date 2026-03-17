import pytest

pytest.importorskip("telegram")

from src.codex_session_service import CodexSessionRecord
from src.task_store import StatusSnapshot, TaskRecord
from src.telegram_views import TelegramViewFactory
from src.autopilot_store import AutopilotRunRecord


def test_projects_panel_marks_active_and_recent() -> None:
    factory = TelegramViewFactory()

    spec = factory.projects_panel(
        active_key="demo",
        recent_keys=["demo", "ops"],
        ordered_keys=["demo", "ops", "lab"],
    )

    assert "当前项目: demo" in spec.text
    rows = spec.reply_markup.inline_keyboard
    assert rows[0][0].text == "当前: demo"
    assert rows[1][0].text == "切换: ops"


def test_main_menu_markup_switches_to_cancel_when_task_running() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="fix auth",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
        active_task=TaskRecord(
            id=12,
            command_type="do",
            original_request="run task",
            status="running",
            codex_session_id="sess-12",
            latest_summary="处理中",
        ),
    )

    markup = factory.main_menu_markup(snapshot=snapshot)

    assert markup.keyboard[0][2].text == "取消任务"
    assert markup.keyboard[1][0].text == "当前任务"


def test_main_menu_markup_switches_to_approval_actions_when_pending() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="fix auth",
        recent_failed_summary=None,
        pending_approval=True,
        pending_approval_id=99,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
    )

    markup = factory.main_menu_markup(snapshot=snapshot)

    assert markup.keyboard[0][2].text == "批准"
    assert markup.keyboard[1][0].text == "拒绝"


def test_status_result_markup_for_pending_approval_has_approve_buttons() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="fix auth",
        recent_failed_summary=None,
        pending_approval=True,
        pending_approval_id=99,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
    )

    markup = factory.status_result_markup(snapshot=snapshot, recent_projects=None)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "approval:approve:99"
    assert rows[0][1].callback_data == "approval:reject:99"


def test_approval_panel_uses_explicit_approval_id_when_available() -> None:
    factory = TelegramViewFactory()

    spec = factory.approval_panel(approval_id=42)

    rows = spec.reply_markup.inline_keyboard
    assert rows[0][0].callback_data == "approval:approve:42"
    assert rows[0][1].callback_data == "approval:reject:42"
    assert rows[1][0].callback_data == "prompt:approve:42"
    assert rows[1][1].callback_data == "prompt:reject:42"
    assert rows[2][0].callback_data == "approval:more"


def test_model_panel_marks_current_model_and_reset_action() -> None:
    factory = TelegramViewFactory()

    spec = factory.model_panel(current_model="o3", model_choices=["gpt-5.4", "o3", "gpt-5"])

    assert "当前: o3" in spec.text
    rows = spec.reply_markup.inline_keyboard
    assert any(button.callback_data == "model:set:o3" for row in rows for button in row)
    assert any(button.callback_data == "model:reset" for row in rows for button in row)


def test_more_panel_contains_download_file_prompt() -> None:
    factory = TelegramViewFactory()

    spec = factory.more_panel()

    assert any(
        button.callback_data == "cmd:context" and button.text == "当前上下文"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "cmd:home" and button.text == "首页控制台"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "panel:autopilot" and button.text == "Autopilot 面板"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "prompt:autopilot" and button.text == "Autopilot"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "cmd:autopilot_status" and button.text == "Autopilot 状态"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "panel:service" and button.text == "服务面板"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "prompt:send_file" and button.text == "下载文件"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "prompt:github_clone" and button.text == "下载 GitHub 仓库"
        for row in spec.reply_markup.inline_keyboard
        for button in row
    )


def test_service_panel_contains_health_and_logs_actions() -> None:
    factory = TelegramViewFactory()

    spec = factory.service_panel()

    rows = spec.reply_markup.inline_keyboard
    assert rows[0][0].callback_data == "cmd:health"
    assert rows[0][1].callback_data == "cmd:version"
    assert any(button.callback_data == "cmd:context" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_status" for row in rows for button in row)
    assert any(button.callback_data == "panel:autopilot" for row in rows for button in row)
    assert any(button.callback_data == "cmd:restart" for row in rows for button in row)
    assert any(button.callback_data == "cmd:logs" for row in rows for button in row)


def test_context_markup_contains_task_and_session_actions() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="fix auth",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
        active_task=TaskRecord(
            id=12,
            command_type="do",
            original_request="run task",
            status="running",
            codex_session_id="sess-12",
            latest_summary="处理中",
        ),
    )

    markup = factory.context_markup(snapshot=snapshot)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "cmd:task_current"
    assert rows[0][1].callback_data == "task:cancel:12"
    assert rows[1][1].callback_data == "cmd:sessions"


def test_mcp_detail_markup_contains_toggle_and_refresh() -> None:
    factory = TelegramViewFactory()

    markup = factory.mcp_detail_markup(name="playwright", enabled=True)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "mcp:disable:playwright"
    assert rows[1][0].callback_data == "cmd:mcp_detail:playwright"


def test_memory_pagination_markup_contains_prev_next_buttons() -> None:
    factory = TelegramViewFactory()

    markup = factory.memory_pagination_markup(page=2, total_pages=3)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "memory:page:1"
    assert rows[0][1].callback_data == "memory:page:3"


def test_sessions_list_and_detail_markup() -> None:
    factory = TelegramViewFactory()
    record = CodexSessionRecord(
        session_id="sess-native-1",
        source="native",
        title="native session",
        updated_at="2026-03-08T10:00:00Z",
        cwd="/tmp/demo",
        project_key=None,
        project_name=None,
        project_path=None,
        task_id=None,
        task_status=None,
        task_summary=None,
        command_type=None,
        session_file_path="/Users/apple/.codex/sessions/native.jsonl",
        importable=True,
    )

    list_markup = factory.sessions_list_markup(sessions=[record], page=2, total_pages=3)
    detail_markup = factory.session_detail_markup(record=record)

    assert list_markup.inline_keyboard[0][0].callback_data == "cmd:session_detail:sess-native-1"
    assert list_markup.inline_keyboard[1][0].callback_data == "sessions:page:1"
    assert list_markup.inline_keyboard[1][1].callback_data == "sessions:page:3"
    assert detail_markup.inline_keyboard[0][0].callback_data == "session:import:sess-native-1"


def test_tasks_list_markup_contains_cancel_delete_and_pagination() -> None:
    factory = TelegramViewFactory()

    markup = factory.tasks_list_markup(
        [
            TaskRecord(
                id=9,
                command_type="do",
                original_request="run task",
                status="running",
                codex_session_id="sess-9",
                latest_summary="处理中",
            ),
            TaskRecord(
                id=8,
                command_type="ask",
                original_request="question",
                status="completed",
                codex_session_id="sess-8",
                latest_summary="ok",
            ),
        ],
        page=2,
        total_pages=3,
    )

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "task:cancel:9"
    assert rows[1][0].callback_data == "task:delete:8"
    assert rows[2][0].callback_data == "tasks:page:1"
    assert rows[2][1].callback_data == "tasks:page:3"
    assert rows[3][0].callback_data == "cmd:tasks_clear"


def test_current_task_markup_contains_cancel_for_active_task() -> None:
    factory = TelegramViewFactory()
    task = TaskRecord(
        id=9,
        command_type="do",
        original_request="run task",
        status="running",
        codex_session_id="sess-9",
        latest_summary="处理中",
    )

    markup = factory.current_task_markup(task)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "task:cancel:9"
    assert rows[1][0].callback_data == "cmd:status"


def test_current_task_markup_for_waiting_approval_includes_decision_buttons() -> None:
    factory = TelegramViewFactory()
    task = TaskRecord(
        id=11,
        command_type="do",
        original_request="run task",
        status="waiting_approval",
        codex_session_id="sess-11",
        latest_summary="等待审批",
    )

    markup = factory.current_task_markup(task)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "approval:approve"
    assert rows[0][1].callback_data == "approval:reject"
    assert rows[0][2].callback_data == "approval:more"
    assert rows[1][0].callback_data == "task:output:11"


def test_task_result_markup_includes_output_button() -> None:
    factory = TelegramViewFactory()

    markup = factory.task_result_markup(task_id=21, status="completed")

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "status:resume"
    assert rows[0][1].callback_data == "status:diff"
    assert rows[1][0].callback_data == "prompt:retry"
    assert rows[2][0].callback_data == "task:output:21"


def test_status_result_markup_prefers_current_task_button_for_active_task() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="fix auth",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
        active_task=TaskRecord(
            id=12,
            command_type="do",
            original_request="run task",
            status="running",
            codex_session_id="sess-12",
            latest_summary="处理中",
        ),
    )

    markup = factory.status_result_markup(snapshot=snapshot, recent_projects=None)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "cmd:task_current"
    assert rows[0][1].callback_data == "task:cancel:12"


def test_home_markup_for_active_task_exposes_current_task_and_cancel() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="fix auth",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
        active_task=TaskRecord(
            id=12,
            command_type="do",
            original_request="run task",
            status="running",
            codex_session_id="sess-12",
            latest_summary="处理中",
        ),
    )

    markup = factory.home_markup(snapshot=snapshot, recent_projects=["demo", "ops"])

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "cmd:context"
    assert rows[0][1].callback_data == "cmd:task_current"
    assert rows[1][0].callback_data == "cmd:digest"
    assert rows[2][0].callback_data == "task:cancel:12"


def test_home_markup_idle_exposes_autopilot_prompt() -> None:
    factory = TelegramViewFactory()
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary=None,
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
        active_task=None,
    )

    markup = factory.home_markup(snapshot=snapshot, recent_projects=["demo"])

    rows = markup.inline_keyboard
    assert any(button.callback_data == "prompt:autopilot" for row in rows for button in row)


def test_autopilot_run_markup_exposes_pause_and_stop_for_running_run() -> None:
    factory = TelegramViewFactory()
    run = AutopilotRunRecord(
        id=1,
        project_id=101,
        chat_id="1",
        created_by_user_id=1,
        goal="持续推进支付修复",
        status="running_worker",
        supervisor_session_id="sess-a",
        worker_session_id="sess-b",
        current_phase="worker",
        cycle_count=1,
        max_cycles=100,
        no_progress_cycles=0,
        same_instruction_cycles=0,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="已修改支付回调",
        last_supervisor_summary="继续测试",
        paused_reason=None,
        stopped_by_user_id=None,
    )

    markup = factory.autopilot_run_markup(run)

    rows = markup.inline_keyboard
    assert any(button.callback_data == "cmd:autopilot_context:1" for row in rows for button in row)
    assert any(button.callback_data == "prompt:autopilot_takeover:1" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_pause:1" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_stop:1" for row in rows for button in row)
    assert any(button.callback_data == "panel:autopilot_run:1" for row in rows for button in row)


def test_autopilot_run_markup_exposes_single_step_when_paused() -> None:
    factory = TelegramViewFactory()
    run = AutopilotRunRecord(
        id=1,
        project_id=101,
        chat_id="1",
        created_by_user_id=1,
        goal="持续推进支付修复",
        status="paused",
        supervisor_session_id="sess-a",
        worker_session_id="sess-b",
        current_phase="idle",
        cycle_count=1,
        max_cycles=100,
        no_progress_cycles=0,
        same_instruction_cycles=0,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="已修改支付回调",
        last_supervisor_summary="继续测试",
        paused_reason="manual pause",
        stopped_by_user_id=None,
    )

    markup = factory.autopilot_run_markup(run)

    rows = markup.inline_keyboard
    assert any(button.callback_data == "cmd:autopilot_step:1" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_resume:1" for row in rows for button in row)


def test_autopilot_run_markup_exposes_takeover_for_blocked_run() -> None:
    factory = TelegramViewFactory()
    run = AutopilotRunRecord(
        id=1,
        project_id=101,
        chat_id="1",
        created_by_user_id=1,
        goal="持续推进支付修复",
        status="blocked",
        supervisor_session_id="sess-a",
        worker_session_id="sess-b",
        current_phase="idle",
        cycle_count=4,
        max_cycles=100,
        no_progress_cycles=2,
        same_instruction_cycles=0,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="无进展",
        last_supervisor_summary="需要人工介入",
        paused_reason=None,
        stopped_by_user_id=None,
    )

    markup = factory.autopilot_run_markup(run)

    rows = markup.inline_keyboard
    assert any(button.callback_data == "cmd:autopilot_context:1" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_log:1" for row in rows for button in row)
    assert any(button.callback_data == "prompt:autopilot_takeover:1" for row in rows for button in row)


def test_autopilot_panel_shows_latest_run_summary() -> None:
    factory = TelegramViewFactory()
    run = AutopilotRunRecord(
        id=3,
        project_id=101,
        chat_id="1",
        created_by_user_id=1,
        goal="持续推进支付修复",
        status="running_worker",
        supervisor_session_id="sess-a",
        worker_session_id="sess-b",
        current_phase="worker",
        cycle_count=7,
        max_cycles=100,
        no_progress_cycles=0,
        same_instruction_cycles=0,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="已修改支付回调",
        last_supervisor_summary="继续测试",
        paused_reason=None,
        stopped_by_user_id=None,
    )

    spec = factory.autopilot_panel(run)

    assert "Autopilot 操作：" in spec.text
    assert "最新 Run: #3" in spec.text
    assert "状态: running_worker" in spec.text


def test_autopilot_runs_markup_exposes_targeted_management_actions() -> None:
    factory = TelegramViewFactory()
    runs = [
        AutopilotRunRecord(
            id=3,
            project_id=101,
            chat_id="1",
            created_by_user_id=1,
            goal="持续推进支付修复",
            status="running_worker",
            supervisor_session_id="sess-a",
            worker_session_id="sess-b",
            current_phase="worker",
            cycle_count=7,
            max_cycles=100,
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="run tests next",
            last_decision="continue",
            last_worker_summary="已修改支付回调",
            last_supervisor_summary="继续测试",
            paused_reason=None,
            stopped_by_user_id=None,
        ),
        AutopilotRunRecord(
            id=2,
            project_id=101,
            chat_id="1",
            created_by_user_id=1,
            goal="分析告警",
            status="paused",
            supervisor_session_id="sess-c",
            worker_session_id="sess-d",
            current_phase="idle",
            cycle_count=2,
            max_cycles=100,
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="inspect findings",
            last_decision="continue",
            last_worker_summary="已分析一轮",
            last_supervisor_summary="继续检查",
            paused_reason="manual pause",
            stopped_by_user_id=None,
        ),
        AutopilotRunRecord(
            id=1,
            project_id=101,
            chat_id="1",
            created_by_user_id=1,
            goal="分析告警",
            status="blocked",
            supervisor_session_id="sess-e",
            worker_session_id="sess-f",
            current_phase="idle",
            cycle_count=3,
            max_cycles=100,
            no_progress_cycles=2,
            same_instruction_cycles=0,
            last_instruction_fingerprint="inspect findings",
            last_decision="continue",
            last_worker_summary="无进展",
            last_supervisor_summary="建议人工接管",
            paused_reason=None,
            stopped_by_user_id=None,
        ),
    ]

    markup = factory.autopilot_runs_markup(runs)

    rows = markup.inline_keyboard
    assert any(button.callback_data == "cmd:autopilot_status:3" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_pause:3" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_context:2" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_log:2" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_resume:2" for row in rows for button in row)
    assert any(button.callback_data == "prompt:autopilot_takeover:1" for row in rows for button in row)


def test_autopilot_panel_shows_recent_runs_summary() -> None:
    factory = TelegramViewFactory()
    runs = [
        AutopilotRunRecord(
            id=3,
            project_id=101,
            chat_id="1",
            created_by_user_id=1,
            goal="持续推进支付修复",
            status="running_worker",
            supervisor_session_id="sess-a",
            worker_session_id="sess-b",
            current_phase="worker",
            cycle_count=7,
            max_cycles=100,
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="run tests next",
            last_decision="continue",
            last_worker_summary="已修改支付回调",
            last_supervisor_summary="继续测试",
            paused_reason=None,
            stopped_by_user_id=None,
        ),
        AutopilotRunRecord(
            id=2,
            project_id=101,
            chat_id="1",
            created_by_user_id=1,
            goal="分析告警",
            status="paused",
            supervisor_session_id="sess-c",
            worker_session_id="sess-d",
            current_phase="idle",
            cycle_count=2,
            max_cycles=100,
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="inspect findings",
            last_decision="continue",
            last_worker_summary="已分析一轮",
            last_supervisor_summary="继续检查",
            paused_reason="manual pause",
            stopped_by_user_id=None,
        ),
    ]

    spec = factory.autopilot_panel(runs[0], recent_runs=runs)

    assert "最近 runs:" in spec.text
    assert "- #3 · running_worker · 7/100" in spec.text
    assert "- #2 · paused · 2/100" in spec.text
