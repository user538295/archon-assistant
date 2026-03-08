"""Tests for EventMapper — S1.2."""
import logging

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import StreamEvent

from archon.ai.event_mapper import (
    ErrorEvent,
    EventMapper,
    Response,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)


# ──────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────


async def _map(*messages: object) -> list:
    async def _stream():  # type: ignore[return]
        for m in messages:
            yield m

    mapper = EventMapper()
    return [e async for e in mapper.map_messages(_stream())]


def _assistant(content: list) -> AssistantMessage:
    return AssistantMessage(content=content, model="test")


# ──────────────────────────────────────────────────────────────────
# ThinkingResult
# ──────────────────────────────────────────────────────────────────


async def test_thinking_block_emits_result() -> None:
    events = await _map(_assistant([ThinkingBlock(thinking="Let me analyze.", signature="sig")]))
    assert [type(e) for e in events] == [ThinkingResult]
    assert events[0].content == "Let me analyze."


async def test_multiple_thinking_blocks_each_emit_result() -> None:
    events = await _map(
        _assistant([ThinkingBlock(thinking="First.", signature="s1")]),
        _assistant([ThinkingBlock(thinking="Second.", signature="s2")]),
    )
    types = [type(e) for e in events]
    assert types.count(ThinkingResult) == 2


# ──────────────────────────────────────────────────────────────────
# ToolStarted
# ──────────────────────────────────────────────────────────────────


async def test_tool_use_block_emits_tool_started() -> None:
    events = await _map(_assistant([ToolUseBlock(id="t1", name="Read", input={"file": "a.py"})]))
    assert len(events) == 1
    assert isinstance(events[0], ToolStarted)
    assert events[0].name == "Read"


async def test_bash_tool_input_extracts_command() -> None:
    events = await _map(_assistant([ToolUseBlock(id="t1", name="Bash", input={"command": "ls -la"})]))
    assert events[0].input == "ls -la"


async def test_file_path_tool_input_extracts_path() -> None:
    events = await _map(_assistant([ToolUseBlock(id="t1", name="Read", input={"file_path": "/foo/bar.py"})]))
    assert events[0].input == "/foo/bar.py"


async def test_single_key_tool_input_uses_value() -> None:
    events = await _map(_assistant([ToolUseBlock(id="t1", name="Custom", input={"key": "val"})]))
    assert events[0].input == "val"


async def test_multi_key_tool_input_is_json() -> None:
    events = await _map(_assistant([ToolUseBlock(id="t1", name="Custom", input={"a": "1", "b": "2"})]))
    assert '"a"' in events[0].input and '"b"' in events[0].input


async def test_empty_tool_input_is_empty_string() -> None:
    events = await _map(_assistant([ToolUseBlock(id="t1", name="Read", input={})]))
    assert events[0].input == ""


async def test_multiple_tool_blocks_emit_in_order() -> None:
    events = await _map(
        _assistant([ToolUseBlock(id="t1", name="Read", input={})]),
        _assistant([ToolUseBlock(id="t2", name="Write", input={})]),
    )
    names = [e.name for e in events if isinstance(e, ToolStarted)]
    assert names == ["Read", "Write"]


async def test_tool_started_gets_sequential_id() -> None:
    events = await _map(_assistant([ToolUseBlock(id="sdk-abc", name="Read", input={})]))
    assert isinstance(events[0], ToolStarted)
    assert events[0].id == 1


async def test_tool_result_gets_matching_id() -> None:
    tool_msg = _assistant([ToolUseBlock(id="sdk-abc", name="Read", input={})])
    result_msg = UserMessage(content=[ToolResultBlock(tool_use_id="sdk-abc", content="ok", is_error=False)])
    events = await _map(tool_msg, result_msg)
    tool_started = next(e for e in events if isinstance(e, ToolStarted))
    tool_result = next(e for e in events if isinstance(e, ToolResult))
    assert tool_started.id == tool_result.id


async def test_multiple_tools_get_different_ids() -> None:
    events = await _map(
        _assistant([ToolUseBlock(id="t1", name="Read", input={})]),
        _assistant([ToolUseBlock(id="t2", name="Write", input={})]),
    )
    ids = [e.id for e in events if isinstance(e, ToolStarted)]
    assert ids == [1, 2]


async def test_tool_result_unknown_id_gets_zero() -> None:
    """ToolResult whose tool_use_id was never seen gets id=0."""
    result_msg = UserMessage(content=[ToolResultBlock(tool_use_id="unknown", content="x", is_error=False)])
    events = await _map(result_msg)
    assert events[0].id == 0


# ──────────────────────────────────────────────────────────────────
# ToolResult
# ──────────────────────────────────────────────────────────────────


async def test_tool_result_block_emits_tool_result() -> None:
    msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="file contents", is_error=False)])
    events = await _map(msg)
    assert len(events) == 1
    assert isinstance(events[0], ToolResult)
    assert events[0].content == "file contents"


async def test_tool_result_none_content_is_empty_string() -> None:
    msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=None, is_error=False)])
    events = await _map(msg)
    assert events[0].content == ""


async def test_tool_result_list_content_is_json_serialized() -> None:
    """Non-text content blocks (no 'type':'text') fall back to JSON serialization."""
    msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=[{"key": "val"}], is_error=False)])
    events = await _map(msg)
    assert '{"key": "val"}' in events[0].content


async def test_tool_result_text_content_blocks_are_extracted() -> None:
    """SDK content blocks [{"type":"text","text":"..."}] must yield plain text, not JSON."""
    msg = UserMessage(content=[ToolResultBlock(
        tool_use_id="t1",
        content=[{"type": "text", "text": "Hello world."}],
        is_error=False,
    )])
    events = await _map(msg)
    assert events[0].content == "Hello world."


async def test_tool_result_multiple_text_blocks_are_joined() -> None:
    """Multiple text content blocks are concatenated in order."""
    msg = UserMessage(content=[ToolResultBlock(
        tool_use_id="t1",
        content=[{"type": "text", "text": "Part 1."}, {"type": "text", "text": " Part 2."}],
        is_error=False,
    )])
    events = await _map(msg)
    assert events[0].content == "Part 1. Part 2."


async def test_tool_result_non_text_blocks_fall_back_to_json() -> None:
    """Content blocks without type='text' fall back to JSON serialization."""
    msg = UserMessage(content=[ToolResultBlock(
        tool_use_id="t1",
        content=[{"type": "image", "source": "data:..."}],
        is_error=False,
    )])
    events = await _map(msg)
    assert '"type": "image"' in events[0].content


async def test_tool_result_mixed_blocks_extracts_only_text() -> None:
    """Mixed blocks: text parts extracted, non-text parts discarded."""
    msg = UserMessage(content=[ToolResultBlock(
        tool_use_id="t1",
        content=[{"type": "text", "text": "Found:"}, {"type": "image", "source": "img"}],
        is_error=False,
    )])
    events = await _map(msg)
    assert events[0].content == "Found:"


async def test_user_message_with_string_content_produces_no_events() -> None:
    msg = UserMessage(content="plain string")
    events = await _map(msg)
    assert events == []


# ──────────────────────────────────────────────────────────────────
# Response
# ──────────────────────────────────────────────────────────────────


async def test_result_message_emits_response() -> None:
    msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="Here is my answer.",
    )
    events = await _map(msg)
    assert len(events) == 1
    assert isinstance(events[0], Response)
    assert events[0].content == "Here is my answer."


async def test_result_message_with_none_result_produces_no_response() -> None:
    msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result=None,
    )
    events = await _map(msg)
    assert events == []


# ──────────────────────────────────────────────────────────────────
# ErrorEvent
# ──────────────────────────────────────────────────────────────────


async def test_error_result_message_emits_error_event() -> None:
    msg = ResultMessage(
        subtype="error",
        duration_ms=100,
        duration_api_ms=50,
        is_error=True,
        num_turns=1,
        session_id="s1",
        result="Something went wrong",
    )
    events = await _map(msg)
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert "Something went wrong" in events[0].message


async def test_error_result_with_no_result_uses_fallback_message() -> None:
    msg = ResultMessage(
        subtype="error",
        duration_ms=100,
        duration_api_ms=50,
        is_error=True,
        num_turns=1,
        session_id="s1",
        result=None,
    )
    events = await _map(msg)
    assert isinstance(events[0], ErrorEvent)
    assert events[0].message  # non-empty fallback


# ──────────────────────────────────────────────────────────────────
# Messages that produce no events
# ──────────────────────────────────────────────────────────────────


async def test_system_message_produces_no_events() -> None:
    events = await _map(SystemMessage(subtype="init", data={"session_id": "s1"}))
    assert events == []


async def test_text_block_in_assistant_message_produces_no_events() -> None:
    events = await _map(_assistant([TextBlock(text="some text")]))
    assert events == []


async def test_text_block_in_assistant_message_emits_debug_log(caplog: pytest.LogCaptureFixture) -> None:
    """TextBlock in AssistantMessage must emit a debug log, not silently pass."""
    with caplog.at_level(logging.DEBUG, logger="archon"):
        await _map(_assistant([TextBlock(text="intermediate text")]))
    assert any("TextBlock" in r.message and "discarded" in r.message for r in caplog.records)


async def test_tool_maps_reset_between_query_rounds() -> None:
    """Tool ID maps are cleared at the start of each map_messages call.

    A tool ID allocated in round 1 must NOT be resolvable in round 2.
    """
    mapper = EventMapper()

    async def _stream_tool():  # type: ignore[return]
        yield _assistant([ToolUseBlock(id="sdk-r1", name="Read", input={})])

    async def _stream_result():  # type: ignore[return]
        yield UserMessage(content=[ToolResultBlock(tool_use_id="sdk-r1", content="x", is_error=False)])

    # Round 1: allocates sequential id=1 for sdk-r1
    events_r1 = [e async for e in mapper.map_messages(_stream_tool())]
    assert events_r1[0].id == 1  # type: ignore[union-attr]

    # Round 2: maps are cleared at start of map_messages, so sdk-r1 is unknown → id=0
    events_r2 = [e async for e in mapper.map_messages(_stream_result())]
    assert events_r2[0].id == 0  # type: ignore[union-attr]


# ──────────────────────────────────────────────────────────────────
# Full sequence
# ──────────────────────────────────────────────────────────────────


async def test_full_event_sequence_in_correct_order() -> None:
    messages = [
        _assistant([ThinkingBlock(thinking="I'll read the file.", signature="sig")]),
        _assistant([ToolUseBlock(id="t1", name="Read", input={"file": "a.py"})]),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="def main(): pass", is_error=False)]),
        ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=2,
            session_id="s1",
            result="The file defines main().",
        ),
    ]
    events = await _map(*messages)
    assert [type(e) for e in events] == [
        ThinkingResult, ToolStarted, ToolResult, Response
    ]


# ──────────────────────────────────────────────────────────────────
# FR.003 — source field defaults
# ──────────────────────────────────────────────────────────────────


def test_thinking_result_source_default() -> None:
    """ThinkingResult.source defaults to 'orchestrator'."""
    event = ThinkingResult(content="thought")
    assert event.source == "orchestrator"


def test_tool_started_source_default() -> None:
    """ToolStarted.source defaults to 'orchestrator'."""
    event = ToolStarted(name="Read")
    assert event.source == "orchestrator"


def test_tool_result_source_default() -> None:
    """ToolResult.source defaults to 'orchestrator'."""
    event = ToolResult(content="result")
    assert event.source == "orchestrator"


def test_response_source_default() -> None:
    """Response.source defaults to 'orchestrator'."""
    event = Response(content="done")
    assert event.source == "orchestrator"


def test_error_event_source_default() -> None:
    """ErrorEvent.source defaults to 'orchestrator'."""
    event = ErrorEvent(message="oops")
    assert event.source == "orchestrator"


def test_subagent_started_source_default() -> None:
    """SubagentStarted.source defaults to 'orchestrator' (hooks override it to 'sub-agent')."""
    event = SubagentStarted(agent_id="a1", agent_type="general")
    assert event.source == "orchestrator"


def test_subagent_stopped_source_default() -> None:
    """SubagentStopped.source defaults to 'orchestrator' (hooks override it to 'sub-agent')."""
    event = SubagentStopped(agent_id="a1", agent_type="general")
    assert event.source == "orchestrator"


def test_source_can_be_overridden() -> None:
    """Explicitly setting source='sub-agent' works on any event."""
    event = ThinkingResult(content="thought", source="sub-agent")
    assert event.source == "sub-agent"


# ──────────────────────────────────────────────────────────────────
# rate_limit_event / unknown SDK message type handling
# ──────────────────────────────────────────────────────────────────


async def test_system_message_yields_no_events() -> None:
    """SystemMessage (e.g. from rate_limit_event patch) must yield zero events."""
    msg = SystemMessage(subtype="rate_limit_event", data={"type": "rate_limit_event"})
    events = await _map(msg)
    assert events == []


async def test_stream_event_yields_no_events() -> None:
    """StreamEvent must yield zero events — it is an informational SDK event."""
    msg = StreamEvent(
        uuid="u1",
        session_id="s1",
        event={"type": "content_block_start"},
    )
    events = await _map(msg)
    assert events == []


def test_sdk_parse_message_returns_none_for_unknown_types() -> None:
    """SDK 0.1.46+ parse_message returns None for unknown types (forward-compatible).

    This guards against a regression where the SDK raises MessageParseError for
    rate_limit_event or other informational CLI messages, crashing the session.
    """
    from claude_agent_sdk._internal.message_parser import parse_message

    data = {"type": "rate_limit_event", "retry_after_ms": 5000}
    result = parse_message(data)
    assert result is None, (
        "SDK parse_message must return None (not raise) for unknown message types"
    )
