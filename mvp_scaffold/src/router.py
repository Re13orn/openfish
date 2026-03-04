"""Command parsing and routing."""

from dataclasses import dataclass
from pathlib import Path
import re
from threading import Lock
from _thread import LockType
from uuid import uuid4

from src.approval import ApprovalService
from src import audit_events
from src.auth import is_allowed_user
from src.formatters import (
    format_approval_required,
    format_diff_card,
    format_do_result,
    format_help,
    format_last_task,
    format_memory,
    format_project_busy,
    format_projects,
    format_skill_install_result,
    format_skills_list,
    format_schedule_added,
    format_schedule_deleted,
    format_schedule_list,
    format_schedule_run_result,
    format_schedule_toggled,
    format_start,
    format_status,
    format_templates,
    format_upload_policy,
    format_upload_rejected,
    format_use_confirmation,
)
from src.models import CommandContext, CommandResult, ProjectConfig, UserRecord
from src.project_registry import ProjectRegistry
from src.redaction import redact_text
from src.repo_inspector import RepoInspector
from src.security_guard import has_symlink_in_path, is_sensitive_file_name
from src.skills_service import SkillsService
from src.task_templates import BUILTIN_TEMPLATES
from src.task_store import ScheduledTaskRecord, TaskStore


PROJECT_ADD_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")


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
    ) -> None:
        self.config = config
        self.projects = projects
        self.tasks = tasks
        self.audit = audit
        self.codex = codex
        self.repo = repo
        self.approvals = approvals
        self.skills_service = skills_service
        self._project_locks: dict[int, LockType] = {}
        self._project_locks_guard = Lock()

    def handle(self, ctx: CommandContext) -> CommandResult:
        if not is_allowed_user(self.config, ctx.telegram_user_id):
            return CommandResult("未授权用户。")

        text = ctx.text.strip()
        if not text:
            return CommandResult(format_help())
        command, _, remainder = text.partition(" ")
        argument = remainder.strip()
        if "@" in command:
            command = command.split("@", 1)[0]

        if command == "/start":
            return self._handle_start(ctx)
        if command == "/help":
            return CommandResult(format_help())
        if command == "/projects":
            return CommandResult(format_projects(self.projects.list_keys()))
        if command == "/project-add":
            return self._handle_project_add(ctx, argument)
        if command == "/project-disable":
            return self._handle_project_disable(ctx, argument)
        if command == "/project-archive":
            return self._handle_project_archive(ctx, argument)
        if command == "/templates":
            return self._handle_templates(ctx)
        if command == "/run":
            return self._handle_run_template(ctx, argument)
        if command == "/skills":
            return self._handle_skills(ctx)
        if command == "/skill-install":
            return self._handle_skill_install(ctx, argument)
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
            return self._handle_memory(ctx)
        if command == "/cancel":
            return self._handle_cancel(ctx)
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
        self.audit.log(
            action=audit_events.START_VIEWED,
            message="用户查看启动引导",
            user_id=user.id,
        )
        return CommandResult(format_start(active_project))

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
            return CommandResult("用法: /project-add <key> <abs_path> [name]")
        key, path, name = parsed

        if not PROJECT_ADD_KEY_PATTERN.match(key):
            return CommandResult("项目 key 非法。只允许字母数字/._-，长度 1-64。")
        if not path.is_absolute():
            return CommandResult("项目路径必须是绝对路径。")
        resolved_path = path.expanduser().resolve()
        if not resolved_path.exists() or not resolved_path.is_dir():
            return CommandResult(f"项目路径不存在或不是目录: {resolved_path}")
        existing = self.projects.get_any(key)
        if existing is not None:
            return CommandResult(f"项目已存在: {key}")

        try:
            self.projects.add_project(key=key, path=resolved_path, name=name)
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
            "可用 /status 查看状态。"
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
                "未识别为命令。请先 /use <project> 选择项目后直接提问，或使用 /help 查看命令。"
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

            codex_result = self.codex.ask(plan.active.project, task_instruction)
            return self._finalize_task_execution(
                active=plan.active,
                task_id=task_id,
                run_mode="upload",
                codex_result=codex_result,
                next_step="可用 /do 对该文件执行后续任务，或继续上传其它文件。",
            )
        finally:
            project_lock.release()

    def _handle_templates(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        self.audit.log(
            action=audit_events.TEMPLATES_VIEWED,
            message="用户查看任务模板",
            user_id=user.id,
        )
        templates = [
            (item.key, item.title, item.mode)
            for item in BUILTIN_TEMPLATES.values()
        ]
        return CommandResult(format_templates(templates))

    def _handle_run_template(self, ctx: CommandContext, argument: str) -> CommandResult:
        if not argument:
            return CommandResult("用法: /run <template> [附加说明]\n可用 /templates 查看模板。")
        template_key, _, extra = argument.partition(" ")
        template = BUILTIN_TEMPLATES.get(template_key.strip())
        if template is None:
            known = ", ".join(sorted(BUILTIN_TEMPLATES.keys()))
            return CommandResult(f"未知模板: {template_key}\n可用模板: {known}")
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        request_text = template.instruction
        if extra.strip():
            request_text += f"\n附加说明: {extra.strip()}"
        self.audit.log(
            action=audit_events.TEMPLATE_RUN,
            message=f"执行模板: {template.key}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"template_key": template.key},
        )
        run_mode = "do" if template.mode == "do" else "ask"
        command_type = "do" if template.mode == "do" else "ask"
        next_step = "可用 /status 查看状态，或 /run 继续使用模板。"
        return self._run_codex_task(
            ctx=ctx,
            active=active,
            command_type=command_type,
            request_text=request_text,
            run_mode=run_mode,
            next_step=next_step,
        )

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

    def _handle_schedule_add(self, ctx: CommandContext, argument: str) -> CommandResult:
        parsed = self._parse_schedule_add_argument(argument)
        if parsed is None:
            return CommandResult("用法: /schedule-add <HH:MM> <ask|do> <text>")

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
            return CommandResult("用法: /use <project>")

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
        return CommandResult(redact_text(format_status(snapshot)))

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

        pending = self.tasks.get_pending_approval(active.project_id)
        if pending is None:
            project_lock.release()
            return CommandResult("当前没有待审批任务。")

        self.tasks.resolve_approval(
            approval_id=pending.approval_id,
            status="approved",
            decided_by_user_id=active.user.id,
            decision_note=note or None,
        )
        self.tasks.mark_task_resumed_after_approval(pending.task_id)
        self.audit.log(
            action=audit_events.APPROVAL_GRANTED,
            message=f"已批准任务 #{pending.task_id}",
            user_id=active.user.id,
            project_id=active.project_id,
            task_id=pending.task_id,
            details={"note": note[:200] if note else None},
        )

        resume_instruction = self.approvals.build_resume_instruction(pending.requested_action, user_note=note or None)
        try:
            codex_result = self.codex.resume_last(active.project, resume_instruction)
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
            project_lock.release()

    def _handle_reject(self, ctx: CommandContext, reason: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        pending = self.tasks.get_pending_approval(active.project_id)
        if pending is None:
            return CommandResult("当前没有待审批任务。")

        reject_reason = redact_text(reason) if reason else "用户拒绝"
        self.tasks.resolve_approval(
            approval_id=pending.approval_id,
            status="rejected",
            decided_by_user_id=active.user.id,
            decision_note=reject_reason,
        )
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

    def _handle_memory(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        snapshot = self.tasks.get_memory_snapshot(project_id=active.project_id)
        self.audit.log(
            action=audit_events.MEMORY_VIEWED,
            message="已查看项目记忆",
            user_id=active.user.id,
            project_id=active.project_id,
        )
        return CommandResult(redact_text(format_memory(snapshot)))

    def _handle_cancel(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        cancelled = self.tasks.cancel_latest_active_task(active.project_id)
        if cancelled is None:
            return CommandResult("当前没有可取消的任务。")

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

        try:
            if run_mode == "ask":
                codex_result = self.codex.ask(active.project, request_text)
            elif run_mode == "resume":
                if resume_session_id:
                    codex_result = self.codex.resume_session(
                        active.project,
                        resume_session_id,
                        request_text,
                    )
                else:
                    codex_result = self.codex.resume_last(active.project, request_text)
            else:
                codex_result = self.codex.run(active.project, request_text)

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
                summary=safe_summary,
                session_id=codex_result.session_id,
                next_action=next_action,
            ),
            metadata={"task_id": task_id, "status": task_status},
        )

    def _resolve_active_project(self, ctx: CommandContext) -> ActiveProjectContext | CommandResult:
        user = self.tasks.ensure_user(ctx)
        project_key = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
        if not project_key:
            return CommandResult("未选择活跃项目，请先使用 /use <project>。")

        project = self.projects.get(project_key)
        if project is None:
            return CommandResult(f"当前项目 {project_key} 不在注册表中，请重新执行 /use <project>。")
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

    def _refresh_repo_state(self, *, project_id: int, project: ProjectConfig) -> None:
        repo_state = self.repo.inspect(project.path)
        self.tasks.update_repo_state(
            project_id=project_id,
            branch=repo_state.branch if repo_state.is_git_repo else None,
            repo_dirty=repo_state.dirty if repo_state.is_git_repo else None,
        )

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

    def _parse_resume_argument(self, argument: str) -> tuple[int | None, str]:
        text = argument.strip()
        if not text:
            return None, ""
        first, _, tail = text.partition(" ")
        if first.isdigit():
            return int(first), tail.strip()
        return None, text

    def _parse_project_add_argument(self, argument: str) -> tuple[str, Path, str] | None:
        text = argument.strip()
        if not text:
            return None
        parts = text.split()
        if len(parts) < 2:
            return None
        key = parts[0].strip()
        path = Path(parts[1].strip())
        display_name = " ".join(parts[2:]).strip() if len(parts) > 2 else key
        return key, path, display_name
