from pathlib import Path
from types import SimpleNamespace

from src import audit_events
from src.approval import ApprovalService
from src.codex_runner import CodexRunResult
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
        self.project = ProjectConfig(
            key="demo",
            name="Demo",
            path=Path("/tmp"),
            allowed_directories=[Path("/tmp")],
        )

    def get(self, key: str) -> ProjectConfig | None:
        if key == "demo":
            return self.project
        return None

    def list_keys(self) -> list[str]:
        return ["demo"]

    def is_path_allowed(self, project: ProjectConfig, candidate_path: Path) -> bool:
        _ = project
        _ = candidate_path
        return True


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

    def run(self, project: ProjectConfig, prompt: str) -> CodexRunResult:
        _ = project
        _ = prompt
        self.calls.append("run")
        return self.run_result

    def ask(self, project: ProjectConfig, question: str) -> CodexRunResult:
        _ = project
        _ = question
        self.calls.append("ask")
        return self.run_result

    def resume_last(self, project: ProjectConfig, instruction: str) -> CodexRunResult:
        _ = project
        _ = instruction
        self.calls.append("resume_last")
        return self.resume_result


class TasksStub:
    def __init__(self) -> None:
        self.user = UserRecord(id=1, telegram_user_id="123")
        self.active_project_key = "demo"
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

    def ensure_user(self, ctx: CommandContext) -> UserRecord:
        _ = ctx
        return self.user

    def set_active_project(self, user_id: int, project_key: str, chat_id: str | None = None) -> None:
        _ = user_id
        _ = chat_id
        self.active_project_key = project_key

    def get_project_id(self, project_key: str) -> int:
        _ = project_key
        return self.project_id

    def get_active_project_key(self, user_id: int, chat_id: str | None = None) -> str | None:
        _ = user_id
        _ = chat_id
        return self.active_project_key

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
            pending_approval=False,
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


def _build_router(
    tasks: TasksStub,
    audit: AuditStub,
    codex: CodexStub,
    skills: SkillsStub | None = None,
) -> CommandRouter:
    config = SimpleNamespace(
        allowed_telegram_user_ids={"123"},
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

    assert "请先 /use <project>" in result.reply_text


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


def test_schedule_add_list_delete() -> None:
    tasks = TasksStub()
    audit = AuditStub()
    codex = CodexStub(_codex_result("unused", ok=True))
    router = _build_router(tasks, audit, codex)

    created = router.handle(_ctx("/schedule-add 09:30 ask 每日检查登录流程风险"))
    listed = router.handle(_ctx("/schedule-list"))
    deleted = router.handle(_ctx("/schedule-del 1"))

    assert "定期任务已创建: #1" in created.reply_text
    assert "#1 09:30 /ask" in listed.reply_text
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
