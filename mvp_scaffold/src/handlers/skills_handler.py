"""Skills, MCP, and session command handlers mixin."""

from __future__ import annotations

import re
from pathlib import Path

from src import audit_events
from src.formatters import (
    format_mcp_detail,
    format_mcp_list,
    format_session_detail,
    format_sessions_list,
    format_skill_install_result,
    format_skills_list,
)
from src.models import CommandContext, CommandResult


class _SkillsHandler:
    """Mixin: /skills, /mcp, /session* commands."""

    # ------------------------------------------------------------------ #
    #  Skills                                                              #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  MCP                                                                 #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Sessions                                                            #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Parse helpers                                                       #
    # ------------------------------------------------------------------ #

    def _parse_session_import_argument(self, argument: str) -> tuple[str | None, str | None, str | None]:
        if not argument.strip():
            return None, None, None
        session_id, _, tail = argument.strip().partition(" ")
        tail = tail.strip()
        if not tail:
            return session_id, None, None
        project_key, _, name = tail.partition(" ")
        return session_id, project_key.strip() or None, name.strip() or None

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
