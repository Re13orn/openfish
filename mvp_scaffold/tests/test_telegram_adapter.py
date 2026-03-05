import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
from telegram.error import BadRequest, NetworkError, TelegramError, TimedOut

from src.telegram_adapter import TelegramBotService


class RouterStub:
    pass


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

    async def reply_text(self, text: str, **kwargs):  # noqa: ANN003
        _ = kwargs
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.sent_texts.append(text)
        return outcome


class AppStub:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.handlers = []
        self.error_handlers = []

    def add_handler(self, handler) -> None:  # noqa: ANN001
        self.handlers.append(handler)

    def add_error_handler(self, handler) -> None:  # noqa: ANN001
        self.error_handlers.append(handler)

    def run_polling(self, **kwargs) -> None:  # noqa: ANN003
        _ = kwargs
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


def test_menu_text_maps_to_status_command() -> None:
    service = _service()
    assert service._map_menu_to_command("状态") == "/status"
    assert service._map_menu_to_command("帮助") == "/help"
    assert service._map_menu_to_command("工具") == "__tools__"
    assert service._map_menu_to_command("unknown") is None


def test_callback_token_maps_to_command() -> None:
    service = _service()
    assert service._resolve_callback_command("status") == "/status"
    assert service._resolve_callback_command("mcp") == "/mcp"
    assert service._resolve_callback_command("project_disable_current") == "/project-disable"
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

    async def fake_safe_reply_text(message, text: str, *, context: str, reply_markup=None) -> bool:  # noqa: ANN001
        _ = message
        _ = context
        _ = reply_markup
        sent_texts.append(text)
        return True

    monkeypatch.setattr(service, "_safe_reply_text", fake_safe_reply_text)
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
