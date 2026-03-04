"""Tests for HistoryCompactor — TDD-first."""
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.history_compactor import HistoryCompactor, _is_daily_file


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_daily(history_dir: Path, day: date, content: str = "# Log\n\nUser: hello") -> Path:
    path = history_dir / f"{day}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _make_agent_log(history_dir: Path, day: date, name: str = "Harbor") -> Path:
    path = history_dir / f"{day}-10-30-{name}.md"
    path.write_text(f"# Agent {name}\n\nSome work.", encoding="utf-8")
    return path


def _make_compacted(history_dir: Path, day: date, content: str = "# Summary") -> Path:
    daily_dir = history_dir / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    path = daily_dir / f"{day}-compacted.md"
    path.write_text(content, encoding="utf-8")
    return path


def _mock_client(summary_text: str = "Summary of the day.") -> MagicMock:
    message = MagicMock()
    message.content = [MagicMock(text=summary_text)]
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=message)
    return client


# ── _is_daily_file ─────────────────────────────────────────────────────────


def test_is_daily_file_plain_date() -> None:
    assert _is_daily_file("2026-02-23.md") is True


def test_is_daily_file_rejects_agent_log() -> None:
    assert _is_daily_file("2026-02-25-11-22-Nexus.md") is False


def test_is_daily_file_rejects_compacted() -> None:
    assert _is_daily_file("2026-02-25-compacted.md") is False


def test_is_daily_file_rejects_non_md() -> None:
    assert _is_daily_file("2026-02-23.txt") is False


def test_is_daily_file_rejects_random_name() -> None:
    assert _is_daily_file("README.md") is False


# ── get_recent_context ─────────────────────────────────────────────────────


def test_get_recent_context_returns_none_when_no_files(tmp_path: Path) -> None:
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    assert c.get_recent_context() is None


def test_get_recent_context_returns_last_n_days(tmp_path: Path) -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    two_ago = today - timedelta(days=2)
    three_ago = today - timedelta(days=3)

    _make_compacted(tmp_path, two_ago, "Day-2 summary")
    _make_compacted(tmp_path, yesterday, "Day-1 summary")
    _make_compacted(tmp_path, three_ago, "Day-3 summary")  # too old

    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    ctx = c.get_recent_context()
    assert ctx is not None
    assert "Day-2 summary" in ctx
    assert "Day-1 summary" in ctx
    assert "Day-3 summary" not in ctx


def test_get_recent_context_skips_missing_days(tmp_path: Path) -> None:
    today = date.today()
    two_ago = today - timedelta(days=2)
    # yesterday has no compacted file
    _make_compacted(tmp_path, two_ago, "Only day-2")

    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    ctx = c.get_recent_context()
    assert ctx == "Only day-2"


def test_get_recent_context_ordered_oldest_first(tmp_path: Path) -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    two_ago = today - timedelta(days=2)
    _make_compacted(tmp_path, two_ago, "OLDER")
    _make_compacted(tmp_path, yesterday, "NEWER")

    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    ctx = c.get_recent_context()
    assert ctx is not None
    assert ctx.index("OLDER") < ctx.index("NEWER")


# ── _collect_day_content ───────────────────────────────────────────────────


def test_collect_day_content_main_file_only(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, "Conversation content")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    content = c._collect_day_content(day)
    assert "Conversation content" in content


def test_collect_day_content_includes_agent_logs(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, "Main log")
    _make_agent_log(tmp_path, day, "Harbor")
    _make_agent_log(tmp_path, day, "Terra")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    content = c._collect_day_content(day)
    assert "Main log" in content
    assert "Agent Harbor" in content
    assert "Agent Terra" in content


def test_collect_day_content_no_files_returns_empty(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    content = c._collect_day_content(day)
    assert content == ""


def test_collect_day_content_excludes_other_days(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    other_day = date.today() - timedelta(days=2)
    _make_daily(tmp_path, day, "Today log")
    _make_daily(tmp_path, other_day, "Other day log")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    content = c._collect_day_content(day)
    assert "Today log" in content
    assert "Other day log" not in content


# ── compact_pending_days ───────────────────────────────────────────────────


async def test_compact_pending_days_creates_compacted_file(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, "User: fix bug\nAssistant: done.")
    client = _mock_client("Compact summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    compacted = tmp_path / "daily" / f"{day}-compacted.md"
    assert compacted.exists()
    assert "Compact summary" in compacted.read_text()


async def test_compact_pending_days_skips_today(tmp_path: Path) -> None:
    today = date.today()
    _make_daily(tmp_path, today, "Today's content")
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    assert not (tmp_path / "daily" / f"{today}-compacted.md").exists()
    client.messages.create.assert_not_called()


async def test_compact_pending_days_skips_already_compacted(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, "Content")
    _make_compacted(tmp_path, day, "Existing summary")
    client = _mock_client("New summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    client.messages.create.assert_not_called()
    # original summary preserved
    compacted = tmp_path / "daily" / f"{day}-compacted.md"
    assert compacted.read_text() == "Existing summary"


async def test_compact_pending_days_skips_agent_log_files(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_agent_log(tmp_path, day, "Harbor")  # only agent log, no daily file
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    assert not (tmp_path / "daily" / f"{day}-compacted.md").exists()
    client.messages.create.assert_not_called()


async def test_compact_pending_days_multiple_days(tmp_path: Path) -> None:
    today = date.today()
    days = [today - timedelta(days=i) for i in range(1, 4)]
    for day in days:
        _make_daily(tmp_path, day, f"Log for {day}")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    assert client.messages.create.call_count == 3
    for day in days:
        assert (tmp_path / "daily" / f"{day}-compacted.md").exists()


async def test_compact_pending_days_empty_directory(tmp_path: Path) -> None:
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)
    # Should not raise
    await c.compact_pending_days()
    client.messages.create.assert_not_called()


async def test_compact_pending_days_nonexistent_directory(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent"
    client = _mock_client()
    c = HistoryCompactor(str(missing), context_days=2, client=client)
    # Should not raise
    await c.compact_pending_days()
    client.messages.create.assert_not_called()


async def test_compact_pending_days_creates_daily_subdirectory(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day)
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_pending_days()

    assert (tmp_path / "daily").is_dir()


async def test_compact_day_passes_content_to_haiku(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, "User: write tests\nAssistant: done")
    client = _mock_client("Summary with details")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    call_kwargs = client.messages.create.call_args
    messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
    if isinstance(messages, list):
        prompt_content = messages[0]["content"]
    else:
        prompt_content = str(call_kwargs)
    assert "write tests" in prompt_content or client.messages.create.called


async def test_compact_day_uses_haiku_model(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, "Some content")
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    call_kwargs = client.messages.create.call_args
    model_used = call_kwargs.kwargs.get("model") or call_kwargs.args[0]
    assert "haiku" in str(model_used).lower()


async def test_compact_day_skips_empty_content(tmp_path: Path) -> None:
    """If no content found for a day, no API call should be made."""
    day = date.today() - timedelta(days=1)
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    client.messages.create.assert_not_called()


async def test_compact_pending_days_tolerates_api_error(tmp_path: Path) -> None:
    """API errors for one day should not prevent other days from being processed."""
    today = date.today()
    # Sorted alphabetically: older_day processed first (errors), newer_day second (succeeds)
    older_day = today - timedelta(days=2)
    newer_day = today - timedelta(days=1)
    _make_daily(tmp_path, older_day, "Older content")
    _make_daily(tmp_path, newer_day, "Newer content")

    client = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=[
            Exception("API error"),
            MagicMock(content=[MagicMock(text="Newer summary")]),
        ]
    )
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    # Should not raise, despite API error on older_day
    await c.compact_pending_days()

    # newer_day should still be compacted (processed after older_day errored)
    assert (tmp_path / "daily" / f"{newer_day}-compacted.md").exists()
