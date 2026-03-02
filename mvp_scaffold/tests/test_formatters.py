from src.formatters import format_do_result, format_help, format_last_task, format_memory, format_status, truncate_for_telegram
from src.task_store import MemorySnapshot, StatusSnapshot, TaskRecord


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
        pending_approval=False,
        next_step=None,
    )
    assert "未选择活跃项目" in format_status(snapshot)


def test_format_memory_snapshot() -> None:
    snapshot = MemorySnapshot(
        notes=["note-1", "note-2"],
        recent_task_summaries=["task-summary"],
        project_summary="project-summary",
    )
    text = format_memory(snapshot)
    assert "【记忆】" in text
    assert "note-1" in text


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


def test_help_contains_last_and_retry() -> None:
    text = format_help()
    assert "/last" in text
    assert "/retry [附加说明]" in text
    assert "/skills" in text
    assert "/skill-install <source>" in text
