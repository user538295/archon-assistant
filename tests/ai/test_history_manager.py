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


def test_directory_created_if_missing(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "hello")

    assert (tmp_path / "history" / "sessions").is_dir()


def test_record_user_message_creates_file(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "hello")

    assert _today_file(tmp_path).exists()


# ──────────────────────────────────────────────────────────────────
# Header
# ──────────────────────────────────────────────────────────────────


def test_file_starts_with_date_header(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "hello")

    content = _today_file(tmp_path).read_text()
    assert content.startswith("# 2026-02-23 — Archon Conversations\n")


def test_header_not_duplicated_on_second_write(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "first message")
        hm.record_user_message(1, "second message")

    content = _today_file(tmp_path).read_text()
    assert content.count("# 2026-02-23 — Archon Conversations") == 1


# ──────────────────────────────────────────────────────────────────
# User message section
# ──────────────────────────────────────────────────────────────────


def test_user_message_section_header(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(42, "hello")

    content = _today_file(tmp_path).read_text()
    assert "## 14:30:45 UTC · User 42\n" in content


def test_user_message_with_cwd(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(42, "hello", cwd="/home/user/project")

    content = _today_file(tmp_path).read_text()
    assert "## 14:30:45 UTC · User 42 · /home/user/project\n" in content


def test_user_message_body_written(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "Can you list files?")

    content = _today_file(tmp_path).read_text()
    assert "Can you list files?" in content


# ──────────────────────────────────────────────────────────────────
# Event rendering
# ──────────────────────────────────────────────────────────────────


def test_thinking_result_emits_content(tmp_path: Path) -> None:
    """ThinkingResult now writes a thought section (ThinkingStarted was removed)."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        size_before = _today_file(tmp_path).stat().st_size
        hm.record_event(1, ThinkingResult(content="pondering"))
        size_after = _today_file(tmp_path).stat().st_size

    assert size_after > size_before


def test_thinking_result_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ThinkingResult(content="I should list files."))

    content = _today_file(tmp_path).read_text()
    assert f"### 💭 Thinking · {_FIXED_TS}\n" in content
    assert "I should list files." in content


def test_tool_started_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolStarted(name="bash", input="ls -la", id=1))

    content = _today_file(tmp_path).read_text()
    assert f"### 🔧 Tool: bash [1] · {_FIXED_TS}\n" in content
    assert "```\nls -la\n```" in content


def test_tool_started_no_id_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolStarted(name="Read", input="/tmp/file.txt"))

    content = _today_file(tmp_path).read_text()
    assert f"### 🔧 Tool: Read · {_FIXED_TS}\n" in content
    assert "[0]" not in content


def test_tool_result_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="file contents here", id=2))

    content = _today_file(tmp_path).read_text()
    assert f"### 📤 Result [2] · {_FIXED_TS}\n" in content
    assert "```\nfile contents here\n```" in content


def test_tool_result_no_id_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="output"))

    content = _today_file(tmp_path).read_text()
    assert f"### 📤 Result · {_FIXED_TS}\n" in content
    assert "[0]" not in content


def test_response_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "list files please")
        hm.record_event(1, Response(content="Here are the files: ..."))

    content = _today_file(tmp_path).read_text()
    assert f"### ✅ Response · {_FIXED_TS}\n" in content
    assert "Here are the files: ..." in content


def test_response_includes_contextual_retrieval_question(tmp_path: Path) -> None:
    """Response must repeat the last user question as a blockquote for contextual retrieval."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "list files please")
        hm.record_event(1, Response(content="Here are the files: ..."))

    content = _today_file(tmp_path).read_text()
    assert '> User: "list files please"' in content


def test_response_question_truncated_at_120_chars(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    long_q = "x" * 150
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, long_q)
        hm.record_event(1, Response(content="answer"))

    content = _today_file(tmp_path).read_text()
    assert '> User: "' + "x" * 120 + '..."' in content


def test_response_ends_with_separator(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, Response(content="done"))

    content = _today_file(tmp_path).read_text()
    assert content.endswith("\n\n---\n")


def test_error_event_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ErrorEvent(message="SDK timeout"))

    content = _today_file(tmp_path).read_text()
    assert f"### ❌ Error · {_FIXED_TS}\n" in content
    assert "SDK timeout" in content


def test_error_event_ends_with_separator(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ErrorEvent(message="oops"))

    content = _today_file(tmp_path).read_text()
    assert content.endswith("\n\n---\n")


# ──────────────────────────────────────────────────────────────────
# Response with no prior question — High gap
# ──────────────────────────────────────────────────────────────────


def test_response_rendered_without_blockquote_when_no_prior_question(tmp_path: Path) -> None:
    """When record_event is called with Response for a user_id that has never had
    record_user_message called, the q_ctx blockquote must be omitted."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        # Seed the file via a different user so the directory + file exist
        hm.record_user_message(1, "question from user one")
        # Record a response for user 999 — who has never sent a message
        hm.record_event(999, Response(content="answer without prior question"))

    content = _today_file(tmp_path).read_text()
    assert "answer without prior question" in content
    # The '> User:' blockquote must NOT appear (user 999 has no prior question)
    assert '> User:' not in content


def test_response_heading_present_when_no_prior_question(tmp_path: Path) -> None:
    """The '✅ Response' heading must still be written even with no prior question."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "seed message")
        hm.record_event(999, Response(content="no-question response"))

    content = _today_file(tmp_path).read_text()
    assert "✅ Response" in content
    assert "no-question response" in content


# ──────────────────────────────────────────────────────────────────
# Date rotation
# ──────────────────────────────────────────────────────────────────


def test_new_file_created_on_date_change(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    day1 = date(2026, 2, 23)
    day2 = date(2026, 2, 24)

    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = day1
        mock_dt.now.return_value = datetime(2026, 2, 23, 10, 0, 0, tzinfo=timezone.utc)
        hm.record_user_message(1, "day 1 message")

    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = day2
        mock_dt.now.return_value = datetime(2026, 2, 24, 10, 0, 0, tzinfo=timezone.utc)
        hm.record_user_message(1, "day 2 message")

    assert (tmp_path / "history" / "sessions" / "2026-02-23.md").exists()
    assert (tmp_path / "history" / "sessions" / "2026-02-24.md").exists()
    assert "day 1 message" in (tmp_path / "history" / "sessions" / "2026-02-23.md").read_text()
    assert "day 2 message" in (tmp_path / "history" / "sessions" / "2026-02-24.md").read_text()


# ──────────────────────────────────────────────────────────────────
# Tool result suppression
# ──────────────────────────────────────────────────────────────────


def test_tool_result_suppressed_for_read(tmp_path: Path) -> None:
    """A successful Read result is suppressed — only the summary line is written."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="line1\nline2\nline3", id=3, tool_name="Read"))

    content = _today_file(tmp_path).read_text()
    assert "✓ Read completed (3 lines," in content
    assert "line1" not in content


def test_tool_result_read_error_not_suppressed(tmp_path: Path) -> None:
    """A failed Read result is NOT suppressed — full content is logged."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="Error: file not found", id=4, tool_name="Read", is_error=True))

    content = _today_file(tmp_path).read_text()
    assert "Error: file not found" in content
    assert "✓ Read" not in content


# ──────────────────────────────────────────────────────────────────
# Pipeline decision events in history (Tier 1 logging)
# ──────────────────────────────────────────────────────────────────


def test_classification_event_written_to_history(tmp_path: Path) -> None:
    """ClassificationEvent must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "hello")
        hm.record_event(1, ClassificationEvent(intent="task", confidence=0.92))

    content = _today_file(tmp_path).read_text()
    assert "🏷 Classification" in content
    assert '"intent": "task"' in content
    assert '"confidence": 0.92' in content


def test_routing_event_direct_written_to_history(tmp_path: Path) -> None:
    """RoutingEvent with direct routing must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "hello")
        hm.record_event(1, RoutingEvent(routing="direct", model="claude-sonnet-4-6"))

    content = _today_file(tmp_path).read_text()
    assert "🔀 Pipeline" in content
    assert "direct response" in content
    assert "claude-sonnet-4-6" in content


def test_plan_event_written_to_history(tmp_path: Path) -> None:
    """PlanEvent must be written to the history file with summary and agent count."""
    hm = _make_manager(tmp_path)
    plan = AgentPlan(
        scope="large",
        summary="Refactor auth module",
        agents=[
            AgentTask(id="a1", task="Extract middleware"),
            AgentTask(id="a2", task="Update imports", depends_on=["a1"]),
        ],
    )
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "big task")
        hm.record_event(1, PlanEvent(plan=plan, summary=plan.summary))

    content = _today_file(tmp_path).read_text()
    assert "📋 Plan" in content
    assert "Refactor auth module" in content
    assert "a1 (Extract middleware)" in content
    assert "a2 (Update imports)" in content


def test_subagent_started_written_to_history(tmp_path: Path) -> None:
    """SubagentStarted must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "big task")
        hm.record_event(1, SubagentStarted(
            agent_id="abc", agent_type="background", agent_name="Atlas",
        ))

    content = _today_file(tmp_path).read_text()
    assert "🤖 Agent" in content
    assert "Atlas" in content
    assert "started" in content


def test_subagent_stopped_written_to_history(tmp_path: Path) -> None:
    """SubagentStopped must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "big task")
        hm.record_event(1, SubagentStopped(
            agent_id="abc", agent_type="background", agent_name="Atlas",
        ))

    content = _today_file(tmp_path).read_text()
    assert "🤖 Agent" in content
    assert "Atlas" in content
    assert "completed" in content


def test_wave_started_written_to_history(tmp_path: Path) -> None:
    """WaveStarted must be written to the history file."""
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt, \
         patch("archon.ai.event_renderer.datetime") as mock_er_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        mock_er_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "big task")
        hm.record_event(1, WaveStarted(wave_number=1, agent_names=["a1", "a2"]))

    content = _today_file(tmp_path).read_text()
    assert "🌊 Wave 1" in content
    assert "a1" in content


def test_tool_result_custom_suppressed_set(tmp_path: Path) -> None:
    """HistoryManager constructed with custom suppression skips only those tools."""
    from archon.ai.history_manager import HistoryManager
    hm = HistoryManager(str(tmp_path / "history"), suppressed_tools=frozenset({"MyTool"}))
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="secret data", id=5, tool_name="MyTool"))
        hm.record_event(1, ToolResult(content="visible data", id=6, tool_name="Read"))

    content = _today_file(tmp_path).read_text()
    assert "✓ MyTool completed" in content
    assert "secret data" not in content
    assert "visible data" in content


# ──────────────────────────────────────────────────────────────────
# Legacy file warning
# ──────────────────────────────────────────────────────────────────


def test_legacy_files_trigger_warning(tmp_path: Path) -> None:
    """HistoryManager logs a WARNING when YYYY-MM-DD.md files exist in the root history dir."""
    history_root = tmp_path / "history"
    history_root.mkdir(parents=True)
    (history_root / "2026-01-15.md").write_text("old content")
    (history_root / "2026-01-16.md").write_text("old content")

    import logging
    with patch("archon.ai.history_manager._log") as mock_log:
        HistoryManager(str(history_root))

    mock_log.warning.assert_called_once()
    call_args = mock_log.warning.call_args
    assert call_args[0][1] == 2  # count of legacy files


def test_no_warning_when_no_legacy_files(tmp_path: Path) -> None:
    """No warning is logged when the history root has no legacy date-named files."""
    with patch("archon.ai.history_manager._log") as mock_log:
        HistoryManager(str(tmp_path / "history"))

    mock_log.warning.assert_not_called()


def test_no_warning_when_root_dir_absent(tmp_path: Path) -> None:
    """No warning is logged (no crash) when the history root directory doesn't exist yet."""
    with patch("archon.ai.history_manager._log") as mock_log:
        HistoryManager(str(tmp_path / "history" / "nonexistent"))

    mock_log.warning.assert_not_called()


def test_non_date_files_in_root_do_not_trigger_warning(tmp_path: Path) -> None:
    """Files in root that don't match YYYY-MM-DD.md are ignored."""
    history_root = tmp_path / "history"
    history_root.mkdir(parents=True)
    (history_root / "README.md").write_text("docs")
    (history_root / "summary.md").write_text("summary")

    with patch("archon.ai.history_manager._log") as mock_log:
        HistoryManager(str(history_root))

    mock_log.warning.assert_not_called()
