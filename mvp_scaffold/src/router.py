"""Command parsing and routing."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from pathlib import Path
from threading import Lock
from _thread import LockType


class _NullLock:
    """No-op lock returned for read-only (ask) operations so they bypass the project mutex."""

    def release(self) -> None:  # noqa: D102
        pass

from src.approval import ApprovalService
from src import audit_events
from src.autopilot_service import AutopilotService
from src.auth import is_allowed_user
from src.codex_session_service import CodexSessionService
from src.formatters import (
    format_approval_required,
    format_do_result,
    format_project_busy,
)
from src.github_repo_service import GitHubRepoService
from src.handlers import (
    _AutopilotHandler,
    _ProjectHandler,
    _ScheduleHandler,
    _SkillsHandler,
    _SystemHandler,
    _TaskHandler,
)
from src.handlers._types import ActiveProjectContext, ActiveTaskExecution, DocumentUploadPlan
from src.mcp_service import McpService
from src.models import CommandContext, CommandResult, ProjectConfig, UserRecord
from src.project_registry import ProjectRegistry
from src.redaction import redact_text
from src.repo_inspector import RepoInspector
from src.skills_service import SkillsService
from src.task_store import ScheduledTaskRecord, TaskStore
from src.update_service import UpdateService


logger = logging.getLogger(__name__)


class CommandRouter(
    _SystemHandler,
    _ProjectHandler,
    _TaskHandler,
    _AutopilotHandler,
    _ScheduleHandler,
    _SkillsHandler,
):
    """Routes supported commands to storage and Codex layers."""

    def __init__(
        self,
        config,
        projects: ProjectRegistry,
        tasks: TaskStore,
        audit,
        codex,
        repo: RepoInspector,
        approvals: ApprovalService,
        skills_service: SkillsService | None = None,
        mcp_service: McpService | None = None,
        update_service: UpdateService | None = None,
        codex_sessions: CodexSessionService | None = None,
        github_repos: GitHubRepoService | None = None,
        autopilot_service: AutopilotService | None = None,
    ) -> None:
        self.config = config
        self.projects = projects
        self.tasks = tasks
        self.audit = audit
        self.codex = codex
        self.repo = repo
        self.approvals = approvals
        self.skills_service = skills_service
        self.mcp_service = mcp_service
        self.update_service = update_service
        self.codex_sessions = codex_sessions
        self.github_repos = github_repos
        self.autopilot = autopilot_service
        self._scheduler = None
        self._project_locks: dict[int, LockType] = {}
        self._project_locks_guard = Lock()
        self._active_task_executions: dict[int, ActiveTaskExecution] = {}
        self._active_task_guard = Lock()

    def set_scheduler(self, scheduler) -> None:  # noqa: ANN001
        """Wire in the scheduler after construction (avoids circular dependency)."""
        self._scheduler = scheduler

    def _build_context_enriched_prompt(
        self,
        *,
        project_id: int,
        request_text: str,
        max_context_chars: int = 600,
    ) -> str:
        """Prepend a compact project memory block to the user prompt for new tasks."""
        try:
            memory = self.tasks.get_memory_snapshot(project_id=project_id, page=1, page_size=3)
        except Exception:  # noqa: BLE001
            return request_text

        parts: list[str] = []
        if memory.project_summary:
            parts.append(memory.project_summary[:200])
        if memory.notes:
            parts.append("Notes: " + " | ".join(n[:80] for n in memory.notes[:3]))
        if memory.recent_task_summaries:
            parts.append("Recent: " + " | ".join(s[:100] for s in memory.recent_task_summaries[:2]))

        if not parts:
            return request_text

        context_block = "\n".join(parts)
        if len(context_block) > max_context_chars:
            context_block = context_block[:max_context_chars] + "..."
        return f"[Project Context]\n{context_block}\n---\n{request_text}"

    # ------------------------------------------------------------------ #
    #  Dispatch                                                            #
    # ------------------------------------------------------------------ #

    def handle(self, ctx: CommandContext) -> CommandResult:
        if not is_allowed_user(self.config, ctx.telegram_user_id):
            return CommandResult("未授权用户。")

        text = ctx.text.strip()
        if not text:
            return self._handle_help(ctx, "")
        command, _, remainder = text.partition(" ")
        argument = remainder.strip()
        if "@" in command:
            command = command.split("@", 1)[0]

        if command == "/start":
            return self._handle_start(ctx)
        if command == "/home":
            return self._handle_home(ctx)
        if command == "/context":
            return self._handle_context(ctx)
        if command == "/help":
            return self._handle_help(ctx, argument)
        if command == "/projects":
            return self._handle_projects(ctx)
        if command == "/project-root":
            return self._handle_project_root(ctx, argument)
        if command == "/project-template-root":
            return self._handle_project_template_root(ctx, argument)
        if command == "/project-templates":
            return self._handle_project_templates(ctx)
        if command == "/project-add":
            return self._handle_project_add(ctx, argument)
        if command == "/project-disable":
            return self._handle_project_disable(ctx, argument)
        if command == "/project-archive":
            return self._handle_project_archive(ctx, argument)
        if command == "/skills":
            return self._handle_skills(ctx)
        if command == "/skill-install":
            return self._handle_skill_install(ctx, argument)
        if command == "/mcp":
            return self._handle_mcp(ctx, argument)
        if command == "/mcp-enable":
            return self._handle_mcp_toggle(ctx, argument, enabled=True)
        if command == "/mcp-disable":
            return self._handle_mcp_toggle(ctx, argument, enabled=False)
        if command == "/sessions":
            return self._handle_sessions(ctx, argument)
        if command == "/session":
            return self._handle_session(ctx, argument)
        if command == "/session-import":
            return self._handle_session_import(ctx, argument)
        if command == "/model":
            return self._handle_model(ctx, argument)
        if command in {"/download-file", "/send-file"}:
            return self._handle_send_file(ctx, argument)
        if command == "/github-clone":
            return self._handle_github_clone(ctx, argument)
        if command == "/version":
            return self._handle_version(ctx)
        if command == "/health":
            return self._handle_health(ctx)
        if command == "/update-check":
            return self._handle_update_check(ctx)
        if command == "/update":
            return self._handle_update(ctx)
        if command == "/restart":
            return self._handle_restart(ctx)
        if command == "/logs":
            return self._handle_logs(ctx)
        if command == "/logs-clear":
            return self._handle_logs_clear(ctx)
        if command == "/ui":
            return self._handle_ui(ctx, argument)
        if command == "/schedule-add":
            return self._handle_schedule_add(ctx, argument)
        if command == "/schedule-list":
            return self._handle_schedule_list(ctx)
        if command == "/schedule-run":
            return self._handle_schedule_run(ctx, argument)
        if command == "/schedule-pause":
            return self._handle_schedule_toggle(ctx, argument, enabled=False)
        if command == "/schedule-enable":
            return self._handle_schedule_toggle(ctx, argument, enabled=True)
        if command == "/schedule-del":
            return self._handle_schedule_delete(ctx, argument)
        if command == "/use":
            return self._handle_use(ctx, argument)
        if command == "/last":
            return self._handle_last(ctx)
        if command == "/retry":
            return self._handle_retry(ctx, argument)
        if command == "/status":
            return self._handle_status(ctx)
        if command == "/task-current":
            return self._handle_task_current(ctx)
        if command == "/autopilot":
            return self._handle_autopilot(ctx, argument)
        if command == "/autopilots":
            return self._handle_autopilots(ctx)
        if command == "/autopilot-status":
            return self._handle_autopilot_status(ctx, argument)
        if command == "/autopilot-context":
            return self._handle_autopilot_context(ctx, argument)
        if command == "/autopilot-log":
            return self._handle_autopilot_log(ctx, argument)
        if command == "/autopilot-takeover":
            return self._handle_autopilot_takeover(ctx, argument)
        if command == "/autopilot-step":
            return self._handle_autopilot_step(ctx, argument)
        if command == "/autopilot-pause":
            return self._handle_autopilot_pause(ctx, argument)
        if command == "/autopilot-resume":
            return self._handle_autopilot_resume(ctx, argument)
        if command == "/autopilot-stop":
            return self._handle_autopilot_stop(ctx, argument)
        if command == "/do":
            return self._handle_do(ctx, argument)
        if command == "/ask":
            return self._handle_ask(ctx, argument)
        if command == "/resume":
            return self._handle_resume(ctx, argument)
        if command == "/approve":
            return self._handle_approve(ctx, argument)
        if command == "/reject":
            return self._handle_reject(ctx, argument)
        if command == "/note":
            return self._handle_note(ctx, argument)
        if command == "/memory":
            return self._handle_memory(ctx, argument)
        if command == "/cancel":
            return self._handle_cancel(ctx)
        if command == "/tasks":
            return self._handle_tasks(ctx, argument)
        if command == "/task-cancel":
            return self._handle_task_cancel(ctx, argument)
        if command == "/task-delete":
            return self._handle_task_delete(ctx, argument)
        if command == "/tasks-clear":
            return self._handle_tasks_clear(ctx)
        if command == "/diff":
            return self._handle_diff(ctx)
        if command == "/upload_policy":
            return self._handle_upload_policy(ctx)
        if text.startswith("/"):
            return CommandResult("未知命令，请使用 /help。")
        return self._handle_plain_text(ctx, text)

    def run_scheduled_task(self, schedule: ScheduledTaskRecord) -> CommandResult:
        project_key = self.tasks.get_project_key_by_id(schedule.project_id)
        if not project_key:
            return CommandResult("定期任务执行失败：关联项目不存在。", metadata={"status": "failed"})
        project = self.projects.get(project_key)
        if project is None:
            return CommandResult("定期任务执行失败：项目不在注册表中。", metadata={"status": "failed"})
        if not self.projects.is_path_allowed(project, project.path):
            return CommandResult("定期任务执行失败：项目路径超出允许范围。", metadata={"status": "failed"})
        if not project.path.exists():
            return CommandResult("定期任务执行失败：项目路径不存在。", metadata={"status": "failed"})

        active = ActiveProjectContext(
            user=UserRecord(id=schedule.user_id, telegram_user_id=str(schedule.user_id)),
            project_key=project_key,
            project=project,
            project_id=schedule.project_id,
        )
        ctx = CommandContext(
            telegram_user_id=str(schedule.user_id),
            telegram_chat_id=schedule.telegram_chat_id,
            telegram_message_id=None,
            text=f"/{schedule.command_type} {schedule.request_text}",
        )
        result = self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type=schedule.command_type,
            request_text=schedule.request_text,
            run_mode=schedule.command_type,
            next_step="该任务由定期调度触发。",
        )
        status = result.metadata.get("status") if result.metadata else None
        task_id_value = result.metadata.get("task_id") if result.metadata else None
        task_id = task_id_value if isinstance(task_id_value, int) else None
        self.audit.log(
            action=audit_events.SCHEDULE_TRIGGERED if status != "failed" else audit_events.SCHEDULE_FAILED,
            message=f"定期任务触发: #{schedule.id}",
            severity="info" if status != "failed" else "warning",
            user_id=schedule.user_id,
            project_id=schedule.project_id,
            task_id=task_id,
            details={"schedule_id": schedule.id, "status": status or "unknown"},
        )
        return result

    # ------------------------------------------------------------------ #
    #  Task execution infrastructure                                       #
    # ------------------------------------------------------------------ #

    def _run_codex_task(
        self,
        *,
        ctx: CommandContext,
        active: ActiveProjectContext,
        command_type: str,
        request_text: str,
        run_mode: str,
        next_step: str,
        resume_session_id: str | None = None,
    ) -> CommandResult:
        lock_or_result = self._try_acquire_project_lock(active=active, operation=run_mode)
        if isinstance(lock_or_result, CommandResult):
            return lock_or_result
        project_lock = lock_or_result

        task_id = self.tasks.create_task(
            user_id=active.user.id,
            project_id=active.project_id,
            chat_id=ctx.telegram_chat_id,
            message_id=ctx.telegram_message_id,
            command_type=command_type,
            original_request=request_text,
        )
        self.audit.log(
            action=audit_events.TASK_CREATED,
            message=f"已创建任务（/{command_type}）",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=task_id,
            details={"request": request_text[:300]},
        )
        self.tasks.mark_task_running(task_id)
        self.audit.log(
            action=audit_events.TASK_STARTED,
            message=f"开始执行任务（/{command_type}）",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=task_id,
        )
        self._register_active_task_execution(task_id=task_id, project_id=active.project_id)

        try:
            model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
            continuation_session_id = self._resolve_continuation_session_id(
                active=active,
                run_mode=run_mode,
                ctx=ctx,
                explicit_resume_session_id=resume_session_id,
            )
            if run_mode == "ask":
                if continuation_session_id:
                    codex_result = self.codex.ask_in_session(
                        active.project,
                        continuation_session_id,
                        request_text,
                        model=model,
                        progress_callback=ctx.progress_callback,
                        process_callback=lambda proc, tid=task_id: self._set_active_task_process(tid, proc),
                    )
                else:
                    enriched = self._build_context_enriched_prompt(
                        project_id=active.project_id,
                        request_text=request_text,
                    )
                    codex_result = self.codex.ask(
                        active.project,
                        enriched,
                        model=model,
                        progress_callback=ctx.progress_callback,
                        process_callback=lambda proc, tid=task_id: self._set_active_task_process(tid, proc),
                    )
            elif run_mode == "resume":
                if resume_session_id:
                    codex_result = self.codex.resume_session(
                        active.project,
                        resume_session_id,
                        request_text,
                        model=model,
                        progress_callback=ctx.progress_callback,
                        process_callback=lambda proc, tid=task_id: self._set_active_task_process(tid, proc),
                    )
                else:
                    codex_result = self.codex.resume_last(
                        active.project,
                        request_text,
                        model=model,
                        progress_callback=ctx.progress_callback,
                        process_callback=lambda proc, tid=task_id: self._set_active_task_process(tid, proc),
                    )
            else:
                if continuation_session_id:
                    codex_result = self.codex.resume_session(
                        active.project,
                        continuation_session_id,
                        request_text,
                        model=model,
                        progress_callback=ctx.progress_callback,
                        process_callback=lambda proc, tid=task_id: self._set_active_task_process(tid, proc),
                    )
                else:
                    enriched = self._build_context_enriched_prompt(
                        project_id=active.project_id,
                        request_text=request_text,
                    )
                    codex_result = self.codex.run(
                        active.project,
                        enriched,
                        model=model,
                        progress_callback=ctx.progress_callback,
                        process_callback=lambda proc, tid=task_id: self._set_active_task_process(tid, proc),
                    )

            if self._is_cancel_requested(task_id):
                cancelled = self.tasks.cancel_task(task_id=task_id, project_id=active.project_id)
                if cancelled is not None:
                    self.tasks.update_project_state_after_task(
                        project_id=active.project_id,
                        task_id=cancelled.id,
                        summary=cancelled.latest_summary or "任务已取消。",
                        codex_session_id=cancelled.codex_session_id,
                        pending_approval_task_id=None,
                        next_step="可用 /do 新建任务，或用 /status 查看状态。",
                    )
                    return CommandResult(f"任务 #{cancelled.id}: 已取消")

            assessment = self.approvals.assess(codex_result)
            if assessment.requires_approval:
                return self._handle_waiting_approval(
                    active=active,
                    task_id=task_id,
                    codex_result=codex_result,
                    reason=assessment.reason or "继续执行前需要审批。",
                )

            return self._finalize_task_execution(
                active=active,
                task_id=task_id,
                run_mode=run_mode,
                codex_result=codex_result,
                next_step=next_step,
            )
        finally:
            self._clear_active_task_execution(task_id)
            project_lock.release()

    def _handle_waiting_approval(
        self,
        *,
        active: ActiveProjectContext,
        task_id: int,
        codex_result,
        reason: str,
    ) -> CommandResult:
        self.tasks.add_task_artifact(
            task_id,
            "codex_stdout",
            content=codex_result.stdout,
            metadata={"exit_code": codex_result.exit_code, "mode": "waiting_approval"},
        )
        if codex_result.stderr:
            self.tasks.add_task_artifact(
                task_id,
                "codex_stderr",
                content=codex_result.stderr,
                metadata={"exit_code": codex_result.exit_code, "mode": "waiting_approval"},
            )

        safe_reason = redact_text(reason)
        waiting_summary = f"等待审批: {safe_reason}"
        self.tasks.mark_task_waiting_approval(
            task_id=task_id,
            summary=waiting_summary,
            pending_action=safe_reason,
            codex_session_id=codex_result.session_id,
        )
        self.tasks.create_approval_request(
            task_id=task_id,
            requested_action=safe_reason,
            requested_by_user_id=active.user.id,
        )
        self.tasks.update_project_state_after_task(
            project_id=active.project_id,
            task_id=task_id,
            summary=waiting_summary,
            codex_session_id=codex_result.session_id,
            pending_approval_task_id=task_id,
            next_step="请使用 /approve 或 /reject。",
        )
        self.audit.log(
            action=audit_events.APPROVAL_REQUESTED,
            message=f"任务 #{task_id} 请求审批",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=task_id,
            details={"reason": safe_reason[:200]},
        )
        return CommandResult(
            redact_text(format_approval_required(task_id=task_id, reason=safe_reason)),
            metadata={"task_id": task_id, "status": "waiting_approval"},
        )

    def _finalize_task_execution(
        self,
        *,
        active: ActiveProjectContext,
        task_id: int,
        run_mode: str,
        codex_result,
        next_step: str,
    ) -> CommandResult:
        self.tasks.add_task_artifact(
            task_id,
            "codex_stdout",
            content=codex_result.stdout,
            metadata={"exit_code": codex_result.exit_code, "mode": run_mode},
        )
        if codex_result.stderr:
            self.tasks.add_task_artifact(
                task_id,
                "codex_stderr",
                content=codex_result.stderr,
                metadata={"exit_code": codex_result.exit_code, "mode": run_mode},
            )

        safe_summary = redact_text(codex_result.summary)
        display_text = redact_text(codex_result.display_text or codex_result.summary)
        task_status = "completed" if codex_result.ok else "failed"
        error_summary = (
            redact_text(codex_result.stderr[:500]) if not codex_result.ok and codex_result.stderr else None
        )
        self.tasks.finalize_task(
            task_id=task_id,
            status=task_status,
            summary=safe_summary,
            error=error_summary,
            codex_session_id=codex_result.session_id,
        )
        # Auto-write a memory note so future tasks benefit from what was done here.
        if codex_result.ok and run_mode in {"do", "resume"} and safe_summary:
            try:
                self.tasks.add_task_completion_note(
                    project_id=active.project_id,
                    title=f"任务 #{task_id}",
                    content=safe_summary[:500],
                )
            except Exception:
                logger.warning(
                    "Failed to persist task completion note for project_id=%s task_id=%s",
                    active.project_id,
                    task_id,
                    exc_info=True,
                )
        self.tasks.update_project_state_after_task(
            project_id=active.project_id,
            task_id=task_id,
            summary=safe_summary,
            codex_session_id=codex_result.session_id,
            pending_approval_task_id=None,
            next_step=next_step,
        )
        self._refresh_repo_state(project_id=active.project_id, project=active.project)
        self.audit.log(
            action=audit_events.TASK_COMPLETED if codex_result.ok else audit_events.TASK_FAILED,
            message=f"任务结束，状态: {task_status}",
            severity="info" if codex_result.ok else "error",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=task_id,
            details={"exit_code": codex_result.exit_code, "session_id": codex_result.session_id},
        )
        next_action = None
        if not codex_result.ok:
            next_action = (
                "先执行 /status 查看详情；可用 /diff 检查变更；"
                "若是目录信任问题，请在本机 Codex 完成 trusted 配置后重试。"
            )
        return CommandResult(
            format_do_result(
                project_key=active.project_key,
                task_id=task_id,
                status=task_status,
                summary=display_text,
                session_id=codex_result.session_id,
                next_action=next_action,
            ),
            metadata={"task_id": task_id, "status": task_status},
        )

    # ------------------------------------------------------------------ #
    #  Project resolution                                                  #
    # ------------------------------------------------------------------ #

    def _resolve_active_project(self, ctx: CommandContext) -> ActiveProjectContext | CommandResult:
        user = self.tasks.ensure_user(ctx)
        project_key = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if not project_key:
            recent_projects = self.tasks.list_recent_project_keys(user_id=user.id)
            return CommandResult(
                "未选择活跃项目。\n请先点\u201c项目\u201d切换，或使用 /projects 查看项目列表。",
                metadata={"recent_projects": recent_projects},
            )

        project = self.projects.get(project_key)
        if project is None:
            recent_projects = self.tasks.list_recent_project_keys(user_id=user.id)
            return CommandResult(
                f"当前项目 {project_key} 不在注册表中，请重新选择项目。",
                metadata={"recent_projects": recent_projects},
            )
        if not self.projects.is_path_allowed(project, project.path):
            return CommandResult(f"项目路径超出允许范围: {project.path}")
        if not project.path.exists():
            return CommandResult(f"项目路径不存在: {project.path}")

        return ActiveProjectContext(
            user=user,
            project_key=project_key,
            project=project,
            project_id=self.tasks.get_project_id(project_key),
        )

    def _resolve_continuation_session_id(
        self,
        *,
        active: ActiveProjectContext,
        run_mode: str,
        ctx: CommandContext,
        explicit_resume_session_id: str | None,
    ) -> str | None:
        if explicit_resume_session_id:
            return None
        if run_mode not in {"ask", "do"}:
            return None
        if ctx.telegram_message_id is None:
            return None
        return self.tasks.get_project_last_codex_session_id(project_id=active.project_id)

    # ------------------------------------------------------------------ #
    #  Repo / lock / process management                                   #
    # ------------------------------------------------------------------ #

    def _refresh_repo_state(self, *, project_id: int, project: ProjectConfig) -> None:
        repo_state = self.repo.inspect(project.path)
        self.tasks.update_repo_state(
            project_id=project_id,
            branch=repo_state.branch if repo_state.is_git_repo else None,
            repo_dirty=repo_state.dirty if repo_state.is_git_repo else None,
        )

    def _register_active_task_execution(self, *, task_id: int, project_id: int) -> None:
        with self._active_task_guard:
            self._active_task_executions[task_id] = ActiveTaskExecution(
                task_id=task_id,
                project_id=project_id,
            )

    def _set_active_task_process(self, task_id: int, process: subprocess.Popen[str] | None) -> None:
        with self._active_task_guard:
            state = self._active_task_executions.get(task_id)
            if state is not None:
                state.process = process

    def _clear_active_task_execution(self, task_id: int) -> None:
        with self._active_task_guard:
            self._active_task_executions.pop(task_id, None)

    def _is_cancel_requested(self, task_id: int) -> bool:
        with self._active_task_guard:
            state = self._active_task_executions.get(task_id)
            return bool(state and state.cancel_requested)

    def _request_active_task_cancel(self, task_id: int) -> bool:
        with self._active_task_guard:
            state = self._active_task_executions.get(task_id)
            if state is None:
                return False
            state.cancel_requested = True
            process = state.process
        if process is None:
            return True
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except Exception:
            try:
                process.kill()
            except Exception:
                return False
        return True

    def _try_acquire_project_lock(
        self, *, active: ActiveProjectContext, operation: str
    ) -> LockType | _NullLock | CommandResult:
        lock = self._get_project_lock(active.project_id)
        if lock.acquire(blocking=False):
            return lock
        self.audit.log(
            action=audit_events.TASK_QUEUE_BLOCKED,
            message="项目任务队列繁忙",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"operation": operation},
        )
        return CommandResult(format_project_busy())

    def _get_project_lock(self, project_id: int) -> LockType:
        with self._project_locks_guard:
            lock = self._project_locks.get(project_id)
            if lock is None:
                lock = Lock()
                self._project_locks[project_id] = lock
            return lock
