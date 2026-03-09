"""Formatting helpers for concise Telegram-friendly replies."""

from src.codex_session_service import CodexSessionListResult, CodexSessionRecord
from src.task_store import MemorySnapshot, StatusSnapshot, TaskPage, TaskRecord


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


def _status_card_title(snapshot: StatusSnapshot) -> str:
    if snapshot.active_project_key is None:
        return "状态"
    if snapshot.pending_approval:
        return "状态·待审批"
    if snapshot.most_recent_task_summary:
        return "状态·进行中"
    return "状态·空闲"


def truncate_for_telegram(text: str, limit: int = 3500) -> str:
    """Keep Telegram responses under message limits."""

    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def format_help(mode: str = "verbose") -> str:
    """Return a short list of supported commands."""

    if mode == "summary":
        return (
            "常用命令：\n"
            "/projects\n"
            "/use <project>\n"
            "/ask <question>\n"
            "/do <task>\n"
            "/status\n"
            "/resume [task_id] [instruction]\n"
            "/diff\n"
            "/model\n"
            "/tasks\n"
            "/sessions\n"
            "/version\n"
            "/update-check\n"
            "/restart\n"
            "/ui summary|verbose|stream\n"
            "\n"
            "更多命令可用 /help verbose 查看。"
        )

    return (
        "高频操作：\n"
        "/projects\n"
        "/use <project>\n"
        "/ask <question>\n"
        "/do <task>\n"
        "/status\n"
        "/resume [task_id] [instruction]\n"
        "/diff\n"
        "/model [show|set <name>|reset]\n"
        "/tasks [page]\n"
        "/task-cancel [id]\n"
        "/task-delete <id>\n"
        "/sessions [page]\n"
        "/session <id>\n"
        "/session-import <id> [project_key] [name]\n"
        "\n"
        "项目与模板：\n"
        "/project-root [abs_path]\n"
        "/project-add <key> [abs_path] [name]\n"
        "/project-disable <key>\n"
        "/project-archive <key>\n"
        "/templates\n"
        "/run <template> [附加说明]\n"
        "\n"
        "定时与审批：\n"
        "/schedule-add <HH:MM> <ask|do> <text>\n"
        "/schedule-list\n"
        "/schedule-run <id>\n"
        "/schedule-pause <id>\n"
        "/schedule-enable <id>\n"
        "/schedule-del <id>\n"
        "/approve [note]\n"
        "/reject [reason]\n"
        "\n"
        "其他：\n"
        "/last\n"
        "/retry [附加说明]\n"
        "/memory\n"
        "/note <text>\n"
        "/skills\n"
        "/skill-install <source>\n"
        "/mcp [name]\n"
        "/mcp-enable <name>\n"
        "/mcp-disable <name>\n"
        "/version\n"
        "/update-check\n"
        "/update\n"
        "/restart\n"
        "/logs\n"
        "/logs-clear\n"
        "/ui [show|summary|verbose|stream]\n"
        "/upload_policy\n"
        "/cancel\n"
        "\n"
        "直接发送普通文本会按 /ask 处理。未选项目时，可先点“项目”或使用 /projects。"
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


def format_status(snapshot: StatusSnapshot, mode: str = "verbose") -> str:
    """Create the concise /status response."""

    if snapshot.active_project_key is None:
        return _card(
            "状态·未选项目",
            [
                "当前项目: 未选择",
                "任务: 暂无",
                "下一步: 先切换项目，再直接提问或执行任务。",
            ],
        )

    title = _status_card_title(snapshot)
    if mode == "summary":
        lines = [
            f"项目: {snapshot.active_project_key}",
            f"审批: {'待处理' if snapshot.pending_approval else '无'}",
        ]
        if snapshot.most_recent_task_summary:
            lines.insert(1, f"任务: {_clip(snapshot.most_recent_task_summary, 80)}")
        else:
            lines.insert(1, "任务: 空闲")
        if snapshot.recent_failed_summary:
            lines.append(f"最近失败: {_clip(snapshot.recent_failed_summary, 60)}")
        if snapshot.next_schedule_id and snapshot.next_schedule_hhmm:
            lines.append(f"定时: #{snapshot.next_schedule_id} {snapshot.next_schedule_hhmm}")
        else:
            lines.append("定时: 暂无")
        lines.append(f"下一步: {_clip(snapshot.next_step, 60) if snapshot.next_step else '暂无'}")
        return _card(title, lines)

    repo_state = "未知"
    if snapshot.repo_dirty is True:
        repo_state = "有变更"
    if snapshot.repo_dirty is False:
        repo_state = "干净"

    lines = [
        f"项目: {snapshot.active_project_key}",
        f"路径: {snapshot.project_path or '未知'}",
        f"分支: {snapshot.current_branch or '未知'}",
        f"工作区: {repo_state}",
        f"会话: {snapshot.last_codex_session_id or '暂无'}",
        f"审批: {'待处理' if snapshot.pending_approval else '无'}",
    ]
    if snapshot.most_recent_task_summary:
        lines.append(f"任务: {_clip(snapshot.most_recent_task_summary, 120)}")
    else:
        lines.append("任务: 空闲")
    if snapshot.next_schedule_id and snapshot.next_schedule_hhmm:
        lines.append(f"定时: #{snapshot.next_schedule_id} {snapshot.next_schedule_hhmm}")
    else:
        lines.append("定时: 暂无")
    if snapshot.recent_failed_summary:
        lines.append(f"最近失败: {_clip(snapshot.recent_failed_summary, 100)}")
    else:
        lines.append("最近失败: 暂无")
    lines.append(f"下一步: {_clip(snapshot.next_step, 100) if snapshot.next_step else '暂无'}")
    return _card(title, lines)


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


def format_tasks_list(result: TaskPage) -> str:
    lines = [
        "【任务】",
        f"页码: {result.page}/{result.total_pages}",
        f"总数: {result.total_count}",
    ]
    if not result.items:
        lines.append("暂无任务。")
        return "\n".join(lines)
    lines.append("最近任务:")
    for item in result.items:
        lines.append(
            f"- #{item.id} /{item.command_type} · {STATUS_LABELS.get(item.status, item.status)}"
        )
        lines.append(f"  请求: {_clip(item.original_request, 80)}")
        if item.latest_summary:
            lines.append(f"  摘要: {_clip(item.latest_summary, 100)}")
    lines.append("可用 /task-cancel [id] 或 /task-delete <id> 管理任务。")
    return "\n".join(lines)


def format_projects(
    project_keys: list[str],
    *,
    active_project_key: str | None = None,
    recent_project_keys: list[str] | None = None,
    mode: str = "verbose",
) -> str:
    """Render concise /projects output."""

    if not project_keys:
        return "没有可用项目。"

    lines = ["项目列表："]
    if active_project_key:
        lines.append(f"当前项目: {active_project_key}")

    recent = [
        key for key in (recent_project_keys or []) if key in project_keys and key != active_project_key
    ]
    if mode == "summary":
        if recent:
            lines.append("最近使用: " + ", ".join(recent[:4]))
        others = [key for key in project_keys if key not in recent and key != active_project_key]
        if others:
            lines.append("可选项目: " + ", ".join(others[:6]))
        return "\n".join(lines)

    if recent:
        lines.append("最近使用:")
        lines.extend(f"- {key}" for key in recent[:5])

    others = [key for key in project_keys if key not in recent and key != active_project_key]
    if others:
        lines.append("其他项目:")
        lines.extend(f"- {key}" for key in others)
    return "\n".join(lines)


def _source_label(source: str) -> str:
    return "OpenFish" if source == "openfish" else "本机"


def format_sessions_list(result: CodexSessionListResult) -> str:
    lines = [
        "【会话】",
        f"页码: {result.page}/{result.total_pages}",
        f"总数: {result.total_count} (OpenFish {result.openfish_count} / 本机 {result.native_count})",
    ]
    if not result.sessions:
        lines.append("暂无可用会话。")
        return "\n".join(lines)
    lines.append("最近会话:")
    for item in result.sessions:
        title = item.title or "未命名会话"
        location = item.project_key or (item.cwd.rsplit("/", 1)[-1] if item.cwd else "未知路径")
        status = item.task_status or "native"
        lines.append(f"- [{_source_label(item.source)}] {item.session_id[:8]} · {title}")
        lines.append(f"  位置: {location} · 状态: {status}")
    lines.append("可用 /session <id> 查看详情。")
    return "\n".join(lines)


def format_session_detail(record: CodexSessionRecord) -> str:
    lines = [
        "【会话详情】",
        f"来源: {_source_label(record.source)}",
        f"会话: {record.session_id}",
    ]
    if record.title:
        lines.append(f"标题: {record.title}")
    if record.updated_at:
        lines.append(f"更新时间: {record.updated_at}")
    if record.cwd:
        lines.append(f"CWD: {record.cwd}")
    if record.project_key:
        lines.append(f"项目: {record.project_key}")
    if record.project_path:
        lines.append(f"项目路径: {record.project_path}")
    if record.task_id is not None:
        lines.append(f"任务: #{record.task_id}")
    if record.command_type:
        lines.append(f"类型: /{record.command_type}")
    if record.task_status:
        lines.append(f"状态: {record.task_status}")
    if record.task_summary:
        lines.append(f"摘要: {record.task_summary}")
    if record.session_file_path:
        lines.append(f"文件: {record.session_file_path}")
    if record.importable:
        lines.append("可导入到 OpenFish 项目，并继续该会话。")
    return "\n".join(lines)


def format_approval_required(*, task_id: int, reason: str) -> str:
    """Format waiting-approval response."""

    return (
        f"任务 #{task_id} 已暂停，等待审批。\n"
        f"原因: {reason}\n"
        "请使用 /approve 或 /reject。"
    )


def format_memory(snapshot: MemorySnapshot) -> str:
    """Format concise project memory response."""

    lines: list[str] = [
        f"摘要: {snapshot.project_summary or '暂无'}",
        f"页码: {snapshot.page}/{snapshot.total_pages}",
    ]

    if snapshot.notes:
        lines.append("笔记:")
        lines.extend(f"- {item}" for item in snapshot.notes)
    else:
        lines.append("笔记: 暂无")

    if snapshot.recent_task_summaries:
        lines.append("任务:")
        lines.extend(f"- {item}" for item in snapshot.recent_task_summaries)
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


def format_start(active_project_key: str | None, recent_project_keys: list[str] | None = None) -> str:
    """Render a quick onboarding message for /start."""

    project_line = f"当前项目: {active_project_key}" if active_project_key else "当前项目: 未选择"
    text = (
        "欢迎使用 OpenFish（小鱼）\n"
        f"{project_line}\n"
        "快速开始：\n"
        "1) 点“项目”查看或切换项目\n"
        "2) 点“提问”或直接发送一句话\n"
        "3) 点“执行”安排改动任务\n"
        "4) 点“状态”查看下一步"
    )
    recent = [key for key in (recent_project_keys or []) if key != active_project_key]
    if recent:
        text += "\n最近项目: " + ", ".join(recent[:4])
    return text


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


def format_mcp_list(items: list[tuple[str, bool, str, str | None, str | None]]) -> str:
    if not items:
        return "当前未配置 MCP 服务。"

    lines = ["MCP 服务："]
    for name, enabled, transport, target, auth_status in items:
        status = "启用" if enabled else "停用"
        auth = f" | 认证: {auth_status}" if auth_status else ""
        lines.append(f"- {name} [{status}] {transport}{auth}")
        if target:
            lines.append(f"  {_clip(target, 100)}")
    lines.append("详情: /mcp <name>")
    lines.append("控制: /mcp-enable <name> /mcp-disable <name>")
    return "\n".join(lines)


def format_mcp_detail(
    *,
    name: str,
    enabled: bool,
    disabled_reason: str | None,
    transport_type: str,
    url: str | None,
    command: str | None,
    args: list[str],
    cwd: str | None,
    bearer_token_env_var: str | None,
    auth_status: str | None,
    startup_timeout_sec: int | None,
    tool_timeout_sec: int | None,
    enabled_tools: list[str],
    disabled_tools: list[str],
) -> str:
    lines = [
        f"MCP: {name}",
        f"状态: {'启用' if enabled else '停用'}",
        f"传输: {transport_type}",
    ]
    if disabled_reason:
        lines.append(f"停用原因: {disabled_reason}")
    if url:
        lines.append(f"URL: {_clip(url, 150)}")
    if command:
        lines.append(f"命令: {_clip(command, 150)}")
    if args:
        lines.append(f"参数: {_clip(' '.join(args), 150)}")
    if cwd:
        lines.append(f"CWD: {_clip(cwd, 150)}")
    if bearer_token_env_var:
        lines.append(f"Token 环境变量: {bearer_token_env_var}")
    if auth_status:
        lines.append(f"认证: {auth_status}")
    if startup_timeout_sec is not None:
        lines.append(f"启动超时: {startup_timeout_sec}s")
    if tool_timeout_sec is not None:
        lines.append(f"工具超时: {tool_timeout_sec}s")
    if enabled_tools:
        lines.append(f"启用工具限制: {', '.join(enabled_tools)}")
    if disabled_tools:
        lines.append(f"禁用工具限制: {', '.join(disabled_tools)}")
    lines.append(f"控制: /mcp-{'disable' if enabled else 'enable'} {name}")
    return "\n".join(lines)


def format_version_info(*, branch: str, version: str, commit: str) -> str:
    return (
        "OpenFish 版本：\n"
        f"分支: {branch}\n"
        f"版本: {version}\n"
        f"提交: {commit}"
    )


def format_update_check(
    *,
    branch: str,
    current_version: str,
    current_commit: str,
    upstream_ref: str,
    upstream_commit: str,
    behind_count: int,
    ahead_count: int,
    commits: list[str],
) -> str:
    lines = [
        "更新检查：",
        f"当前: {current_version} ({current_commit})",
        f"分支: {branch}",
        f"上游: {upstream_ref} ({upstream_commit})",
        f"落后: {behind_count}",
        f"领先: {ahead_count}",
    ]
    if behind_count > 0:
        lines.append("待更新提交：")
        lines.extend(f"- {item}" for item in commits[:5])
        lines.append("可执行: /update")
    else:
        lines.append("当前已是最新版本。")
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
