from pathlib import Path
from types import SimpleNamespace

from src import audit_events
from src.approval import ApprovalService
from src.codex_runner import CodexRunResult
from src.mcp_service import McpDetailResult, McpListResult, McpServerDetail, McpServerSummary
from src.models import CommandContext, CommandResult, ProjectConfig, UserRecord
from src.repo_inspector import RepoState
from src.router import CommandRouter
from src.skills_service import SkillInstallResult, SkillsListResult
from src.task_store import PendingApprovalRecord, ScheduledTaskRecord, TaskRecord


def _ctx(text: str) -> CommandContext:
    return CommandContext(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        text=text,
        telegram_username="owner",
        telegram_display_name="Owner",
    )


def _codex_result(summary: str, ok: bool = True) -> CodexRunResult:
    return CodexRunResult(
        ok=ok,
        stdout=summary,
        stderr="",
        exit_code=0 if ok else 1,
        summary=summary,
        session_id="sess-1",
        used_json_output=False,
        command=["codex", "exec"],
    )


class AuditStub:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def log(self, *, action: str, message: str, **kwargs) -> None:  # noqa: ANN003
        _ = kwargs
        self.events.append((action, message))


class ProjectsStub:
    def __init__(self) -> None:
        self.default_project_root = Path("/tmp/openfish_projects")
        self.projects = {
            "demo": ProjectConfig(
                key="demo",
                name="Demo",
                path=Path("/tmp"),
                allowed_directories=[Path("/tmp")],
            )
        }

    def get(self, key: str) -> ProjectConfig | None:
        return self.projects.get(key)

    def get_any(self, key: str) -> ProjectConfig | None:
        return self.projects.get(key)

    def list_keys(self) -> list[str]:
        return sorted(self.projects.keys())

    def is_path_allowed(self, project: ProjectConfig, candidate_path: Path) -> bool:
        _ = project
        _ = candidate_path
        return True

    def add_project(
        self,
        *,
        key: str,
        path: Path,
        name: str | None = None,
        create_if_missing: bool = False,
    ) -> None:
        _ = create_if_missing
        self.projects[key] = ProjectConfig(
            key=key,
            name=name or key,
            path=path,
            allowed_directories=[path],
        )

    def set_default_project_root(self, root_path: Path) -> Path:
        resolved = root_path.expanduser().resolve()
        self.default_project_root = resolved
        return resolved

    def set_project_active(self, *, key: str, is_active: bool) -> bool:
        project = self.projects.get(key)
        if project is None:
            return False
        project.is_active = is_active
        return True

    def archive_project(self, *, key: str) -> bool:
        return self.set_project_active(key=key, is_active=False)


class RepoStub:
    def inspect(self, project_path: Path) -> RepoState:
        _ = project_path
        return RepoState(is_git_repo=False, branch=None, dirty=None)

    def diff_summary(self, project_path: Path, max_files: int = 12) -> str:
        _ = project_path
        _ = max_files
        return "最近变更：\nM a.py"


class CodexStub:
    def __init__(self, run_result: CodexRunResult, resume_result: CodexRunResult | None = None) -> None:
        self.run_result = run_result
        self.resume_result = resume_result or run_result
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def run(self, project: ProjectConfig, prompt: str) -> CodexRunResult:
        _ = project
        self.prompts.append(prompt)
        self.calls.append("run")
        return self.run_result

    def ask(self, project: ProjectConfig, question: str) -> CodexRunResult:
        _ = project
        self.prompts.append(question)
        self.calls.append("ask")
        return self.run_result

    def resume_last(self, project: ProjectConfig, instruction: str) -> CodexRunResult:
        _ = project
        _ = instruction
        self.calls.append("resume_last")
        return self.resume_result

    def resume_session(
        self,
        project: ProjectConfig,
        session_id: str,
        instruction: str,
    ) -> CodexRunResult:
        _ = project
        _ = session_id
        _ = instruction
        self.calls.append("resume_session")
        return self.resume_result


class TasksStub:
    def __init__(self) -> None:
        self.user = UserRecord(id=1, telegram_user_id="123")
        self.active_project_key = "demo"
        self.recent_project_keys = ["demo"]
        self.ui_mode = None
        self.project_id = 101
        self.next_task_id = 1
        self.pending: PendingApprovalRecord | None = None
        self.waiting_marked = False
        self.finalized: list[tuple[int, str, str]] = []
        self.last_command_type: str | None = None
        self.latest_task: TaskRecord | None = TaskRecord(
            id=9,
            command_type="do",
            original_request="old task",
            status="completed",
            codex_session_id="sess-9",
            latest_summary="old summary",
        )
        self.scheduled_tasks: list[ScheduledTaskRecord] = []
        self.next_schedule_id = 1
        self.cleared_project_session_ids: list[int] = []

    def ensure_user(self, ctx: CommandContext) -> UserRecord:
        _ = ctx
        return self.user

    def sync_projects_from_registry(self, projects) -> None:  # noqa: ANN001
        _ = projects

    def set_active_project(self, user_id: int, project_key: str, chat_id: str | None = None) -> None:
        _ = user_id
        _ = chat_id
        self.active_project_key = project_key
        if project_key in self.recent_project_keys:
            self.recent_project_keys.remove(project_key)
        self.recent_project_keys.insert(0, project_key)

    def get_project_id(self, project_key: str) -> int:
        _ = project_key
        return self.project_id

    def get_active_project_key(self, user_id: int, chat_id: str | None = None) -> str | None:
        _ = user_id
        _ = chat_id
        return self.active_project_key

    def clear_active_project(self, user_id: int, chat_id: str | None = None) -> None:
        _ = user_id
        _ = chat_id
        self.active_project_key = None

    def list_recent_project_keys(self, *, user_id: int, limit: int = 6) -> list[str]:
        _ = user_id
        return self.recent_project_keys[:limit]

    def get_chat_ui_mode(self, *, chat_id: str) -> str | None:
        _ = chat_id
        return self.ui_mode

    def set_chat_ui_mode(self, *, chat_id: str, user_id: int, mode: str) -> None:
        _ = chat_id
        _ = user_id
        self.ui_mode = mode

    def create_task(
        self,
        *,
        user_id: int,
        project_id: int,
        chat_id: str,
        message_id: str | None,
        command_type: str,
        original_request: str,
    ) -> int:
        _ = user_id
        _ = project_id
        _ = chat_id
        _ = message_id
        _ = original_request
        self.last_command_type = command_type
        task_id = self.next_task_id
        self.next_task_id += 1
        return task_id

    def mark_task_running(self, task_id: int) -> None:
        _ = task_id

    def add_task_artifact(self, task_id: int, artifact_type: str, **kwargs) -> None:  # noqa: ANN003
        _ = task_id
        _ = artifact_type
        _ = kwargs

    def finalize_task(
        self,
        *,
        task_id: int,
        status: str,
        summary: str,
        error: str | None,
        codex_session_id: str | None,
        requires_approval: bool = False,
        pending_approval_action: str | None = None,
    ) -> None:
        _ = error
        _ = codex_session_id
        _ = requires_approval
        _ = pending_approval_action
        self.finalized.append((task_id, status, summary))

    def update_project_state_after_task(self, **kwargs) -> None:  # noqa: ANN003
        _ = kwargs

    def update_repo_state(self, **kwargs) -> None:  # noqa: ANN003
        _ = kwargs

    def clear_project_session_state(self, *, project_id: int) -> None:
        self.cleared_project_session_ids.append(project_id)

    def get_latest_resumable_task(self, project_id: int) -> TaskRecord | None:
        _ = project_id
        return TaskRecord(
            id=1,
            command_type="do",
            original_request="old task",
            status="completed",
            codex_session_id="sess-1",
            latest_summary="ok",
        )

    def get_latest_task(self, project_id: int) -> TaskRecord | None:
        _ = project_id
        return self.latest_task

    def get_pending_approval(self, project_id: int) -> PendingApprovalRecord | None:
        _ = project_id
        return self.pending

    def resolve_approval(
        self,
        *,
        approval_id: int,
        status: str,
        decided_by_user_id: int,
        decision_note: str | None,
    ) -> None:
        _ = approval_id
        _ = status
        _ = decided_by_user_id
        _ = decision_note

    def mark_task_resumed_after_approval(self, task_id: int) -> None:
        _ = task_id

    def mark_task_waiting_approval(
        self,
        *,
        task_id: int,
        summary: str,
        pending_action: str,
        codex_session_id: str | None,
    ) -> None:
        _ = task_id
        _ = summary
        _ = pending_action
        _ = codex_session_id
        self.waiting_marked = True

    def create_approval_request(
        self,
        *,
        task_id: int,
        requested_action: str,
        requested_by_user_id: int,
        approval_kind: str = "codex_action",
    ) -> int:
        _ = requested_by_user_id
        _ = approval_kind
        self.pending = PendingApprovalRecord(
            task_id=task_id,
            approval_id=1,
            requested_action=requested_action,
            task_summary="等待审批",
            codex_session_id="sess-1",
        )
        return 1

    def reject_task(self, *, task_id: int, summary: str) -> None:
        _ = task_id
        _ = summary

    def add_project_note(self, *, project_id: int, content: str, title: str | None = None) -> None:
        _ = project_id
        _ = content
        _ = title

    def get_memory_snapshot(self, *, project_id: int):  # noqa: ANN001
        _ = project_id
        return SimpleNamespace(notes=[], recent_task_summaries=[], project_summary=None)

    def cancel_latest_active_task(self, project_id: int):  # noqa: ANN001
        _ = project_id
        return None

    def get_status_snapshot(self, user_id: int, chat_id: str | None = None):  # noqa: ANN001
        _ = user_id
        _ = chat_id
        return SimpleNamespace(
            active_project_key="demo",
            active_project_name="Demo",
            project_path="/tmp/demo",
            current_branch=None,
            repo_dirty=None,
            last_codex_session_id=None,
            most_recent_task_summary=None,
            recent_failed_summary=None,
            pending_approval=False,
            next_schedule_id=None,
            next_schedule_hhmm=None,
            next_step=None,
        )

    def create_scheduled_task(
        self,
        *,
        user_id: int,
        project_id: int,
        chat_id: str,
        command_type: str,
        request_text: str,
        minute_of_day: int,
    ) -> int:
        schedule_id = self.next_schedule_id
        self.next_schedule_id += 1
        self.scheduled_tasks.append(
            ScheduledTaskRecord(
                id=schedule_id,
                user_id=user_id,
                project_id=project_id,
                telegram_chat_id=chat_id,
                command_type=command_type,
                request_text=request_text,
                minute_of_day=minute_of_day,
                enabled=True,
                last_triggered_on=None,
                last_task_id=None,
                last_run_status=None,
                last_run_summary=None,
            )
        )
        return schedule_id

    def list_scheduled_tasks(self, project_id: int) -> list[ScheduledTaskRecord]:
        return [item for item in self.scheduled_tasks if item.project_id == project_id]

    def delete_scheduled_task(self, *, schedule_id: int, project_id: int) -> bool:
        for idx, item in enumerate(self.scheduled_tasks):
            if item.id == schedule_id and item.project_id == project_id:
                self.scheduled_tasks.pop(idx)
                return True
        return False

    def get_project_key_by_id(self, project_id: int) -> str | None:
        _ = project_id
        return "demo"

    def get_scheduled_task(self, *, schedule_id: int, project_id: int) -> ScheduledTaskRecord | None:
        for item in self.scheduled_tasks:
            if item.id == schedule_id and item.project_id == project_id:
                return item
        return None

    def set_scheduled_task_enabled(self, *, schedule_id: int, project_id: int, enabled: bool) -> bool:
        for idx, item in enumerate(self.scheduled_tasks):
            if item.id == schedule_id and item.project_id == project_id:
                self.scheduled_tasks[idx] = ScheduledTaskRecord(
                    id=item.id,
                    user_id=item.user_id,
                    project_id=item.project_id,
                    telegram_chat_id=item.telegram_chat_id,
                    command_type=item.command_type,
                    request_text=item.request_text,
                    minute_of_day=item.minute_of_day,
                    enabled=enabled,
                    last_triggered_on=item.last_triggered_on,
                    last_task_id=item.last_task_id,
                    last_run_status=item.last_run_status,
                    last_run_summary=item.last_run_summary,
                )
                return True
        return False

    def record_scheduled_task_run(
        self,
        *,
        schedule_id: int,
        task_id: int | None,
        status: str,
        summary: str,
    ) -> None:
        _ = schedule_id
        _ = task_id
        _ = status
        _ = summary

    def get_task_for_project(self, *, task_id: int, project_id: int) -> TaskRecord | None:
        _ = project_id
        if self.latest_task and self.latest_task.id == task_id:
            return self.latest_task
        return None


class SkillsStub:
    def __init__(self) -> None:
        self.listed = SkillsListResult(
            skills_root=Path("/tmp/.codex/skills"),
            skills=["android-pentest", "ios-pentest"],
            total_count=2,
            hidden_count=1,
            omitted_count=0,
        )
        self.install_result = SkillInstallResult(
            ok=True,
            source="openfish/skills/sample",
            summary="Skill 安装成功。",
            stdout="ok",
            stderr="",
            command=["codex", "skills", "install", "openfish/skills/sample"],
        )

    def list_skills(self, *, limit: int = 30) -> SkillsListResult:
        _ = limit
        return self.listed

    def install_skill(self, source: str) -> SkillInstallResult:
        self.install_result.source = source
        return self.install_result


class McpStub:
    def __init__(self) -> None:
        self.list_result = McpListResult(
            ok=True,
            summary="ok",
            servers=[
                McpServerSummary(
                    name="playwright",
                    enabled=True,
                    transport_type="stdio",
                    target="npx",
                    auth_status="unsupported",
                )
            ],
            stdout="",
            stderr="",
            command=["codex", "mcp", "list", "--json"],
        )
        self.detail_result = McpDetailResult(
            ok=True,
            summary="ok",
            detail=McpServerDetail(
                name="playwright",
                enabled=True,
                disabled_reason=None,
                transport_type="stdio",
                url=None,
                command="npx",
                args=["@playwright/mcp@latest"],
                cwd=None,
                bearer_token_env_var=None,
                auth_status="unsupported",
                startup_timeout_sec=None,
                tool_timeout_sec=None,
                enabled_tools=[],
                disabled_tools=[],
            ),
            stdout="",
            stderr="",
            command=["codex", "mcp", "get", "playwright", "--json"],
        )

    def list_servers(self) -> McpListResult:
        return self.list_result

    def get_server(self, name: str) -> McpDetailResult:
        _ = name
        return self.detail_result


def _build_router(
    tasks: TasksStub,
    audit: AuditStub,
    codex: CodexStub,
    skills: SkillsStub | None = None,
    mcp: McpStub | None = None,
) -> CommandRouter:
    config = SimpleNamespace(
        allowed_telegram_user_ids={"123"},
        default_project_root=Path("/tmp/openfish_projects"),
        enable_document_upload=True,
        max_upload_size_bytes=1024,
        upload_temp_dir_name=".codex_telegram_uploads",
        allowed_upload_extensions={"txt", "md", "json"},
    )
    return CommandRouter(
        config=config,
        projects=ProjectsStub(),
        tasks=tasks,
        audit=audit,
        codex=codex,
        repo=RepoStub(),
        approvals=ApprovalService(),
        skills_service=skills,
        mcp_service=mcp,
    )


def test_do_success_end_to_end_with_stubs() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("任务执行完成", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/do 修复 bug"))

    assert "任务 #1: 已完成" in result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.TASK_CREATED in codes
    assert audit_events.TASK_STARTED in codes
    assert audit_events.TASK_COMPLETED in codes


def test_do_enters_waiting_approval_branch() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("此步骤需要审批后继续", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/do 危险操作"))

    assert "等待审批" in result.reply_text
    assert tasks.waiting_marked is True
    codes = [event[0] for event in audit.events]
    assert audit_events.APPROVAL_REQUESTED in codes


def test_approve_resumes_pending_task() -> None:
    tasks = TasksStub()
    tasks.pending = PendingApprovalRecord(
        task_id=7,
        approval_id=99,
        requested_action="修改关键文件，需要审批",
        task_summary="等待审批",
        codex_session_id="sess-2",
    )
    audit = AuditStub()
    codex = CodexStub(
        run_result=_codex_result("unused", ok=True),
        resume_result=_codex_result("继续执行完成", ok=True),
    )
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/approve 继续"))

    assert "任务 #7: 已完成" in result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.APPROVAL_GRANTED in codes
    assert audit_events.TASK_COMPLETED in codes


def test_prepare_document_upload_rejects_too_large_file() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("ok", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.prepare_document_upload(
        _ctx("/upload"),
        original_name="a.txt",
        size_bytes=2048,
    )
    assert isinstance(result, CommandResult)
    assert "超过限制" in result.reply_text


def test_uploaded_document_runs_codex_analysis() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("文件分析完成", ok=True))
    router = _build_router(tasks, audit, codex)

    planned = router.prepare_document_upload(
        _ctx("/upload"),
        original_name="report.txt",
        size_bytes=100,
    )
    assert not isinstance(planned, CommandResult)

    result = router.handle_uploaded_document(
        ctx=_ctx("/upload"),
        plan=planned,
        caption="请总结风险",
    )

    assert "已完成" in result.reply_text
    assert tasks.last_command_type == "upload"
    assert codex.calls == ["ask"]
    codes = [event[0] for event in audit.events]
    assert audit_events.UPLOAD_RECEIVED in codes


def test_plain_text_routes_to_ask_when_project_selected() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("纯文本已按 ask 处理", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("帮我看下这个项目的结构"))

    assert "任务 #1: 已完成" in result.reply_text
    assert codex.calls == ["ask"]


def test_plain_text_without_active_project_returns_hint() -> None:
    tasks = TasksStub()
    tasks.active_project_key = None
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("你好"))

    assert "请先切换项目" in result.reply_text
    assert result.metadata == {"recent_projects": ["demo"]}


def test_projects_command_prioritizes_current_and_recent() -> None:
    tasks = TasksStub()
    tasks.ui_mode = "verbose"
    tasks.active_project_key = "demo"
    tasks.recent_project_keys = ["demo", "ops"]
    projects = ProjectsStub()
    projects.projects["ops"] = ProjectConfig(
        key="ops",
        name="Ops",
        path=Path("/tmp"),
        allowed_directories=[Path("/tmp")],
    )
    router = CommandRouter(
        SimpleNamespace(
            allowed_telegram_user_ids={"123"},
            enable_document_upload=True,
            max_upload_size_bytes=1024,
            allowed_upload_extensions={"txt"},
            upload_temp_dir_name=".tmp",
            default_project_root=Path("/tmp/openfish_projects"),
        ),
        projects,
        tasks,
        AuditStub(),
        CodexStub(_codex_result("unused", ok=True)),
        RepoStub(),
        ApprovalService(),
        skills_service=SkillsStub(),
        mcp_service=McpStub(),
    )

    result = router.handle(_ctx("/projects"))

    assert "当前项目: demo" in result.reply_text
    assert "最近使用:" in result.reply_text
    assert "- ops" in result.reply_text


def test_projects_command_uses_summary_ui_mode() -> None:
    tasks = TasksStub()
    tasks.ui_mode = "summary"
    tasks.recent_project_keys = ["demo", "ops"]
    projects = ProjectsStub()
    projects.projects["ops"] = ProjectConfig(
        key="ops",
        name="Ops",
        path=Path("/tmp"),
        allowed_directories=[Path("/tmp")],
    )
    router = CommandRouter(
        SimpleNamespace(
            allowed_telegram_user_ids={"123"},
            enable_document_upload=True,
            max_upload_size_bytes=1024,
            allowed_upload_extensions={"txt"},
            upload_temp_dir_name=".tmp",
            default_project_root=Path("/tmp/openfish_projects"),
        ),
        projects,
        tasks,
        AuditStub(),
        CodexStub(_codex_result("unused", ok=True)),
        RepoStub(),
        ApprovalService(),
        skills_service=SkillsStub(),
        mcp_service=McpStub(),
    )

    result = router.handle(_ctx("/projects"))

    assert "最近使用: ops" in result.reply_text
    assert "其他项目:" not in result.reply_text


def test_ui_command_sets_chat_mode() -> None:
    tasks = TasksStub()
    router = _build_router(tasks, AuditStub(), CodexStub(_codex_result("unused", ok=True)))

    result = router.handle(_ctx("/ui verbose"))

    assert result.reply_text == "界面模式已切换为: verbose"
    assert tasks.ui_mode == "verbose"


def test_help_command_uses_current_ui_mode() -> None:
    tasks = TasksStub()
    tasks.ui_mode = "summary"
    router = _build_router(tasks, AuditStub(), CodexStub(_codex_result("unused", ok=True)))

    result = router.handle(_ctx("/help"))

    assert "更多命令可用 /help verbose 查看。" in result.reply_text
    assert "/project-add <key> [abs_path] [name]" not in result.reply_text


def test_start_and_upload_policy_commands() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    start_result = router.handle(_ctx("/start"))
    policy_result = router.handle(_ctx("/upload_policy"))

    assert "快速开始" in start_result.reply_text
    assert "上传策略" in policy_result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.START_VIEWED in codes
    assert audit_events.UPLOAD_POLICY_VIEWED in codes


def test_last_returns_latest_task() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/last"))

    assert "最近任务: #9" in result.reply_text
    assert "类型: /do" in result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.TASK_LAST_VIEWED in codes


def test_retry_replays_latest_ask_task() -> None:
    tasks = TasksStub()
    tasks.latest_task = TaskRecord(
        id=11,
        command_type="ask",
        original_request="旧问题",
        status="failed",
        codex_session_id="sess-x",
        latest_summary="失败了",
    )
    audit = AuditStub()
    codex = CodexStub(_codex_result("重试成功", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/retry 补充要求"))

    assert "任务 #1: 已完成" in result.reply_text
    assert codex.calls == ["ask"]
    assert tasks.last_command_type == "ask"
    codes = [event[0] for event in audit.events]
    assert audit_events.TASK_RETRIED in codes


def test_retry_rejects_non_retryable_latest_task() -> None:
    tasks = TasksStub()
    tasks.latest_task = TaskRecord(
        id=12,
        command_type="upload",
        original_request="上传分析",
        status="failed",
        codex_session_id=None,
        latest_summary="失败",
    )
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/retry"))

    assert "仅支持重试 /ask 或 /do" in result.reply_text


def test_templates_and_run_template_commands() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("模板执行完成", ok=True))
    router = _build_router(tasks, audit, codex)

    templates_result = router.handle(_ctx("/templates"))
    run_result = router.handle(_ctx("/run quick-audit 重点看认证模块"))

    assert "可用模板" in templates_result.reply_text
    assert "任务 #1: 已完成" in run_result.reply_text
    assert codex.calls == ["ask"]
    assert any(".codex_telegram_uploads" in prompt for prompt in codex.prompts)
    codes = [event[0] for event in audit.events]
    assert audit_events.TEMPLATES_VIEWED in codes
    assert audit_events.TEMPLATE_RUN in codes


def test_queue_busy_returns_hint() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("ok", ok=True))
    router = _build_router(tasks, audit, codex)
    lock = router._get_project_lock(tasks.project_id)
    lock.acquire()
    try:
        result = router.handle(_ctx("/ask hello"))
    finally:
        lock.release()

    assert "已有任务在执行中" in result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.TASK_QUEUE_BLOCKED in codes


def test_skills_lists_installed_entries() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    skills = SkillsStub()
    router = _build_router(tasks, audit, codex, skills=skills)

    result = router.handle(_ctx("/skills"))

    assert "android-pentest" in result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.SKILLS_VIEWED in codes


def test_skill_install_success() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    skills = SkillsStub()
    router = _build_router(tasks, audit, codex, skills=skills)

    result = router.handle(_ctx("/skill-install openfish/skills/sample"))

    assert "Skill 安装: 成功" in result.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.SKILL_INSTALL_REQUESTED in codes
    assert audit_events.SKILL_INSTALLED in codes


def test_skill_install_without_argument() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex, skills=SkillsStub())

    result = router.handle(_ctx("/skill-install"))

    assert "用法: /skill-install <source>" in result.reply_text


def test_mcp_list_and_detail_commands() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    mcp = McpStub()
    router = _build_router(tasks, audit, codex, mcp=mcp)

    listed = router.handle(_ctx("/mcp"))
    detail = router.handle(_ctx("/mcp playwright"))

    assert "MCP 服务" in listed.reply_text
    assert "playwright" in listed.reply_text
    assert "MCP: playwright" in detail.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.MCP_VIEWED in codes


def test_schedule_add_list_delete() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    created = router.handle(_ctx("/schedule-add 09:30 ask 每日检查登录流程风险"))
    listed = router.handle(_ctx("/schedule-list"))
    deleted = router.handle(_ctx("/schedule-del 1"))

    assert "定期任务已创建: #1" in created.reply_text
    assert "#1 09:30 /ask [启用]" in listed.reply_text
    assert "已删除定期任务 #1" in deleted.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.SCHEDULE_CREATED in codes
    assert audit_events.SCHEDULE_VIEWED in codes
    assert audit_events.SCHEDULE_DELETED in codes


def test_schedule_add_invalid_usage() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/schedule-add 25:10 ask test"))
    assert "用法: /schedule-add" in result.reply_text


def test_run_scheduled_task_runs_codex() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("定期任务执行完成", ok=True))
    router = _build_router(tasks, audit, codex)

    scheduled = ScheduledTaskRecord(
        id=10,
        user_id=1,
        project_id=tasks.project_id,
        telegram_chat_id="1",
        command_type="ask",
        request_text="请总结今日风险",
        minute_of_day=60,
        enabled=True,
        last_triggered_on=None,
        last_task_id=None,
        last_run_status=None,
        last_run_summary=None,
    )
    result = router.run_scheduled_task(scheduled)

    assert codex.calls == ["ask"]
    assert result.metadata is not None
    assert result.metadata.get("status") == "completed"
    codes = [event[0] for event in audit.events]
    assert audit_events.SCHEDULE_TRIGGERED in codes


def test_schedule_run_and_toggle_commands() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("定时执行完成", ok=True))
    router = _build_router(tasks, audit, codex)
    _ = router.handle(_ctx("/schedule-add 10:00 do 每日修复"))

    paused = router.handle(_ctx("/schedule-pause 1"))
    enabled = router.handle(_ctx("/schedule-enable 1"))
    ran = router.handle(_ctx("/schedule-run 1"))

    assert "已暂停" in paused.reply_text
    assert "已启用" in enabled.reply_text
    assert "已触发定期任务 #1" in ran.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.SCHEDULE_TOGGLED in codes
    assert audit_events.SCHEDULE_MANUAL_RUN in codes


def test_resume_with_task_id_uses_resume_session() -> None:
    tasks = TasksStub()
    tasks.latest_task = TaskRecord(
        id=42,
        command_type="do",
        original_request="old task",
        status="failed",
        codex_session_id="sess-task-42",
        latest_summary="failed",
    )
    audit = AuditStub()
    codex = CodexStub(_codex_result("恢复成功", ok=True), resume_result=_codex_result("恢复成功", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/resume 42 继续并修复剩余问题"))

    assert "任务 #1: 已完成" in result.reply_text
    assert "resume_session" in codex.calls


def test_project_add_disable_archive_commands() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    added = router.handle(_ctx("/project-add demo2 /tmp Demo2"))
    disabled = router.handle(_ctx("/project-disable demo2"))
    archived = router.handle(_ctx("/project-archive demo"))

    assert "项目已新增并切换" in added.reply_text
    assert "项目已停用: demo2" in disabled.reply_text
    assert "项目已归档并停用: demo" in archived.reply_text
    assert tasks.cleared_project_session_ids == [tasks.project_id, tasks.project_id]
    codes = [event[0] for event in audit.events]
    assert audit_events.PROJECT_ADDED in codes
    assert audit_events.PROJECT_DISABLED in codes
    assert audit_events.PROJECT_ARCHIVED in codes


def test_project_add_reactivates_disabled_project() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    _ = router.handle(_ctx("/project-add demo2 /tmp Demo2"))
    _ = router.handle(_ctx("/project-disable demo2"))
    readded = router.handle(_ctx("/project-add demo2"))

    assert "项目已重新启用并切换" in readded.reply_text
    assert "项目: demo2" in readded.reply_text


def test_project_add_without_path_uses_default_root() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    result = router.handle(_ctx("/project-add demo_new Demo New"))

    assert "项目已新增并切换" in result.reply_text
    assert "目录来源: 默认根目录" in result.reply_text
    assert "/tmp/openfish_projects/demo_new" in result.reply_text


def test_project_add_without_path_requires_default_root() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)
    router.projects.default_project_root = None
    router.config.default_project_root = None

    result = router.handle(_ctx("/project-add demo_new"))

    assert "未设置默认项目根目录" in result.reply_text


def test_project_root_show_and_set() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    show = router.handle(_ctx("/project-root"))
    updated = router.handle(_ctx("/project-root /tmp/new_projects_root"))

    assert "默认项目根目录: /tmp/openfish_projects" in show.reply_text
    assert "默认项目根目录已设置:" in updated.reply_text
    assert "new_projects_root" in updated.reply_text
    codes = [event[0] for event in audit.events]
    assert audit_events.PROJECT_ROOT_UPDATED in codes
