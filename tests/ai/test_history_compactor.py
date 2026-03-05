"""Tests for HistoryCompactor — TDD-first."""
import logging
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.history_compactor import HistoryCompactor, _extract_responses, _is_daily_file


# ── Helpers ────────────────────────────────────────────────────────────────


_RESPONSE_CONTENT = (
    "### ✅ Response · 10:00:00 UTC\n\n"
    '> User: "do the work"\n\n'
    "Work done successfully.\n\n---\n"
)


def _make_daily(
    history_dir: Path,
    day: date,
    content: str = _RESPONSE_CONTENT,
) -> Path:
    sessions_dir = history_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{day}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _make_agent_log(history_dir: Path, day: date, name: str = "Harbor") -> Path:
    sessions_dir = history_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / f"{day}-10-30-{name}.md"
    path.write_text(
        f"# Agent {name}\n\n"
        f"### ✅ Response · 10:30:00 UTC\n\nAgent {name} completed the task.\n\n---\n",
        encoding="utf-8",
    )
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
    _make_daily(tmp_path, day, _RESPONSE_CONTENT)
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
        _make_daily(tmp_path, day, _RESPONSE_CONTENT)
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
    content = "### ✅ Response · 10:00:00 UTC\n\n> User: \"write tests\"\n\nTests written.\n\n---\n"
    _make_daily(tmp_path, day, content)
    client = _mock_client("Summary with details")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "write tests" in prompt


async def test_compact_day_uses_haiku_model(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    _make_daily(tmp_path, day, _RESPONSE_CONTENT)
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    model_used = client.messages.create.call_args.kwargs["model"]
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
    _make_daily(tmp_path, older_day, _RESPONSE_CONTENT)
    _make_daily(tmp_path, newer_day, _RESPONSE_CONTENT)

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


# ── compact_today ──────────────────────────────────────────────────────────


async def test_compact_today_creates_partial_file(tmp_path: Path) -> None:
    today = date.today()
    _make_daily(tmp_path, today, _RESPONSE_CONTENT)
    client = _mock_client("Today summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    partial = tmp_path / "daily" / f"{today}-partial.md"
    assert partial.exists()
    assert "Today summary" in partial.read_text()


async def test_compact_today_always_overwrites_existing_partial(tmp_path: Path) -> None:
    today = date.today()
    _make_daily(tmp_path, today, _RESPONSE_CONTENT)
    old = tmp_path / "daily"
    old.mkdir(parents=True, exist_ok=True)
    (old / f"{today}-partial.md").write_text("Old partial", encoding="utf-8")

    client = _mock_client("Fresh summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    assert "Fresh summary" in (old / f"{today}-partial.md").read_text()
    client.messages.create.assert_called_once()


async def test_compact_today_skips_empty_content(tmp_path: Path) -> None:
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    client.messages.create.assert_not_called()


async def test_compact_today_does_not_create_permanent_compacted_file(tmp_path: Path) -> None:
    today = date.today()
    _make_daily(tmp_path, today, _RESPONSE_CONTENT)
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    assert not (tmp_path / "daily" / f"{today}-compacted.md").exists()
    assert (tmp_path / "daily" / f"{today}-partial.md").exists()


async def test_compact_today_includes_agent_logs(tmp_path: Path) -> None:
    today = date.today()
    _make_daily(tmp_path, today, _RESPONSE_CONTENT)
    _make_agent_log(tmp_path, today, "Nexus")
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    prompt_content = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Work done successfully." in prompt_content
    assert "Agent Nexus completed the task." in prompt_content


# ── get_recent_context with today partial ─────────────────────────────────


def test_get_recent_context_includes_today_partial(tmp_path: Path) -> None:
    today = date.today()
    (tmp_path / "daily").mkdir(parents=True)
    (tmp_path / "daily" / f"{today}-partial.md").write_text("Today partial", encoding="utf-8")

    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    ctx = c.get_recent_context()

    assert ctx is not None
    assert "Today partial" in ctx


def test_get_recent_context_partial_only_no_past_summaries(tmp_path: Path) -> None:
    today = date.today()
    (tmp_path / "daily").mkdir(parents=True)
    (tmp_path / "daily" / f"{today}-partial.md").write_text("Only today", encoding="utf-8")

    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    assert c.get_recent_context() == "Only today"


def test_get_recent_context_today_partial_is_last(tmp_path: Path) -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    _make_compacted(tmp_path, yesterday, "Yesterday summary")
    (tmp_path / "daily").mkdir(parents=True, exist_ok=True)
    (tmp_path / "daily" / f"{today}-partial.md").write_text("Today partial", encoding="utf-8")

    c = HistoryCompactor(str(tmp_path), context_days=1, client=_mock_client())
    ctx = c.get_recent_context()
    assert ctx is not None
    assert ctx.index("Yesterday summary") < ctx.index("Today partial")


# ── startup_context_prompt ─────────────────────────────────────────────────


def test_startup_context_prompt_contains_history_dir(tmp_path: Path) -> None:
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt()
    assert str(tmp_path) in prompt
    assert "sessions" in prompt


def test_startup_context_prompt_contains_today(tmp_path: Path) -> None:
    today = date.today().isoformat()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt()
    assert today in prompt


def test_startup_context_prompt_without_qmd_has_no_qmd_mention(tmp_path: Path) -> None:
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt(qmd_enabled=False)
    assert "qmd" not in prompt.lower()


def test_startup_context_prompt_with_qmd_mentions_qmd(tmp_path: Path) -> None:
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt(qmd_enabled=True)
    assert "qmd" in prompt.lower()


def test_startup_context_prompt_mentions_partial_file(tmp_path: Path) -> None:
    today = date.today().isoformat()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt()
    assert f"{today}-partial.md" in prompt


def test_startup_context_prompt_mentions_compacted_file_format(tmp_path: Path) -> None:
    c = HistoryCompactor(str(tmp_path), context_days=2, client=_mock_client())
    prompt = c.startup_context_prompt()
    assert "compacted.md" in prompt


# ── _extract_responses ─────────────────────────────────────────────────────


def test_extract_responses_single_response() -> None:
    content = (
        "### 💭 Thinking · 10:00:01 UTC\n\nThinking...\n"
        "\n### ✅ Response · 10:00:04 UTC\n\n> User: \"hi\"\n\nHello!\n\n---\n"
    )
    result = _extract_responses(content)
    assert "### ✅ Response" in result
    assert "Hello!" in result
    assert "Thinking..." not in result


def test_extract_responses_multiple_responses() -> None:
    content = (
        "### ✅ Response · 10:00:04 UTC\n\nFirst response.\n\n---\n"
        "### 🔧 Tool: Read · 10:01:00 UTC\n\n```\nsome tool\n```\n"
        "\n### ✅ Response · 10:02:00 UTC\n\nSecond response.\n\n---\n"
    )
    result = _extract_responses(content)
    assert "First response." in result
    assert "Second response." in result


def test_extract_responses_includes_user_blockquote() -> None:
    content = (
        "\n### ✅ Response · 10:00:04 UTC\n\n"
        '> User: "user question"\n\nAnswer here.\n\n---\n'
    )
    result = _extract_responses(content)
    assert "> User:" in result
    assert "user question" in result


def test_extract_responses_excludes_tool_calls() -> None:
    content = (
        "\n### 🔧 Tool: Read [t1] · 10:00:02 UTC\n\n```\n/path/to/file\n```\n"
        "\n### ✅ Response · 10:00:04 UTC\n\nAnswer.\n\n---\n"
    )
    result = _extract_responses(content)
    assert "Tool: Read" not in result
    assert "Answer." in result


def test_extract_responses_excludes_thinking() -> None:
    content = (
        "\n### 💭 Thinking · 10:00:01 UTC\n\nDeep thoughts...\n"
        "\n### ✅ Response · 10:00:04 UTC\n\nConclusion.\n\n---\n"
    )
    result = _extract_responses(content)
    assert "Deep thoughts..." not in result
    assert "Conclusion." in result


def test_extract_responses_empty_content() -> None:
    assert _extract_responses("") == ""


def test_extract_responses_no_responses() -> None:
    content = (
        "### 🔧 Tool: Read · 10:00:01 UTC\n\n```\n/file\n```\n"
        "### 📤 Result · 10:00:02 UTC\n\n```\nresult\n```\n"
        "### 💭 Thinking · 10:00:03 UTC\n\nThinking...\n"
    )
    assert _extract_responses(content) == ""


def test_extract_responses_handles_response_at_end_without_separator() -> None:
    """Last response in a partial/incomplete file may not have trailing ---."""
    content = "\n### ✅ Response · 10:00:04 UTC\n\nFinal answer."
    result = _extract_responses(content)
    assert "Final answer." in result


def test_extract_responses_excludes_routing_and_classification() -> None:
    content = (
        "### 🏷 Classification · 10:00:00 UTC\n\n`{\"intent\": \"task\"}`\n"
        "### 🔀 Pipeline · 10:00:01 UTC\n\nRouting: direct task response\n"
        "\n### ✅ Response · 10:00:04 UTC\n\nTask done.\n\n---\n"
    )
    result = _extract_responses(content)
    assert "Classification" not in result
    assert "Pipeline" not in result
    assert "Task done." in result


# ── filter applied in compact methods ─────────────────────────────────────


async def test_compact_day_sends_only_responses_to_api(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    md_content = (
        "### 🔧 Tool: Read · 10:00:01 UTC\n\n```\n/file\n```\n"
        "\n### 💭 Thinking · 10:00:02 UTC\n\nLet me think...\n"
        "\n### ✅ Response · 10:00:03 UTC\n\n> User: \"fix bug\"\n\nBug fixed.\n\n---\n"
    )
    _make_daily(tmp_path, day, md_content)
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Bug fixed." in prompt
    assert "Let me think..." not in prompt
    assert "Tool: Read" not in prompt


async def test_compact_day_skips_when_no_response_sections(tmp_path: Path) -> None:
    day = date.today() - timedelta(days=1)
    md_content = (
        "### 🔧 Tool: Read · 10:00:01 UTC\n\n```\n/file\n```\n"
        "### 💭 Thinking · 10:00:02 UTC\n\nJust thinking...\n"
    )
    _make_daily(tmp_path, day, md_content)
    client = _mock_client()
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c._compact_day(day)

    client.messages.create.assert_not_called()
    assert not (tmp_path / "daily" / f"{day}-compacted.md").exists()


async def test_compact_today_sends_only_responses_to_api(tmp_path: Path) -> None:
    today = date.today()
    md_content = (
        "### 🔧 Tool: Write · 10:00:01 UTC\n\n```\n/file\n```\n"
        "\n### ✅ Response · 10:00:02 UTC\n\n> User: \"write tests\"\n\nTests written.\n\n---\n"
    )
    _make_daily(tmp_path, today, md_content)
    client = _mock_client("Today summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    await c.compact_today()

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Tests written." in prompt
    assert "Tool: Write" not in prompt


async def test_compact_day_logs_warning_and_tail_truncates_when_too_large(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import archon.ai.history_compactor as mod

    day = date.today() - timedelta(days=1)
    big_response = "### ✅ Response · 10:00:00 UTC\n\n" + "x" * 200 + "\n\n---\n"
    _make_daily(tmp_path, day, big_response)
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    original_limit = mod._MAX_CONTENT_CHARS
    try:
        mod._MAX_CONTENT_CHARS = 50
        with caplog.at_level(logging.WARNING, logger="archon"):
            await c._compact_day(day)
    finally:
        mod._MAX_CONTENT_CHARS = original_limit

    assert any("truncat" in r.message.lower() for r in caplog.records)
    client.messages.create.assert_called_once()


async def test_compact_day_tail_truncates_keeps_most_recent_content(
    tmp_path: Path,
) -> None:
    """Tail-truncation keeps the END of the content, not the start."""
    import archon.ai.history_compactor as mod

    day = date.today() - timedelta(days=1)
    response1 = "### ✅ Response · 09:00:00 UTC\n\nOLD_CONTENT\n\n---\n"
    response2 = "### ✅ Response · 23:00:00 UTC\n\nRECENT_CONTENT\n\n---\n"
    _make_daily(tmp_path, day, response1 + response2)
    client = _mock_client("Summary")
    c = HistoryCompactor(str(tmp_path), context_days=2, client=client)

    # Limit to keep only the last ~50 chars (enough for RECENT_CONTENT, not OLD_CONTENT)
    original_limit = mod._MAX_CONTENT_CHARS
    try:
        mod._MAX_CONTENT_CHARS = 60
        await c._compact_day(day)
    finally:
        mod._MAX_CONTENT_CHARS = original_limit

    prompt = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "RECENT_CONTENT" in prompt
    assert "OLD_CONTENT" not in prompt
