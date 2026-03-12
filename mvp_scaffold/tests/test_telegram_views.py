import pytest

pytest.importorskip("telegram")

from src.codex_session_service import CodexSessionRecord
from src.task_store import StatusSnapshot, TaskRecord
from src.telegram_views import TelegramViewFactory


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
        button.callback_data == "cmd:home" and button.text == "首页控制台"
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
    assert rows[0][0].callback_data == "cmd:task_current"
    assert rows[0][1].callback_data == "task:cancel:12"
