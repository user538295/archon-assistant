"""Tests for message handler and event formatter — S2.3."""
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message

from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import DEFAULT_MAX_LEN, format_event, handle_message


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

_split = SplitStrategy()


def _mock_message(text: str = "hello") -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock(id=42)
    return msg


def _mock_session(*events: object) -> MagicMock:
    """Session whose send() yields the given events."""
    session = MagicMock()

    async def _send(prompt: str) -> AsyncGenerator:
        for event in events:
            yield event

    session.send = _send
    return session


def _mock_session_manager(*events: object) -> SessionManager:
    session = _mock_session(*events)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    return mgr


# ──────────────────────────────────────────────────────────────────
# format_event — each event type
# ──────────────────────────────────────────────────────────────────


def test_format_thinking_started() -> None:
    assert format_event(ThinkingStarted(), _split) == ["💭 Thinking..."]


def test_format_thinking_result() -> None:
    result = format_event(ThinkingResult(content="pondering"), _split)
    assert result == ["💭 Thought:\npondering"]


def test_format_tool_started() -> None:
    result = format_event(ToolStarted(name="Read"), _split)
    assert result == ["🔧 Tool: Read"]


def test_format_tool_result() -> None:
    result = format_event(ToolResult(content="file content"), _split)
    assert result == ["📤 Result:\nfile content"]


def test_format_response() -> None:
    result = format_event(Response(content="All done."), _split)
    assert result == ["✅ Response:\nAll done."]


def test_format_error_event() -> None:
    result = format_event(ErrorEvent(message="timeout"), _split)
    assert result == ["❌ Error: timeout"]


# ──────────────────────────────────────────────────────────────────
# format_event — truncation
# ──────────────────────────────────────────────────────────────────


def test_format_response_splits_long_content() -> None:
    long_text = "x" * 10
    result = format_event(Response(content=long_text), _split, max_len=4)
    assert len(result) == 3
    assert all(r.startswith("✅ Response:\n") for r in result)


def test_format_thinking_result_splits_long_content() -> None:
    long_text = "a" * 10
    result = format_event(ThinkingResult(content=long_text), _split, max_len=4)
    assert len(result) == 3
    assert all(r.startswith("💭 Thought:\n") for r in result)


def test_format_tool_result_splits_long_content() -> None:
    long_text = "b" * 10
    result = format_event(ToolResult(content=long_text), _split, max_len=4)
    assert len(result) == 3
    assert all(r.startswith("📤 Result:\n") for r in result)


# ──────────────────────────────────────────────────────────────────
# handle_message
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_sends_each_event() -> None:
    mgr = _mock_session_manager(ThinkingStarted(), Response(content="Hi"))
    msg = _mock_message("Say hi")

    await handle_message(msg, mgr, _split)

    assert msg.answer.await_count == 2
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "💭 Thinking..."
    assert texts[1] == "✅ Response:\nHi"


async def test_handle_message_gets_or_creates_session_for_user() -> None:
    mgr = _mock_session_manager()
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_awaited_once_with(42)


async def test_handle_message_sends_multi_chunk_event() -> None:
    long_text = "y" * 10
    mgr = _mock_session_manager(Response(content=long_text))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, max_len=4)

    # 10 chars / 4 = 3 chunks
    assert msg.answer.await_count == 3


async def test_handle_message_no_text_is_noop() -> None:
    mgr = MagicMock(spec=SessionManager)
    msg = _mock_message()
    msg.text = None

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_not_called()


async def test_handle_message_no_from_user_is_noop() -> None:
    mgr = MagicMock(spec=SessionManager)
    msg = _mock_message()
    msg.from_user = None

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_not_called()


async def test_handle_message_all_event_types_formatted() -> None:
    events = [
        ThinkingStarted(),
        ThinkingResult(content="thinking"),
        ToolStarted(name="Bash"),
        ToolResult(content="output"),
        Response(content="done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("do it")

    await handle_message(msg, mgr, _split)

    assert msg.answer.await_count == 5
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "💭 Thinking..."
    assert texts[1] == "💭 Thought:\nthinking"
    assert texts[2] == "🔧 Tool: Bash"
    assert texts[3] == "📤 Result:\noutput"
    assert texts[4] == "✅ Response:\ndone"
