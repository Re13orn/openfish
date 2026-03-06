import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
from telegram.error import TimedOut

from src.telegram_sink import TelegramMessageSink, TelegramSendSpec


class MessageStub:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent = []

    async def reply_text(self, text: str, **kwargs):  # noqa: ANN003
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.sent.append((text, kwargs.get("reply_markup")))
        return outcome


def _sink() -> TelegramMessageSink:
    config = SimpleNamespace(max_telegram_message_length=3500)
    return TelegramMessageSink(config, default_reply_markup_factory=lambda: "MAIN")


def test_send_uses_default_markup_when_missing() -> None:
    sink = _sink()
    message = MessageStub([object()])

    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test")))

    assert ok is True
    assert message.sent == [("hello", "MAIN")]


def test_send_retries_after_timeout(monkeypatch) -> None:
    sink = _sink()
    message = MessageStub([TimedOut(), object()])

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("src.telegram_sink.asyncio.sleep", _no_sleep)
    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test", reply_markup="X")))

    assert ok is True
    assert message.sent == [("hello", "X")]
