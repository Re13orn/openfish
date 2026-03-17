"""Project management command handlers mixin."""

from __future__ import annotations

import shlex
from pathlib import Path

from src import audit_events
from src.formatters import format_use_confirmation
from src.handlers._types import PROJECT_ADD_KEY_PATTERN, ActiveProjectContext, _clip_text
from src.models import CommandContext, CommandResult, ProjectAddRequest, ProjectTemplatePreset, UserRecord


class _ProjectHandler:
    """Mixin: project add/edit/use/archive commands."""

    # ------------------------------------------------------------------ #
    #  /project-add and helpers                                            #
    # ------------------------------------------------------------------ #

    def _handle_project_add(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        parsed = self._parse_project_add_argument(argument)
        if parsed is None:
            return CommandResult(
                "用法: /project-add <key> [abs_path] [name] "
                "[--template <name>] [--mode normal|autopilot] [--autopilot-goal <text>]\n"
                "也可以直接按引导逐步填写。",
                metadata={"wizard": "project_add"},
            )
        key = parsed.key
        path = parsed.path
        name = parsed.name

        if not PROJECT_ADD_KEY_PATTERN.match(key):
            return CommandResult("项目 key 非法。只允许字母数字/._-，长度 1-64。")
        if parsed.default_run_mode not in {None, "normal", "autopilot"}:
            return CommandResult("项目运行模式非法，只允许 normal 或 autopilot。")
        if parsed.default_run_mode == "autopilot" and self.autopilot is None:
            return CommandResult("当前未启用 autopilot，无法创建 Autopilot 项目。")

        template: ProjectTemplatePreset | None = None
        if parsed.template_name:
            template = self.projects.get_project_template(parsed.template_name)
            if template is None:
                return CommandResult(f"项目模板不存在: {parsed.template_name}")

        autopilot_goal = parsed.autopilot_goal.strip() if parsed.autopilot_goal else None
        if parsed.default_run_mode == "autopilot" and not autopilot_goal:
            autopilot_goal = template.default_autopilot_goal if template is not None else None
        if autopilot_goal == "":
            autopilot_goal = None
        if parsed.default_run_mode == "autopilot" and not autopilot_goal:
            return CommandResult(
                "Autopilot 项目需要目标描述。\n"
                "请使用 --autopilot-goal <text>，或在模板元数据里设置 default_autopilot_goal。"
            )
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
            if template is not None:
                self.projects.apply_project_template(template_key=template.key, target_path=resolved_path)
            self.projects.add_project(
                key=key,
                path=resolved_path,
                name=name,
                create_if_missing=True,
                template_name=template.key if template is not None else None,
                default_run_mode=parsed.default_run_mode,
                default_autopilot_goal=autopilot_goal,
                default_autopilot_bootstrap_instruction=(
                    template.default_autopilot_bootstrap_instruction if template is not None else None
                ),
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
        reply_lines = [
            "项目已新增并切换。\n"
            f"项目: {key}\n"
            f"路径: {resolved_path}\n"
            f"目录来源: {'默认根目录' if used_default_root else '指定目录'}",
            f"模板: {template.name if template is not None else '未使用'}",
            f"默认模式: {parsed.default_run_mode or 'normal'}",
        ]
        if parsed.default_run_mode == "autopilot" and project is not None and self.autopilot is not None:
            run = self._start_project_autopilot(
                ctx=ctx,
                user=user,
                project=project,
                project_id=project_id,
                goal=autopilot_goal or "",
            )
            reply_lines.append(f"Autopilot: 已启动 run #{run.id}")
            reply_lines.append("可用 /autopilot-status 查看自治状态。")
            return CommandResult("\n".join(reply_lines), metadata={"autopilot_run_id": run.id, "autopilot_run": run})

        reply_lines.append("可用 /status 查看状态。")
        return CommandResult("\n".join(reply_lines))

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

    def _handle_project_template_root(self, ctx: CommandContext, argument: str) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        text = argument.strip()
        if not text:
            current = getattr(self.projects, "project_template_root", None)
            if current is None:
                return CommandResult(
                    "当前未设置项目模板根目录。\n"
                    "请使用 /project-template-root <abs_path> 设置后，再用 /project-templates 查看可用模板。"
                )
            return CommandResult(f"项目模板根目录: {current}")

        path = Path(text).expanduser()
        if not path.is_absolute():
            return CommandResult("项目模板根目录必须是绝对路径。")
        try:
            resolved = self.projects.set_project_template_root(path)
        except ValueError as exc:
            return CommandResult(str(exc))

        self.audit.log(
            action=audit_events.PROJECT_ROOT_UPDATED,
            message="更新项目模板根目录",
            user_id=user.id,
            details={"path": str(resolved), "kind": "template_root"},
        )
        return CommandResult(
            f"项目模板根目录已设置: {resolved}\n"
            "后续可用 /project-templates 查看可用模板。"
        )

    def _handle_project_templates(self, ctx: CommandContext) -> CommandResult:
        user = self.tasks.ensure_user(ctx)
        templates = self.projects.list_project_templates()
        current = getattr(self.projects, "project_template_root", None)
        lines = ["【项目模板】"]
        lines.append(f"模板根目录: {current or '未设置'}")
        if not templates:
            lines.append("当前没有可用模板。")
            lines.append("下一步: 执行 /project-template-root <abs_path>，并在该目录下放置模板子目录。")
        else:
            lines.append("可用模板:")
            for preset in templates[:20]:
                description = f" · {preset.description}" if preset.description else ""
                goal = (
                    f" · 默认 Autopilot: {_clip_text(preset.default_autopilot_goal, 60)}"
                    if preset.default_autopilot_goal
                    else ""
                )
                bootstrap = (
                    f" · 首轮启动: {_clip_text(preset.default_autopilot_bootstrap_instruction, 60)}"
                    if preset.default_autopilot_bootstrap_instruction
                    else ""
                )
                lines.append(f"- {preset.key} ({preset.name}){description}{goal}{bootstrap}")
            lines.append("下一步: /project-add <key> --template <模板名> [--mode autopilot]")
        self.audit.log(
            action=audit_events.TEMPLATES_VIEWED,
            message="查看项目模板列表",
            user_id=user.id,
            details={"count": len(templates)},
        )
        return CommandResult("\n".join(lines), metadata={"project_templates": templates})

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

    # ------------------------------------------------------------------ #
    #  /use                                                                #
    # ------------------------------------------------------------------ #

    def _handle_use(self, ctx: CommandContext, project_key: str) -> CommandResult:
        if not project_key:
            user = self.tasks.ensure_user(ctx)
            ordered_keys = self.projects.list_keys()
            active_key = self.tasks.get_active_project_key(user.id, ctx.telegram_chat_id)
            return CommandResult(
                "请选择要切换的项目。",
                metadata={
                    "recent_projects": self.tasks.list_recent_project_keys(user_id=user.id),
                    "projects_panel": True,
                    "projects_ordered_keys": ordered_keys,
                    "projects_active_key": active_key,
                },
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

    # ------------------------------------------------------------------ #
    #  Parse helpers                                                       #
    # ------------------------------------------------------------------ #

    def _parse_project_add_argument(self, argument: str) -> ProjectAddRequest | None:
        text = argument.strip()
        if not text:
            return None
        try:
            parts = shlex.split(text)
        except ValueError:
            return None
        if not parts:
            return None
        key = parts[0].strip()
        positionals: list[str] = []
        template_name: str | None = None
        default_run_mode: str | None = None
        autopilot_goal: str | None = None
        index = 1
        while index < len(parts):
            token = parts[index].strip()
            if token == "--template":
                index += 1
                if index >= len(parts):
                    return None
                template_name = parts[index].strip() or None
            elif token == "--mode":
                index += 1
                if index >= len(parts):
                    return None
                default_run_mode = parts[index].strip().lower() or None
            elif token == "--autopilot-goal":
                index += 1
                if index >= len(parts):
                    return None
                autopilot_goal = parts[index].strip() or None
            elif token.startswith("--"):
                return None
            else:
                positionals.append(token)
            index += 1

        path: Path | None = None
        display_name = key
        if positionals:
            first = positionals[0].strip()
            if first.startswith("/") or first.startswith("~"):
                path = Path(first)
                if len(positionals) > 1:
                    display_name = " ".join(positionals[1:]).strip() or key
            else:
                display_name = " ".join(positionals).strip() or key
        if autopilot_goal and default_run_mode is None:
            default_run_mode = "autopilot"
        return ProjectAddRequest(
            key=key,
            path=path,
            name=display_name,
            template_name=template_name,
            default_run_mode=default_run_mode,
            autopilot_goal=autopilot_goal,
        )

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

    def _start_project_autopilot(
        self,
        *,
        ctx: CommandContext,
        user: UserRecord,
        project,
        project_id: int,
        goal: str,
    ):
        if self.autopilot is None:
            raise ValueError("当前未启用 autopilot。")
        run = self.autopilot.create_run(
            project_id=project_id,
            chat_id=ctx.telegram_chat_id,
            created_by_user_id=user.id,
            goal=goal.strip(),
            max_cycles=100,
        )
        model = self.tasks.get_chat_codex_model(chat_id=ctx.telegram_chat_id)
        self.autopilot.start_run_loop(
            project=project,
            run_id=run.id,
            model=model,
            progress_callback=ctx.progress_callback,
        )
        self.audit.log(
            action=audit_events.AUTOPILOT_CREATED,
            message=f"创建 autopilot run #{run.id}",
            user_id=user.id,
            project_id=project_id,
            details={"run_id": run.id, "source": "project_add"},
        )
        return self.autopilot.get_run(run_id=run.id) or run
