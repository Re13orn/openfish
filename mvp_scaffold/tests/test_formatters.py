from src.codex_session_service import CodexSessionListResult, CodexSessionRecord
from src.formatters import (
    format_do_result,
    format_help,
    format_last_task,
    format_memory,
    format_projects,
    format_session_detail,
    format_sessions_list,
    format_status,
    format_tasks_list,
    truncate_for_telegram,
)
from src.task_store import MemorySnapshot, StatusSnapshot, TaskPage, TaskRecord


def test_truncate_for_telegram_short() -> None:
    assert truncate_for_telegram('hello', 10) == 'hello'


def test_truncate_for_telegram_long() -> None:
    assert truncate_for_telegram("abcdefghij", 8) == "abcde..."


def test_format_status_without_active_project() -> None:
    snapshot = StatusSnapshot(
        active_project_key=None,
        active_project_name=None,
        project_path=None,
        current_branch=None,
        repo_dirty=None,
        last_codex_session_id=None,
        most_recent_task_summary=None,
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
    )
    text = format_status(snapshot)
    assert "当前项目: 未选择" in text
    assert "【状态·未选项目】" in text
    assert "下一步" in text


def test_format_status_summary_mode() -> None:
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="修复支付回调问题",
        recent_failed_summary="pytest failed",
        pending_approval=True,
        next_schedule_id=3,
        next_schedule_hhmm="09:30",
        next_step="批准后继续",
    )
    text = format_status(snapshot, mode="summary")
    assert "路径:" not in text
    assert "分支:" not in text
    assert "【状态·待审批】" in text
    assert "项目: demo" in text
    assert "审批: 待处理" in text


def test_format_status_running_card_title_and_empty_card_title() -> None:
    running_snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="执行代码修复",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step="查看 diff",
    )
    idle_snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id=None,
        most_recent_task_summary=None,
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
    )

    running_text = format_status(running_snapshot)
    idle_text = format_status(idle_snapshot)

    assert "【状态·进行中】" in running_text
    assert "【状态·空闲】" in idle_text
    assert "任务: 空闲" in idle_text


def test_format_memory_snapshot() -> None:
    snapshot = MemorySnapshot(
        notes=["note-1", "note-2"],
        recent_task_summaries=["task-summary"],
        project_summary="project-summary",
        page=1,
        page_size=5,
        total_notes=2,
        total_task_summaries=1,
    )
    text = format_memory(snapshot)
    assert "【记忆】" in text
    assert "note-1" in text
    assert "页码: 1/1" in text


def test_format_memory_keeps_full_task_summary() -> None:
    long_summary = "A" * 140
    snapshot = MemorySnapshot(
        notes=[],
        recent_task_summaries=[long_summary],
        project_summary="project-summary",
        page=2,
        page_size=5,
        total_notes=0,
        total_task_summaries=8,
    )

    text = format_memory(snapshot)

    assert long_summary in text
    assert "..." not in text
    assert "页码: 2/2" in text


def test_format_do_result_status_translation() -> None:
    text = format_do_result(
        project_key="demo",
        task_id=7,
        status="waiting_approval",
        summary="需要审批",
        session_id=None,
    )
    assert "等待审批" in text


def test_format_last_task_with_record() -> None:
    task = TaskRecord(
        id=3,
        command_type="ask",
        original_request="请分析登录模块风险",
        status="failed",
        codex_session_id=None,
        latest_summary="执行失败",
    )
    text = format_last_task(project_key="demo", task=task)
    assert "最近任务: #3" in text
    assert "类型: /ask" in text
    assert "状态: 失败" in text


def test_format_tasks_list() -> None:
    page = TaskPage(
        items=[
            TaskRecord(
                id=8,
                command_type="do",
                original_request="实现任务管理能力",
                status="running",
                codex_session_id="sess-8",
                latest_summary="处理中",
            ),
            TaskRecord(
                id=7,
                command_type="ask",
                original_request="分析 stuck task 原因",
                status="failed",
                codex_session_id="sess-7",
                latest_summary="执行失败",
            ),
        ],
        page=1,
        page_size=8,
        total_count=2,
        total_pages=1,
    )

    text = format_tasks_list(page)

    assert "【任务】" in text
    assert "- #8 /do · 运行中" in text
    assert "处理中" in text
    assert "/task-cancel [id]" in text
    assert "/task-delete <id>" in text


def test_help_contains_last_and_retry() -> None:
    text = format_help()
    assert "/last" in text
    assert "/retry [附加说明]" in text
    assert "/project-root [abs_path]" in text
    assert "/skills" in text
    assert "/skill-install <source>" in text
    assert "/mcp [name]" in text
    assert "/mcp-enable <name>" in text
    assert "/mcp-disable <name>" in text
    assert "/model [show|set <name>|reset]" in text
    assert "/project-add <key> [abs_path] [name]" in text
    assert "/schedule-add <HH:MM> <ask|do> <text>" in text
    assert "/schedule-run <id>" in text


def test_help_summary_mode_is_shorter() -> None:
    text = format_help("summary")
    assert "/ui summary|verbose|stream" in text
    assert "/model" in text
    assert "/project-add <key> [abs_path] [name]" not in text


def test_format_projects_with_recent_section() -> None:
    text = format_projects(
        ["demo", "ops", "lab"],
        active_project_key="demo",
        recent_project_keys=["ops", "demo"],
    )
    assert "当前项目: demo" in text
    assert "最近使用:" in text
    assert "- ops" in text
    assert "其他项目:" in text


def test_format_projects_summary_mode() -> None:
    text = format_projects(
        ["demo", "ops", "lab"],
        active_project_key="demo",
        recent_project_keys=["ops", "demo"],
        mode="summary",
    )
    assert "最近使用: ops" in text
    assert "可选项目:" in text
    assert "其他项目:" not in text


def test_format_sessions_list_and_detail() -> None:
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
    list_result = CodexSessionListResult(
        sessions=[record],
        page=1,
        page_size=10,
        total_count=1,
        total_pages=1,
        openfish_count=0,
        native_count=1,
    )

    list_text = format_sessions_list(list_result)
    detail_text = format_session_detail(record)

    assert "【会话】" in list_text
    assert "[本机] sess-nat" in list_text
    assert "【会话详情】" in detail_text
    assert "来源: 本机" in detail_text
    assert "可导入到 OpenFish 项目" in detail_text
