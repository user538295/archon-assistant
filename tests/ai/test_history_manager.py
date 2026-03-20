"""Tests for HistoryManager — TDD for chat history persistence."""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    PlanEvent,
    Response,
    RoutingEvent,
    SubagentStarted,
    SubagentStopped,
    ThinkingResult,
    ToolResult,
    ToolStarted,
    WaveStarted,
)
from archon.ai.history_manager import HistoryManager

_FIXED_DATE = date(2026, 2, 23)
_FIXED_DT = datetime(2026, 2, 23, 14, 30, 45, tzinfo=timezone.utc)
_FIXED_TS = "14:30:45 UTC"


def _make_manager(tmp_path: Path) -> HistoryManager:
    return HistoryManager(str(tmp_path / "history"))


def _today_file(tmp_path: Path) -> Path:
    return tmp_path / "history" / "sessions" / f"{_FIXED_DATE.isoformat()}.md"


# ──────────────────────────────────────────────────────────────────
# Directory and file creation
# ──────────────────────────────────────────────────────────────────


async def test_directory_created_if_missing(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")

    assert (tmp_path / "history" / "sessions").is_dir()


async def test_record_user_message_creates_file(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")

    assert _today_file(tmp_path).exists()


# ──────────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────────


async def test_file_starts_with_date_header(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")

    content = _today_file(tmp_path).read_text()
    assert content.startswith("# 2026-02-23 — Archon Conversations\n")


async def test_header_not_duplicated_on_second_write(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "first message")
        await hm.record_user_message(1, "second message")

    content = _today_file(tmp_path).read_text()
    assert content.count("# 2026-02-23 — Archon Conversations") == 1


# ──────────────────────────────────────────────────────────────────
# User message section
# ──────────────────────────────────────────────────────────────────


async def test_user_message_section_header(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(42, "hello")

    content = _today_file(tmp_path).read_text()
    assert "## 14:30:45 UTC · User 42\n" in content


async def test_user_message_with_cwd(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(42, "hello", cwd="/home/user/project")

    content = _today_file(tmp_path).read_text()
    assert "## 14:30:45 UTC · User 42 · /home/user/project\n" in content


async def test_user_message_body_written(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "Can you list files?")

    content = _today_file(tmp_path).read_text()
    assert "Can you list files?" in content


# ──────────────────────────────────────────────────────────────────
# Event rendering
# ──────────────────────────────────────────────────────────────────


async def test_thinking_result_emits_content(tmp_path: Path) -> None:
    """ThinkingResult now writes a thought section (ThinkingStarted was removed)."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        size_before = _today_file(tmp_path).stat().st_size
        await hm.record_event(1, ThinkingResult(content="pondering"))
        size_after = _today_file(tmp_path).stat().st_size

    assert size_after > size_before


async def test_thinking_result_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ThinkingResult(content="I should list files."))

    content = _today_file(tmp_path).read_text()
    assert f"### 💭 Thinking · {_FIXED_TS}\n" in content
    assert "I should list files." in content


async def test_tool_started_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolStarted(name="bash", input="ls -la", id=1))

    content = _today_file(tmp_path).read_text()
    assert f"### 🔧 Tool: bash [1] · {_FIXED_TS}\n" in content
    assert "```\nls -la\n```" in content


async def test_tool_started_no_id_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolStarted(name="Read", input="/tmp/file.txt"))

    content = _today_file(tmp_path).read_text()
    assert f"### 🔧 Tool: Read · {_FIXED_TS}\n" in content
    assert "[0]" not in content


async def test_tool_result_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolResult(content="file contents here", id=2))

    content = _today_file(tmp_path).read_text()
    assert f"### 📤 Result [2] · {_FIXED_TS}\n" in content
    assert "```\nfile contents here\n```" in content


async def test_tool_result_no_id_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolResult(content="output"))

    content = _today_file(tmp_path).read_text()
    assert f"### 📤 Result · {_FIXED_TS}\n" in content
    assert "[0]" not in content


async def test_response_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "list files please")
        await hm.record_event(1, Response(content="Here are the files: ..."))

    content = _today_file(tmp_path).read_text()
    assert f"### ✅ Response · {_FIXED_TS}\n" in content
    assert "Here are the files: ..." in content


async def test_response_includes_contextual_retrieval_question(tmp_path: Path) -> None:
    """Response must repeat the last user question as a blockquote for contextual retrieval."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "list files please")
        await hm.record_event(1, Response(content="Here are the files: ..."))

    content = _today_file(tmp_path).read_text()
    assert '> User: "list files please"' in content


async def test_response_question_truncated_at_120_chars(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    long_q = "x" * 150
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, long_q)
        await hm.record_event(1, Response(content="answer"))

    content = _today_file(tmp_path).read_text()
    assert '> User: "' + "x" * 120 + '..."' in content


async def test_response_ends_with_separator(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, Response(content="done"))

    content = _today_file(tmp_path).read_text()
    assert content.endswith("\n\n---\n")


async def test_error_event_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ErrorEvent(message="SDK timeout"))

    content = _today_file(tmp_path).read_text()
    assert f"### ❌ Error · {_FIXED_TS}\n" in content
    assert "SDK timeout" in content


async def test_error_event_ends_with_separator(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ErrorEvent(message="oops"))

    content = _today_file(tmp_path).read_text()
    assert content.endswith("\n\n---\n")


# ──────────────────────────────────────────────────────────────────
# Response with no prior question — High gap
# ──────────────────────────────────────────────────────────────────


async def test_response_rendered_without_blockquote_when_no_prior_question(tmp_path: Path) -> None:
    """When record_event is called with Response for a user_id that has never had
    record_user_message called, the q_ctx blockquote must be omitted."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        # Seed the file via a different user so the directory + file exist
        await hm.record_user_message(1, "question from user one")
        # Record a response for user 999 — who has never sent a message
        await hm.record_event(999, Response(content="answer without prior question"))

    content = _today_file(tmp_path).read_text()
    assert "answer without prior question" in content
    # The '> User:' blockquote must NOT appear (user 999 has no prior question)
    assert '> User:' not in content


async def test_response_heading_present_when_no_prior_question(tmp_path: Path) -> None:
    """The '✅ Response' heading must still be written even with no prior question."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "seed message")
        await hm.record_event(999, Response(content="no-question response"))

    content = _today_file(tmp_path).read_text()
    assert "✅ Response" in content
    assert "no-question response" in content


# ──────────────────────────────────────────────────────────────────
# UTC consistency — file date and record timestamp from same source
# ──────────────────────────────────────────────────────────────────


async def test_file_date_and_record_timestamp_both_use_utc(tmp_path: Path) -> None:
    """File path date and record timestamp must both derive from datetime.now(utc).

    Simulates a UTC+2 timezone scenario where local date is 2026-02-24 but UTC
    date is still 2026-02-23 (23:30 UTC). Both the filename and the timestamp
    in the record must reflect UTC (2026-02-23).
    """
    # 23:30 UTC on 2026-02-23 — local time in UTC+2 would be 2026-02-24 01:30
    utc_late_night = datetime(2026, 2, 23, 23, 30, 0, tzinfo=timezone.utc)

    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_dt.now.return_value = utc_late_night
        await hm.record_user_message(1, "late night message")

    sessions_dir = tmp_path / "history" / "sessions"
    # File must be named after UTC date (2026-02-23), not local date (2026-02-24)
    assert (sessions_dir / "2026-02-23.md").exists(), "file must use UTC date"
    assert not (sessions_dir / "2026-02-24.md").exists(), "must not use local date"

    content = (sessions_dir / "2026-02-23.md").read_text()
    # Timestamp in the record must also reflect UTC
    assert "23:30:00 UTC" in content


# ──────────────────────────────────────────────────────────────────
# Date rotation
# ──────────────────────────────────────────────────────────────────


async def test_new_file_created_on_date_change(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    day1 = date(2026, 2, 23)
    day2 = date(2026, 2, 24)

    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = day1
        mock_dt.now.return_value = datetime(2026, 2, 23, 10, 0, 0, tzinfo=timezone.utc)
        await hm.record_user_message(1, "day 1 message")

    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = day2
        mock_dt.now.return_value = datetime(2026, 2, 24, 10, 0, 0, tzinfo=timezone.utc)
        await hm.record_user_message(1, "day 2 message")

    assert (tmp_path / "history" / "sessions" / "2026-02-23.md").exists()
    assert (tmp_path / "history" / "sessions" / "2026-02-24.md").exists()
    assert "day 1 message" in (tmp_path / "history" / "sessions" / "2026-02-23.md").read_text()
    assert "day 2 message" in (tmp_path / "history" / "sessions" / "2026-02-24.md").read_text()


# ──────────────────────────────────────────────────────────────────
# Tool result suppression
# ──────────────────────────────────────────────────────────────────


async def test_tool_result_suppressed_for_read(tmp_path: Path) -> None:
    """A successful Read result is suppressed — only the summary line is written."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolResult(content="line1\nline2\nline3", id=3, tool_name="Read"))

    content = _today_file(tmp_path).read_text()
    assert "✓ Read completed (3 lines," in content
    assert "line1" not in content


async def test_tool_result_read_error_not_suppressed(tmp_path: Path) -> None:
    """A failed Read result is NOT suppressed — full content is logged."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolResult(content="Error: file not found", id=4, tool_name="Read", is_error=True))

    content = _today_file(tmp_path).read_text()
    assert "Error: file not found" in content
    assert "✓ Read" not in content


# ──────────────────────────────────────────────────────────────────
# Pipeline decision events in history (Tier 1 logging)
# ──────────────────────────────────────────────────────────────────


async def test_classification_event_written_to_history(tmp_path: Path) -> None:
    """ClassificationEvent must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        await hm.record_event(1, ClassificationEvent(intent="task", confidence=0.92))

    content = _today_file(tmp_path).read_text()
    assert "🏷 Classification" in content
    assert '"intent": "task"' in content
    assert '"confidence": 0.92' in content


async def test_routing_event_chat_written_to_history(tmp_path: Path) -> None:
    """RoutingEvent with chat routing must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        await hm.record_event(1, RoutingEvent(routing="chat", model="claude-sonnet-4-6"))

    content = _today_file(tmp_path).read_text()
    assert "🔀 Pipeline" in content
    assert "direct chat response" in content
    assert "claude-sonnet-4-6" in content


async def test_plan_event_written_to_history(tmp_path: Path) -> None:
    """PlanEvent must be written to the history file with summary and agent count."""
    hm = _make_manager(tmp_path)
    plan = AgentPlan(
        scope="large",
        summary="Refactor auth module",
        agents=[
            AgentTask(id="a1", task="Extract middleware"),
            AgentTask(id="a2", task="Update imports", depends_on=("a1",)),
        ],
    )
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "big task")
        await hm.record_event(1, PlanEvent(plan=plan, summary=plan.summary))

    content = _today_file(tmp_path).read_text()
    assert "📋 Plan" in content
    assert "Refactor auth module" in content
    assert "a1 (Extract middleware)" in content
    assert "a2 (Update imports)" in content


async def test_subagent_started_written_to_history(tmp_path: Path) -> None:
    """SubagentStarted must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "big task")
        await hm.record_event(1, SubagentStarted(
            agent_id="abc", agent_type="background", agent_name="Atlas",
        ))

    content = _today_file(tmp_path).read_text()
    assert "🤖 Agent" in content
    assert "Atlas" in content
    assert "started" in content


async def test_subagent_stopped_written_to_history(tmp_path: Path) -> None:
    """SubagentStopped must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "big task")
        await hm.record_event(1, SubagentStopped(
            agent_id="abc", agent_type="background", agent_name="Atlas",
        ))

    content = _today_file(tmp_path).read_text()
    assert "🤖 Agent" in content
    assert "Atlas" in content
    assert "completed" in content


async def test_wave_started_written_to_history(tmp_path: Path) -> None:
    """WaveStarted must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "big task")
        await hm.record_event(1, WaveStarted(wave_number=1, agent_names=["a1", "a2"]))

    content = _today_file(tmp_path).read_text()
    assert "🌊 Wave 1" in content
    assert "a1" in content


# ──────────────────────────────────────────────────────────────────
# Legacy file migration
# ──────────────────────────────────────────────────────────────────


async def test_migrate_legacy_files_moves_date_files(tmp_path: Path) -> None:
    """YYYY-MM-DD.md files at the root of history_dir are moved to sessions/."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    legacy = history_dir / "2026-01-01.md"
    legacy.write_text("old content", encoding="utf-8")

    HistoryManager(str(history_dir))

    assert not legacy.exists()
    assert (history_dir / "sessions" / "2026-01-01.md").read_text() == "old content"


async def test_migrate_legacy_files_skips_non_date_md(tmp_path: Path) -> None:
    """Non-date .md files at the root are not moved."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    readme = history_dir / "README.md"
    readme.write_text("docs", encoding="utf-8")

    HistoryManager(str(history_dir))

    assert readme.exists()
    assert not (history_dir / "sessions" / "README.md").exists()


async def test_migrate_legacy_files_logs_info(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Migration logs one INFO message per moved file."""
    import logging
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "2026-02-10.md").write_text("x", encoding="utf-8")

    with caplog.at_level(logging.INFO, logger="archon"):
        HistoryManager(str(history_dir))

    assert any("Migrated legacy history file" in r.message for r in caplog.records)


async def test_migrate_legacy_files_no_op_when_none_exist(tmp_path: Path) -> None:
    """No error when history root has no .md files to migrate."""
    history_dir = tmp_path / "history"
    history_dir.mkdir()

    # Should not raise
    HistoryManager(str(history_dir))


async def test_record_archon_message_appends_blockquote(tmp_path: Path) -> None:
    """record_archon_message writes a blockquote line to the session file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        await hm.record_archon_message("⏳ Processing...")

    content = _today_file(tmp_path).read_text()
    assert "> Archon" in content
    assert "⏳ Processing..." in content


async def test_record_archon_message_no_prior_user_message(tmp_path: Path) -> None:
    """record_archon_message works even without a prior record_user_message call."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_dt.now.return_value = _FIXED_DT
        # Manually create the file so _append has somewhere to write
        (tmp_path / "history" / "sessions").mkdir(parents=True, exist_ok=True)
        (tmp_path / "history" / "sessions" / f"{_FIXED_DATE.isoformat()}.md").write_text("")
        await hm.record_archon_message("⏳ Working...")

    content = _today_file(tmp_path).read_text()
    assert "⏳ Working..." in content


async def test_tool_result_custom_suppressed_set(tmp_path: Path) -> None:
    """HistoryManager constructed with custom suppression skips only those tools."""
    from archon.ai.history_manager import HistoryManager
    hm = HistoryManager(str(tmp_path / "history"), suppressed_tools=frozenset({"MyTool"}))
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "q")
        await hm.record_event(1, ToolResult(content="secret data", id=5, tool_name="MyTool"))
        await hm.record_event(1, ToolResult(content="visible data", id=6, tool_name="Read"))

    content = _today_file(tmp_path).read_text()
    assert "✓ MyTool completed" in content
    assert "secret data" not in content
    assert "visible data" in content


# ──────────────────────────────────────────────────────────────────
# Task 2.3 — Auto-separator on source transitions + record_raw()
# ──────────────────────────────────────────────────────────────────


async def test_history_manager_auto_separator_on_source_transition(tmp_path: Path) -> None:
    """A separator (---) is inserted when source transitions from router to main."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        # First: a router ToolStarted
        router_tool = ToolStarted(name="ListHistory", id=1, source="router")
        await hm.record_event(1, router_tool)
        # Then: a main-session Response (source transition router → main)
        main_response = Response(content="Here is the answer")
        await hm.record_event(1, main_response)

    content = _today_file(tmp_path).read_text()
    # A separator must appear between the router and main event sections
    assert "\n---\n" in content


async def test_history_manager_no_separator_without_router_events(tmp_path: Path) -> None:
    """No source-transition separator when all events are from the main session only.

    Note: Response events already include '---' in their own rendered output.
    We verify there is NO separator between two ToolStarted events (same source, no transition).
    """
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        # Two main-session ToolStarted in a row — no source transition → no separator injected
        await hm.record_event(1, ToolStarted(name="Read", id=1))
        await hm.record_event(1, ToolStarted(name="Write", id=2))

    content = _today_file(tmp_path).read_text()
    # No source transition → no separator injected between the two ToolStarted events
    assert "\n---\n" not in content


async def test_history_manager_separator_not_duplicated(tmp_path: Path) -> None:
    """Multiple consecutive router events produce only one transition separator when switching to main."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        # Two router events in a row — no separator between them
        await hm.record_event(1, ToolStarted(name="ListHistory", id=1, source="router"))
        await hm.record_event(1, ToolStarted(name="ReadHistory", id=2, source="router"))
        # Then a main-session ToolStarted (no built-in '---') — exactly one separator from transition
        await hm.record_event(1, ToolStarted(name="Read", id=3))

    content = _today_file(tmp_path).read_text()
    # Exactly one separator: the source-transition separator (no built-in '---' from ToolStarted)
    assert content.count("\n---\n") == 1
    # The separator appears between router block and main block
    router_tool_pos = content.index("[Router] Tool: ListHistory")
    main_tool_pos = content.index("🔧 Tool: Read")
    sep_pos = content.index("\n---\n")
    assert router_tool_pos < sep_pos < main_tool_pos


async def test_history_manager_record_raw_appends_content(tmp_path: Path) -> None:
    """record_raw() appends arbitrary content directly to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "setup")
        await hm.record_raw(1, "## Custom raw section\n")

    content = _today_file(tmp_path).read_text()
    assert "## Custom raw section" in content


async def test_history_manager_record_raw_empty_string_noop(tmp_path: Path) -> None:
    """record_raw() with empty string does not write anything."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        await hm.record_user_message(1, "hello")
        await hm.record_raw(1, "")
        await hm.record_raw(1, "real content")

    content = _today_file(tmp_path).read_text()
    # "real content" appears, empty string didn't cause issues
    assert "real content" in content


# ──────────────────────────────────────────────────────────────────
# Fix 2 — _last_source reset between conversations + bidirectional separator
# ──────────────────────────────────────────────────────────────────


async def test_history_manager_separator_bidirectional(tmp_path: Path) -> None:
    """Separator inserted on both router->main AND main->router transitions."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT

        router_event = ToolStarted(name="history_read", input={}, source="router")
        main_event = ToolStarted(name="Read", input={}, source="orchestrator")

        # Sequence: router -> main -> router -> main = 3 transitions = 3 separators
        await hm.record_user_message(1, "hello")
        await hm.record_event(1, router_event)
        await hm.record_event(1, main_event)
        await hm.record_event(1, router_event)
        await hm.record_event(1, main_event)

    content = _today_file(tmp_path).read_text()
    assert content.count("---") == 3  # 3 transitions = 3 separators


async def test_history_manager_last_source_reset_on_new_message(tmp_path: Path) -> None:
    """_last_source is cleared on record_user_message so no spurious separator at conversation start."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT

        # First message: ends with a router event
        await hm.record_user_message(1, "first message")
        await hm.record_event(1, ToolStarted(name="history_read", input={}, source="router"))

        # Second message: starts with a main-session event (no router event before it in THIS turn)
        await hm.record_user_message(1, "second message")
        await hm.record_event(1, ToolStarted(name="Read", input={}, source="orchestrator"))

    content = _today_file(tmp_path).read_text()
    # Second message's ToolStarted must NOT have a separator before it
    # (the separator between turns is the user message heading, not a "---")
    second_msg_pos = content.index("second message")
    read_tool_pos = content.index("Tool: Read")
    # No "---" between the second user message and the first main-session event
    section = content[second_msg_pos:read_tool_pos]
    assert "---" not in section
