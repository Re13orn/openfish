import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
from telegram.error import NetworkError, TelegramError, TimedOut

from src.telegram_adapter import TelegramBotService


class RouterStub:
    pass


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
    assert service._map_menu_to_command("unknown") is None
