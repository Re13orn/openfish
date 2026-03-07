import pytest

pytest.importorskip("telegram")

from src.task_store import StatusSnapshot
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


def test_mcp_detail_markup_contains_toggle_and_refresh() -> None:
    factory = TelegramViewFactory()

    markup = factory.mcp_detail_markup(name="playwright", enabled=True)

    rows = markup.inline_keyboard
    assert rows[0][0].callback_data == "mcp:disable:playwright"
    assert rows[1][0].callback_data == "cmd:mcp_detail:playwright"
