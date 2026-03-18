"""System / meta command handlers mixin."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from src import audit_events
from src.formatters import (
    format_context,
    format_digest,
    format_health,
    format_help,
    format_home,
    format_projects,
    format_update_check,
    format_upload_policy,
    format_version_info,
)
from src.models import CommandContext, CommandResult
from src.security_guard import has_symlink_in_path, is_sensitive_file_name


class _SystemHandler:
    """Mixin: system, meta, and file commands."""

    # ------------------------------------------------------------------ #
    #  UI helpers                                                          #
    # ------------------------------------------------------------------ #

    def _chat_ui_mode(self, *, chat_id: str) -> str:
        return self.tasks.get_chat_ui_mode(chat_id=chat_id) or getattr(
            self.config, "default_ui_mode", "stream"
        )

    # ------------------------------------------------------------------ #
    #  Navigation                                                          #
    # ------------------------------------------------------------------ #

    def _handle_start(self, ctx: CommandContext) -> CommandResult:
        return self._handle_home(ctx)

    def _handle_home(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        snapshot = self.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
        recent_projects = self.tasks.list_recent_project_keys(user_id=user.id)
        current_model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        self.audit.log(
            action=audit_events.START_VIEWED,
            message="用户查看启动引导",
            user_id=user.id,
        )
        return CommandResult(
            format_home(
                snapshot=snapshot,
                current_model=current_model,
                recent_project_keys=recent_projects,
            ),
            metadata={
                "recent_projects": recent_projects,
                "home_snapshot": snapshot,
                "current_model": current_model,
            },
        )

    def _handle_context(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        snapshot = self.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
        current_model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        ui_mode = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        return CommandResult(
            format_context(
                snapshot=snapshot,
                current_model=current_model,
                ui_mode=ui_mode,
            ),
            metadata={
                "context_snapshot": snapshot,
                "current_model": current_model,
                "ui_mode": ui_mode,
            },
        )

    def _handle_digest(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        return self._build_project_digest(active=active)

    def _handle_help(self, ctx: CommandContext, argument: str) -> CommandResult:
        mode_arg = argument.strip().lower()
        if mode_arg == "verbose":
            mode = "verbose"
        elif mode_arg in {"", "show"}:
            mode = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        else:
            mode = "summary"
        return CommandResult(format_help(mode))

    def _build_project_digest(self, *, active) -> CommandResult:  # noqa: ANN001
        repo_state = self.repo.inspect(active.project.path)
        active_task = self.tasks.get_latest_active_task(active.project_id)
        recent_task_page = self.tasks.list_tasks(project_id=active.project_id, page=1, page_size=5)
        recent_tasks = recent_task_page.items
        pending_approval = self.tasks.get_pending_approval(active.project_id) is not None
        schedules = self.tasks.list_scheduled_tasks(active.project_id)
        next_schedule_label = None
        enabled = [item for item in schedules if item.enabled]
        if enabled:
            next_item = enabled[0]
            if next_item.schedule_type == "interval" and next_item.interval_minutes is not None:
                if next_item.interval_minutes % 60 == 0:
                    next_schedule_label = f"每{next_item.interval_minutes // 60}小时"
                else:
                    next_schedule_label = f"每{next_item.interval_minutes}分钟"
            else:
                hour = next_item.minute_of_day // 60
                minute = next_item.minute_of_day % 60
                next_schedule_label = f"{hour:02d}:{minute:02d}"
        recent_runs = (
            self.tasks.autopilot.list_runs_for_project(project_id=active.project_id, limit=2)
            if self.autopilot is not None
            else []
        )
        self.audit.log(
            action=audit_events.SYSTEM_DIGEST_VIEWED,
            message="查看项目摘要",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"recent_tasks": len(recent_tasks), "recent_runs": len(recent_runs)},
        )
        return CommandResult(
            format_digest(
                project_key=active.project_key,
                branch=repo_state.branch,
                repo_dirty=repo_state.dirty,
                active_task=active_task,
                pending_approval=pending_approval,
                recent_tasks=recent_tasks,
                recent_runs=recent_runs,
                next_schedule_label=next_schedule_label,
            )
        )

    # ------------------------------------------------------------------ #
    #  Preferences                                                         #
    # ------------------------------------------------------------------ #

    def _handle_ui(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        mode_arg = argument.strip().lower()
        current = self._chat_ui_mode(chat_id=ctx.telegram_chat_id)
        if mode_arg in {"", "show"}:
            return CommandResult(f"当前界面模式: {current}")
        if mode_arg == "reset":
            self.tasks.clear_chat_ui_mode(chat_id=ctx.telegram_chat_id)
            default_mode = getattr(self.config, "default_ui_mode", "stream")
            return CommandResult(f"界面模式已重置为默认值: {default_mode}")
        if mode_arg not in {"summary", "verbose", "stream"}:
            return CommandResult("用法: /ui [show|summary|verbose|stream|reset]")
        self.tasks.set_chat_ui_mode(chat_id=ctx.telegram_chat_id, user_id=user.id, mode=mode_arg)
        return CommandResult(f"界面模式已切换为: {mode_arg}")

    def _handle_model(self, ctx: CommandContext, argument: str) -> CommandResult:
        from src.handlers._types import MODEL_NAME_PATTERN

        user = self.tasks.ensure_user(ctx)
        current = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        text = argument.strip()
        if not text or text.lower() == "show":
            choices = ", ".join(self.list_available_models()) or "未发现（可手动输入）"
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

    # ------------------------------------------------------------------ #
    #  File                                                                #
    # ------------------------------------------------------------------ #

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

    def _parse_github_clone_argument(self, argument: str) -> tuple[str | None, str | None]:
        import shlex

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

    # ------------------------------------------------------------------ #
    #  Version / health / update / logs                                    #
    # ------------------------------------------------------------------ #

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

    def _handle_health(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        if self.update_service is None:
            return CommandResult("当前未启用服务健康检查。")
        info = self.update_service.get_current_version()
        snapshot = self.tasks.get_status_snapshot(user.id, ctx.telegram_chat_id)
        active_task_summary = None
        active_task = getattr(snapshot, "active_task", None)
        if active_task is not None:
            status = getattr(active_task, "status", "running")
            active_task_summary = f"#{active_task.id} · {status}"
        elif getattr(snapshot, "most_recent_task_summary", None):
            active_task_summary = str(snapshot.most_recent_task_summary)
        current_model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        codex_available = shutil.which(getattr(self.config, "codex_bin", "codex")) is not None
        project_count = len(self.projects.list_keys())
        scheduler_alive: bool | None = None
        scheduler_restart_count: int = 0
        if self._scheduler is not None:
            scheduler_alive = self._scheduler.is_alive()
            scheduler_restart_count = getattr(self._scheduler, "restart_count", 0)
        self.audit.log(
            action=audit_events.SYSTEM_VERSION_VIEWED,
            message="查看服务健康状态",
            user_id=user.id,
            details={
                "codex_available": codex_available,
                "project_count": project_count,
                "scheduler_alive": scheduler_alive,
                "scheduler_restart_count": scheduler_restart_count,
            },
        )
        return CommandResult(
            format_health(
                version=info.version,
                branch=info.branch,
                commit=info.commit,
                codex_available=codex_available,
                project_count=project_count,
                active_project_key=getattr(snapshot, "active_project_key", None),
                active_task_summary=active_task_summary,
                pending_approval=bool(getattr(snapshot, "pending_approval", False)),
                current_model=current_model,
                session_id=getattr(snapshot, "last_codex_session_id", None),
                scheduler_alive=scheduler_alive,
                scheduler_restart_count=scheduler_restart_count,
            )
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

    # ------------------------------------------------------------------ #
    #  Projects list & upload policy                                       #
    # ------------------------------------------------------------------ #

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
