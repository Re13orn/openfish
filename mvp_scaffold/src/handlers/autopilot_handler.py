"""Autopilot command handlers mixin."""

from __future__ import annotations

from src import audit_events
from src.formatters import (
    format_autopilot_action_result,
    format_autopilot_context,
    format_autopilot_log,
    format_autopilot_runs,
    format_autopilot_status,
    format_autopilot_step_result,
)
from src.models import CommandContext, CommandResult
from src.redaction import redact_text


class _AutopilotHandler:
    """Mixin: /autopilot* commands."""

    def _split_autopilot_takeover_argument(self, argument: str) -> tuple[str | None, str]:
        normalized = argument.strip()
        if not normalized:
            return None, ""
        run_token, _, remainder = normalized.partition(" ")
        candidate = run_token[1:] if run_token.startswith("#") else run_token
        if candidate.isdigit():
            return candidate, remainder.strip()
        return None, normalized

    def _handle_autopilot(self, ctx: CommandContext, goal: str) -> CommandResult:
        if not goal.strip():
            return CommandResult("用法: /autopilot <goal>")
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        run = self.autopilot.create_run(
            project_id=active.project_id,
            chat_id=ctx.telegram_chat_id,
            created_by_user_id=active.user.id,
            goal=goal.strip(),
            max_cycles=100,
        )
        model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        self.autopilot.start_run_loop(
            project=active.project,
            run_id=run.id,
            model=model,
            progress_callback=ctx.progress_callback,
        )
        run = self.autopilot.get_run(run_id=run.id) or run
        self.audit.log(
            action=audit_events.AUTOPILOT_CREATED,
            message=f"创建 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id},
        )
        events = self.autopilot.list_events(run_id=run.id, limit=10)
        raw_output_lines = self.autopilot.get_recent_output(run_id=run.id, limit=6)
        return CommandResult(
            redact_text(format_autopilot_status(run=run, events=events, raw_output_lines=raw_output_lines)),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilots(self, ctx: CommandContext) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        runs = self.autopilot.list_runs_for_project(project_id=active.project_id, limit=8)
        latest_run = runs[0] if runs else None
        self.audit.log(
            action=audit_events.AUTOPILOT_VIEWED,
            message="查看 autopilot runs 列表",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"count": len(runs)},
        )
        return CommandResult(
            redact_text(format_autopilot_runs(runs)),
            metadata={"autopilot_runs": runs, "autopilot_run": latest_run},
        )

    def _handle_autopilot_status(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        run = run_or_result
        events = self.autopilot.list_events(run_id=run.id, limit=10)
        runtime = self.autopilot.get_runtime_snapshot(run_id=run.id)
        raw_output_lines = self.autopilot.get_recent_output(run_id=run.id, limit=6)
        self.audit.log(
            action=audit_events.AUTOPILOT_VIEWED,
            message=f"查看 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id},
        )
        return CommandResult(
            redact_text(
                format_autopilot_status(
                    run=run,
                    events=events,
                    runtime=runtime,
                    raw_output_lines=raw_output_lines,
                )
            ),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilot_context(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        run = run_or_result
        events = self.autopilot.list_events(run_id=run.id, limit=10)
        runtime = self.autopilot.get_runtime_snapshot(run_id=run.id)
        raw_output_lines = self.autopilot.get_recent_output(run_id=run.id, limit=8)
        persisted_stream_lines = self._render_autopilot_stream_lines(
            self.autopilot.list_stream_chunks(run_id=run.id, limit=12)
        )
        self.audit.log(
            action=audit_events.AUTOPILOT_VIEWED,
            message=f"查看 autopilot context #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id, "view": "context"},
        )
        return CommandResult(
            redact_text(
                format_autopilot_context(
                    run=run,
                    events=events,
                    runtime=runtime,
                    raw_output_lines=raw_output_lines,
                    persisted_stream_lines=persisted_stream_lines,
                )
            ),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilot_log(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        run = run_or_result
        chunks = self.autopilot.list_stream_chunks(run_id=run.id, limit=200)
        self.audit.log(
            action=audit_events.AUTOPILOT_VIEWED,
            message=f"查看 autopilot log #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id, "view": "log"},
        )
        return CommandResult(
            redact_text(format_autopilot_log(run=run, chunks=chunks)),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilot_step(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。请先执行 /autopilot <goal>。")
        run = run_or_result
        model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        step = self.autopilot.step_run(
            project=active.project,
            run_id=run.id,
            model=model,
            progress_callback=ctx.progress_callback,
        )
        self.audit.log(
            action=audit_events.AUTOPILOT_STEPPED,
            message=f"推进 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id, "status": step.run.status},
        )
        return CommandResult(
            redact_text(
                format_autopilot_step_result(
                    run=step.run,
                    worker_summary=step.worker_result.summary,
                    supervisor_summary=step.supervisor_result.summary,
                )
            ),
            metadata={"autopilot_run_id": run.id, "autopilot_run": step.run},
        )

    def _handle_autopilot_takeover(self, ctx: CommandContext, argument: str) -> CommandResult:
        run_argument, instruction = self._split_autopilot_takeover_argument(argument)
        if not instruction:
            return CommandResult("用法: /autopilot-takeover [run_id] <instruction>")
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")

        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=run_argument or "")
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        try:
            run = self.autopilot.takeover_run(
                run_id=run_or_result.id,
                instruction=instruction,
                taken_by_user_id=active.user.id,
            )
        except ValueError as exc:
            return CommandResult(str(exc))
        model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        self.autopilot.start_run_loop(
            project=active.project,
            run_id=run.id,
            model=model,
            progress_callback=ctx.progress_callback,
        )
        run = self.autopilot.get_run(run_id=run.id) or run
        self.audit.log(
            action=audit_events.AUTOPILOT_TAKEOVER,
            message=f"人工接管 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id, "instruction": instruction},
        )
        return CommandResult(
            redact_text(format_autopilot_action_result(run=run, action="takeover", note=instruction)),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilot_pause(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")
        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        try:
            run = self.autopilot.pause_run(run_id=run_or_result.id)
        except ValueError as exc:
            return CommandResult(str(exc))
        self.audit.log(
            action=audit_events.AUTOPILOT_PAUSED,
            message=f"暂停 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id},
        )
        return CommandResult(
            redact_text(format_autopilot_action_result(run=run, action="pause")),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilot_resume(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")
        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        try:
            run = self.autopilot.resume_run(run_id=run_or_result.id)
        except ValueError as exc:
            return CommandResult(str(exc))
        model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        self.autopilot.start_run_loop(
            project=active.project,
            run_id=run.id,
            model=model,
            progress_callback=ctx.progress_callback,
        )
        run = self.autopilot.get_run(run_id=run.id) or run
        self.audit.log(
            action=audit_events.AUTOPILOT_RESUMED,
            message=f"恢复 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id},
        )
        return CommandResult(
            redact_text(format_autopilot_action_result(run=run, action="resume")),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    def _handle_autopilot_stop(self, ctx: CommandContext, argument: str) -> CommandResult:
        active = self._resolve_active_project(ctx)
        if isinstance(active, CommandResult):
            return active
        if self.autopilot is None:
            return CommandResult("当前未启用 autopilot。")
        run_or_result = self._resolve_autopilot_run(project_id=active.project_id, argument=argument)
        if isinstance(run_or_result, CommandResult):
            return run_or_result
        if run_or_result is None:
            return CommandResult("当前项目还没有 autopilot run。")
        try:
            run = self.autopilot.stop_run(
                run_id=run_or_result.id,
                stopped_by_user_id=active.user.id,
            )
        except ValueError as exc:
            return CommandResult(str(exc))
        self.audit.log(
            action=audit_events.AUTOPILOT_STOPPED,
            message=f"停止 autopilot run #{run.id}",
            user_id=active.user.id,
            project_id=active.project_id,
            details={"run_id": run.id},
        )
        return CommandResult(
            redact_text(format_autopilot_action_result(run=run, action="stop")),
            metadata={"autopilot_run_id": run.id, "autopilot_run": run},
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def _resolve_autopilot_run(self, *, project_id: int, argument: str):
        if self.autopilot is None:
            return None
        raw = argument.strip()
        if not raw:
            return self.autopilot.get_latest_run_for_project(project_id=project_id)
        if raw.startswith("#"):
            raw = raw[1:].strip()
        if not raw.isdigit():
            return CommandResult("autopilot run id 必须是整数。")
        run = self.autopilot.get_run(run_id=int(raw))
        if run is None or run.project_id != project_id:
            return CommandResult(f"autopilot run #{raw} 不存在或不属于当前项目。")
        return run

    def _render_autopilot_stream_lines(self, chunks) -> list[str]:
        lines: list[str] = []
        for chunk in chunks:
            actor_label = "A" if chunk.actor == "supervisor" else "B"
            lines.append(f"{chunk.cycle_no}:{actor_label}>[{chunk.channel}] {chunk.content}")
        return lines
