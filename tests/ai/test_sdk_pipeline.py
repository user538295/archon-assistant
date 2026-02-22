"""AI pipeline integration test — S5.1.

Drives EventMapper with a FakeClaudeClient (scripted SDK message sequence)
and verifies all six event types are produced and truncation works.
No internal methods are mocked — only the SDK client boundary is substituted.
"""
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
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
from archon.ai.truncation import SplitStrategy


# ──────────────────────────────────────────────────────────────────
# FakeClaudeClient
# ──────────────────────────────────────────────────────────────────

# Pre-built SDK message sequence covering all six event types:
#   ThinkingBlock            → ThinkingStarted + ThinkingResult
#   ToolUseBlock             → ToolStarted
#   UserMessage+ToolResult   → ToolResult
#   ResultMessage is_error   → ErrorEvent
#   ResultMessage success    → Response
#
# Note: ErrorEvent is produced by an error ResultMessage mid-sequence;
# we model this as two separate receive_response calls.
_SCRIPT: list = [
    AssistantMessage(
        content=[ThinkingBlock(thinking="I should read the file.", signature="sig")],
        model="test",
    ),
    AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Read", input={"file": "file.txt"})],
        model="test",
    ),
    UserMessage(
        content=[ToolResultBlock(tool_use_id="t1", content="file content here", is_error=False)]
    ),
    ResultMessage(
        subtype="error",
        duration_ms=100,
        duration_api_ms=50,
        is_error=True,
        num_turns=2,
        session_id="s1",
        result="tool failed with code 1",
    ),
    ResultMessage(
        subtype="success",
        duration_ms=200,
        duration_api_ms=100,
        is_error=False,
        num_turns=3,
        session_id="s1",
        result="Here is the answer.",
    ),
]


async def _run_pipeline() -> list:
    async def _stream():  # type: ignore[return]
        for msg in _SCRIPT:
            yield msg

    mapper = EventMapper()
    return [e async for e in mapper.map_messages(_stream())]


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


async def test_all_six_event_types_produced() -> None:
    events = await _run_pipeline()
    assert {type(e) for e in events} == {
        ThinkingStarted, ThinkingResult, ToolStarted, ToolResult, ErrorEvent, Response
    }


async def test_event_order_matches_script() -> None:
    events = await _run_pipeline()
    assert [type(e) for e in events] == [
        ThinkingStarted, ThinkingResult, ToolStarted, ToolResult, ErrorEvent, Response
    ]


async def test_thinking_result_content() -> None:
    events = await _run_pipeline()
    result = next(e for e in events if isinstance(e, ThinkingResult))
    assert "read the file" in result.content


async def test_tool_started_name() -> None:
    events = await _run_pipeline()
    tool = next(e for e in events if isinstance(e, ToolStarted))
    assert tool.name == "Read"


async def test_tool_result_content() -> None:
    events = await _run_pipeline()
    result = next(e for e in events if isinstance(e, ToolResult))
    assert "file content here" in result.content


async def test_error_event_message() -> None:
    events = await _run_pipeline()
    error = next(e for e in events if isinstance(e, ErrorEvent))
    assert "tool failed" in error.message


async def test_response_content() -> None:
    events = await _run_pipeline()
    response = next(e for e in events if isinstance(e, Response))
    assert "Here is the answer" in response.content


async def test_split_strategy_truncates_long_event_content() -> None:
    """Full chain: FakeClaudeClient → EventMapper → event → SplitStrategy."""
    events = await _run_pipeline()
    response = next(e for e in events if isinstance(e, Response))
    long_content = response.content * 20  # exceed max_len
    chunks = SplitStrategy().apply(long_content, max_len=50)
    assert len(chunks) > 1
    assert all(len(c.split("] ", 1)[1]) <= 50 for c in chunks)


async def test_split_strategy_passthrough_for_short_content() -> None:
    """Content within max_len passes through truncation unchanged."""
    events = await _run_pipeline()
    thinking = next(e for e in events if isinstance(e, ThinkingResult))
    chunks = SplitStrategy().apply(thinking.content, max_len=4000)
    assert chunks == [thinking.content]
