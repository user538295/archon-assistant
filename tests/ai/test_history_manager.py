"""Tests for HistoryManager — TDD for chat history persistence."""
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)
from archon.ai.history_manager import HistoryManager

_FIXED_DATE = date(2026, 2, 23)
_FIXED_DT = datetime(2026, 2, 23, 14, 30, 45, tzinfo=timezone.utc)
_FIXED_TS = "14:30:45"


def _make_manager(tmp_path: Path) -> HistoryManager:
    return HistoryManager(str(tmp_path / "history"))


def _today_file(tmp_path: Path) -> Path:
    return tmp_path / "history" / f"{_FIXED_DATE.isoformat()}.md"


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

    assert (tmp_path / "history").is_dir()


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


def test_thinking_started_emits_nothing(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        size_before = _today_file(tmp_path).stat().st_size
        hm.record_event(1, ThinkingStarted())
        size_after = _today_file(tmp_path).stat().st_size

    assert size_before == size_after


def test_thinking_result_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ThinkingResult(content="I should list files."))

    content = _today_file(tmp_path).read_text()
    assert f"### 💭 Thought · {_FIXED_TS}\n" in content
    assert "I should list files." in content


def test_tool_started_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolStarted(name="bash", input="ls -la", id=1))

    content = _today_file(tmp_path).read_text()
    assert f"### 🔧 Tool: bash [1] · {_FIXED_TS}\n" in content
    assert "```\nls -la\n```" in content


def test_tool_started_no_id_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolStarted(name="Read", input="/tmp/file.txt"))

    content = _today_file(tmp_path).read_text()
    assert f"### 🔧 Tool: Read · {_FIXED_TS}\n" in content
    assert "[0]" not in content


def test_tool_result_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="file contents here", id=2))

    content = _today_file(tmp_path).read_text()
    assert f"### 📤 Result [2] · {_FIXED_TS}\n" in content
    assert "```\nfile contents here\n```" in content


def test_tool_result_no_id_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
        hm.record_user_message(1, "q")
        hm.record_event(1, ToolResult(content="output"))

    content = _today_file(tmp_path).read_text()
    assert f"### 📤 Result · {_FIXED_TS}\n" in content
    assert "[0]" not in content


def test_response_rendered(tmp_path: Path) -> None:
    hm = _make_manager(tmp_path)
    with patch("archon.ai.history_manager.date") as mock_date, \
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
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
         patch("archon.ai.history_manager.datetime") as mock_dt:
        mock_date.today.return_value = _FIXED_DATE
        mock_dt.now.return_value = _FIXED_DT
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

    assert (tmp_path / "history" / "2026-02-23.md").exists()
    assert (tmp_path / "history" / "2026-02-24.md").exists()
    assert "day 1 message" in (tmp_path / "history" / "2026-02-23.md").read_text()
    assert "day 2 message" in (tmp_path / "history" / "2026-02-24.md").read_text()
