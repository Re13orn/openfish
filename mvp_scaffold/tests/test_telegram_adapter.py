import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut

from src.telegram_adapter import TelegramBotService


class RouterStub:
    pass


class WizardTasksStub:
    def __init__(self) -> None:
        self.state = None
        self.ui_mode = None
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


class WizardRouterStub:
    def __init__(self) -> None:
        self.tasks = WizardTasksStub()
        self.projects = SimpleNamespace(default_project_root=Path("/tmp/projects"))

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
        self.edit_reply_markup_calls: list[object] = []
        self.chat_actions: list[str] = []

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
    assert service._resolve_callback_command("mcp") == "/mcp"
    assert service._resolve_callback_command("project_disable_current") == "/project-disable"
    assert service._resolve_callback_command("ui_summary") == "/ui summary"
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

    async def fake_send_view_spec(message, spec, *, context: str) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
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
    assert any("项目新增向导 1/4" in text for text in sent_texts)


def test_activate_approve_prompt_starts_approval_note_wizard(monkeypatch) -> None:
    config = SimpleNamespace(
        telegram_bot_token="dummy",
        poll_interval_seconds=1,
        max_telegram_message_length=3500,
    )
    router = WizardRouterStub()
    service = TelegramBotService(config=config, router=router)
    sent_texts: list[str] = []

    async def fake_send_view_spec(message, spec, *, context: str, command=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
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

    async def fake_send_view_spec(message, spec, *, context: str, command=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
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

    async def fake_send_view_spec(message, spec, *, context: str) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
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
        "step": "name",
        "data": {"key": "demo", "path": ""},
    }
    assert message.edit_reply_markup_calls == [None]
    assert any("项目新增向导 3/4" in text for text in sent_texts)


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

    async def fake_send_view_spec(message, spec, *, context: str, command=None) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        _ = command
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

    async def fake_send_view_spec(message, spec, *, context: str) -> bool:  # noqa: ANN001, ANN202
        _ = message
        _ = context
        captured["markup"] = spec.reply_markup
        return True

    monkeypatch.setattr(service, "_send_view_spec", fake_send_view_spec)

    asyncio.run(service._send_more_panel(object()))

    markup = captured["markup"]
    rows = markup.inline_keyboard
    assert any(button.callback_data == "cmd:ui_summary" for row in rows for button in row)
    assert any(button.callback_data == "cmd:ui_verbose" for row in rows for button in row)


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
