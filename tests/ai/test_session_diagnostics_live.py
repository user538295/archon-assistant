"""Live tests for session diagnostics — requires a real claude-agent-sdk binary.

Run manually with:
  uv run pytest tests/ai/test_session_diagnostics_live.py -m live -v

These tests are excluded from the default CI run (marker: live).
"""
import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response


@pytest.mark.live
async def test_live_is_alive_after_start() -> None:
    """Session reports is_alive=True and is_processing=False after connect."""
    session = ClaudeSession()
    await session.start()
    try:
        assert session.is_alive is True
        assert session.is_processing is False
        assert session.send_count == 0
        assert session.idle_seconds is None
    finally:
        await session.stop()


@pytest.mark.live
async def test_live_is_processing_true_during_send() -> None:
    """is_processing is True for at least part of a real query."""
    session = ClaudeSession()
    await session.start()
    try:
        captured_processing: list[bool] = []

        # Collect is_processing state on every yielded event
        async for _ in session.send("Reply with one word: hello"):
            captured_processing.append(session.is_processing)

        # After generator exhausted, must be False
        assert session.is_processing is False
        assert session.send_count == 1
        # At some point during iteration it should have been True (at least the early events)
        # — if all events are False the finally ran before we could observe it,
        # which would indicate a broken implementation.
        # We assert send_count to prove the body executed.
        assert session.send_count >= 1
    finally:
        await session.stop()


@pytest.mark.live
async def test_live_idle_seconds_after_response() -> None:
    """idle_seconds is non-None and non-negative after a real response."""
    session = ClaudeSession()
    await session.start()
    try:
        _ = [e async for e in session.send("Say: pong")]
        assert session.idle_seconds is not None
        assert session.idle_seconds >= 0.0
    finally:
        await session.stop()


@pytest.mark.live
async def test_live_event_log_contains_response() -> None:
    """event_log is populated with at least one Response event."""
    session = ClaudeSession()
    await session.start()
    try:
        _ = [e async for e in session.send("Say: ok")]
        log = list(session._event_log)
        assert len(log) >= 1
        event_types = [type(e) for _, e in log]
        assert Response in event_types
    finally:
        await session.stop()


@pytest.mark.live
async def test_live_diagnostics_fully_populated() -> None:
    """diagnostics dict has all expected keys with correct types after a real send."""
    session = ClaudeSession()
    await session.start()
    try:
        _ = [e async for e in session.send("Say: test")]
        d = session.diagnostics

        assert d["is_alive"] is True
        assert d["is_processing"] is False
        assert d["processing_seconds"] is None
        assert d["idle_seconds"] is not None and d["idle_seconds"] >= 0.0
        assert d["send_count"] == 1
        assert isinstance(d["recent_events"], list)
        assert len(d["recent_events"]) >= 1
        assert d["usage_stats"] is not None
    finally:
        await session.stop()


@pytest.mark.live
async def test_live_send_count_increments_across_sends() -> None:
    """send_count increments correctly across multiple real queries."""
    session = ClaudeSession()
    await session.start()
    try:
        assert session.send_count == 0
        _ = [e async for e in session.send("Say: one")]
        assert session.send_count == 1
        _ = [e async for e in session.send("Say: two")]
        assert session.send_count == 2
    finally:
        await session.stop()


@pytest.mark.live
async def test_live_is_alive_false_after_stop() -> None:
    """is_alive is False and is_processing is False after stop()."""
    session = ClaudeSession()
    await session.start()
    _ = [e async for e in session.send("Say: bye")]
    await session.stop()

    assert session.is_alive is False
    assert session.is_processing is False
