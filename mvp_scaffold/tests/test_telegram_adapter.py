import asyncio
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut
from telegram.request import HTTPXRequest

from src.autopilot_store import AutopilotRunRecord
from src.models import CommandResult, ProjectTemplatePreset
from src.telegram_adapter import TelegramBotService


class RouterStub:
    pass


class WizardTasksStub:
    def __init__(self) -> None:
        self.state = None
        self.ui_mode = None
        self.codex_model = None
        self.user = SimpleNamespace(id=1)
        self.active_project_key = "demo"
        self.recent_projects = ["demo", "ops"]
        self.recent_by_context = {
            ("1", "sending approval panel"): "10",
            ("1", "sending status result"): "10",
        }
        self.pending_approval = SimpleNamespace(
            approval_id=12,
            task_summary="等待审批",
        )
        self.scheduled_tasks = []
        self.pending_system_notifications = []
        self.autopilot = SimpleNamespace(list_runs_for_project=lambda project_id, limit=6: [])

    def ensure_user(self, ctx):  # noqa: ANN001
        _ = ctx
        return self.user

    def clear_chat_wizard_state(self, *, chat_id: str) -> None:
        _ = chat_id
        self.state = None

    def get_chat_wizard_state(self, *, chat_id: str):  # noqa: ANN201
        _ = chat_id
        return self.state

    def set_chat_wizard_state(self, *, chat_id: str, user_id: int, state: dict) -> None:
        _ = chat_id
        _ = user_id
        self.state = state

    def get_active_project_key(self, user_id: int, chat_id: str | None = None) -> str | None:
        _ = user_id
        _ = chat_id
        return self.active_project_key

    def get_project_id(self, project_key: str) -> int:
        _ = project_key
        return 101

    def get_pending_approval(self, project_id: int, approval_id: int | None = None):  # noqa: ANN201
        _ = project_id
        if approval_id is not None and self.pending_approval is not None:
            if int(self.pending_approval.approval_id) != approval_id:
                return None
        return self.pending_approval

    def get_recent_outbound_message_id_by_context(
        self,
        *,
        chat_id: str,
        context: str,
        max_age_seconds: float,
    ) -> str | None:
        _ = max_age_seconds
        return self.recent_by_context.get((chat_id, context))

    def list_recent_project_keys(self, *, user_id: int, limit: int = 6) -> list[str]:
        _ = user_id
        return self.recent_projects[:limit]

    def get_chat_ui_mode(self, *, chat_id: str) -> str | None:
        _ = chat_id
        return self.ui_mode

    def set_chat_ui_mode(self, *, chat_id: str, user_id: int, mode: str) -> None:
        _ = chat_id
        _ = user_id
        self.ui_mode = mode

    def get_chat_codex_model(self, *, chat_id: str) -> str | None:
        _ = chat_id
        return self.codex_model

    def set_chat_codex_model(self, *, chat_id: str, user_id: int, model: str) -> None:
        _ = chat_id
        _ = user_id
        self.codex_model = model

    def clear_chat_codex_model(self, *, chat_id: str) -> None:
        _ = chat_id
        self.codex_model = None

    def get_status_snapshot(self, user_id: int, chat_id: str | None = None):  # noqa: ANN201
        _ = user_id
        _ = chat_id
        return SimpleNamespace(
            active_project_key=None,
            pending_approval=False,
            most_recent_task_summary=None,
        )

    def list_scheduled_tasks(self, project_id: int):  # noqa: ANN201
        _ = project_id
        return self.scheduled_tasks

    def list_pending_system_notifications(self, *, limit: int = 32):  # noqa: ANN201
        return self.pending_system_notifications[:limit]

    def delete_system_notification(self, *, notification_id: int) -> None:
        self.pending_system_notifications = [
            item for item in self.pending_system_notifications if item.id != notification_id
        ]


class WizardRouterStub:
    def __init__(self) -> None:
        self.tasks = WizardTasksStub()
        self.projects = SimpleNamespace(
            default_project_root=Path("/tmp/projects"),
            list_project_templates=lambda: [
                ProjectTemplatePreset(
                    key="recon",
                    name="自动化信息收集",
                    path=Path("/tmp/project_templates/recon"),
                    description="信息收集目录模板",
                    default_autopilot_goal="对目标进行自动化信息收集",
                )
            ],
        )
        latest_run = AutopilotRunRecord(
            id=1,
            project_id=101,
            chat_id="1",
            created_by_user_id=1,
            goal="持续推进支付修复",
            status="running_worker",
            supervisor_session_id="sess-a",
            worker_session_id="sess-b",
            current_phase="worker",
            cycle_count=2,
            max_cycles=100,
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="run tests next",
            last_decision="continue",
            last_worker_summary="已修改支付回调",
            last_supervisor_summary="继续测试",
            paused_reason=None,
            stopped_by_user_id=None,
        )
        self.autopilot = SimpleNamespace(
            get_latest_run_for_project=lambda project_id: latest_run,
            list_runs_for_project=lambda project_id, limit=6: [latest_run][:limit],
        )

    def handle(self, ctx):  # noqa: ANN001, ANN201
        _ = ctx
        return SimpleNamespace(reply_text="ok", metadata=None)


class UploadRouterStub:
    def __init__(self) -> None:
        self.handle_called = False

    def prepare_document_upload(self, ctx, *, original_name: str, size_bytes: int):  # noqa: ANN001, ANN003
        _ = ctx
        return SimpleNamespace(
            active=SimpleNamespace(project=SimpleNamespace(path=Path("/tmp"))),
            original_name=original_name,
            size_bytes=size_bytes,
            local_path=Path("/tmp/upload.bin"),
        )

    def handle_uploaded_document(self, **kwargs):  # noqa: ANN003
        _ = kwargs
        self.handle_called = True
        return SimpleNamespace(reply_text="ok")


class MessageStub:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent_texts: list[str] = []
        self.sent_markups: list[object] = []
        self.sent_documents: list[dict[str, object]] = []
        self.edit_reply_markup_calls: list[object] = []
        self.chat_actions: list[str] = []
        self.message_id = 10

    async def reply_text(self, text: str, **kwargs):  # noqa: ANN003
        self.sent_markups.append(kwargs.get("reply_markup"))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.sent_texts.append(text)
        return outcome

    async def reply_chat_action(self, action: str, **kwargs):  # noqa: ANN003
        _ = kwargs
        self.chat_actions.append(action)
        return object()

    async def reply_document(self, document, **kwargs):  # noqa: ANN003
        payload = document.read()
        self.sent_documents.append(
            {
                "payload": payload,
                "filename": kwargs.get("filename"),
                "caption": kwargs.get("caption"),
            }
        )
        return object()

    async def edit_reply_markup(self, **kwargs):  # noqa: ANN003
        self.edit_reply_markup_calls.append(kwargs.get("reply_markup"))
        return object()


class AppStub:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.handlers = []
        self.error_handlers = []
        self.run_polling_kwargs = None

    def add_handler(self, handler) -> None:  # noqa: ANN001
        self.handlers.append(handler)

    def add_error_handler(self, handler) -> None:  # noqa: ANN001
        self.error_handlers.append(handler)

    def run_polling(self, **kwargs) -> None:  # noqa: ANN003
        self.run_polling_kwargs = kwargs
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome


class DocumentTooBigStub:
    file_name = "large.bin"
    file_size = 999_999_999

    async def get_file(self):  # noqa: ANN201
        raise BadRequest("File is too big")


def _service() -> TelegramBotService:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    return TelegramBotService(config=config, router=RouterStub())


def test_safe_reply_success() -> None:
    service = _service()
    message = MessageStub([object()])

    ok = asyncio.run(service._safe_reply_text(message, "hello", context="test"))

    assert ok is True
    assert message.sent_texts == ["hello"]


def test_resolve_callback_command_supports_home() -> None:
    service = _service()

    assert service._resolve_callback_command("home") == "/home"
    assert service._resolve_callback_command("context") == "/context"


def test_safe_reply_retries_after_timeout(monkeypatch) -> None:
    service = _service()
    message = MessageStub([TimedOut(), object()])

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("src.telegram_adapter.asyncio.sleep", _no_sleep)
    ok = asyncio.run(service._safe_reply_text(message, "hello", context="test"))

    assert ok is True
    assert message.sent_texts == ["hello"]


def test_safe_reply_returns_false_on_telegram_error() -> None:
    service = _service()
    message = MessageStub([TelegramError("bad request")])

    ok = asyncio.run(service._safe_reply_text(message, "hello", context="test"))

    assert ok is False
    assert message.sent_texts == []


def test_run_polling_retries_on_network_error(monkeypatch) -> None:
    service = _service()
    first_app = AppStub([NetworkError("connect")])
    second_app = AppStub([None])
    apps = [first_app, second_app]

    monkeypatch.setattr(service, "_build_application", lambda: apps.pop(0))
    monkeypatch.setattr("src.telegram_adapter.time.sleep", lambda _: None)

    service.run_polling()

    assert apps == []
    assert len(first_app.error_handlers) == 1
    assert len(second_app.error_handlers) == 1
    assert first_app.run_polling_kwargs["bootstrap_retries"] == -1
    assert second_app.run_polling_kwargs["bootstrap_retries"] == -1


def test_build_application_uses_dedicated_requests(monkeypatch) -> None:
    service = _service()
    captured: dict[str, object] = {}

    class FakeBuilder:
        def token(self, value):  # noqa: ANN001, ANN201
            captured["token"] = value
            return self

        def request(self, value):  # noqa: ANN001, ANN201
            captured["request"] = value
            return self

        def get_updates_request(self, value):  # noqa: ANN001, ANN201
            captured["get_updates_request"] = value
            return self

        def post_init(self, value):  # noqa: ANN001, ANN201
            captured["post_init"] = value
            return self

        def build(self):  # noqa: ANN201
            return "app"

    monkeypatch.setattr("src.telegram_adapter.ApplicationBuilder", lambda: FakeBuilder())

    app = service._build_application()

    assert app == "app"
    assert captured["token"] == "dummy"
    assert isinstance(captured["request"], HTTPXRequest)
    assert isinstance(captured["get_updates_request"], HTTPXRequest)


def test_build_application_registers_post_init(monkeypatch) -> None:
    service = _service()
    captured: dict[str, object] = {}

    class FakeBuilder:
        def token(self, value):  # noqa: ANN001, ANN201
            _ = value
            return self

        def request(self, value):  # noqa: ANN001, ANN201
            _ = value
            return self

        def get_updates_request(self, value):  # noqa: ANN001, ANN201
            _ = value
            return self

        def post_init(self, value):  # noqa: ANN001, ANN201
            captured["post_init"] = value
            return self

        def build(self):  # noqa: ANN201
            return "app"

    monkeypatch.setattr("src.telegram_adapter.ApplicationBuilder", lambda: FakeBuilder())

    service._build_application()

    assert captured["post_init"] == service._on_post_init


def test_deliver_pending_system_notifications_sends_and_clears() -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.update_service = SimpleNamespace(
        get_current_version=lambda: SimpleNamespace(version="v1.0.0", commit="abc1234")
    )
    router.tasks.pending_system_notifications = [
        SimpleNamespace(id=1, telegram_chat_id="1", notification_kind="restart_completed"),
        SimpleNamespace(id=2, telegram_chat_id="2", notification_kind="update_completed"),
    ]
    service = TelegramBotService(config=config, router=router)
    sent: list[tuple[str, str, object]] = []

    class BotStub:
        async def send_message(self, *, chat_id, text, reply_markup=None):  # noqa: ANN001
            sent.append((chat_id, text, reply_markup))
            return object()

    asyncio.run(service._deliver_pending_system_notifications(SimpleNamespace(bot=BotStub())))

    assert len(sent) == 2
    assert sent[0][0] == "1"
    assert "已重启完成" in sent[0][1]
    assert "v1.0.0" in sent[0][1]
    assert sent[1][0] == "2"
    assert "已更新并重启完成" in sent[1][1]
    assert router.tasks.pending_system_notifications == []


def test_menu_text_maps_to_status_command() -> None:
    service = _service()
    assert service._map_menu_to_command("状态") == "/status"
    assert service._map_menu_to_command("帮助") == "/help"
    assert service._map_menu_to_command("提问") == "__ask__"
    assert service._map_menu_to_command("更多") == "__more__"
    assert service._map_menu_to_command("unknown") is None


def test_callback_token_maps_to_command() -> None:
    service = _service()
    assert service._resolve_callback_command("status") == "/status"
    assert service._resolve_callback_command("task_current") == "/task-current"
    assert service._resolve_callback_command("mcp") == "/mcp"
    assert service._resolve_callback_command("sessions") == "/sessions"
    assert service._resolve_callback_command("tasks") == "/tasks"
    assert service._resolve_callback_command("tasks_clear") == "/tasks-clear"
    assert service._resolve_callback_command("model") == "/model"
    assert service._resolve_callback_command("version") == "/version"
    assert service._resolve_callback_command("update_check") == "/update-check"
    assert service._resolve_callback_command("update") == "/update"
    assert service._resolve_callback_command("restart") == "/restart"
    assert service._resolve_callback_command("logs") == "/logs"
    assert service._resolve_callback_command("logs_clear") == "/logs-clear"
    assert service._resolve_callback_command("project_disable_current") == "/project-disable"
    assert service._resolve_callback_command("ui_summary") == "/ui summary"
    assert service._resolve_callback_command("ui_stream") == "/ui stream"
    assert service._resolve_callback_command("missing") is None


def test_document_upload_handles_telegram_file_too_big(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = UploadRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_text(message, text: str, *, context: str, reply_markup=None) -> bool:  # noqa: ANN001
        _ = message
        _ = context
        _ = reply_markup
        sent_texts.append(text)
        return True

    monkeypatch.setattr(service, "_send_text", fake_send_text)
    update = SimpleNamespace(
        effective_message=SimpleNamespace(
            document=DocumentTooBigStub(),
            caption="/upload",
            message_id=10,
        ),
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_document_message(update, SimpleNamespace()))

    assert any("文件过大" in text for text in sent_texts)
    assert router.handle_called is False


def test_activate_project_add_wizard_persists_state(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._activate_prompt(object(), ctx, "project_add"))

    assert router.tasks.state == {"kind": "project_add", "step": "key", "data": {}}
    assert any("项目新增向导 1/7" in text for text in sent_texts)


def test_activate_approve_prompt_starts_approval_note_wizard(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._activate_prompt(object(), ctx, "approve"))

    assert router.tasks.state == {
        "kind": "approve_note",
        "step": "note",
        "data": {"approval_id": 12, "task_summary": "等待审批"},
    }
    assert any("批准向导 1/2" in text for text in sent_texts)


def test_activate_explicit_approve_prompt_uses_matching_approval_id(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._activate_prompt(object(), ctx, "approve:12"))

    assert router.tasks.state == {
        "kind": "approve_note",
        "step": "note",
        "data": {"approval_id": 12, "task_summary": "等待审批"},
    }
    assert any("批准向导 1/2" in text for text in sent_texts)


def test_activate_explicit_approve_prompt_rejects_stale_approval_id(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_text(message, text: str, *, context: str, reply_markup=None) -> bool:  # noqa: ANN001
        _ = message
        _ = context
        _ = reply_markup
        sent_texts.append(text)
        return True

    monkeypatch.setattr(service, "_send_text", fake_send_text)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._activate_prompt(object(), ctx, "approve:99"))

    assert any("审批 #99 不存在" in text for text in sent_texts)


def test_project_add_path_step_has_default_button() -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)

    markup = service._wizard_markup({"kind": "project_add", "step": "path", "data": {"key": "demo"}})
    rows = markup.inline_keyboard

    assert rows[0][0].text == "默认目录"
    assert rows[0][0].callback_data == "wizard:default"
    assert rows[0][1].text == "取消"


def test_wizard_default_callback_advances_project_add(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.tasks.state = {"kind": "project_add", "step": "path", "data": {"key": "demo"}}
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    message = MessageStub([object()])
    asyncio.run(service._handle_wizard_callback(message, ctx, "wizard:default"))

    assert router.tasks.state == {
        "kind": "project_add",
        "step": "template",
        "data": {"key": "demo", "path": ""},
    }
    assert message.edit_reply_markup_calls == [None]
    assert any("项目新增向导 3/7" in text for text in sent_texts)


def test_project_add_path_step_accepts_quoted_default(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.tasks.state = {"kind": "project_add", "step": "path", "data": {"key": "demo"}}
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    message = MessageStub([object()])
    handled = asyncio.run(service._handle_wizard_input(message, ctx, router.tasks.state, "“默认”"))

    assert handled is True
    assert router.tasks.state == {
        "kind": "project_add",
        "step": "template",
        "data": {"key": "demo", "path": ""},
    }
    assert any("项目新增向导 3/7" in text for text in sent_texts)


def test_on_text_message_treats_absolute_path_as_project_wizard_input(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.tasks.state = {"kind": "project_add", "step": "path", "data": {"key": "demo"}}
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    message = MessageStub([object()])
    message.text = "/workspace/projects/test1"
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_text_message(update, SimpleNamespace()))

    assert router.tasks.state == {
        "kind": "project_add",
        "step": "template",
        "data": {"key": "demo", "path": "/workspace/projects/test1"},
    }
    assert any("项目新增向导 3/7" in text for text in sent_texts)


def test_project_add_template_step_accepts_template_key() -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)

    next_state = service._advance_wizard_state(
        {"kind": "project_add", "step": "template", "data": {"key": "demo", "path": ""}},
        "recon",
    )

    assert next_state == {
        "kind": "project_add",
        "step": "mode",
        "data": {
            "key": "demo",
            "path": "",
            "template_name": "recon",
            "autopilot_goal": "对目标进行自动化信息收集",
        },
    }


def test_project_add_mode_autopilot_skips_goal_when_template_has_default_goal() -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)

    next_state = service._advance_wizard_state(
        {
            "kind": "project_add",
            "step": "mode",
            "data": {
                "key": "demo",
                "path": "",
                "template_name": "recon",
                "autopilot_goal": "对目标进行自动化信息收集",
            },
        },
        "autopilot",
    )

    assert next_state == {
        "kind": "project_add",
        "step": "name",
        "data": {
            "key": "demo",
            "path": "",
            "template_name": "recon",
            "autopilot_goal": "对目标进行自动化信息收集",
            "default_run_mode": "autopilot",
        },
    }


def test_project_add_wizard_command_includes_template_and_autopilot_flags() -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)

    command = service._wizard_command(
        {
            "kind": "project_add",
            "data": {
                "key": "demo",
                "path": "/workspace/projects/demo",
                "template_name": "recon",
                "default_run_mode": "autopilot",
                "autopilot_goal": "收集域名和子域名",
                "name": "Demo Recon",
            },
        }
    )

    assert command.startswith("/project-add demo ")
    assert "--template recon" in command
    assert "--mode autopilot" in command
    assert "--autopilot-goal '收集域名和子域名'" in command or '--autopilot-goal "收集域名和子域名"' in command


def test_reject_note_wizard_preset_advances_to_confirm(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.tasks.state = {
        "kind": "reject_note",
        "step": "note",
        "data": {"approval_id": 12, "task_summary": "等待审批"},
    }
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        sent_texts.append(spec.text)
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )
    message = MessageStub([object()])

    asyncio.run(service._handle_wizard_callback(message, ctx, "wizard:preset:风险太高"))

    assert router.tasks.state == {
        "kind": "reject_note",
        "step": "confirm",
        "data": {"approval_id": 12, "task_summary": "等待审批", "note": "风险太高"},
    }
    assert any("拒绝向导 2/2" in text for text in sent_texts)


def test_approve_note_wizard_confirm_executes_explicit_approval_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.tasks.state = {
        "kind": "approve_note",
        "step": "confirm",
        "data": {"approval_id": 12, "task_summary": "等待审批", "note": "继续执行"},
    }
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )
    message = MessageStub([object()])

    asyncio.run(service._handle_wizard_callback(message, ctx, "wizard:confirm"))

    assert executed == ["/approve 12 继续执行"]


def test_wizard_callback_with_missing_state_reports_expired(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_text(message, text: str, *, context: str, reply_markup=None) -> bool:  # noqa: ANN001
        _ = message
        _ = context
        _ = reply_markup
        sent_texts.append(text)
        return True

    monkeypatch.setattr(service, "_send_text", fake_send_text)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="chat-1",
        telegram_message_id="1",
        telegram_username="owner",
        telegram_display_name="Owner",
    )
    message = MessageStub([object()])

    asyncio.run(service._handle_wizard_callback(message, ctx, "wizard:confirm"))

    assert message.edit_reply_markup_calls == [None]
    assert any("按钮可能已过期" in text for text in sent_texts)


def test_more_panel_contains_ui_mode_buttons(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    captured = {}

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        captured["markup"] = spec.reply_markup
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)

    asyncio.run(service._send_more_panel(object()))

    markup = captured["markup"]
    rows = markup.inline_keyboard
    assert any(button.callback_data == "panel:service" for row in rows for button in row)
    assert any(button.callback_data == "panel:autopilot" for row in rows for button in row)
    assert any(button.callback_data == "prompt:autopilot" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_status" for row in rows for button in row)
    assert any(button.callback_data == "cmd:sessions" for row in rows for button in row)
    assert any(button.callback_data == "cmd:tasks" for row in rows for button in row)
    assert any(button.callback_data == "cmd:task_current" for row in rows for button in row)
    assert any(button.callback_data == "cmd:tasks_clear" for row in rows for button in row)
    assert any(button.callback_data == "panel:model" for row in rows for button in row)
    assert any(button.callback_data == "cmd:restart" for row in rows for button in row)
    assert any(button.callback_data == "cmd:logs" for row in rows for button in row)
    assert any(button.callback_data == "cmd:logs_clear" for row in rows for button in row)
    assert any(button.callback_data == "cmd:ui_summary" for row in rows for button in row)
    assert any(button.callback_data == "cmd:ui_stream" for row in rows for button in row)
    assert any(button.callback_data == "cmd:ui_verbose" for row in rows for button in row)


def test_send_service_panel_marks_panel_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)

    asyncio.run(service._send_service_panel(MessageStub([])))

    spec = captured["spec"]
    assert spec.context == "sending service panel"
    assert spec.edit_context == "sending service panel"
    assert spec.edit_window_seconds == 300.0


def test_send_autopilot_panel_marks_panel_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._send_autopilot_panel(MessageStub([]), ctx))

    spec = captured["spec"]
    assert spec.context == "sending autopilot panel"
    assert spec.edit_context == "sending autopilot panel"
    assert spec.edit_window_seconds == 300.0
    assert "最新 Run: #1" in spec.text
    assert "最近 runs:" in spec.text


def test_send_autopilot_panel_shows_recent_runs_when_available(monkeypatch) -> None:
    router = WizardRouterStub()
    router.autopilot = SimpleNamespace(
        get_latest_run_for_project=lambda project_id: AutopilotRunRecord(
            id=1,
            project_id=project_id,
            chat_id="1",
            created_by_user_id=1,
            goal="持续推进支付修复",
            status="running_worker",
            supervisor_session_id="sess-a",
            worker_session_id="sess-b",
            current_phase="worker",
            cycle_count=2,
            max_cycles=100,
            no_progress_cycles=0,
            same_instruction_cycles=0,
            last_instruction_fingerprint="run tests next",
            last_decision="continue",
            last_worker_summary="已修改支付回调",
            last_supervisor_summary="继续测试",
            paused_reason=None,
            stopped_by_user_id=None,
        ),
        list_runs_for_project=lambda project_id, limit=6: [
            AutopilotRunRecord(
                id=1,
                project_id=project_id,
                chat_id="1",
                created_by_user_id=1,
                goal="持续推进支付修复",
                status="running_worker",
                supervisor_session_id="sess-a",
                worker_session_id="sess-b",
                current_phase="worker",
                cycle_count=2,
                max_cycles=100,
                no_progress_cycles=0,
                same_instruction_cycles=0,
                last_instruction_fingerprint="run tests next",
                last_decision="continue",
                last_worker_summary="已修改支付回调",
                last_supervisor_summary="继续测试",
                paused_reason=None,
                stopped_by_user_id=None,
            ),
            AutopilotRunRecord(
                id=2,
                project_id=project_id,
                chat_id="1",
                created_by_user_id=1,
                goal="分析告警",
                status="paused",
                supervisor_session_id="sess-c",
                worker_session_id="sess-d",
                current_phase="idle",
                cycle_count=1,
                max_cycles=100,
                no_progress_cycles=0,
                same_instruction_cycles=0,
                last_instruction_fingerprint="check warnings",
                last_decision="continue",
                last_worker_summary="已分析一轮",
                last_supervisor_summary="继续检查",
                paused_reason="manual pause",
                stopped_by_user_id=None,
            ),
        ][:limit]
    )
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=router,
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._send_autopilot_panel(MessageStub([]), ctx))

    spec = captured["spec"]
    assert "最近 runs:" in spec.text
    assert "- #2 · paused · 1/100" in spec.text


def test_send_model_panel_contains_model_buttons(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
        codex_model_choices=("gpt-5.4", "gpt-5", "o3"),
    )
    router = WizardRouterStub()
    router.tasks.codex_model = "o3"
    service = TelegramBotService(config=config, router=router)
    captured = {}

    async def fake_send_view_spec(message, spec, *, context: str, command=None, edit_context=None, edit_window_seconds=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
        _ = edit_context
        _ = edit_window_seconds
        captured["text"] = spec.text
        captured["markup"] = spec.reply_markup
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)

    ctx = SimpleNamespace(telegram_chat_id="1")
    asyncio.run(service._send_model_panel(object(), ctx))

    assert "当前: o3" in captured["text"]
    rows = captured["markup"].inline_keyboard
    assert any(button.callback_data == "model:set:o3" for row in rows for button in row)
    assert any(button.callback_data == "model:reset" for row in rows for button in row)


def test_approval_callback_clears_inline_keyboard(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(
        message=message,
        data="approval:approve:12",
        answer=lambda *args, **kwargs: None,
    )

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/approve 12"]
    assert message.edit_reply_markup_calls == [None]


def test_expired_approval_callback_is_rejected(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    router.tasks.recent_by_context = {("1", "sending approval panel"): "99"}
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []
    sent_texts: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    async def fake_send_text(message, text: str, *, context: str, reply_markup=None) -> bool:  # noqa: ANN001
        _ = message
        _ = context
        _ = reply_markup
        sent_texts.append(text)
        return True

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    monkeypatch.setattr(service, "_send_text", fake_send_text)
    message = MessageStub([object()])
    message.message_id = 10
    query = SimpleNamespace(
        message=message,
        data="approval:approve:12",
        answer=lambda *args, **kwargs: None,
    )

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == []
    assert any("已过期" in text for text in sent_texts)


def test_memory_page_callback_executes_memory_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(
        message=message,
        data="memory:page:2",
    )

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/memory 2"]


def test_sessions_page_callback_executes_sessions_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="sessions:page:2")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/sessions 2"]


def test_tasks_page_callback_executes_tasks_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="tasks:page:2")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/tasks 2"]


def test_task_cancel_callback_executes_cancel_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="task:cancel:12")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/task-cancel 12"]


def test_task_delete_callback_executes_delete_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="task:delete:12")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/task-delete 12"]


def test_session_detail_callback_executes_session_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="cmd:session_detail:sess-native-1")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/session sess-native-1"]


def test_session_import_callback_executes_import_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="session:import:sess-native-1")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/session-import sess-native-1"]


def test_model_set_callback_executes_model_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="model:set:o3")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/model set o3"]


def test_mcp_disable_callback_executes_toggle_command(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    executed: list[str] = []

    async def fake_execute_command(message, base_ctx, text: str) -> None:  # noqa: ANN001
        _ = message
        _ = base_ctx
        executed.append(text)

    monkeypatch.setattr(service, "_execute_command", fake_execute_command)
    message = MessageStub([object()])
    query = SimpleNamespace(message=message, data="mcp:disable:playwright")

    async def fake_answer(*args, **kwargs):  # noqa: ANN003
        _ = args
        _ = kwargs
        return None

    query.answer = fake_answer
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_callback_query(update, SimpleNamespace()))

    assert executed == ["/mcp-disable playwright"]


def test_send_command_result_splits_long_text() -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=120,
        ),
        router=WizardRouterStub(),
    )
    message = MessageStub([object(), object(), object()])
    ctx = SimpleNamespace(telegram_chat_id="1", telegram_user_id="123")
    result = CommandResult(
        "项目: demo\n任务 #1: 已完成\n摘要:\n" + ("A" * 220)
    )

    ok = asyncio.run(service._send_command_result(message, "/do", ctx, result))

    assert ok is True
    assert len(message.sent_texts) >= 2
    assert message.sent_texts[0].startswith("项目: demo")
    assert any(text.startswith("续 2/") for text in message.sent_texts[1:])


def test_execute_command_sends_typing_for_long_running_command() -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    message = MessageStub([object(), object()])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._execute_command(message, ctx, "/do fix issue"))

    assert message.chat_actions == ["typing"]


def test_on_text_message_sends_typing_for_prompted_long_running_command() -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    service._pending_command_by_chat["1"] = "/do"
    message = MessageStub([object(), object()])
    message.text = "fix issue"
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_text_message(update, SimpleNamespace()))

    assert message.chat_actions == ["typing"]


def test_on_text_message_sends_typing_for_plain_text_question() -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    message = MessageStub([object(), object()])
    message.text = "帮我看看这个报错"
    update = SimpleNamespace(
        effective_message=message,
        effective_user=SimpleNamespace(id=123, username="owner", full_name="Owner"),
        effective_chat=SimpleNamespace(id=1),
    )

    asyncio.run(service._on_text_message(update, SimpleNamespace()))

    assert message.chat_actions == ["typing"]


def test_dispatch_router_command_keeps_typing_heartbeat_during_wait() -> None:
    class SlowRouterStub(WizardRouterStub):
        def handle(self, ctx):  # noqa: ANN001, ANN201
            _ = ctx
            time.sleep(2.2)
            return SimpleNamespace(reply_text="ok", metadata=None)

    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
            telegram_typing_heartbeat_seconds=1.0,
        ),
        router=SlowRouterStub(),
    )
    message = MessageStub([])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
        text="/do fix issue",
    )

    asyncio.run(service._dispatch_router_command(message, ctx, command="/do"))

    assert len(message.chat_actions) >= 2


def test_dispatch_router_command_stream_mode_sends_progress_updates(monkeypatch) -> None:
    class SlowRouterStub(WizardRouterStub):
        def handle(self, ctx):  # noqa: ANN001, ANN201
            if getattr(ctx, "progress_callback", None):
                ctx.progress_callback("stdout", "Reading package.json")
            _ = ctx
            time.sleep(1.3)
            return SimpleNamespace(reply_text="ok", metadata=None)

    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
            telegram_typing_heartbeat_seconds=1.0,
            telegram_stream_phase_delay_seconds=0.2,
            telegram_stream_heartbeat_seconds=2.0,
        ),
        router=SlowRouterStub(),
    )
    service.router.tasks.ui_mode = "stream"
    captured: list[tuple[str, str, str | None]] = []

    async def fake_send(message, spec):  # noqa: ANN001, ANN202
        captured.append((spec.context, spec.text, spec.edit_context))
        return True

    async def fake_delete(message, *, context: str, max_age_seconds: float):  # noqa: ANN001, ANN202
        _ = message
        _ = max_age_seconds
        captured.append(("delete", context, None))
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    monkeypatch.setattr(service.sink, "delete_recent_message_by_context", fake_delete)
    message = MessageStub([])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
        text="/do fix issue",
    )

    asyncio.run(service._dispatch_router_command(message, ctx, command="/do"))

    progress_contexts = {context for context, _, edit_context in captured if edit_context}
    assert len(progress_contexts) == 1
    progress_context = next(iter(progress_contexts))
    assert any("当前阶段" in text for _, text, _ in captured)
    assert any("Reading package.json" in text for _, text, _ in captured)
    assert ("delete", progress_context, None) in captured


def test_execute_command_skips_typing_for_fast_status_command() -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    message = MessageStub([object()])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._execute_command(message, ctx, "/status"))

    assert message.chat_actions == []


def test_send_command_result_marks_status_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    message = MessageStub([])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(
        service._send_command_result(
            message,
            "/status",
            ctx,
            SimpleNamespace(reply_text="status", metadata={"recent_projects": []}),
        )
    )

    spec = captured["spec"]
    assert spec.context == "sending status result"
    assert spec.edit_context == "sending status result"
    assert spec.edit_window_seconds == 300.0


def test_send_command_result_sends_local_file_document(tmp_path) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    file_path = tmp_path / "demo.txt"
    file_path.write_text("hello file", encoding="utf-8")
    message = MessageStub([])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    ok = asyncio.run(
        service._send_command_result(
            message,
            "/download-file",
            ctx,
            CommandResult(
                f"下载文件: {file_path.name}\n路径: {file_path}",
                metadata={"send_local_file": {"path": str(file_path), "name": file_path.name}},
            ),
        )
    )

    assert ok is True
    assert len(message.sent_documents) == 1
    assert message.sent_documents[0]["filename"] == "demo.txt"
    assert message.sent_documents[0]["payload"] == b"hello file"


def test_send_command_result_marks_projects_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    message = MessageStub([])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(
        service._send_command_result(
            message,
            "/projects",
            ctx,
            SimpleNamespace(reply_text="projects", metadata={"recent_projects": []}),
        )
    )

    spec = captured["spec"]
    assert spec.context == "sending projects panel"
    assert spec.edit_context == "sending projects panel"
    assert spec.edit_window_seconds == 300.0


def test_send_command_result_marks_schedule_list_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    message = MessageStub([])
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(
        service._send_command_result(
            message,
            "/schedule-list",
            ctx,
            SimpleNamespace(reply_text="schedules", metadata={}),
        )
    )

    spec = captured["spec"]
    assert spec.context == "sending schedule panel"
    assert spec.edit_context == "sending schedule panel"
    assert spec.edit_window_seconds == 300.0


def test_send_approval_panel_marks_panel_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)
    ctx = SimpleNamespace(
        telegram_user_id="123",
        telegram_chat_id="1",
        telegram_message_id="10",
        telegram_username="owner",
        telegram_display_name="Owner",
    )

    asyncio.run(service._send_approval_panel(MessageStub([]), ctx))

    spec = captured["spec"]
    assert spec.context == "sending approval panel"
    assert spec.edit_context == "sending approval panel"
    assert spec.edit_window_seconds == 300.0


def test_send_more_panel_marks_panel_for_editing(monkeypatch) -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    captured = {}

    async def fake_send(message, spec):  # noqa: ANN001, ANN201
        _ = message
        captured["spec"] = spec
        return True

    monkeypatch.setattr(service.sink, "send", fake_send)

    asyncio.run(service._send_more_panel(MessageStub([])))

    spec = captured["spec"]
    assert spec.context == "sending more panel"
    assert spec.edit_context == "sending more panel"
    assert spec.edit_window_seconds == 300.0


def test_reply_markup_for_autopilot_result_uses_autopilot_controls() -> None:
    service = TelegramBotService(
        config=SimpleNamespace(
            telegram_bot_token="dummy",
            poll_interval_seconds=1,
            max_telegram_message_length=3500,
        ),
        router=WizardRouterStub(),
    )
    run = AutopilotRunRecord(
        id=1,
        project_id=101,
        chat_id="1",
        created_by_user_id=1,
        goal="持续推进支付修复",
        status="running_worker",
        supervisor_session_id="sess-a",
        worker_session_id="sess-b",
        current_phase="worker",
        cycle_count=1,
        max_cycles=100,
        no_progress_cycles=0,
        same_instruction_cycles=0,
        last_instruction_fingerprint="run tests next",
        last_decision="continue",
        last_worker_summary="已修改支付回调",
        last_supervisor_summary="继续测试",
        paused_reason=None,
        stopped_by_user_id=None,
    )

    markup = service._reply_markup_for_result(
        "/autopilot-status",
        SimpleNamespace(telegram_chat_id="1"),
        CommandResult("ok", metadata={"autopilot_run": run}),
    )

    rows = markup.inline_keyboard
    assert any(button.callback_data == "prompt:autopilot_takeover" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_pause" for row in rows for button in row)
    assert any(button.callback_data == "cmd:autopilot_stop" for row in rows for button in row)
