"""Tests for ClaudeSession — S1.1."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response, ThinkingStarted, ThinkingResult, ToolStarted


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_mock_client(messages: list = []):
    """Build a mock ClaudeSDKClient that yields given messages from receive_response."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def _receive_response():  # type: ignore[return]
        for m in messages:
            yield m

    client.receive_response = _receive_response
    return client


# ──────────────────────────────────────────────────────────────────
# is_alive
# ──────────────────────────────────────────────────────────────────


def test_is_alive_false_before_start() -> None:
    session = ClaudeSession()
    assert not session.is_alive


async def test_is_alive_true_after_start() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
    assert session.is_alive


async def test_is_alive_false_after_stop() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()
    assert not session.is_alive


# ──────────────────────────────────────────────────────────────────
# start
# ──────────────────────────────────────────────────────────────────


async def test_start_calls_connect() -> None:
    session = ClaudeSession(cwd="/some/path")
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
        MockClient.assert_called_once()
        mock_client.connect.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# send
# ──────────────────────────────────────────────────────────────────


async def test_send_raises_if_not_started() -> None:
    session = ClaudeSession()
    with pytest.raises(RuntimeError, match="not started"):
        async for _ in session.send("hello"):
            pass


async def test_send_calls_query_with_prompt() -> None:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    messages = [
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="s1",
            result="OK",
        )
    ]
    session = ClaudeSession()
    mock_client = _make_mock_client(messages)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("Say: OK")]

    mock_client.query.assert_awaited_once_with("Say: OK")
    assert len(events) == 1
    assert isinstance(events[0], Response)
    assert events[0].content == "OK"


async def test_send_yields_all_mapped_events() -> None:
    from claude_agent_sdk import AssistantMessage, ResultMessage, ThinkingBlock, ToolUseBlock

    messages = [
        AssistantMessage(content=[ThinkingBlock(thinking="thinking", signature="s")], model="m"),
        AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={})], model="m"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=2,
            session_id="s1",
            result="Done.",
        ),
    ]
    session = ClaudeSession()
    mock_client = _make_mock_client(messages)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("Do it")]

    types = [type(e) for e in events]
    assert types == [ThinkingStarted, ThinkingResult, ToolStarted, Response]


# ──────────────────────────────────────────────────────────────────
# stop
# ──────────────────────────────────────────────────────────────────


async def test_stop_calls_disconnect() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()
    mock_client.disconnect.assert_awaited_once()


async def test_stop_is_noop_when_not_started() -> None:
    session = ClaudeSession()
    await session.stop()  # must not raise


async def test_stop_is_noop_when_already_stopped() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()
        await session.stop()  # second stop must not raise
    mock_client.disconnect.assert_awaited_once()  # called only once
