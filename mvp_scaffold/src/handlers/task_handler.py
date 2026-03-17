"""Task execution and management command handlers mixin."""

from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4

from src import audit_events
from src.formatters import (
    format_approval_required,
    format_current_task,
    format_diff_card,
    format_do_result,
    format_last_task,
    format_memory,
    format_status,
    format_tasks_list,
    format_upload_rejected,
)
from src.handlers._types import ActiveProjectContext, DocumentUploadPlan
from src.models import CommandContext, CommandResult
from src.redaction import redact_text
from src.security_guard import has_symlink_in_path, is_sensitive_file_name


class _TaskHandler:
    """Mixin: do/ask/resume/approve/reject/task-management commands."""

    # ------------------------------------------------------------------ #
    #  Plain-text dispatch                                                 #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Document upload                                                     #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Status / task views                                                 #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Task control                                                        #
    # ------------------------------------------------------------------ #

    def _handle_cancel(self, ctx: CommandContext) -> CommandResult:
        return self._handle_task_cancel(ctx, "")

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

    def _handle_task_output(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        task_id = self._parse_task_id(argument)
        if task_id is None and argument.strip():
            return CommandResult("用法: /task-output [id]")

        task = (
            self.tasks.get_task_for_project(task_id=task_id, project_id=active.project_id)
            if task_id is not None
            else self.tasks.get_latest_task(active.project_id)
        )
        if task is None:
            return CommandResult("当前项目暂无可查看输出的任务。")

        artifact = self.tasks.get_latest_task_artifact(task_id=task.id)
        if artifact is None or not artifact.content:
            return CommandResult(f"任务 #{task.id} 暂无完整输出。")

        header = [
            f"任务 #{task.id}",
            f"类型: /{task.command_type}",
            f"状态: {task.status}",
            f"输出类型: {artifact.artifact_type}",
            "",
        ]
        full_text = "\n".join(header) + artifact.content
        if len(full_text) > 3200:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=f"-task-{task.id}-{artifact.artifact_type}.log",
                prefix="openfish-",
                delete=False,
            ) as handle:
                handle.write(full_text)
                path = handle.name
            return CommandResult(
                f"任务 #{task.id} 完整输出已导出为文件。",
                metadata={"send_local_file": {"path": path}, "task_id": task.id, "status": task.status},
            )
        return CommandResult(full_text, metadata={"task_id": task.id, "status": task.status})

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

    # ------------------------------------------------------------------ #
    #  Task execution                                                      #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Approval                                                            #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Memory / notes / diff                                               #
    # ------------------------------------------------------------------ #

    _NOTE_CATEGORIES = {"fact", "error", "convention", "decision"}

    def _handle_note(self, ctx: CommandContext, note_text: str) -> CommandResult:
        if not note_text:
            return CommandResult(
                "用法: /note [fact|error|convention|decision] <text>\n"
                "不带分类时默认为 general。"
            )
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active

        # Optional category prefix: /note fact <text>
        category = "general"
        first_word, _, rest = note_text.partition(" ")
        if first_word in self._NOTE_CATEGORIES and rest.strip():
            category = first_word
            note_text = rest.strip()

        title = note_text[:40]
        self.tasks.add_project_note(
            project_id=active.project_id,
            content=note_text,
            title=title,
            category=category,
        )
        self.audit.log(
            action=audit_events.NOTE_ADDED,
            message="已添加项目笔记",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"category": category},
        )
        label = f"（{category}）" if category != "general" else ""
        return CommandResult(f"已保存项目笔记{label}。")

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

    # ------------------------------------------------------------------ #
    #  Parse helpers                                                       #
    # ------------------------------------------------------------------ #

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
