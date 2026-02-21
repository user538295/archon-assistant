"""AI pipeline integration test — S5.1.

Drives OutputParser with a FakePtySession (scripted byte stream)
and verifies all six event types are produced and truncation works.
No internal methods are mocked — only the PTY process boundary is substituted.
"""
from typing import AsyncGenerator

from archon.ai.output_parser import (
    ErrorEvent,
    OutputParser,
    Response,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)
from archon.ai.truncation import SplitStrategy


# ──────────────────────────────────────────────────────────────────
# FakePtySession
# ──────────────────────────────────────────────────────────────────

# Pre-recorded Claude-like PTY output covering all six event types:
#   Thinking...\r\n                 → ThinkingStarted
#   I should read the file.\r\n     → (buffered as thinking content)
#   ⏺ Read(file.txt)\r\n           → ThinkingResult (flushed) + ToolStarted
#   ⎿ file content here\r\n        → (buffered as tool result)
#   Error: tool failed\r\n          → ToolResult (flushed) + ErrorEvent
#   Here is the answer.\r\n         → (buffered as response)
#   <end of stream>                 → Response (flushed)
_SCRIPT: list[bytes] = [
    b"Thinking...\r\n",
    b"I should read the file.\r\n",
    b"\xe2\x8f\xba Read(file.txt)\r\n",    # ⏺
    b"\xe2\x8e\xbf file content here\r\n", # ⎿
    b"Error: tool failed with code 1\r\n",
    b"Here is the answer.\r\n",
]


class FakePtySession:
    """Emits a pre-recorded byte sequence as a PTY stream substitute."""

    async def read_stream(self) -> AsyncGenerator[bytes, None]:
        for chunk in _SCRIPT:
            yield chunk


# ──────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────

async def _run_pipeline() -> list:
    session = FakePtySession()
    parser = OutputParser()
    return [e async for e in parser.parse(session.read_stream())]


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
    """Full chain: FakePtySession → OutputParser → event → SplitStrategy."""
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
