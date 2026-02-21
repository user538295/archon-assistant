"""Tests for OutputParser — S1.2."""
import pytest
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


# ──────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────

async def _stream(*chunks: bytes) -> AsyncGenerator[bytes, None]:
    for chunk in chunks:
        yield chunk


async def _parse(*chunks: bytes) -> list:
    parser = OutputParser()
    return [e async for e in parser.parse(_stream(*chunks))]


# ──────────────────────────────────────────────────────────────────
# ThinkingStarted
# ──────────────────────────────────────────────────────────────────

async def test_thinking_started_emitted_on_thinking_line() -> None:
    events = await _parse(b"\x1b[1;34m\xe2\x8f\xba\x1b[0m Thinking...\r\n")
    assert any(isinstance(e, ThinkingStarted) for e in events)


async def test_thinking_started_strips_ansi_before_matching() -> None:
    # Same content, heavier ANSI decoration
    events = await _parse(b"\x1b[2J\x1b[H\x1b[1m Thinking...\x1b[0m\r\n")
    assert any(isinstance(e, ThinkingStarted) for e in events)


# ──────────────────────────────────────────────────────────────────
# ThinkingResult
# ──────────────────────────────────────────────────────────────────

async def test_thinking_result_emitted_with_content_at_stream_end() -> None:
    events = await _parse(
        b"Thinking...\r\n",
        b"The user wants to read a file.\r\n",
    )
    thinking_results = [e for e in events if isinstance(e, ThinkingResult)]
    assert len(thinking_results) == 1
    assert "user wants to read" in thinking_results[0].content


async def test_thinking_result_emitted_before_tool_start() -> None:
    events = await _parse(
        b"Thinking...\r\n",
        b"I should read the file.\r\n",
        b"\xe2\x8f\xba Read(file.py)\r\n",
    )
    types = [type(e) for e in events]
    assert types.index(ThinkingResult) < types.index(ToolStarted)


# ──────────────────────────────────────────────────────────────────
# ToolStarted
# ──────────────────────────────────────────────────────────────────

async def test_tool_started_emitted_with_name() -> None:
    events = await _parse(b"\xe2\x8f\xba Read(file.py)\r\n")
    tool_events = [e for e in events if isinstance(e, ToolStarted)]
    assert len(tool_events) == 1
    assert tool_events[0].name == "Read"


async def test_tool_started_multiple_tools_in_sequence() -> None:
    events = await _parse(
        b"\xe2\x8f\xba Read(a.py)\r\n",
        b"\xe2\x8f\xba Write(b.py)\r\n",
    )
    tool_names = [e.name for e in events if isinstance(e, ToolStarted)]
    assert tool_names == ["Read", "Write"]


async def test_tool_started_with_ansi_decoration() -> None:
    events = await _parse(b"\x1b[32m\xe2\x8f\xba\x1b[0m \x1b[1mBash\x1b[0m(ls -la)\r\n")
    tool_events = [e for e in events if isinstance(e, ToolStarted)]
    assert tool_events[0].name == "Bash"


# ──────────────────────────────────────────────────────────────────
# ToolResult
# ──────────────────────────────────────────────────────────────────

async def test_tool_result_emitted_from_indented_lines() -> None:
    events = await _parse(
        b"\xe2\x8f\xba Read(file.py)\r\n",
        b"\xe2\x8e\xbf def foo(): pass\r\n",
        b"\xe2\x8e\xbf     return 1\r\n",
    )
    results = [e for e in events if isinstance(e, ToolResult)]
    assert len(results) == 1
    assert "def foo(): pass" in results[0].content


async def test_tool_result_emitted_when_next_tool_starts() -> None:
    events = await _parse(
        b"\xe2\x8f\xba Read(a.py)\r\n",
        b"\xe2\x8e\xbf content of a\r\n",
        b"\xe2\x8f\xba Write(b.py)\r\n",
    )
    types = [type(e) for e in events]
    # ToolResult for first tool must appear before second ToolStarted
    first_result = next(i for i, t in enumerate(types) if t is ToolResult)
    second_tool = [i for i, t in enumerate(types) if t is ToolStarted][1]
    assert first_result < second_tool


# ──────────────────────────────────────────────────────────────────
# Response
# ──────────────────────────────────────────────────────────────────

async def test_unrecognized_lines_emitted_as_response_on_flush() -> None:
    events = await _parse(b"Here is my answer to your question.\r\n")
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "Here is my answer" in responses[0].content


async def test_multiple_unrecognized_lines_merged_into_one_response() -> None:
    events = await _parse(
        b"Line one.\r\n",
        b"Line two.\r\n",
    )
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "Line one" in responses[0].content
    assert "Line two" in responses[0].content


# ──────────────────────────────────────────────────────────────────
# ErrorEvent
# ──────────────────────────────────────────────────────────────────

async def test_error_event_emitted_on_error_line() -> None:
    events = await _parse(b"\x1b[31mError: something went wrong\x1b[0m\r\n")
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert "something went wrong" in errors[0].message


# ──────────────────────────────────────────────────────────────────
# Full sequence
# ──────────────────────────────────────────────────────────────────

async def test_full_event_sequence_in_correct_order() -> None:
    events = await _parse(
        b"Thinking...\r\n",
        b"I should read the config file.\r\n",
        b"\xe2\x8f\xba Read(config.toml)\r\n",
        b"\xe2\x8e\xbf [access]\r\n",
        b"\xe2\x8e\xbf allowed_user_ids = [1]\r\n",
        b"Here is a summary of the config.\r\n",
    )
    types = [type(e) for e in events]
    assert types == [
        ThinkingStarted,
        ThinkingResult,
        ToolStarted,
        ToolResult,
        Response,
    ]


async def test_empty_lines_are_ignored() -> None:
    events = await _parse(b"\r\n", b"\r\n", b"Hello.\r\n")
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


async def test_consecutive_thinking_blocks_each_emit_started_and_result() -> None:
    events = await _parse(
        b"Thinking...\r\n",
        b"First thought.\r\n",
        b"Thinking...\r\n",
        b"Second thought.\r\n",
    )
    types = [type(e) for e in events]
    assert types.count(ThinkingStarted) == 2
    assert types.count(ThinkingResult) == 2
    contents = [e.content for e in events if isinstance(e, ThinkingResult)]
    assert any("First thought" in c for c in contents)
    assert any("Second thought" in c for c in contents)


async def test_thinking_start_while_tool_result_buffered_flushes_tool_result() -> None:
    events = await _parse(
        b"\xe2\x8f\xba Read(file.py)\r\n",
        b"\xe2\x8e\xbf some output\r\n",
        b"Thinking...\r\n",
    )
    types = [type(e) for e in events]
    assert ToolResult in types
    assert ThinkingStarted in types
    assert types.index(ToolResult) < types.index(ThinkingStarted)


async def test_chunked_delivery_produces_same_result() -> None:
    """Parser works correctly even when a single line arrives in multiple chunks."""
    events = await _parse(
        b"Think",
        b"ing...\r\n",
        b"I need to",
        b" act.\r\n",
    )
    assert any(isinstance(e, ThinkingStarted) for e in events)
    assert any(isinstance(e, ThinkingResult) for e in events)
