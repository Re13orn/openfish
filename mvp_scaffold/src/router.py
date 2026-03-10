"""Command parsing and routing."""

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
from threading import Lock
from _thread import LockType
from uuid import uuid4

from src.approval import ApprovalService
from src import audit_events
from src.auth import is_allowed_user
from src.codex_session_service import CodexSessionService
from src.formatters import (
    format_approval_required,
    format_current_task,
    format_diff_card,
    format_do_result,
    format_help,
    format_last_task,
    format_memory,
    format_mcp_detail,
    format_mcp_list,
    format_project_busy,
    format_projects,
    format_session_detail,
    format_sessions_list,
    format_tasks_list,
    format_skill_install_result,
    format_skills_list,
    format_schedule_added,
    format_schedule_deleted,
    format_schedule_list,
    format_schedule_run_result,
    format_schedule_toggled,
    format_start,
    format_status,
    format_update_check,
    format_upload_policy,
    format_upload_rejected,
    format_use_confirmation,
    format_version_info,
)
from src.github_repo_service import GitHubRepoService
from src.mcp_service import McpService
from src.models import CommandContext, CommandResult, ProjectConfig, UserRecord
from src.project_registry import ProjectRegistry
from src.redaction import redact_text
from src.repo_inspector import RepoInspector
from src.security_guard import has_symlink_in_path, is_sensitive_file_name
from src.skills_service import SkillsService
from src.task_store import ScheduledTaskRecord, TaskStore
from src.update_service import UpdateService


PROJECT_ADD_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(slots=True)
class ActiveProjectContext:
    user: UserRecord
    project_key: str
    project: ProjectConfig
    project_id: int


@dataclass(slots=True)
class DocumentUploadPlan:
    active: ActiveProjectContext
    original_name: str
    safe_name: str
    size_bytes: int
    local_path: Path


@dataclass(slots=True)
class ActiveTaskExecution:
    task_id: int
    project_id: int
    process: subprocess.Popen[str] | None = None
    cancel_requested: bool = False


class CommandRouter:
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
        self._project_locks: dict[int, LockType] = {}
        self._project_locks_guard = Lock()
        self._active_task_executions: dict[int, ActiveTaskExecution] = {}
        self._active_task_guard = Lock()

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
        if command == "/help":
            return self._handle_help(ctx, argument)
        if command == "/projects":
            return self._handle_projects(ctx)
        if command == "/project-root":
            return self._handle_project_root(ctx, argument)
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

    def _handle_start(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        active_project = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        recent_projects = self.tasks.list_recent_project_keys(user_id=user.id)
        self.audit.log(
            action=audit_events.START_VIEWED,
            message="用户查看启动引导",
            user_id=user.id,
        )
        return CommandResult(
            format_start(active_project, recent_projects),
            metadata={"recent_projects": recent_projects},
        )

    def _chat_ui_mode(self, *, chat_id: str) -> str:
        return self.tasks.get_chat_ui_mode(chat_id=chat_id) or getattr(self.config, "default_ui_mode", "stream")

    def _handle_help(self, ctx: CommandContext, argument: str) -> CommandResult:
        mode_arg = argument.strip().lower()
        if mode_arg == "verbose":
            mode = "verbose"
        elif mode_arg in {"", "show"}:
            mode = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        else:
            mode = "summary"
        return CommandResult(format_help(mode))

    def _handle_ui(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        mode_arg = argument.strip().lower()
        current = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        if mode_arg in {"", "show"}:
            return CommandResult(f"当前界面模式: {current}")
        if mode_arg not in {"summary", "verbose", "stream"}:
            return CommandResult("用法: /ui [show|summary|verbose|stream]")
        self.tasks.set_chat_ui_mode(chat_id=ctx.telegram_chat_id, user_id=user.id, mode=mode_arg)
        return CommandResult(f"界面模式已切换为: {mode_arg}")

    def _handle_model(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        current = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        text = argument.strip()
        if not text or text.lower() == "show":
            choices = ", ".join(getattr(self.config, "codex_model_choices", ()) or ()) or "未配置"
            return CommandResult(
                f"当前模型: {current or '默认（跟随 Codex 配置）'}\n"
                f"快捷可选: {choices}\n"
                "用法: /model set <name> | /model reset"
            )

        normalized = text
        lowered = text.lower()
        if lowered.startswith("set "):
            normalized = text[4:].strip()
        if lowered in {"reset", "default"}:
            self.tasks.clear_chat_codex_model(chat_id=ctx.telegram_chat_id)
            return CommandResult("当前会话模型已恢复为默认配置。")
        if not normalized or not MODEL_NAME_PATTERN.fullmatch(normalized):
            return CommandResult("模型名称不合法。用法: /model set <name> | /model reset")

        self.tasks.set_chat_codex_model(
            chat_id=ctx.telegram_chat_id,
            user_id=user.id,
            model=normalized,
        )
        return CommandResult(f"当前会话模型已切换为: {normalized}")

    def _handle_send_file(self, ctx: CommandContext, argument: str) -> CommandResult:
        raw_path = argument.strip()
        if not raw_path:
            return CommandResult("用法: /download-file <abs_path>")

        candidate = Path(os.path.expanduser(raw_path))
        if not candidate.is_absolute():
            return CommandResult("文件路径必须是绝对路径，或使用 ~ 开头。")
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            return CommandResult(f"文件不存在: {candidate}")
        except OSError as exc:
            return CommandResult(f"读取文件失败: {exc}")

        if not resolved.is_file():
            return CommandResult("只支持发送单个文件，不支持目录。")

        try:
            size_bytes = int(resolved.stat().st_size)
        except OSError as exc:
            return CommandResult(f"读取文件失败: {exc}")

        max_size_bytes = int(
            getattr(self.config, "telegram_send_local_file_max_size_bytes", 49 * 1024 * 1024)
        )
        if size_bytes > max_size_bytes:
            return CommandResult(
                f"文件过大，无法通过 Telegram 发送。\n"
                f"当前文件: {resolved.name} ({size_bytes} bytes)\n"
                f"上限: {max_size_bytes} bytes"
            )

        user = self.tasks.ensure_user(ctx)
        self.audit.log(
            action=audit_events.SYSTEM_LOCAL_FILE_SENT,
            message=f"发送本机文件: {resolved.name}",
            user_id=user.id,
            details={
                "path": str(resolved),
                "size_bytes": size_bytes,
                "chat_id": ctx.telegram_chat_id,
            },
        )
        return CommandResult(
            f"下载文件: {resolved.name}\n路径: {resolved}",
            metadata={
                "send_local_file": {
                    "path": str(resolved),
                    "name": resolved.name,
                    "size_bytes": size_bytes,
                }
            },
        )

    def _handle_github_clone(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.github_repos is None:
            return CommandResult("当前未启用 GitHub 仓库下载。")

        repo_input, target_name = self._parse_github_clone_argument(argument)
        if repo_input is None:
            return CommandResult("用法: /github-clone <repo_url|owner/repo> [relative_dir]")

        try:
            plan = self.github_repos.plan_clone(
                repo_input=repo_input,
                project_root=active.project.path,
                target_name=target_name,
            )
        except ValueError as exc:
            return CommandResult(str(exc))

        if not self.projects.is_path_allowed(active.project, plan.target_dir):
            return CommandResult(f"目标目录超出当前项目允许范围: {plan.target_dir}")
        if has_symlink_in_path(active.project.path, plan.target_dir):
            return CommandResult(f"目标路径包含符号链接，已拒绝: {plan.target_dir}")
        if is_sensitive_file_name(plan.target_dir.name):
            return CommandResult(f"目标目录名称过于敏感，已拒绝: {plan.target_dir.name}")

        lock_or_result = self._try_acquire_project_lock(active=active, operation="github_clone")
        if isinstance(lock_or_result, CommandResult):
            return lock_or_result
        project_lock = lock_or_result
        try:
            result = self.github_repos.clone(plan)
        except (ValueError, RuntimeError) as exc:
            return CommandResult(f"GitHub 仓库下载失败: {exc}")
        finally:
            project_lock.release()

        self._refresh_repo_state(project_id=active.project_id, project=active.project)
        self.audit.log(
            action=audit_events.SYSTEM_GITHUB_CLONED,
            message=f"下载 GitHub 仓库: {plan.owner}/{plan.repo}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={
                "clone_url": plan.clone_url,
                "target_dir": str(plan.target_dir),
            },
        )
        try:
            relative_target = str(plan.target_dir.relative_to(active.project.path.resolve()))
        except ValueError:
            relative_target = str(plan.target_dir)
        return CommandResult(
            f"已下载 GitHub 仓库: {plan.owner}/{plan.repo}\n"
            f"目标目录: {relative_target}\n"
            f"完整路径: {plan.target_dir}"
        )

    def _handle_version(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用版本管理。")
        info = self.update_service.get_current_version()
        self.audit.log(
            action=audit_events.SYSTEM_VERSION_VIEWED,
            message="查看当前版本",
            user_id=user.id,
        )
        return CommandResult(
            format_version_info(branch=info.branch, version=info.version, commit=info.commit)
        )

    def _handle_update_check(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用自更新。")
        try:
            checked = self.update_service.check_for_updates()
        except RuntimeError as exc:
            return CommandResult(f"更新检查失败: {exc}")
        self.audit.log(
            action=audit_events.SYSTEM_UPDATE_CHECKED,
            message="检查更新",
            user_id=user.id,
            details={"behind_count": checked.behind_count, "ahead_count": checked.ahead_count},
        )
        current = checked.current
        if current is None or checked.upstream_ref is None or checked.upstream_commit is None:
            return CommandResult(checked.summary)
        return CommandResult(
            format_update_check(
                branch=current.branch,
                current_version=current.version,
                current_commit=current.commit,
                upstream_ref=checked.upstream_ref,
                upstream_commit=checked.upstream_commit,
                behind_count=checked.behind_count,
                ahead_count=checked.ahead_count,
                commits=checked.commits,
            )
        )

    def _handle_update(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用自更新。")
        try:
            triggered = self.update_service.trigger_update()
        except RuntimeError as exc:
            return CommandResult(f"启动更新失败: {exc}")
        self.tasks.queue_system_notification(
            chat_id=ctx.telegram_chat_id,
            kind="update_completed",
        )
        self.audit.log(
            action=audit_events.SYSTEM_UPDATE_TRIGGERED,
            message="触发自更新",
            user_id=user.id,
        )
        return CommandResult(
            "已开始自更新。\n"
            "OpenFish 会拉取 GitHub 最新代码、刷新依赖，并在需要时自动重启。\n"
            f"{triggered.summary}"
        )

    def _handle_restart(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用服务控制。")
        try:
            triggered = self.update_service.trigger_restart()
        except RuntimeError as exc:
            return CommandResult(f"启动重启失败: {exc}")
        self.tasks.queue_system_notification(
            chat_id=ctx.telegram_chat_id,
            kind="restart_completed",
        )
        self.audit.log(
            action=audit_events.SYSTEM_RESTART_TRIGGERED,
            message="触发服务重启",
            user_id=user.id,
        )
        return CommandResult(
            "已开始重启服务。\n"
            "OpenFish 会在几秒内重启，随后恢复 Telegram 响应。\n"
            f"{triggered.summary}"
        )

    def _handle_logs(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用日志查看。")
        try:
            logs = self.update_service.read_logs()
        except RuntimeError as exc:
            return CommandResult(f"读取日志失败: {exc}")
        self.audit.log(
            action=audit_events.SYSTEM_LOGS_VIEWED,
            message="查看运行日志",
            user_id=user.id,
        )
        return CommandResult(logs.text)

    def _handle_logs_clear(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用日志控制。")
        try:
            logs = self.update_service.clear_logs()
        except RuntimeError as exc:
            return CommandResult(f"清空日志失败: {exc}")
        self.audit.log(
            action=audit_events.SYSTEM_LOGS_CLEARED,
            message="清空运行日志",
            user_id=user.id,
        )
        return CommandResult(logs.text)

    def _handle_projects(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        active_project = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        recent_projects = self.tasks.list_recent_project_keys(user_id=user.id)
        mode = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        return CommandResult(
            format_projects(
                self.projects.list_keys(),
                active_project_key=active_project,
                recent_project_keys=recent_projects,
                mode=mode,
            ),
            metadata={"recent_projects": recent_projects},
        )

    def _handle_upload_policy(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        self.audit.log(
            action=audit_events.UPLOAD_POLICY_VIEWED,
            message="用户查看上传策略",
            user_id=user.id,
        )
        return CommandResult(
            format_upload_policy(
                enabled=self.config.enable_document_upload,
                max_size_bytes=self.config.max_upload_size_bytes,
                allowed_extensions=sorted(self.config.allowed_upload_extensions),
            )
        )

    def _handle_project_add(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        parsed = self._parse_project_add_argument(argument)
        if parsed is None:
            return CommandResult(
                "用法: /project-add <key> [abs_path] [name]\n也可以直接按引导逐步填写。",
                metadata={"wizard": "project_add"},
            )
        key, path, name = parsed

        if not PROJECT_ADD_KEY_PATTERN.match(key):
            return CommandResult("项目 key 非法。只允许字母数字/._-，长度 1-64。")
        existing = self.projects.get_any(key)
        if existing is not None:
            if existing.is_active:
                return CommandResult(f"项目已存在: {key}")

            requested_path: Path | None = None
            if path is not None:
                if not path.is_absolute():
                    return CommandResult("项目路径必须是绝对路径。")
                requested_path = path.expanduser().resolve()
            elif self._get_default_project_root() is not None:
                requested_path = (self._get_default_project_root() / key).expanduser().resolve()

            ok = self.projects.set_project_active(key=key, is_active=True)
            if not ok:
                return CommandResult(f"项目不存在: {key}")

            self.tasks.sync_projects_from_registry(self.projects)
            self.tasks.set_active_project(user.id, key, ctx.telegram_chat_id)
            project_id = self.tasks.get_project_id(key)
            self.audit.log(
                action=audit_events.PROJECT_ADDED,
                message=f"重新启用项目: {key}",
                user_id=user.id,
                project_id=project_id,
                details={"requested_path": str(requested_path) if requested_path else None},
            )
            project = self.projects.get_any(key)
            if project is not None:
                self._refresh_repo_state(project_id=project_id, project=project)
                path_hint = ""
                if requested_path and requested_path != project.path:
                    path_hint = f"\n提示: 已沿用原项目路径 {project.path}。"
                return CommandResult(
                    "项目已重新启用并切换。\n"
                    f"项目: {key}\n"
                    f"路径: {project.path}"
                    f"{path_hint}\n"
                    "可用 /status 查看状态。"
                )
            return CommandResult(f"项目已重新启用并切换: {key}")

        resolved_path_or_error = self._resolve_project_add_path(key=key, path=path)
        if isinstance(resolved_path_or_error, CommandResult):
            return resolved_path_or_error
        resolved_path, used_default_root = resolved_path_or_error

        try:
            self.projects.add_project(
                key=key,
                path=resolved_path,
                name=name,
                create_if_missing=True,
            )
        except ValueError as exc:
            return CommandResult(str(exc))

        self.tasks.sync_projects_from_registry(self.projects)
        self.tasks.set_active_project(user.id, key, ctx.telegram_chat_id)
        project_id = self.tasks.get_project_id(key)
        self.audit.log(
            action=audit_events.PROJECT_ADDED,
            message=f"新增项目: {key}",
            user_id=user.id,
            project_id=project_id,
            details={"path": str(resolved_path), "name": name},
        )
        project = self.projects.get_any(key)
        if project is not None:
            self._refresh_repo_state(
                project_id=project_id,
                project=project,
            )
        return CommandResult(
            "项目已新增并切换。\n"
            f"项目: {key}\n"
            f"路径: {resolved_path}\n"
            f"目录来源: {'默认根目录' if used_default_root else '指定目录'}\n"
            "可用 /status 查看状态。"
        )

    def _handle_project_root(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        text = argument.strip()
        if not text:
            current = self._get_default_project_root()
            if current is None:
                return CommandResult(
                    "当前未设置默认项目根目录。\n"
                    "请使用 /project-root <abs_path> 设置后，再用 /project-add <key> 快速创建项目。"
                )
            return CommandResult(f"默认项目根目录: {current}")

        path = Path(text).expanduser()
        if not path.is_absolute():
            return CommandResult("默认项目根目录必须是绝对路径。")
        try:
            resolved = self.projects.set_default_project_root(path)
        except ValueError as exc:
            return CommandResult(str(exc))

        self.audit.log(
            action=audit_events.PROJECT_ROOT_UPDATED,
            message="更新默认项目根目录",
            user_id=user.id,
            details={"path": str(resolved)},
        )
        return CommandResult(
            f"默认项目根目录已设置: {resolved}\n"
            "后续可用 /project-add <key> [name] 自动创建目录并新增项目。"
        )

    def _handle_project_disable(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        project_key = argument.strip() if argument.strip() else self.tasks.get_active_project_key(
            user.id, ctx.telegram_chat_id
        )
        if not project_key:
            return CommandResult("用法: /project-disable <key>")

        exists = self.projects.get_any(project_key)
        if exists is None:
            return CommandResult(f"项目不存在: {project_key}")
        ok = self.projects.set_project_active(key=project_key, is_active=False)
        if not ok:
            return CommandResult(f"项目不存在: {project_key}")

        self.tasks.sync_projects_from_registry(self.projects)
        current_active = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if current_active == project_key:
            self.tasks.clear_active_project(user.id, ctx.telegram_chat_id)
        project_id = self.tasks.get_project_id(project_key)
        self.tasks.clear_project_session_state(project_id=project_id)
        self.audit.log(
            action=audit_events.PROJECT_DISABLED,
            message=f"停用项目: {project_key}",
            user_id=user.id,
            project_id=project_id,
        )
        return CommandResult(f"项目已停用: {project_key}\n可用 /projects 查看可选项目。")

    def _handle_project_archive(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        project_key = argument.strip() if argument.strip() else self.tasks.get_active_project_key(
            user.id, ctx.telegram_chat_id
        )
        if not project_key:
            return CommandResult("用法: /project-archive <key>")

        exists = self.projects.get_any(project_key)
        if exists is None:
            return CommandResult(f"项目不存在: {project_key}")
        ok = self.projects.archive_project(key=project_key)
        if not ok:
            return CommandResult(f"项目不存在: {project_key}")

        self.tasks.sync_projects_from_registry(self.projects)
        current_active = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if current_active == project_key:
            self.tasks.clear_active_project(user.id, ctx.telegram_chat_id)
        project_id = self.tasks.get_project_id(project_key)
        self.tasks.clear_project_session_state(project_id=project_id)
        self.audit.log(
            action=audit_events.PROJECT_ARCHIVED,
            message=f"归档项目: {project_key}",
            user_id=user.id,
            project_id=project_id,
        )
        return CommandResult(f"项目已归档并停用: {project_key}\n可用 /projects 查看可选项目。")

    def _handle_plain_text(self, ctx: CommandContext, text: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return CommandResult(
                "未识别为命令。请先切换项目，然后直接发问题或任务。",
                metadata=active.metadata,
            )
        return self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type="ask",
            request_text=text,
            run_mode="ask",
            next_step="如需改代码，请执行 /do。",
        )

    def prepare_document_upload(
        self,
        ctx: CommandContext,
        *,
        original_name: str,
        size_bytes: int,
    ) -> DocumentUploadPlan | CommandResult:
        """Validate an incoming Telegram document and return safe local destination."""

        if not self.config.enable_document_upload:
            return CommandResult("当前未启用文件上传分析。")

        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        def reject(reason: str) -> CommandResult:
            self.audit.log(
                action=audit_events.UPLOAD_REJECTED,
                message=f"上传文件被拒绝: {reason}",
                user_id=active.user.id,
                project_id=active.project_id,
                details={"file_name": original_name, "size_bytes": size_bytes},
            )
            return CommandResult(format_upload_rejected(reason))

        if size_bytes <= 0:
            return reject("无法识别文件大小。")
        if size_bytes > self.config.max_upload_size_bytes:
            return reject(
                f"文件过大（{size_bytes} bytes），超过限制 {self.config.max_upload_size_bytes} bytes。"
            )

        source_name = original_name.strip() or "upload.bin"
        if is_sensitive_file_name(source_name):
            return reject("命中敏感文件命名规则，已拒绝上传。")
        extension = Path(source_name).suffix.lower().lstrip(".")
        if self.config.allowed_upload_extensions:
            if not extension:
                return reject("文件缺少扩展名，已拒绝。请使用受支持后缀名。")
            if extension not in self.config.allowed_upload_extensions:
                return reject(
                    f"不支持扩展名 .{extension}。允许: {', '.join(sorted(self.config.allowed_upload_extensions))}"
                )

        safe_basename = Path(source_name).name.replace("/", "_").replace("\\", "_")
        unique_name = f"{uuid4().hex[:8]}_{safe_basename}"
        upload_dir = (active.project.path / self.config.upload_temp_dir_name).resolve()
        target_path = (upload_dir / unique_name).resolve()

        if not self.projects.is_path_allowed(active.project, upload_dir):
            return reject(f"上传目录不在项目允许范围内: {upload_dir}")
        if not self.projects.is_path_allowed(active.project, target_path):
            return reject(f"目标文件路径不在项目允许范围内: {target_path}")
        try:
            target_path.relative_to(active.project.path.resolve())
        except ValueError:
            return reject("上传文件必须保存到当前项目目录下。")
        if has_symlink_in_path(active.project.path, target_path):
            return reject("上传路径存在符号链接，已拒绝。")

        upload_dir.mkdir(parents=True, exist_ok=True)
        return DocumentUploadPlan(
            active=active,
            original_name=source_name,
            safe_name=unique_name,
            size_bytes=size_bytes,
            local_path=target_path,
        )

    def handle_uploaded_document(
        self,
        *,
        ctx: CommandContext,
        plan: DocumentUploadPlan,
        caption: str | None,
    ) -> CommandResult:
        """Run Codex analysis for an uploaded file in active project context."""
        lock_or_result = self._try_acquire_project_lock(active=plan.active, operation="upload")
        if isinstance(lock_or_result, CommandResult):
            return lock_or_result
        project_lock = lock_or_result

        try:
            project_root = plan.active.project.path.resolve()
            try:
                relative_path = str(plan.local_path.resolve().relative_to(project_root))
            except ValueError:
                relative_path = plan.local_path.name
            task_instruction = (
                "请在只读模式下分析用户上传文件，禁止修改任何文件。\n"
                f"文件路径: {relative_path}\n"
                f"原始文件名: {plan.original_name}\n"
                f"文件大小: {plan.size_bytes} bytes\n"
            )
            if caption:
                task_instruction += f"用户备注: {caption}\n"
            task_instruction += (
                "请输出：1) 文件用途 2) 关键风险/问题 3) 建议下一步（简短）。"
            )

            task_id = self.tasks.create_task(
                user_id=plan.active.user.id,
                project_id=plan.active.project_id,
                chat_id=ctx.telegram_chat_id,
                message_id=ctx.telegram_message_id,
                command_type="upload",
                original_request=task_instruction,
            )
            self.audit.log(
                action=audit_events.UPLOAD_RECEIVED,
                message=f"收到上传文件: {plan.original_name}",
                user_id=plan.active.user.id,
                project_id=plan.active.project_id,
                task_id=task_id,
                details={
                    "local_path": str(plan.local_path),
                    "size_bytes": plan.size_bytes,
                    "caption": caption[:200] if caption else None,
                },
            )
            self.tasks.mark_task_running(task_id)
            self.tasks.add_task_artifact(
                task_id,
                "uploaded_file",
                metadata={
                    "local_path": str(plan.local_path),
                    "original_name": plan.original_name,
                    "size_bytes": plan.size_bytes,
                },
            )

            codex_result = self.codex.ask(
                plan.active.project,
                task_instruction,
                model=self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id),
                progress_callback=ctx.progress_callback,
            )
            return self._finalize_task_execution(
                active=plan.active,
                task_id=task_id,
                run_mode="upload",
                codex_result=codex_result,
                next_step="可用 /do 对该文件执行后续任务，或继续上传其它文件。",
            )
        finally:
            project_lock.release()

    def _handle_skills(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.skills_service is None:
            return CommandResult("当前未启用 skills 功能。")

        listed = self.skills_service.list_skills()
        self.audit.log(
            action=audit_events.SKILLS_VIEWED,
            message="用户查看已安装 skills",
            user_id=user.id,
            details={
                "skills_root": str(listed.skills_root),
                "visible_count": listed.total_count,
                "hidden_count": listed.hidden_count,
            },
        )
        return CommandResult(
            format_skills_list(
                skills_root=str(listed.skills_root),
                skills=listed.skills,
                total_count=listed.total_count,
                hidden_count=listed.hidden_count,
                omitted_count=listed.omitted_count,
            )
        )

    def _handle_skill_install(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.skills_service is None:
            return CommandResult("当前未启用 skills 功能。")
        if not argument:
            return CommandResult("用法: /skill-install <source>")

        source = argument.strip()
        self.audit.log(
            action=audit_events.SKILL_INSTALL_REQUESTED,
            message=f"请求安装 skill: {source}",
            user_id=user.id,
            details={"source": source[:200]},
        )
        result = self.skills_service.install_skill(source)
        self.audit.log(
            action=audit_events.SKILL_INSTALLED if result.ok else audit_events.SKILL_INSTALL_FAILED,
            message=f"skill 安装结果: {'成功' if result.ok else '失败'}",
            severity="info" if result.ok else "warning",
            user_id=user.id,
            details={
                "source": result.source[:200],
                "summary": result.summary[:250],
                "command": result.command,
            },
        )
        return CommandResult(
            format_skill_install_result(
                source=result.source,
                ok=result.ok,
                summary=result.summary,
                command=result.command,
            )
        )

    def _handle_mcp(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.mcp_service is None:
            return CommandResult("当前未启用 MCP 查看功能。")

        name = argument.strip()
        if not name:
            result = self.mcp_service.list_servers()
            self.audit.log(
                action=audit_events.MCP_VIEWED,
                message="用户查看 MCP 列表",
                user_id=user.id,
                severity="info" if result.ok else "warning",
                details={"ok": result.ok, "count": len(result.servers)},
            )
            if not result.ok:
                return CommandResult(f"MCP 列表读取失败：{result.summary}")

            items = [
                (item.name, item.enabled, item.transport_type, item.target, item.auth_status)
                for item in result.servers
            ]
            return CommandResult(format_mcp_list(items), metadata={"mcp_panel": "list"})

        result = self.mcp_service.get_server(name)
        self.audit.log(
            action=audit_events.MCP_VIEWED,
            message=f"用户查看 MCP 详情: {name}",
            user_id=user.id,
            severity="info" if result.ok else "warning",
            details={"ok": result.ok, "name": name[:128]},
        )
        if not result.ok or result.detail is None:
            return CommandResult(f"MCP 详情读取失败：{result.summary}")

        detail = result.detail
        return CommandResult(
            format_mcp_detail(
                name=detail.name,
                enabled=detail.enabled,
                disabled_reason=detail.disabled_reason,
                transport_type=detail.transport_type,
                url=detail.url,
                command=detail.command,
                args=detail.args,
                cwd=detail.cwd,
                bearer_token_env_var=detail.bearer_token_env_var,
                auth_status=detail.auth_status,
                startup_timeout_sec=detail.startup_timeout_sec,
                tool_timeout_sec=detail.tool_timeout_sec,
                enabled_tools=detail.enabled_tools,
                disabled_tools=detail.disabled_tools,
            ),
            metadata={"mcp_name": detail.name, "mcp_enabled": detail.enabled},
        )

    def _handle_mcp_toggle(self, ctx: CommandContext, argument: str, *, enabled: bool) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.mcp_service is None:
            return CommandResult("当前未启用 MCP 管理功能。")
        name = argument.strip()
        if not name:
            return CommandResult(f"用法: /mcp-{'enable' if enabled else 'disable'} <name>")
        result = self.mcp_service.set_server_enabled(name, enabled=enabled)
        self.audit.log(
            action=audit_events.MCP_UPDATED,
            message=f"{'启用' if enabled else '停用'} MCP: {name}",
            user_id=user.id,
            severity="info" if result.ok else "warning",
            details={"ok": result.ok, "name": name[:128], "enabled": enabled},
        )
        if not result.ok:
            return CommandResult(f"MCP 配置更新失败：{result.summary}")
        suffix = f"\n配置文件: {result.config_path}" if result.config_path else ""
        return CommandResult(
            f"{result.summary}{suffix}",
            metadata={"mcp_name": result.name, "mcp_enabled": result.enabled},
        )

    def _handle_sessions(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.codex_sessions is None:
            return CommandResult("当前未启用会话查询。")
        page_arg = argument.strip()
        if not page_arg:
            page = 1
        else:
            try:
                page = int(page_arg)
            except ValueError:
                return CommandResult("用法: /sessions [page]，page 必须是正整数。")
            if page < 1:
                return CommandResult("用法: /sessions [page]，page 必须是正整数。")
        result = self.codex_sessions.list_sessions(page=page, page_size=10)
        self.audit.log(
            action=audit_events.SESSIONS_VIEWED,
            message="查看 Codex 会话列表",
            user_id=user.id,
            details={"page": result.page, "total_count": result.total_count},
        )
        return CommandResult(
            format_sessions_list(result),
            metadata={
                "sessions_page": result.page,
                "sessions_total_pages": result.total_pages,
                "sessions_items": result.sessions,
            },
        )

    def _handle_session(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.codex_sessions is None:
            return CommandResult("当前未启用会话查询。")
        session_id = argument.strip()
        if not session_id:
            return CommandResult("用法: /session <id>")
        record = self.codex_sessions.get_session(session_id)
        if record is None:
            return CommandResult(f"未找到会话: {session_id}")
        self.audit.log(
            action=audit_events.SESSION_VIEWED,
            message=f"查看会话 {record.session_id}",
            user_id=user.id,
            details={"session_id": record.session_id, "source": record.source},
        )
        return CommandResult(format_session_detail(record), metadata={"session_record": record})

    def _handle_session_import(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.codex_sessions is None:
            return CommandResult("当前未启用会话导入。")
        session_id, project_key, project_name = self._parse_session_import_argument(argument)
        if not session_id:
            return CommandResult("用法: /session-import <id> [project_key] [name]")
        record = self.codex_sessions.get_session(session_id)
        if record is None:
            return CommandResult(f"未找到会话: {session_id}")

        if record.source == "openfish" and record.project_key:
            project = self.projects.get_any(record.project_key)
            if project is None:
                return CommandResult(f"会话关联项目不存在: {record.project_key}")
            if not project.is_active:
                self.projects.set_project_active(key=record.project_key, is_active=True)
                self.tasks.sync_projects_from_registry(self.projects)
            self.tasks.set_active_project(user.id, record.project_key, ctx.telegram_chat_id)
            project_id = self.tasks.get_project_id(record.project_key)
            self.tasks.bind_project_session(
                project_id=project_id,
                codex_session_id=record.session_id,
                next_step="后续 /ask 或 /do 将继续该会话。",
            )
            self.audit.log(
                action=audit_events.SESSION_IMPORTED,
                message=f"绑定 OpenFish 会话 {record.session_id}",
                user_id=user.id,
                project_id=project_id,
                details={"session_id": record.session_id, "project_key": record.project_key, "source": record.source},
            )
            return CommandResult(
                f"已切换到项目: {record.project_key}\n"
                f"已绑定会话: {record.session_id}\n"
                "后续 /ask 或 /do 将继续这个会话。"
            )

        session_cwd = Path(record.cwd or "").expanduser()
        if not record.cwd or not session_cwd.exists() or not session_cwd.is_dir():
            return CommandResult("该本机会话没有可用的工作目录，无法导入为项目。")

        existing_key = self._find_project_key_by_path(session_cwd.resolve())
        chosen_key = project_key or existing_key or self._derive_project_key(session_cwd)
        chosen_name = project_name or session_cwd.name or chosen_key

        if existing_key is not None:
            chosen_key = existing_key
            project = self.projects.get_any(chosen_key)
            if project is not None and not project.is_active:
                self.projects.set_project_active(key=chosen_key, is_active=True)
                self.tasks.sync_projects_from_registry(self.projects)
        else:
            existing = self.projects.get_any(chosen_key)
            if existing is not None:
                return CommandResult(f"项目 key 已存在且路径不同: {chosen_key}")
            self.projects.add_project(
                key=chosen_key,
                path=session_cwd.resolve(),
                name=chosen_name,
                create_if_missing=False,
            )
            self.tasks.sync_projects_from_registry(self.projects)

        self.tasks.set_active_project(user.id, chosen_key, ctx.telegram_chat_id)
        project_id = self.tasks.get_project_id(chosen_key)
        self.tasks.bind_project_session(
            project_id=project_id,
            codex_session_id=record.session_id,
            next_step="后续 /ask 或 /do 将继续该会话。",
        )
        project = self.projects.get_any(chosen_key)
        if project is not None:
            self._refresh_repo_state(project_id=project_id, project=project)
        self.audit.log(
            action=audit_events.SESSION_IMPORTED,
            message=f"导入本机会话 {record.session_id}",
            user_id=user.id,
            project_id=project_id,
            details={"session_id": record.session_id, "project_key": chosen_key, "source": record.source},
        )
        return CommandResult(
            f"已导入本机会话并切换项目。\n"
            f"项目: {chosen_key}\n"
            f"路径: {session_cwd.resolve()}\n"
            f"会话: {record.session_id}\n"
            "后续 /ask 或 /do 将继续这个会话。"
        )

    def _handle_schedule_add(self, ctx: CommandContext, argument: str) -> CommandResult:
        parsed = self._parse_schedule_add_argument(argument)
        if parsed is None:
            return CommandResult(
                "用法: /schedule-add <HH:MM> <ask|do> <text>\n也可以直接按引导逐步填写。",
                metadata={"wizard": "schedule_add"},
            )

        hhmm, minute_of_day, command_type, request_text = parsed
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        schedule_id = self.tasks.create_scheduled_task(
            user_id=active.user.id,
            project_id=active.project_id,
            chat_id=ctx.telegram_chat_id,
            command_type=command_type,
            request_text=request_text,
            minute_of_day=minute_of_day,
        )
        self.audit.log(
            action=audit_events.SCHEDULE_CREATED,
            message=f"创建定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={
                "schedule_id": schedule_id,
                "hhmm": hhmm,
                "command_type": command_type,
                "request_preview": request_text[:200],
            },
        )
        return CommandResult(
            format_schedule_added(
                schedule_id=schedule_id,
                hhmm=hhmm,
                command_type=command_type,
                request_text=request_text,
            )
        )

    def _handle_schedule_list(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        schedules = self.tasks.list_scheduled_tasks(active.project_id)
        self.audit.log(
            action=audit_events.SCHEDULE_VIEWED,
            message="查看定期任务列表",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"count": len(schedules)},
        )
        items = [
            (
                item.id,
                self._minute_to_hhmm(item.minute_of_day),
                item.enabled,
                item.command_type,
                item.request_text,
                item.last_run_status,
            )
            for item in schedules
        ]
        return CommandResult(format_schedule_list(items))

    def _handle_schedule_run(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        schedule_id = self._parse_schedule_id(argument)
        if schedule_id is None:
            return CommandResult("用法: /schedule-run <id>")
        schedule = self.tasks.get_scheduled_task(
            schedule_id=schedule_id,
            project_id=active.project_id,
        )
        if schedule is None:
            return CommandResult(f"未找到定期任务 #{schedule_id}。")

        self.audit.log(
            action=audit_events.SCHEDULE_MANUAL_RUN,
            message=f"手动触发定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"schedule_id": schedule_id},
        )
        result = self.run_scheduled_task(schedule)
        metadata = result.metadata or {}
        task_id_raw = metadata.get("task_id")
        task_id = task_id_raw if isinstance(task_id_raw, int) else None
        status = str(metadata.get("status") or "unknown")
        self.tasks.record_scheduled_task_run(
            schedule_id=schedule.id,
            task_id=task_id,
            status=status,
            summary=result.reply_text,
        )
        return CommandResult(format_schedule_run_result(schedule.id, result.reply_text))

    def _handle_schedule_toggle(
        self,
        ctx: CommandContext,
        argument: str,
        *,
        enabled: bool,
    ) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        schedule_id = self._parse_schedule_id(argument)
        if schedule_id is None:
            return CommandResult(f"用法: /schedule-{'enable' if enabled else 'pause'} <id>")
        updated = self.tasks.set_scheduled_task_enabled(
            schedule_id=schedule_id,
            project_id=active.project_id,
            enabled=enabled,
        )
        if not updated:
            return CommandResult(f"未找到定期任务 #{schedule_id}。")
        self.audit.log(
            action=audit_events.SCHEDULE_TOGGLED,
            message=f"{'启用' if enabled else '暂停'}定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"schedule_id": schedule_id, "enabled": enabled},
        )
        return CommandResult(format_schedule_toggled(schedule_id, enabled=enabled))

    def _handle_schedule_delete(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if not argument:
            return CommandResult("用法: /schedule-del <id>")
        try:
            schedule_id = int(argument.strip())
        except ValueError:
            return CommandResult("任务 id 必须是数字。")

        deleted = self.tasks.delete_scheduled_task(
            schedule_id=schedule_id,
            project_id=active.project_id,
        )
        if not deleted:
            return CommandResult(f"未找到定期任务 #{schedule_id}。")
        self.audit.log(
            action=audit_events.SCHEDULE_DELETED,
            message=f"删除定期任务 #{schedule_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"schedule_id": schedule_id},
        )
        return CommandResult(format_schedule_deleted(schedule_id))

    def _handle_use(self, ctx: CommandContext, project_key: str) -> CommandResult:
        if not project_key:
            user = self.tasks.ensure_user(ctx)
            return CommandResult(
                "用法: /use <project>\n可直接点“项目”选择最近项目。",
                metadata={"recent_projects": self.tasks.list_recent_project_keys(user_id=user.id)},
            )

        project = self.projects.get(project_key)
        if project is None:
            known = ", ".join(self.projects.list_keys()) or "无"
            return CommandResult(f"未知项目: {project_key}\n可用项目: {known}")

        user = self.tasks.ensure_user(ctx)
        self.tasks.set_active_project(user.id, project_key, ctx.telegram_chat_id)
        project_id = self.tasks.get_project_id(project_key)
        self.audit.log(
            action=audit_events.PROJECT_SELECTED,
            message=f"已切换项目: {project_key}",
            user_id=user.id,
            project_id=project_id,
            details={"project_key": project_key},
        )
        self._refresh_repo_state(project_id=project_id, project=project)
        return CommandResult(
            format_use_confirmation(
                project_name=project.name,
                project_path=str(project.path),
                default_branch=project.default_branch,
                test_command=project.test_command,
            )
        )

    def _handle_status(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        project_key = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if project_key:
            project = self.projects.get(project_key)
            if project is not None:
                project_id = self.tasks.get_project_id(project_key)
                self._refresh_repo_state(project_id=project_id, project=project)

        snapshot = self.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
        mode = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        return CommandResult(
            redact_text(format_status(snapshot, mode=mode)),
            metadata={"recent_projects": self.tasks.list_recent_project_keys(user_id=user.id)},
        )

    def _handle_task_current(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        task = self.tasks.get_latest_active_task(active.project_id) or self.tasks.get_latest_task(active.project_id)
        self.audit.log(
            action=audit_events.TASK_LAST_VIEWED,
            message="查看当前任务卡片",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=task.id if task else None,
        )
        return CommandResult(
            redact_text(format_current_task(project_key=active.project_key, task=task)),
            metadata={"current_task": task},
        )

    def _handle_last(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        latest = self.tasks.get_latest_task(active.project_id)
        self.audit.log(
            action=audit_events.TASK_LAST_VIEWED,
            message="已查看最近任务",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=latest.id if latest else None,
        )
        return CommandResult(redact_text(format_last_task(project_key=active.project_key, task=latest)))

    def _handle_retry(self, ctx: CommandContext, extra_note: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        latest = self.tasks.get_latest_task(active.project_id)
        if latest is None:
            return CommandResult("没有可重试任务。请先执行 /ask 或 /do。")
        if latest.status in {"created", "running", "waiting_approval"}:
            return CommandResult("最近任务仍在进行中，请先用 /status 查看，或等待其结束。")
        if latest.command_type not in {"ask", "do"}:
            return CommandResult(
                f"最近任务类型为 /{latest.command_type}，当前仅支持重试 /ask 或 /do。"
            )

        request_text = latest.original_request
        if extra_note:
            request_text = f"{request_text}\n\n补充说明: {extra_note}"

        self.audit.log(
            action=audit_events.TASK_RETRIED,
            message=f"重试任务 #{latest.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=latest.id,
            details={"command_type": latest.command_type},
        )
        run_mode = "ask" if latest.command_type == "ask" else "do"
        next_step = "如需改代码，请执行 /do。" if run_mode == "ask" else "可用 /diff 查看变更，或 /resume 继续任务。"
        return self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type=latest.command_type,
            request_text=request_text,
            run_mode=run_mode,
            next_step=next_step,
        )

    def _handle_do(self, ctx: CommandContext, task_text: str) -> CommandResult:
        if not task_text:
            return CommandResult("用法: /do <task>")
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        return self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type="do",
            request_text=task_text,
            run_mode="do",
            next_step="可用 /diff 查看变更，或 /resume 继续任务。",
        )

    def _handle_ask(self, ctx: CommandContext, question: str) -> CommandResult:
        if not question:
            return CommandResult("用法: /ask <question>")
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        return self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type="ask",
            request_text=question,
            run_mode="ask",
            next_step="如需改代码，请执行 /do。",
        )

    def _handle_resume(self, ctx: CommandContext, instruction: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        task_id_arg, resume_note = self._parse_resume_argument(instruction)
        if task_id_arg is not None:
            resumable = self.tasks.get_task_for_project(
                task_id=task_id_arg,
                project_id=active.project_id,
            )
            if resumable is None:
                return CommandResult(f"任务 #{task_id_arg} 不存在或不属于当前项目。")
        else:
            resumable = self.tasks.get_latest_resumable_task(active.project_id)
        if resumable is None:
            return CommandResult("没有可恢复任务。请先运行 /do 或 /ask。")
        if resumable.status in {"cancelled", "rejected"}:
            return CommandResult(f"任务 #{resumable.id} 当前状态为 {resumable.status}，不可恢复。")

        default_instruction = (
            f"继续任务 #{resumable.id}，并用简洁摘要说明剩余阻塞。"
            if task_id_arg is not None
            else "继续上一个任务，并用简洁摘要说明剩余阻塞。"
        )
        resume_instruction = resume_note or default_instruction
        return self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type="resume",
            request_text=resume_instruction,
            run_mode="resume",
            resume_session_id=resumable.codex_session_id,
            next_step="可用 /status 查看最新状态。",
        )

    def _handle_approve(self, ctx: CommandContext, note: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        lock_or_result = self._try_acquire_project_lock(active=active, operation="approve")
        if isinstance(lock_or_result, CommandResult):
            return lock_or_result
        project_lock = lock_or_result
        active_execution_task_id: int | None = None
        try:
            approval_id_arg, approval_note = self._parse_approval_argument(note)
            pending = self.tasks.get_pending_approval(active.project_id, approval_id=approval_id_arg)
            if pending is None:
                if approval_id_arg is not None:
                    return CommandResult(f"审批 #{approval_id_arg} 不存在、已处理或不属于当前项目。")
                return CommandResult("当前没有待审批任务。")

            resolved = self.tasks.resolve_approval(
                approval_id=pending.approval_id,
                status="approved",
                decided_by_user_id=active.user.id,
                decision_note=approval_note or None,
            )
            if not resolved:
                return CommandResult(f"审批 #{pending.approval_id} 已处理或已过期。")

            self.tasks.mark_task_resumed_after_approval(pending.task_id)
            self.audit.log(
                action=audit_events.APPROVAL_GRANTED,
                message=f"已批准任务 #{pending.task_id}",
                user_id=active.user.id,
                project_id=active.project_id,
                task_id=pending.task_id,
                details={"note": approval_note[:200] if approval_note else None},
            )

            resume_instruction = self.approvals.build_resume_instruction(
                pending.requested_action,
                user_note=approval_note or None,
            )
            self._register_active_task_execution(task_id=pending.task_id, project_id=active.project_id)
            active_execution_task_id = pending.task_id
            codex_result = self.codex.resume_last(
                active.project,
                resume_instruction,
                progress_callback=ctx.progress_callback,
                process_callback=lambda proc, tid=pending.task_id: self._set_active_task_process(tid, proc),
            )
            if self._is_cancel_requested(pending.task_id):
                cancelled = self.tasks.cancel_task(task_id=pending.task_id, project_id=active.project_id)
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
            reassessment = self.approvals.assess(codex_result)
            if reassessment.requires_approval:
                return self._handle_waiting_approval(
                    active=active,
                    task_id=pending.task_id,
                    codex_result=codex_result,
                    reason=reassessment.reason or "继续执行前仍需审批。",
                )

            return self._finalize_task_execution(
                active=active,
                task_id=pending.task_id,
                run_mode="approve_resume",
                codex_result=codex_result,
                next_step="可用 /status 查看当前状态。",
            )
        finally:
            if active_execution_task_id is not None:
                self._clear_active_task_execution(active_execution_task_id)
            project_lock.release()

    def _handle_reject(self, ctx: CommandContext, reason: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        lock_or_result = self._try_acquire_project_lock(active=active, operation="reject")
        if isinstance(lock_or_result, CommandResult):
            return lock_or_result
        project_lock = lock_or_result
        try:
            approval_id_arg, reject_note = self._parse_approval_argument(reason)
            pending = self.tasks.get_pending_approval(active.project_id, approval_id=approval_id_arg)
            if pending is None:
                if approval_id_arg is not None:
                    return CommandResult(f"审批 #{approval_id_arg} 不存在、已处理或不属于当前项目。")
                return CommandResult("当前没有待审批任务。")

            reject_reason = redact_text(reject_note) if reject_note else "用户拒绝"
            resolved = self.tasks.resolve_approval(
                approval_id=pending.approval_id,
                status="rejected",
                decided_by_user_id=active.user.id,
                decision_note=reject_reason,
            )
            if not resolved:
                return CommandResult(f"审批 #{pending.approval_id} 已处理或已过期。")

            reject_summary = f"任务已拒绝: {reject_reason}"
            self.tasks.reject_task(task_id=pending.task_id, summary=reject_summary)
            self.tasks.update_project_state_after_task(
                project_id=active.project_id,
                task_id=pending.task_id,
                summary=reject_summary,
                codex_session_id=pending.codex_session_id,
                pending_approval_task_id=None,
                next_step="准备好后可执行 /do 新建任务。",
            )
            self.audit.log(
                action=audit_events.APPROVAL_REJECTED,
                message=f"已拒绝任务 #{pending.task_id}",
                user_id=active.user.id,
                project_id=active.project_id,
                task_id=pending.task_id,
                details={"reason": reject_reason[:200]},
            )
            return CommandResult(f"已拒绝任务 #{pending.task_id}。原因: {reject_reason}")
        finally:
            project_lock.release()

    def _handle_note(self, ctx: CommandContext, note_text: str) -> CommandResult:
        if not note_text:
            return CommandResult("用法: /note <text>")
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        title = note_text[:40]
        self.tasks.add_project_note(project_id=active.project_id, content=note_text, title=title)
        self.audit.log(
            action=audit_events.NOTE_ADDED,
            message="已添加项目笔记",
            user_id=active.user.id,
            project_id=active.project_id,
        )
        return CommandResult("已保存项目笔记。")

    def _handle_memory(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        page_arg = argument.strip()
        if not page_arg:
            page = 1
        else:
            try:
                page = int(page_arg)
            except ValueError:
                return CommandResult("用法: /memory [page]，page 必须是正整数。")
            if page < 1:
                return CommandResult("用法: /memory [page]，page 必须是正整数。")

        snapshot = self.tasks.get_memory_snapshot(project_id=active.project_id, page=page, page_size=5)
        self.audit.log(
            action=audit_events.MEMORY_VIEWED,
            message="已查看项目记忆",
            user_id=active.user.id,
            project_id=active.project_id,
        )
        return CommandResult(
            redact_text(format_memory(snapshot)),
            metadata={
                "memory_page": snapshot.page,
                "memory_total_pages": snapshot.total_pages,
            },
        )

    def _handle_cancel(self, ctx: CommandContext) -> CommandResult:
        return self._handle_task_cancel(ctx, "")

    def _handle_tasks(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        page_arg = argument.strip()
        if not page_arg:
            page = 1
        else:
            try:
                page = int(page_arg)
            except ValueError:
                return CommandResult("用法: /tasks [page]，page 必须是正整数。")
            if page < 1:
                return CommandResult("用法: /tasks [page]，page 必须是正整数。")

        result = self.tasks.list_tasks(project_id=active.project_id, page=page, page_size=8)
        self.audit.log(
            action=audit_events.TASK_LAST_VIEWED,
            message="查看任务列表",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"page": result.page, "count": result.total_count},
        )
        return CommandResult(
            redact_text(format_tasks_list(result)),
            metadata={
                "tasks_page": result.page,
                "tasks_total_pages": result.total_pages,
                "tasks_items": result.items,
            },
        )

    def _handle_task_cancel(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        task_id = self._parse_task_id(argument)
        if task_id is None and argument.strip():
            return CommandResult("用法: /task-cancel [id]")

        task = (
            self.tasks.get_task_for_project(task_id=task_id, project_id=active.project_id)
            if task_id is not None
            else self.tasks.get_latest_active_task(active.project_id)
        )
        if task is None or task.status not in {"created", "running", "waiting_approval"}:
            return CommandResult("当前没有可取消的任务。")

        if self._request_active_task_cancel(task.id):
            self.audit.log(
                action=audit_events.TASK_CANCELLED,
                message="请求终止运行中任务",
                user_id=active.user.id,
                project_id=active.project_id,
                task_id=task.id,
            )
            return CommandResult(f"已发送取消信号给任务 #{task.id}，等待执行进程退出。")

        cancelled = self.tasks.cancel_task(task_id=task.id, project_id=active.project_id)
        if cancelled is None:
            return CommandResult(f"任务 #{task.id} 当前不可取消。")

        self.tasks.update_project_state_after_task(
            project_id=active.project_id,
            task_id=cancelled.id,
            summary=cancelled.latest_summary or "任务已取消。",
            codex_session_id=cancelled.codex_session_id,
            pending_approval_task_id=None,
            next_step="可用 /do 新建任务，或用 /status 查看状态。",
        )
        self.audit.log(
            action=audit_events.TASK_CANCELLED,
            message="用户取消任务",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=cancelled.id,
        )
        return CommandResult(f"已取消任务 #{cancelled.id}。")

    def _handle_task_delete(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        task_id = self._parse_task_id(argument)
        if task_id is None:
            return CommandResult("用法: /task-delete <id>")

        deleted = self.tasks.delete_task(task_id=task_id, project_id=active.project_id)
        if deleted is None:
            return CommandResult(f"任务 #{task_id} 不存在，或仍在运行中不可删除。")

        self.audit.log(
            action=audit_events.TASK_CANCELLED,
            message="删除历史任务",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"deleted_task_id": deleted.id},
        )
        return CommandResult(f"已删除任务 #{deleted.id}。")

    def _handle_tasks_clear(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        active_task = self.tasks.get_latest_active_task(active.project_id)
        if active_task is not None:
            return CommandResult(
                f"当前仍有活动任务 #{active_task.id}（{active_task.status}）。\n"
                "请先用 /task-cancel [id] 取消，再执行 /tasks-clear。"
            )

        deleted_count = self.tasks.clear_tasks(project_id=active.project_id)
        self.audit.log(
            action=audit_events.TASK_CANCELLED,
            message="清空历史任务",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"deleted_count": deleted_count},
        )
        if deleted_count == 0:
            return CommandResult("当前没有可清空的历史任务。")
        return CommandResult(f"已清空 {deleted_count} 条历史任务。")

    def _handle_diff(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        diff_text = self.repo.diff_summary(active.project.path)
        self._refresh_repo_state(project_id=active.project_id, project=active.project)
        self.audit.log(
            action=audit_events.DIFF_VIEWED,
            message="已查看差异摘要",
            user_id=active.user.id,
            project_id=active.project_id,
        )
        return CommandResult(redact_text(format_diff_card(diff_text)))

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
                    codex_result = self.codex.ask(
                        active.project,
                        request_text,
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
                    codex_result = self.codex.run(
                        active.project,
                        request_text,
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

    def _resolve_active_project(self, ctx: CommandContext) -> ActiveProjectContext | CommandResult:
        user = self.tasks.ensure_user(ctx)
        project_key = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if not project_key:
            recent_projects = self.tasks.list_recent_project_keys(user_id=user.id)
            return CommandResult(
                "未选择活跃项目。\n请先点“项目”切换，或使用 /projects 查看项目列表。",
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
    ) -> LockType | CommandResult:
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

    def _parse_schedule_add_argument(
        self, argument: str
    ) -> tuple[str, int, str, str] | None:
        hhmm, _, tail = argument.partition(" ")
        if not hhmm or not tail:
            return None
        command_type, _, request_text = tail.strip().partition(" ")
        command_type = command_type.lstrip("/")
        if command_type not in {"ask", "do"}:
            return None
        request_text = request_text.strip()
        if not request_text:
            return None
        hour_text, sep, minute_text = hhmm.strip().partition(":")
        if sep != ":":
            return None
        try:
            hour = int(hour_text)
            minute = int(minute_text)
        except ValueError:
            return None
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        normalized_hhmm = f"{hour:02d}:{minute:02d}"
        minute_of_day = hour * 60 + minute
        return normalized_hhmm, minute_of_day, command_type, request_text

    def _minute_to_hhmm(self, minute_of_day: int) -> str:
        hour = minute_of_day // 60
        minute = minute_of_day % 60
        return f"{hour:02d}:{minute:02d}"

    def _parse_schedule_id(self, argument: str) -> int | None:
        if not argument:
            return None
        try:
            return int(argument.strip())
        except ValueError:
            return None

    def _parse_task_id(self, argument: str) -> int | None:
        if not argument:
            return None
        try:
            return int(argument.strip())
        except ValueError:
            return None

    def _parse_resume_argument(self, argument: str) -> tuple[int | None, str]:
        text = argument.strip()
        if not text:
            return None, ""
        first, _, tail = text.partition(" ")
        if first.isdigit():
            return int(first), tail.strip()
        return None, text

    def _parse_approval_argument(self, argument: str) -> tuple[int | None, str]:
        text = argument.strip()
        if not text:
            return None, ""
        first, _, tail = text.partition(" ")
        if first.isdigit():
            return int(first), tail.strip()
        return None, text

    def _parse_project_add_argument(self, argument: str) -> tuple[str, Path | None, str] | None:
        text = argument.strip()
        if not text:
            return None
        parts = text.split()
        key = parts[0].strip()
        if len(parts) == 1:
            return key, None, key

        second = parts[1].strip()
        if second.startswith("/") or second.startswith("~"):
            path = Path(second)
            display_name = " ".join(parts[2:]).strip() if len(parts) > 2 else key
            return key, path, display_name

        display_name = " ".join(parts[1:]).strip() or key
        return key, None, display_name

    def _parse_github_clone_argument(self, argument: str) -> tuple[str | None, str | None]:
        text = argument.strip()
        if not text:
            return None, None
        try:
            parts = shlex.split(text)
        except ValueError:
            return None, None
        if not parts:
            return None, None
        repo_input = parts[0].strip()
        target_name = " ".join(parts[1:]).strip() if len(parts) > 1 else None
        return (repo_input or None), (target_name or None)

    def _resolve_project_add_path(
        self,
        *,
        key: str,
        path: Path | None,
    ) -> tuple[Path, bool] | CommandResult:
        if path is not None:
            if not path.is_absolute():
                return CommandResult("项目路径必须是绝对路径。")
            return path.expanduser().resolve(), False

        default_root = self._get_default_project_root()
        if default_root is None:
            return CommandResult(
                "未设置默认项目根目录，无法省略项目路径。\n"
                "请先执行 /project-root <abs_path>，或使用 /project-add <key> <abs_path> [name]。"
            )
        return (default_root / key).expanduser().resolve(), True

    def _get_default_project_root(self) -> Path | None:
        if self.projects.default_project_root is not None:
            return self.projects.default_project_root
        config_root = getattr(self.config, "default_project_root", None)
        if isinstance(config_root, Path):
            return config_root.expanduser().resolve()
        if isinstance(config_root, str) and config_root.strip():
            candidate = Path(config_root.strip()).expanduser()
            if candidate.is_absolute():
                return candidate.resolve()
        return None

    def _find_project_key_by_path(self, candidate_path: Path) -> str | None:
        resolved = candidate_path.resolve()
        for key in self.projects.list_keys(include_inactive=True):
            project = self.projects.get_any(key)
            if project is None:
                continue
            if project.path.resolve() == resolved:
                return key
        return None

    def _derive_project_key(self, path: Path) -> str:
        raw = path.name.lower()
        normalized = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
        return normalized or "imported-session"

    def _parse_session_import_argument(self, argument: str) -> tuple[str | None, str | None, str | None]:
        if not argument.strip():
            return None, None, None
        session_id, _, tail = argument.strip().partition(" ")
        tail = tail.strip()
        if not tail:
            return session_id, None, None
        project_key, _, name = tail.partition(" ")
        return session_id, (project_key.strip() or None), (name.strip() or None)
