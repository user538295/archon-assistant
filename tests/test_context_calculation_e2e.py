"""End-to-end tests for context window calculation.

Verifies the full data path:
  ClaudeSession.send() → _cumulative_cache_creation → usage_stats()
  → _fmt_context() → displayed context percentage and token count.

Written to catch the regression introduced in commit 7432c50.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.claude_session import ClaudeSession
from archon.chat.commands import _fmt_context


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _result_msg(
    *,
    cache_creation: int = 0,
    cache_read: int = 0,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost: float = 0.01,
):
    """Build a ResultMessage with precise usage values."""
    from claude_agent_sdk import ResultMessage

    return ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
        total_cost_usd=cost,
    )


def _make_batch_client(batches: list) -> MagicMock:
    """Mock client returning a different batch per receive_response() call."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    client._transport = None
    batch_iter = iter(batches)

    def _receive_response():
        msgs = next(batch_iter, [])

        async def _gen():
            for m in msgs:
                yield m

        return _gen()

    client.receive_response = _receive_response
    return client


# ──────────────────────────────────────────────────────────────────
# E2E: Multi-turn context accumulation
# ──────────────────────────────────────────────────────────────────


async def test_context_calculation_multi_turn_accumulates_correctly() -> None:
    """cumulative_cache_creation must equal the sum of all turns' cache_creation,
    and _fmt_context must compute total_ctx = cumul_cc + input_tokens correctly.
    """
    # Turn 1 (cold cache): system prompt + first user message cached.
    # Turn 2 (warm): assistant response + second user message cached.
    # Turn 3 (warm): smaller incremental caching.
    batches = [
        [_result_msg(cache_creation=15_000, cache_read=0, input_tokens=100, output_tokens=500)],
        [_result_msg(cache_creation=800, cache_read=15_000, input_tokens=50, output_tokens=200)],
        [_result_msg(cache_creation=500, cache_read=15_800, input_tokens=50, output_tokens=150)],
    ]
    mock_client = _make_batch_client(batches)

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        for i in range(3):
            _ = [e async for e in session.send(f"turn {i+1}")]

    stats = session.usage_stats
    assert stats is not None

    # cumul_cc = 15000 + 800 + 500 = 16300
    assert stats["cumulative_cache_creation"] == 16_300

    # Last turn's usage should be from turn 3
    usage = stats["usage"]
    assert usage["input_tokens"] == 50
    assert usage["cache_creation_input_tokens"] == 500
    assert usage["cache_read_input_tokens"] == 15_800

    # _fmt_context: total_ctx = cumul_cc(16300) + input_t(50) = 16350
    text = _fmt_context(stats)
    assert "16,350" in text, f"Expected total 16,350 in output, got:\n{text}"
    # pct = round(100 * 16350 / 200000) = 8%
    assert "8%" in text, f"Expected 8% context usage, got:\n{text}"


async def test_context_calculation_single_turn_cold_cache() -> None:
    """First turn: cumulative_cache_creation == cache_creation_input_tokens."""
    msg = _result_msg(cache_creation=20_000, cache_read=0, input_tokens=100, output_tokens=500)
    mock_client = _make_batch_client([[msg]])

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    stats = session.usage_stats
    assert stats is not None
    assert stats["cumulative_cache_creation"] == 20_000

    text = _fmt_context(stats)
    # total_ctx = 20000 + 100 = 20100
    assert "20,100" in text
    # pct = round(100 * 20100 / 200000) = 10%
    assert "10%" in text


# ──────────────────────────────────────────────────────────────────
# E2E: Context after reminder injection
# ──────────────────────────────────────────────────────────────────


async def test_context_calculation_with_reminder_injection(tmp_path) -> None:
    """When a reminder is injected, its cache_creation must be included
    in cumulative_cache_creation, and the context display must reflect both
    the reminder and the user turn.
    """
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    # Fire after 1 message so the NEXT send() injects the reminder.
    config = ReminderConfig(enabled=True, interval_messages=1, interval_tokens=1_000_000)
    reminder = ContextReminder(config, tmp_path)
    reminder.record_message()  # Push counter to 1 → should_inject() = True

    # Batch 1 (reminder turn) → batch 2 (user turn)
    reminder_result = _result_msg(
        cache_creation=200, cache_read=0, input_tokens=50, output_tokens=5, cost=0.001,
    )
    user_result = _result_msg(
        cache_creation=800, cache_read=200, input_tokens=100, output_tokens=300, cost=0.005,
    )
    # The mock client yields batch 1 for the reminder query, batch 2 for the user query.
    batches = [[reminder_result], [user_result]]
    mock_client = _make_batch_client(batches)

    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello after reminder")]

    stats = session.usage_stats
    assert stats is not None

    # cumul_cc = reminder(200) + user(800) = 1000
    assert stats["cumulative_cache_creation"] == 1_000, (
        f"Expected 1000 (reminder 200 + user 800), got {stats['cumulative_cache_creation']}"
    )

    # _last_usage must be from the USER turn, not the reminder turn
    assert stats["usage"]["input_tokens"] == 100
    assert stats["usage"]["cache_creation_input_tokens"] == 800

    # _fmt_context: total_ctx = cumul_cc(1000) + input_t(100) = 1100
    text = _fmt_context(stats)
    assert "1,100" in text, f"Expected total 1,100 in output, got:\n{text}"


# ──────────────────────────────────────────────────────────────────
# E2E: Reminder record_tokens includes cache_creation
# ──────────────────────────────────────────────────────────────────


async def test_reminder_record_tokens_excludes_cache_creation(tmp_path) -> None:
    """record_tokens must be called with input + output only.

    cache_creation_input_tokens must NOT be included because the cold-cache
    first turn writes the entire system prompt (~20-50K+) to cache, which
    would cause the token threshold to fire after 1-2 turns — before any
    meaningful context drift can occur.
    """
    from unittest.mock import MagicMock as StdMock
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("ctx", encoding="utf-8")

    config = ReminderConfig(enabled=True, interval_messages=100, interval_tokens=1_000_000)
    reminder = ContextReminder(config, tmp_path)
    reminder.record_message = StdMock()  # type: ignore[method-assign]
    reminder.record_tokens = StdMock()  # type: ignore[method-assign]

    msg = _result_msg(cache_creation=5_000, input_tokens=100, output_tokens=200)
    mock_client = _make_batch_client([[msg]])

    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("test")]

    # input (100) + output (200) = 300 — cache_creation excluded
    reminder.record_tokens.assert_called_once_with(300)


async def test_reminder_token_threshold_uses_input_output_only(tmp_path) -> None:
    """The token threshold tracks input_tokens + output_tokens per turn.

    cache_creation_input_tokens is excluded because the cold-cache first turn
    includes the entire system prompt, which would cause the threshold to fire
    after 1-2 turns regardless of actual conversation activity.
    """
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("ctx", encoding="utf-8")

    # Threshold at 5000 tokens, 100 messages
    config = ReminderConfig(enabled=True, interval_messages=100, interval_tokens=5_000)
    reminder = ContextReminder(config, tmp_path)

    # Simulate cold-cache turn 1: input=100, output=1500
    # (cache_creation=30000 would be huge but is NOT included in record_tokens)
    reminder.record_message()
    reminder.record_tokens(100 + 1500)  # 1600

    assert not reminder.should_inject(), "Should not fire after 1600 tokens (threshold=5000)"

    # Simulate turn 2: input=50, output=1200
    reminder.record_message()
    reminder.record_tokens(50 + 1200)  # 1250, cumulative = 2850

    assert not reminder.should_inject(), "Should not fire after 2850 tokens"

    # Simulate turn 3: input=50, output=2500
    reminder.record_message()
    reminder.record_tokens(50 + 2500)  # 2550, cumulative = 5400

    assert reminder.should_inject(), (
        "Token threshold (5000) should be reached at cumulative=5400"
    )


# ──────────────────────────────────────────────────────────────────
# E2E: Cold-cache turn must NOT cause premature reminder firing
# ──────────────────────────────────────────────────────────────────


async def test_cold_cache_turn_does_not_trigger_premature_reminder(tmp_path) -> None:
    """A cold-cache first turn writes the entire system prompt to cache
    (cache_creation=50K+). This must NOT cause the reminder token threshold
    to fire on turn 2, because cache warmup is not conversation drift.

    This is the regression test for the bug introduced in commit 7432c50.
    """
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Stay focused.", encoding="utf-8")

    config = ReminderConfig(enabled=True, interval_messages=20, interval_tokens=10_000)
    reminder = ContextReminder(config, tmp_path)

    # Turn 1 (cold cache): system prompt 50K → cache_creation.
    # record_tokens should only get input + output, NOT cache_creation.
    batches = [
        [_result_msg(cache_creation=50_000, cache_read=0, input_tokens=200, output_tokens=1500)],
    ]
    mock_client = _make_batch_client(batches)

    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("first message")]

    # After 1 turn: record_tokens(200 + 1500) = 1700 — well below 10K threshold
    assert not reminder.should_inject(), (
        "Reminder should NOT fire after 1 turn — the 50K cache_creation is system prompt "
        "warmup, not conversation drift"
    )


# ──────────────────────────────────────────────────────────────────
# E2E: Context correctness — total must match cache_read + cache_creation + input
# ──────────────────────────────────────────────────────────────────


async def test_context_total_matches_api_breakdown() -> None:
    """The displayed total context must equal the sum of the individual
    token categories for the current turn: cache_read + cache_creation + input.

    This is the fundamental invariant:
      total_ctx = cumul_cc + input_t
      where cumul_cc ≈ cache_read + cache_creation for the last turn
    """
    # After 3 turns:
    #   Turn 1: cc=10000, cr=0
    #   Turn 2: cc=1000, cr=10000
    #   Turn 3: cc=500, cr=11000, input=80
    # cumul_cc = 10000 + 1000 + 500 = 11500
    # For turn 3: cr(11000) + cc(500) = 11500 == cumul_cc ✓
    # total_ctx = 11500 + 80 = 11580
    # Actual context = cr(11000) + cc(500) + input(80) = 11580 ✓
    batches = [
        [_result_msg(cache_creation=10_000, cache_read=0, input_tokens=100, output_tokens=300)],
        [_result_msg(cache_creation=1_000, cache_read=10_000, input_tokens=100, output_tokens=200)],
        [_result_msg(cache_creation=500, cache_read=11_000, input_tokens=80, output_tokens=150)],
    ]
    mock_client = _make_batch_client(batches)

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        for i in range(3):
            _ = [e async for e in session.send(f"turn {i+1}")]

    stats = session.usage_stats
    assert stats is not None

    cumul_cc = stats["cumulative_cache_creation"]
    usage = stats["usage"]
    input_t = usage["input_tokens"]
    cache_r = usage["cache_read_input_tokens"]
    cache_c = usage["cache_creation_input_tokens"]

    # Fundamental invariant: cumul_cc == cache_read + cache_creation for last turn
    assert cumul_cc == cache_r + cache_c, (
        f"cumul_cc({cumul_cc}) != cache_read({cache_r}) + cache_creation({cache_c})"
    )

    # Context total = cumul_cc + input
    total_ctx = cumul_cc + input_t
    assert total_ctx == cache_r + cache_c + input_t, (
        f"total_ctx({total_ctx}) != cr({cache_r}) + cc({cache_c}) + input({input_t})"
    )

    # Verify _fmt_context shows the correct total
    text = _fmt_context(stats)
    assert f"{total_ctx:,}" in text, f"Expected {total_ctx:,} in output, got:\n{text}"


# ──────────────────────────────────────────────────────────────────
# Edge cases: None values, missing keys
# ──────────────────────────────────────────────────────────────────


async def test_context_calculation_cache_creation_is_none() -> None:
    """SDK returning cache_creation_input_tokens=None must not crash
    and must be treated as 0 for cumulative tracking.
    """
    from claude_agent_sdk import ResultMessage

    msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": None,
            "cache_read_input_tokens": None,
        },
        total_cost_usd=0.01,
    )
    mock_client = _make_batch_client([[msg]])

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    stats = session.usage_stats
    assert stats is not None
    assert stats["cumulative_cache_creation"] == 0

    # _fmt_context must not crash
    text = _fmt_context(stats)
    assert "0%" in text or "100 / " in text  # 0 + 100 = 100 tokens, ~0%


async def test_context_calculation_missing_cache_keys() -> None:
    """SDK returning usage dict WITHOUT cache keys must not crash."""
    from claude_agent_sdk import ResultMessage

    msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={"input_tokens": 200, "output_tokens": 80},  # no cache keys
        total_cost_usd=0.01,
    )
    mock_client = _make_batch_client([[msg]])

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    stats = session.usage_stats
    assert stats is not None
    assert stats["cumulative_cache_creation"] == 0

    text = _fmt_context(stats)
    # total_ctx = 0 + 200 = 200 → ~0%
    assert "200 / " in text


# ──────────────────────────────────────────────────────────────────
# E2E: Context display breakdown consistency
# ──────────────────────────────────────────────────────────────────


def test_fmt_context_total_equals_cumul_cc_plus_input() -> None:
    """The headline total must be exactly cumul_cc + input_tokens, not some other formula."""
    stats = {
        "usage": {
            "input_tokens": 500,
            "output_tokens": 200,
            "cache_read_input_tokens": 30_000,
            "cache_creation_input_tokens": 2_000,
        },
        "cumulative_cache_creation": 32_000,
        "total_cost_usd": 0.05,
        "num_turns": 10,
        "user_turns": 10,
        "last_duration_ms": 5_000,
    }
    text = _fmt_context(stats)
    # total_ctx = 32000 + 500 = 32500
    assert "32,500" in text, f"Expected 32,500 total, got:\n{text}"
    assert "16%" in text, f"Expected 16%, got:\n{text}"  # round(100*32500/200000)


def test_fmt_context_does_not_include_cache_read_in_total() -> None:
    """cache_read_input_tokens is inflated by tool-call multiplicity
    and must NOT be part of the context total.
    """
    stats = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 500_000,  # inflated by 10 tool calls
            "cache_creation_input_tokens": 1_000,
        },
        "cumulative_cache_creation": 50_000,
        "total_cost_usd": 0.5,
        "num_turns": 5,
        "user_turns": 5,
        "last_duration_ms": 5_000,
    }
    text = _fmt_context(stats)
    # total_ctx = 50000 + 100 = 50100 → 25%
    assert "50,100" in text
    assert "25%" in text
    # Must NOT show inflated values
    assert "250%" not in text
    assert "501,100" not in text
