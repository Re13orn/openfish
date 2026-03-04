"""Formatting helpers for concise Telegram-friendly replies."""

from src.task_store import MemorySnapshot, StatusSnapshot, TaskRecord


STATUS_LABELS = {
    "created": "已创建",
    "running": "运行中",
    "waiting_approval": "等待审批",
    "completed": "已完成",
    "failed": "失败",
    "cancelled": "已取消",
    "rejected": "已拒绝",
}


def _clip(text: str, limit: int = 160) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _card(title: str, lines: list[str]) -> str:
    return f"【{title}】\n" + "\n".join(lines)


def truncate_for_telegram(text: str, limit: int = 3500) -> str:
    """Keep Telegram responses under message limits."""

    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_help() -> str:
    """Return a short list of supported commands."""

    return (
        "可用命令：\n"
        "/start\n"
        "/projects\n"
        "/project-add <key> <abs_path> [name]\n"
        "/project-disable <key>\n"
        "/project-archive <key>\n"
        "/use <project>\n"
        "/ask <question>\n"
        "/do <task>\n"
        "/templates\n"
        "/run <template> [附加说明]\n"
        "/skills\n"
        "/skill-install <source>\n"
        "/schedule-add <HH:MM> <ask|do> <text>\n"
        "/schedule-list\n"
        "/schedule-run <id>\n"
        "/schedule-pause <id>\n"
        "/schedule-enable <id>\n"
        "/schedule-del <id>\n"
        "/last\n"
        "/retry [附加说明]\n"
        "/resume\n"
        "/approve [note]\n"
        "/reject [reason]\n"
        "/memory\n"
        "/note <text>\n"
        "/cancel\n"
        "/diff\n"
        "/upload_policy\n"
        "/status\n"
        "/help\n"
        "发送文档文件（受大小与后缀限制）可自动分析\n"
        "直接发送普通文本会按 /ask 处理（需先 /use 选项目）"
    )


def format_use_confirmation(
    *,
    project_name: str,
    project_path: str,
    default_branch: str | None,
    test_command: str | None,
) -> str:
    """Create a concise confirmation message for /use."""

    return (
        f"当前项目: {project_name}\n"
        f"路径: {project_path}\n"
        f"默认分支: {default_branch or '未知'}\n"
        f"测试命令: {test_command or '暂无'}"
    )


def format_status(snapshot: StatusSnapshot) -> str:
    """Create the concise /status response."""

    if snapshot.active_project_key is None:
        return "未选择活跃项目。\n请先使用 /use <project>。"

    repo_state = "未知"
    if snapshot.repo_dirty is True:
        repo_state = "有变更"
    if snapshot.repo_dirty is False:
        repo_state = "干净"

    return _card(
        "状态",
        [
            f"项目: {snapshot.active_project_key}",
            f"路径: {snapshot.project_path or '未知'}",
            f"分支: {snapshot.current_branch or '未知'}",
            f"工作区: {repo_state}",
            f"会话: {snapshot.last_codex_session_id or '暂无'}",
            f"任务: {_clip(snapshot.most_recent_task_summary, 120) if snapshot.most_recent_task_summary else '暂无'}",
            (
                f"定时: #{snapshot.next_schedule_id} {snapshot.next_schedule_hhmm}"
                if snapshot.next_schedule_id and snapshot.next_schedule_hhmm
                else "定时: 暂无"
            ),
            (
                f"最近失败: {_clip(snapshot.recent_failed_summary, 100)}"
                if snapshot.recent_failed_summary
                else "最近失败: 暂无"
            ),
            f"审批: {'待处理' if snapshot.pending_approval else '无'}",
            f"下一步: {_clip(snapshot.next_step, 100) if snapshot.next_step else '暂无'}",
        ],
    )


def format_do_result(
    *,
    project_key: str,
    task_id: int,
    status: str,
    summary: str,
    session_id: str | None,
    next_action: str | None = None,
) -> str:
    """Create concise result text for /do."""

    display_status = STATUS_LABELS.get(status, status)
    body = (
        f"项目: {project_key}\n"
        f"任务 #{task_id}: {display_status}\n"
        f"会话: {session_id or '暂无'}\n"
        f"摘要: {summary}"
    )
    if next_action:
        return f"{body}\n下一步: {next_action}"
    return body


def format_last_task(*, project_key: str, task: TaskRecord | None) -> str:
    """Render concise /last output for the active project."""

    if task is None:
        return f"项目: {project_key}\n最近任务: 暂无"

    return (
        f"项目: {project_key}\n"
        f"最近任务: #{task.id}\n"
        f"类型: /{task.command_type}\n"
        f"状态: {STATUS_LABELS.get(task.status, task.status)}\n"
        f"会话: {task.codex_session_id or '暂无'}\n"
        f"请求: {_clip(task.original_request, 120)}\n"
        f"摘要: {_clip(task.latest_summary, 160) if task.latest_summary else '暂无'}"
    )


def format_projects(project_keys: list[str]) -> str:
    """Render concise /projects output."""

    if not project_keys:
        return "没有可用项目。"
    return "已注册项目:\n" + "\n".join(f"- {key}" for key in project_keys)


def format_approval_required(*, task_id: int, reason: str) -> str:
    """Format waiting-approval response."""

    return (
        f"任务 #{task_id} 已暂停，等待审批。\n"
        f"原因: {reason}\n"
        "请使用 /approve 或 /reject。"
    )


def format_memory(snapshot: MemorySnapshot) -> str:
    """Format concise project memory response."""

    lines: list[str] = [f"摘要: {snapshot.project_summary or '暂无'}"]

    if snapshot.notes:
        lines.append("笔记:")
        lines.extend(f"- {_clip(item, 80)}" for item in snapshot.notes[:3])
    else:
        lines.append("笔记: 暂无")

    if snapshot.recent_task_summaries:
        lines.append("任务:")
        lines.extend(f"- {_clip(item, 80)}" for item in snapshot.recent_task_summaries[:2])
    else:
        lines.append("任务: 暂无")
    return _card("记忆", lines)


def format_diff_card(diff_text: str) -> str:
    """Render /diff output in a stable mobile card shape."""

    raw_lines = [line.strip() for line in diff_text.splitlines() if line.strip()]
    if not raw_lines:
        return _card("变更", ["暂无信息"])

    if "工作区干净" in diff_text:
        return _card("变更", ["工作区干净，没有未提交变更。"])

    lines: list[str] = []
    for line in raw_lines:
        if line.endswith("："):
            continue
        lines.append(line)
        if len(lines) >= 8:
            break

    if not lines:
        lines = ["暂无可展示变更"]
    return _card("变更", lines)


def format_upload_received(*, file_name: str, size_bytes: int, local_path: str) -> str:
    """Format acknowledgment for accepted document upload."""

    return (
        f"已接收文件: {file_name}\n"
        f"大小: {size_bytes} bytes\n"
        f"保存路径: {local_path}"
    )


def format_upload_rejected(reason: str) -> str:
    """Format rejection message for unsafe/invalid uploads."""

    return f"文件上传已拒绝：{reason}"


def format_start(active_project_key: str | None) -> str:
    """Render a quick onboarding message for /start."""

    project_line = f"当前项目: {active_project_key}" if active_project_key else "当前项目: 未选择"
    return (
        "欢迎使用 OpenFish（小鱼）\n"
        f"{project_line}\n"
        "快速开始：\n"
        "1) /projects 查看项目\n"
        "2) /use <project> 选择项目\n"
        "3) 直接发一句话（自动按 /ask）或用 /do 执行任务\n"
        "4) /status 查看状态"
    )


def format_upload_policy(*, enabled: bool, max_size_bytes: int, allowed_extensions: list[str]) -> str:
    """Render upload policy for mobile view."""

    if not enabled:
        return "上传分析: 已禁用"
    ext_text = ", ".join(allowed_extensions) if allowed_extensions else "无限制"
    return (
        "上传策略：\n"
        f"- 启用: 是\n"
        f"- 大小上限: {max_size_bytes} bytes\n"
        f"- 允许后缀: {ext_text}"
    )


def format_templates(templates: list[tuple[str, str, str]]) -> str:
    if not templates:
        return "暂无内置模板。"
    lines = ["可用模板："]
    for key, title, mode in templates:
        lines.append(f"- {key} [{mode}] {title}")
    lines.append("用法: /run <template> [附加说明]")
    return "\n".join(lines)


def format_project_busy() -> str:
    return "当前项目已有任务在执行中，请稍后重试。可用 /status 查看状态。"


def format_skills_list(
    *,
    skills_root: str,
    skills: list[str],
    total_count: int,
    hidden_count: int,
    omitted_count: int,
) -> str:
    lines = [f"Skills 目录: {skills_root}"]
    if total_count == 0:
        lines.append("可见 skills: 暂无")
    else:
        lines.append(f"可见 skills: {total_count}")
        lines.extend(f"- {item}" for item in skills)
        if omitted_count > 0:
            lines.append(f"... 还有 {omitted_count} 个未展示")
    if hidden_count > 0:
        lines.append(f"（已隐藏系统 skills: {hidden_count}）")
    lines.append("安装用法: /skill-install <source>")
    return "\n".join(lines)


def format_skill_install_result(
    *,
    source: str,
    ok: bool,
    summary: str,
    command: list[str] | None,
) -> str:
    status = "成功" if ok else "失败"
    lines = [
        f"Skill 安装: {status}",
        f"来源: {source}",
        f"摘要: {summary}",
    ]
    if command:
        lines.append(f"命令: {' '.join(command)}")
    return "\n".join(lines)


def format_schedule_added(*, schedule_id: int, hhmm: str, command_type: str, request_text: str) -> str:
    return (
        f"定期任务已创建: #{schedule_id}\n"
        f"时间: {hhmm}\n"
        f"类型: /{command_type}\n"
        f"内容: {_clip(request_text, 120)}"
    )


def format_schedule_list(items: list[tuple[int, str, bool, str, str, str | None]]) -> str:
    if not items:
        return "当前项目没有定期任务。"

    lines = ["定期任务："]
    for item in items:
        schedule_id, hhmm, enabled, command_type, request_text, last_status = item
        status_text = f" | 上次: {last_status}" if last_status else ""
        enabled_text = "启用" if enabled else "暂停"
        lines.append(f"- #{schedule_id} {hhmm} /{command_type} [{enabled_text}]{status_text}")
        lines.append(f"  {_clip(request_text, 80)}")
    lines.append("新增: /schedule-add <HH:MM> <ask|do> <text>")
    lines.append("控制: /schedule-run <id> /schedule-pause <id> /schedule-enable <id>")
    return "\n".join(lines)


def format_schedule_deleted(schedule_id: int) -> str:
    return f"已删除定期任务 #{schedule_id}。"


def format_schedule_toggled(schedule_id: int, *, enabled: bool) -> str:
    return f"定期任务 #{schedule_id} 已{'启用' if enabled else '暂停'}。"


def format_schedule_run_result(schedule_id: int, result_text: str) -> str:
    return f"已触发定期任务 #{schedule_id}\n{result_text}"
