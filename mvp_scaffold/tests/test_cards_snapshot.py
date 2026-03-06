from src.formatters import format_diff_card, format_memory, format_status
from src.task_store import MemorySnapshot, StatusSnapshot


def test_status_card_snapshot() -> None:
    snapshot = StatusSnapshot(
        active_project_key="myapp",
        active_project_name="My App",
        project_path="/tmp/myapp",
        current_branch="main",
        repo_dirty=False,
        last_codex_session_id="sess-1",
        most_recent_task_summary="修复登录接口 500 并补充测试",
        recent_failed_summary="pytest tests/auth failed",
        pending_approval=True,
        next_schedule_id=3,
        next_schedule_hhmm="09:30",
        next_step="执行 /approve 继续",
    )
    assert format_status(snapshot) == (
        "【状态·待审批】\n"
        "项目: myapp\n"
        "路径: /tmp/myapp\n"
        "分支: main\n"
        "工作区: 干净\n"
        "会话: sess-1\n"
        "审批: 待处理\n"
        "任务: 修复登录接口 500 并补充测试\n"
        "定时: #3 09:30\n"
        "最近失败: pytest tests/auth failed\n"
        "下一步: 执行 /approve 继续"
    )


def test_memory_card_snapshot() -> None:
    snapshot = MemorySnapshot(
        notes=["优先最小改动", "先跑认证相关测试"],
        recent_task_summaries=["定位 500 原因并给出修复方案"],
        project_summary="主应用，包含认证与设置模块",
    )
    assert format_memory(snapshot) == (
        "【记忆】\n"
        "摘要: 主应用，包含认证与设置模块\n"
        "笔记:\n"
        "- 优先最小改动\n"
        "- 先跑认证相关测试\n"
        "任务:\n"
        "- 定位 500 原因并给出修复方案"
    )


def test_diff_card_snapshot() -> None:
    diff_text = "最近变更：\nM src/router.py\nA tests/test_cards_snapshot.py"
    assert format_diff_card(diff_text) == (
        "【变更】\n"
        "M src/router.py\n"
        "A tests/test_cards_snapshot.py"
    )
