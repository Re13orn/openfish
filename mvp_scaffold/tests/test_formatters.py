from src.autopilot_store import AutopilotEventRecord, AutopilotRunRecord
from src.codex_session_service import CodexSessionListResult, CodexSessionRecord
from src.formatters import (
    format_autopilot_context,
    format_autopilot_status,
    format_context,
    format_current_task,
    format_do_result,
    format_health,
    format_help,
    format_home,
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


def test_format_home_uses_dashboard_layout() -> None:
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="修复支付回调问题",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=3,
        next_schedule_hhmm="09:30",
        next_step="继续修复测试",
    )

    text = format_home(snapshot=snapshot, current_model="o3", recent_project_keys=["demo", "ops"])

    assert "【控制台】" in text
    assert "项目: demo" in text
    assert "模型: o3" in text
    assert "会话: sess-1" in text
    assert "最近项目: ops" in text


def test_format_context_explains_continuation_session() -> None:
    snapshot = StatusSnapshot(
        active_project_key="demo",
        active_project_name="Demo",
        project_path="/tmp/demo",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="修复支付回调问题",
        recent_failed_summary=None,
        pending_approval=False,
        next_schedule_id=None,
        next_schedule_hhmm=None,
        next_step=None,
    )

    text = format_context(snapshot=snapshot, current_model="o3", ui_mode="stream")

    assert "【当前上下文】" in text
    assert "项目: demo" in text
    assert "会话: sess-1" in text
    assert "界面: stream" in text
    assert "新的 /ask /do 会续接会话 sess-1" in text


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
    assert "/tasks-clear" in text


def test_format_current_task_card() -> None:
    task = TaskRecord(
        id=8,
        command_type="do",
        original_request="实现任务管理能力",
        status="running",
        codex_session_id="sess-8",
        latest_summary="处理中",
    )

    text = format_current_task(project_key="demo", task=task)

    assert "【当前任务】" in text
    assert "任务: #8" in text
    assert "状态: 运行中" in text
    assert "请求: 实现任务管理能力" in text


def test_help_contains_last_and_retry() -> None:
    text = format_help()
    assert "/task-current" in text
    assert "/health" in text
    assert "/last" in text
    assert "/retry [附加说明]" in text
    assert "/project-root [abs_path]" in text
    assert "/skills" in text
    assert "/skill-install <source>" in text
    assert "/mcp [name]" in text
    assert "/mcp-enable <name>" in text
    assert "/mcp-disable <name>" in text
    assert "/model [show|set <name>|reset]" in text


def test_format_health_card() -> None:
    text = format_health(
        version="v1.1.0",
        branch="main",
        commit="abc1234",
        codex_available=True,
        project_count=3,
        active_project_key="demo",
        active_task_summary="#12 · running",
        pending_approval=False,
        current_model="o3",
        session_id="sess-1",
    )

    assert "【服务】" in text
    assert "结论: 良好" in text
    assert "版本: v1.1.0" in text
    assert "Codex: 可用" in text
    assert "项目数: 3" in text
    assert "当前项目: demo" in text
    assert "关注点: 暂无" in text


def test_format_health_card_surfaces_blockers() -> None:
    text = format_health(
        version="v1.1.0",
        branch="main",
        commit="abc1234",
        codex_available=False,
        project_count=0,
        active_project_key=None,
        active_task_summary=None,
        pending_approval=False,
        current_model=None,
        session_id=None,
    )

    assert "结论: 阻塞" in text
    assert "关注点: Codex CLI 不可用；还没有已注册项目" in text
    assert "下一步: 先确认 codex 可执行，再重新运行 /health。" in text


def test_format_autopilot_status_surfaces_near_blocked_signals() -> None:
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
        cycle_count=98,
        max_cycles=100,
        no_progress_cycles=1,
        same_instruction_cycles=1,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="已修改支付回调",
        last_supervisor_summary="继续测试",
        paused_reason=None,
        stopped_by_user_id=None,
    )
    events = [
        AutopilotEventRecord(
            id=1,
            run_id=1,
            cycle_no=98,
            actor="worker",
            event_type="stage_completed",
            summary="已修改支付回调",
            payload=None,
        ),
        AutopilotEventRecord(
            id=2,
            run_id=1,
            cycle_no=98,
            actor="supervisor",
            event_type="decision_made",
            summary="继续测试",
            payload=None,
        ),
    ]

    text = format_autopilot_status(run=run, events=events)

    assert "【Autopilot】" in text
    assert "结论: 接近阻塞" in text
    assert "关注点: 最近一轮无进展；最近指令开始重复；接近轮次上限" in text


def test_format_autopilot_context_includes_recent_event_timeline() -> None:
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
        cycle_count=3,
        max_cycles=100,
        no_progress_cycles=0,
        same_instruction_cycles=0,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="已修改支付回调",
        last_supervisor_summary="继续测试",
        paused_reason="用户暂停",
        stopped_by_user_id=None,
    )
    events = [
        AutopilotEventRecord(
            id=1,
            run_id=1,
            cycle_no=2,
            actor="worker",
            event_type="stage_completed",
            summary="worker 2",
            payload={
                "blockers": "pytest still failing on auth flow",
                "recommended_next_step": "fix auth tests and rerun pytest",
            },
        ),
        AutopilotEventRecord(
            id=2,
            run_id=1,
            cycle_no=2,
            actor="supervisor",
            event_type="decision_made",
            summary="supervisor 2",
            payload={
                "reason": "worker made progress but auth tests still fail",
                "next_instruction_for_b": "fix auth tests and rerun targeted pytest",
            },
        ),
        AutopilotEventRecord(
            id=3,
            run_id=1,
            cycle_no=3,
            actor="human",
            event_type="paused",
            summary="用户暂停",
            payload=None,
        ),
    ]

    text = format_autopilot_context(run=run, events=events)

    assert "【Autopilot Context】" in text
    assert "结论: 已暂停" in text
    assert "B 当前阻塞: pytest still failing on auth flow" in text
    assert "B 建议下一步: fix auth tests and rerun pytest" in text
    assert "A 判定理由: worker made progress but auth tests still fail" in text
    assert "A 给 B 的下一步: fix auth tests and rerun targeted pytest" in text
    assert "最近事件:" in text
    assert "- 2:worker/stage_completed · worker 2" in text
    assert "- 3:human/paused · 用户暂停" in text


def test_help_summary_mode_is_shorter() -> None:
    text = format_help("summary")
    assert "/ui summary|verbose|stream|reset" in text
    assert "/model" in text
    assert "/task-current" in text
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
