import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("telegram")
from telegram.error import BadRequest, TelegramError, TimedOut

from src.telegram_sink import TelegramMessageSink, TelegramSendSpec


class MessageStub:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.sent = []
        self.chat_actions: list[str] = []
        self.chat_id = 123
        self.bot = BotStub()

    async def reply_text(self, text: str, **kwargs):  # noqa: ANN003
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.sent.append((text, kwargs.get("reply_markup")))
        return outcome

    async def reply_chat_action(self, action: str, **kwargs):  # noqa: ANN003
        _ = kwargs
        self.chat_actions.append(action)
        return object()

    def get_bot(self):  # noqa: ANN201
        return self.bot


class BotStub:
    def __init__(self) -> None:
        self.edits: list[tuple[str, str, str]] = []

    async def edit_message_text(self, *, chat_id, message_id, text: str, reply_markup=None, **kwargs):  # noqa: ANN003
        _ = reply_markup
        _ = kwargs
        self.edits.append((str(chat_id), str(message_id), text))
        return SimpleNamespace(message_id=message_id)


class DeliveryTrackerStub:
    def __init__(self) -> None:
        self.recent: dict[tuple[str, str], str] = {}
        self.remembered: list[tuple[str, str, str, str | None]] = []
        self.by_context: dict[tuple[str, str], str] = {}

    def get_recent_outbound_message_id(self, *, chat_id: str, dedup_key: str, max_age_seconds: float) -> str | None:
        _ = max_age_seconds
        return self.recent.get((chat_id, dedup_key))

    def get_recent_outbound_message_id_by_context(
        self,
        *,
        chat_id: str,
        context: str,
        max_age_seconds: float,
    ) -> str | None:
        _ = max_age_seconds
        return self.by_context.get((chat_id, context))

    def remember_outbound_message(
        self,
        *,
        chat_id: str,
        dedup_key: str,
        context: str,
        message_id: str | None,
    ) -> None:
        self.recent[(chat_id, dedup_key)] = message_id or ""
        self.by_context[(chat_id, context)] = message_id or ""
        self.remembered.append((chat_id, dedup_key, context, message_id))


def _sink(tracker=None) -> TelegramMessageSink:
    config = SimpleNamespace(max_telegram_message_length=3500)
    return TelegramMessageSink(
        config,
        default_reply_markup_factory=lambda: "MAIN",
        delivery_tracker=tracker,
    )


def test_send_uses_default_markup_when_missing() -> None:
    sink = _sink()
    message = MessageStub([SimpleNamespace(message_id=1)])

    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test")))

    assert ok is True
    assert message.sent == [("hello", "MAIN")]


def test_send_retries_after_timeout(monkeypatch) -> None:
    sink = _sink()
    message = MessageStub([TimedOut(), SimpleNamespace(message_id=1)])

    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("src.telegram_sink.asyncio.sleep", _no_sleep)
    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test", reply_markup="X")))

    assert ok is True
    assert message.sent == [("hello", "X")]


def test_send_does_not_retry_client_bad_request(monkeypatch) -> None:
    sink = _sink()
    message = MessageStub([BadRequest("chat not found")])
    slept: list[float] = []

    async def _capture_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("src.telegram_sink.asyncio.sleep", _capture_sleep)
    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test")))

    assert ok is False
    assert slept == []


def test_send_retries_server_side_telegram_error(monkeypatch) -> None:
    sink = _sink()
    message = MessageStub([TelegramError("Internal Server Error"), SimpleNamespace(message_id=1)])
    slept: list[float] = []

    async def _capture_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("src.telegram_sink.asyncio.sleep", _capture_sleep)
    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test")))

    assert ok is True
    assert slept == [1.0]


def test_send_typing_uses_reply_chat_action() -> None:
    sink = _sink()
    message = MessageStub([object()])

    ok = asyncio.run(sink.send_typing(message, context="test"))

    assert ok is True
    assert message.chat_actions == ["typing"]


def test_send_skips_recent_duplicate_delivery() -> None:
    tracker = DeliveryTrackerStub()
    sink = _sink(tracker=tracker)
    message = MessageStub([SimpleNamespace(message_id=1)])
    dedup_key = sink.build_dedup_key(text="hello", context="test", reply_markup=None)
    tracker.recent[("123", dedup_key)] = "88"

    ok = asyncio.run(
        sink.send(
            message,
            TelegramSendSpec(text="hello", context="test"),
        )
    )

    assert ok is True
    assert message.sent == []


def test_send_remembers_delivery_reference_on_success() -> None:
    tracker = DeliveryTrackerStub()
    sink = _sink(tracker=tracker)
    message = MessageStub([SimpleNamespace(message_id=77)])

    ok = asyncio.run(sink.send(message, TelegramSendSpec(text="hello", context="test")))

    assert ok is True
    assert tracker.remembered
    assert tracker.remembered[0][0] == "123"
    assert tracker.remembered[0][2] == "test"
    assert tracker.remembered[0][3] == "77"


def test_send_edits_recent_message_when_edit_context_matches() -> None:
    tracker = DeliveryTrackerStub()
    tracker.by_context[("123", "sending status result")] = "66"
    sink = _sink(tracker=tracker)
    message = MessageStub([])

    ok = asyncio.run(
        sink.send(
            message,
            TelegramSendSpec(
                text="new status",
                context="sending status result",
                edit_context="sending status result",
                edit_window_seconds=300,
            ),
        )
    )

    assert ok is True
    assert message.sent == []
    assert message.bot.edits == [("123", "66", "new status")]
