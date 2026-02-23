"""Tests for EventMapper — S1.2."""
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

from archon.ai.event_mapper import (
    ErrorEvent,
    EventMapper,
    Response,
    ThinkingResult,
    ThinkingStarted,
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
# ThinkingStarted + ThinkingResult
# ──────────────────────────────────────────────────────────────────


async def test_thinking_block_emits_started_then_result() -> None:
    events = await _map(_assistant([ThinkingBlock(thinking="Let me analyze.", signature="sig")]))
    assert [type(e) for e in events] == [ThinkingStarted, ThinkingResult]
    assert events[1].content == "Let me analyze."


async def test_multiple_thinking_blocks_each_emit_pair() -> None:
    events = await _map(
        _assistant([ThinkingBlock(thinking="First.", signature="s1")]),
        _assistant([ThinkingBlock(thinking="Second.", signature="s2")]),
    )
    types = [type(e) for e in events]
    assert types.count(ThinkingStarted) == 2
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
    msg = UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=[{"key": "val"}], is_error=False)])
    events = await _map(msg)
    assert '{"key": "val"}' in events[0].content


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
        ThinkingStarted, ThinkingResult, ToolStarted, ToolResult, Response
    ]
