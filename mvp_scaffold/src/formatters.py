"""Formatting helpers for concise Telegram-friendly replies."""

import time

from src.autopilot_service import AutopilotRuntimeSnapshot
from src.autopilot_store import AutopilotEventRecord, AutopilotRunRecord, AutopilotStreamChunkRecord
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
            "/context\n"
            "/task-current\n"
            "/autopilot <goal>\n"
            "/autopilots\n"
            "/autopilot-status [id]\n"
            "/autopilot-context [id]\n"
            "/autopilot-log [id]\n"
            "/autopilot-takeover <instruction>\n"
            "/autopilot-pause [id]\n"
            "/autopilot-resume [id]\n"
            "/autopilot-stop [id]\n"
            "/resume [task_id] [instruction]\n"
            "/diff\n"
            "/model\n"
            "/tasks\n"
            "/download-file <abs_path>\n"
            "/github-clone <repo_url|owner/repo> [relative_dir]\n"
            "/sessions\n"
            "/version\n"
            "/health\n"
            "/update-check\n"
            "/restart\n"
            "/ui summary|verbose|stream|reset\n"
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
        "/context\n"
        "/task-current\n"
        "/autopilot <goal>\n"
        "/autopilots\n"
        "/autopilot-status [id]\n"
        "/autopilot-context [id]\n"
        "/autopilot-log [id]\n"
        "/autopilot-takeover <instruction>\n"
        "/autopilot-step [id]\n"
        "/autopilot-pause [id]\n"
        "/autopilot-resume [id]\n"
        "/autopilot-stop [id]\n"
        "/resume [task_id] [instruction]\n"
        "/diff\n"
        "/model [show|set <name>|reset]\n"
        "/tasks [page]\n"
        "/download-file <abs_path>\n"
        "/github-clone <repo_url|owner/repo> [relative_dir]\n"
        "/task-cancel [id]\n"
        "/task-delete <id>\n"
        "/tasks-clear\n"
        "/sessions [page]\n"
        "/session <id>\n"
        "/session-import <id> [project_key] [name]\n"
        "\n"
        "项目：\n"
        "/project-root [abs_path]\n"
        "/project-template-root [abs_path]\n"
        "/project-templates\n"
        "/project-add <key> [abs_path] [name]\n"
        "/project-disable <key>\n"
        "/project-archive <key>\n"
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
        "/health\n"
        "/version\n"
        "/update-check\n"
        "/update\n"
        "/restart\n"
        "/logs\n"
        "/logs-clear\n"
        "/ui [show|summary|verbose|stream|reset]\n"
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


def format_home(
    *,
    snapshot: StatusSnapshot,
    current_model: str | None,
    recent_project_keys: list[str] | None = None,
) -> str:
    """Render the Telegram home/control panel summary."""

    active_project = snapshot.active_project_key or "未选择"
    task_line = "空闲"
    if snapshot.active_task is not None:
        task_line = f"#{snapshot.active_task.id} · {STATUS_LABELS.get(snapshot.active_task.status, snapshot.active_task.status)}"
    elif snapshot.most_recent_task_summary:
        task_line = _clip(snapshot.most_recent_task_summary, 80)

    lines = [
        "服务: 在线",
        f"项目: {active_project}",
        f"任务: {task_line}",
        f"模型: {current_model or '默认'}",
        f"会话: {snapshot.last_codex_session_id or '暂无'}",
        f"审批: {'待处理' if snapshot.pending_approval else '无'}",
    ]
    if snapshot.next_schedule_id and snapshot.next_schedule_hhmm:
        lines.append(f"定时: #{snapshot.next_schedule_id} {snapshot.next_schedule_hhmm}")
    else:
        lines.append("定时: 暂无")
    if snapshot.next_step:
        lines.append(f"下一步: {_clip(snapshot.next_step, 100)}")
    elif snapshot.active_project_key is None:
        lines.append("下一步: 先切换项目，再直接提问或执行任务。")
    elif snapshot.active_task is not None:
        lines.append("下一步: 查看当前任务，或等待执行完成。")
    else:
        lines.append("下一步: 直接提问，或点“执行”安排任务。")

    recent = [key for key in (recent_project_keys or []) if key and key != snapshot.active_project_key]
    if recent:
        lines.append("最近项目: " + ", ".join(recent[:4]))
    return _card("控制台", lines)


def format_context(
    *,
    snapshot: StatusSnapshot,
    current_model: str | None,
    ui_mode: str | None,
) -> str:
    """Render a focused card that explains the current continuation context."""

    if snapshot.active_project_key is None:
        return _card(
            "当前上下文",
            [
                "项目: 未选择",
                "任务: 暂无",
                "会话: 暂无",
                f"模型: {current_model or '默认'}",
                f"界面: {ui_mode or 'stream'}",
                "续接: 当前没有可续接上下文。",
                "下一步: 先切换项目，再直接提问或执行任务。",
            ],
        )

    if snapshot.active_task is not None:
        task_line = (
            f"#{snapshot.active_task.id} · "
            f"{STATUS_LABELS.get(snapshot.active_task.status, snapshot.active_task.status)}"
        )
    elif snapshot.most_recent_task_summary:
        task_line = _clip(snapshot.most_recent_task_summary, 80)
    else:
        task_line = "空闲"

    if snapshot.last_codex_session_id:
        continuation = f"新的 /ask /do 会续接会话 {snapshot.last_codex_session_id}。"
    else:
        continuation = "当前没有历史会话，新的 /ask /do 会新建上下文。"

    if snapshot.active_task is not None:
        next_step = "当前任务仍在运行，完成后会继续绑定到这个项目上下文。"
    elif snapshot.pending_approval:
        next_step = "当前任务在等待审批，审批后会继续使用这个上下文。"
    else:
        next_step = "可以直接继续提问或执行任务。"

    return _card(
        "当前上下文",
        [
            f"项目: {snapshot.active_project_key}",
            f"路径: {snapshot.project_path or '未知'}",
            f"任务: {task_line}",
            f"会话: {snapshot.last_codex_session_id or '暂无'}",
            f"模型: {current_model or '默认'}",
            f"界面: {ui_mode or 'stream'}",
            f"续接: {continuation}",
            f"下一步: {next_step}",
        ],
    )


def format_autopilot_status(
    *,
    run: AutopilotRunRecord,
    events: list[AutopilotEventRecord],
    runtime: AutopilotRuntimeSnapshot | None = None,
    raw_output_lines: list[str] | None = None,
) -> str:
    verdict, concerns, next_step = _autopilot_verdict(run, events)
    latest_worker = next((event for event in reversed(events) if event.actor == "worker"), None)
    latest_supervisor = next((event for event in reversed(events) if event.actor == "supervisor"), None)
    supervisor_state = _autopilot_actor_state(actor="supervisor", events=events, runtime=runtime)
    worker_state = _autopilot_actor_state(actor="worker", events=events, runtime=runtime)
    lines = [
        f"Run: #{run.id}",
        f"目标: {_clip(run.goal, 160)}",
        f"状态: {run.status}",
        f"阶段: {run.current_phase}",
        f"轮次: {run.cycle_count}/{run.max_cycles}",
        f"结论: {verdict}",
        f"关注点: {concerns}",
        f"A 状态: {supervisor_state}",
        f"B 状态: {worker_state}",
        f"A 会话: {run.supervisor_session_id or '暂无'}",
        f"B 会话: {run.worker_session_id or '暂无'}",
        f"最近判定: {run.last_decision or '暂无'}",
        f"无进展计数: {run.no_progress_cycles}",
        f"重复指令计数: {run.same_instruction_cycles}",
    ]
    if runtime is not None and runtime.thread_alive:
        lines.append(f"运行线程: {'存活' if runtime.thread_alive else '未运行'}")
        if runtime.actor:
            lines.append(f"当前执行者: {runtime.actor}")
        if runtime.pid is not None:
            lines.append(f"当前 PID: {runtime.pid}")
        if runtime.process_started_at is not None:
            lines.append(f"已运行时长: {_format_elapsed(runtime.process_started_at)}")
    if latest_worker is not None:
        lines.append(f"B 最近摘要: {_clip(latest_worker.summary or '暂无', 120)}")
    if latest_supervisor is not None:
        lines.append(f"A 最近摘要: {_clip(latest_supervisor.summary or '暂无', 120)}")
    if raw_output_lines:
        lines.append("原始输出:")
        lines.extend(f"- {_clip(line, 140)}" for line in raw_output_lines[-6:])
    lines.append(f"下一步: {next_step}")
    return _card("Autopilot", lines)


def format_autopilot_runs(runs: list[AutopilotRunRecord]) -> str:
    lines = ["【Autopilot Runs】"]
    if not runs:
        lines.append("当前项目暂无 autopilot run。")
        lines.append("下一步: 执行 /autopilot <goal> 创建长期任务。")
        return "\n".join(lines)

    lines.append(f"总数: {len(runs)}")
    lines.append("最近 runs:")
    for run in runs:
        lines.append(
            f"- #{run.id} · {run.status} · {run.cycle_count}/{run.max_cycles} · {_clip(run.goal, 60)}"
        )
    lines.append("下一步: 可执行 /autopilot-status <id>、/autopilot-context <id>，或直接点按钮管理。")
    return "\n".join(lines)


def format_autopilot_step_result(
    *,
    run: AutopilotRunRecord,
    worker_summary: str,
    supervisor_summary: str,
) -> str:
    return _card(
        "Autopilot Step",
        [
            f"Run: #{run.id}",
            f"状态: {run.status}",
            f"阶段: {run.current_phase}",
            f"轮次: {run.cycle_count}/{run.max_cycles}",
            f"B: {_clip(worker_summary, 140)}",
            f"A: {_clip(supervisor_summary, 140)}",
            (
                "下一步: 后台会继续自治推进；可查看 /autopilot-status。"
                if run.status in {"running_worker", "running_supervisor"}
                else "下一步: 已执行单轮并重新暂停；可再次 /autopilot-step 或 /autopilot-resume。"
                if run.status == "paused"
                else "下一步: 可查看 /autopilot-status。"
            ),
        ],
    )


def format_autopilot_action_result(
    *,
    run: AutopilotRunRecord,
    action: str,
    note: str | None = None,
) -> str:
    next_step = "下一步: 可继续查看 /autopilot-status。"
    if action == "takeover":
        next_step = "下一步: 后台会按新的高层指令继续自治推进；可查看 /autopilot-context。"
    return _card(
        "Autopilot",
        [
            f"Run: #{run.id}",
            f"动作: {action}",
            f"状态: {run.status}",
            f"阶段: {run.current_phase}",
            f"轮次: {run.cycle_count}/{run.max_cycles}",
            f"最近判定: {run.last_decision or '暂无'}",
            f"备注: {note or run.paused_reason or '暂无'}",
            next_step,
        ],
    )


def format_autopilot_context(
    *,
    run: AutopilotRunRecord,
    events: list[AutopilotEventRecord],
    runtime: AutopilotRuntimeSnapshot | None = None,
    raw_output_lines: list[str] | None = None,
    persisted_stream_lines: list[str] | None = None,
) -> str:
    verdict, concerns, next_step = _autopilot_verdict(run, events)
    latest_worker = next((event for event in reversed(events) if event.actor == "worker"), None)
    latest_supervisor = next((event for event in reversed(events) if event.actor == "supervisor"), None)
    supervisor_state = _autopilot_actor_state(actor="supervisor", events=events, runtime=runtime)
    worker_state = _autopilot_actor_state(actor="worker", events=events, runtime=runtime)
    lines = [
        f"Run: #{run.id}",
        f"状态: {run.status}",
        f"阶段: {run.current_phase}",
        f"轮次: {run.cycle_count}/{run.max_cycles}",
        f"结论: {verdict}",
        f"关注点: {concerns}",
        f"A 状态: {supervisor_state}",
        f"B 状态: {worker_state}",
        f"A 会话: {run.supervisor_session_id or '暂无'}",
        f"B 会话: {run.worker_session_id or '暂无'}",
        f"最近判定: {run.last_decision or '暂无'}",
        f"无进展计数: {run.no_progress_cycles}",
        f"重复指令计数: {run.same_instruction_cycles}",
    ]
    if runtime is not None and runtime.thread_alive:
        lines.append(f"运行线程: {'存活' if runtime.thread_alive else '未运行'}")
        if runtime.actor:
            lines.append(f"当前执行者: {runtime.actor}")
        if runtime.pid is not None:
            lines.append(f"当前 PID: {runtime.pid}")
        if runtime.process_started_at is not None:
            lines.append(f"已运行时长: {_format_elapsed(runtime.process_started_at)}")
    if latest_worker is not None:
        lines.append(f"B 最近事件: {latest_worker.event_type}")
        lines.append(f"B 最近摘要: {_clip(latest_worker.summary or '暂无', 120)}")
        if latest_worker.payload:
            blockers = latest_worker.payload.get("blockers")
            if isinstance(blockers, str) and blockers.strip() and blockers.strip().lower() != "none":
                lines.append(f"B 当前阻塞: {_clip(blockers.strip(), 120)}")
            recommended_next = latest_worker.payload.get("recommended_next_step")
            if isinstance(recommended_next, str) and recommended_next.strip():
                lines.append(f"B 建议下一步: {_clip(recommended_next.strip(), 120)}")
    if latest_supervisor is not None:
        lines.append(f"A 最近事件: {latest_supervisor.event_type}")
        lines.append(f"A 最近摘要: {_clip(latest_supervisor.summary or '暂无', 120)}")
        if latest_supervisor.payload:
            reason = latest_supervisor.payload.get("reason")
            if isinstance(reason, str) and reason.strip():
                lines.append(f"A 判定理由: {_clip(reason.strip(), 120)}")
            next_instruction = latest_supervisor.payload.get("next_instruction_for_b")
            if isinstance(next_instruction, str) and next_instruction.strip():
                lines.append(f"A 给 B 的下一步: {_clip(next_instruction.strip(), 120)}")
    recent_events = events[-4:]
    if recent_events:
        lines.append("最近事件:")
        for event in recent_events:
            lines.append(
                f"- {event.cycle_no}:{event.actor}/{event.event_type} · {_clip(event.summary or '暂无', 80)}"
            )
    if raw_output_lines:
        lines.append("原始输出:")
        lines.extend(f"- {_clip(line, 140)}" for line in raw_output_lines[-8:])
    if persisted_stream_lines:
        lines.append("持久化流回顾:")
        lines.extend(f"- {_clip(line, 140)}" for line in persisted_stream_lines[-12:])
    lines.append(f"下一步: {next_step}")
    return _card("Autopilot Context", lines)


def format_autopilot_log(
    *,
    run: AutopilotRunRecord,
    chunks: list[AutopilotStreamChunkRecord],
) -> str:
    lines = [
        f"Run: #{run.id}",
        f"状态: {run.status}",
        f"阶段: {run.current_phase}",
        f"轮次: {run.cycle_count}/{run.max_cycles}",
    ]
    if not chunks:
        lines.append("持久化原始流: 暂无")
        lines.append("下一步: 先运行 autopilot，或等待新的流式输出写入。")
        return _card("Autopilot Log", lines)

    lines.append(f"持久化流条数: {len(chunks)}")
    lines.append("最近原始流:")
    lines.extend(f"- {_clip(_render_stream_chunk(chunk), 140)}" for chunk in chunks[-40:])
    lines.append("下一步: 可继续查看 /autopilot-context，或等待 run 继续推进。")
    return _card("Autopilot Log", lines)


def _autopilot_verdict(
    run: AutopilotRunRecord,
    events: list[AutopilotEventRecord] | None = None,
) -> tuple[str, str, str]:
    if (
        run.status in {"running_worker", "running_supervisor"}
        and run.cycle_count == 0
        and run.supervisor_session_id is None
        and run.worker_session_id is None
        and not events
    ):
        return (
            "启动中",
            "已进入后台自治流程，首轮结果尚未产出",
            "无需继续输入；等待首轮完成，或执行 /autopilot-context 查看是否已有新事件。",
        )
    if run.status == "completed":
        return ("已完成", "任务已结束", "可查看 /autopilot-context，或创建新的 autopilot run。")
    if run.status in {"blocked", "needs_human", "failed"}:
        concern = "任务已停止推进"
        if run.no_progress_cycles >= 2:
            concern = "连续无进展，已停止推进"
        elif run.same_instruction_cycles >= 2:
            concern = "指令重复，已停止推进"
        elif run.status == "needs_human":
            concern = "需要人工判断"
        elif run.status == "failed":
            concern = "执行异常"
        return ("阻塞", concern, "可查看 /autopilot-context，必要时人工接管或新建 run。")
    if run.status == "paused":
        return ("已暂停", run.paused_reason or "用户暂停", "可执行 /autopilot-resume 恢复，或执行 /autopilot-stop 停止。")
    concerns: list[str] = []
    if run.no_progress_cycles >= 1:
        concerns.append("最近一轮无进展")
    if run.same_instruction_cycles >= 1:
        concerns.append("最近指令开始重复")
    if run.cycle_count >= max(1, run.max_cycles - 5):
        concerns.append("接近轮次上限")
    if not concerns:
        return ("正常推进", "暂无", "后台会继续自治推进；可执行 /autopilot-pause 或 /autopilot-stop。")
    return ("接近阻塞", "；".join(concerns), "建议查看 /autopilot-context；必要时执行 /autopilot-pause 或 /autopilot-stop。")


def _autopilot_actor_state(
    *,
    actor: str,
    events: list[AutopilotEventRecord],
    runtime: AutopilotRuntimeSnapshot | None,
) -> str:
    if runtime is not None and runtime.thread_alive and runtime.actor == actor:
        if runtime.last_output_at is not None:
            quiet_for = max(0.0, time.monotonic() - runtime.last_output_at)
            if quiet_for >= 120:
                return f"静默中（{_format_elapsed_seconds(quiet_for)} 无新输出）"
        return "运行中"

    if actor == "worker":
        started_types = {"stage_started"}
        completed_types = {"stage_completed", "stage_failed"}
    else:
        started_types = {"decision_started"}
        completed_types = {"decision_made", "decision_failed"}

    latest_started = next(
        (event for event in reversed(events) if event.actor == actor and event.event_type in started_types),
        None,
    )
    latest_completed = next(
        (event for event in reversed(events) if event.actor == actor and event.event_type in completed_types),
        None,
    )

    if latest_started is None and latest_completed is None:
        return "未启动"
    if latest_started is not None and (
        latest_completed is None
        or latest_started.cycle_no > latest_completed.cycle_no
        or (latest_started.cycle_no == latest_completed.cycle_no and latest_started.id > latest_completed.id)
    ):
        return "已退出"
    return "已完成上一轮"


def _format_elapsed(started_at: float) -> str:
    elapsed = max(0.0, time.monotonic() - started_at)
    return _format_elapsed_seconds(elapsed)


def _format_elapsed_seconds(elapsed: float) -> str:
    if elapsed < 1:
        return "<1s"
    if elapsed < 60:
        return f"{int(elapsed)}s"
    minutes, seconds = divmod(int(elapsed), 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


def _render_stream_chunk(chunk: AutopilotStreamChunkRecord) -> str:
    actor_label = "A" if chunk.actor == "supervisor" else "B"
    return f"{chunk.cycle_no}:{actor_label}>[{chunk.channel}] {chunk.content}"


def format_health(
    *,
    version: str,
    branch: str,
    commit: str,
    codex_available: bool,
    project_count: int,
    active_project_key: str | None,
    active_task_summary: str | None,
    pending_approval: bool,
    current_model: str | None,
    session_id: str | None,
) -> str:
    issues: list[str] = []
    if not codex_available:
        issues.append("Codex CLI 不可用")
    if project_count == 0:
        issues.append("还没有已注册项目")
    if active_project_key is None and project_count > 0:
        issues.append("当前未选择项目")
    if pending_approval:
        issues.append("有待处理审批")

    if not issues:
        verdict = "良好"
    elif not codex_available:
        verdict = "阻塞"
    else:
        verdict = "需处理"

    if not codex_available:
        next_step = "先确认 codex 可执行，再重新运行 /health。"
    elif project_count == 0:
        next_step = "先创建或导入项目，再开始使用。"
    elif active_project_key is None:
        next_step = "先切换项目，再直接提问或执行任务。"
    elif pending_approval:
        next_step = "先处理审批，再继续当前任务。"
    elif active_task_summary:
        next_step = "可查看当前任务，或等待执行完成。"
    else:
        next_step = "服务正常，可直接提问、执行任务或查看日志。"

    lines = [
        "服务: 在线",
        f"结论: {verdict}",
        f"版本: {version}",
        f"分支: {branch}",
        f"提交: {commit}",
        f"Codex: {'可用' if codex_available else '不可用'}",
        f"项目数: {project_count}",
        f"当前项目: {active_project_key or '未选择'}",
        f"当前任务: {active_task_summary or '空闲'}",
        f"当前模型: {current_model or '默认'}",
        f"当前会话: {session_id or '暂无'}",
        f"审批: {'待处理' if pending_approval else '无'}",
    ]
    if issues:
        lines.append("关注点: " + "；".join(issues))
    else:
        lines.append("关注点: 暂无")
    lines.append(f"下一步: {next_step}")
    return _card("服务", lines)


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
    lines.append("可用 /task-cancel [id]、/task-delete <id> 或 /tasks-clear 管理任务。")
    return "\n".join(lines)


def format_current_task(*, project_key: str, task: TaskRecord | None) -> str:
    lines = [f"项目: {project_key}"]
    if task is None:
        lines.append("当前任务: 暂无")
        lines.append("下一步: 直接 /ask、/do，或打开 /tasks 查看历史任务。")
        return _card("当前任务", lines)

    lines.extend(
        [
            f"任务: #{task.id}",
            f"类型: /{task.command_type}",
            f"状态: {STATUS_LABELS.get(task.status, task.status)}",
            f"会话: {task.codex_session_id or '暂无'}",
            f"请求: {_clip(task.original_request, 160)}",
            f"摘要: {_clip(task.latest_summary, 180) if task.latest_summary else '暂无'}",
        ]
    )
    if task.status in {"created", "running", "waiting_approval"}:
        lines.append("操作: 可直接取消当前任务，或回到状态页继续观察。")
    else:
        lines.append("操作: 可继续该上下文、查看任务列表，或直接发起新任务。")
    return _card("当前任务", lines)


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
