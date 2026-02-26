"""E2E tests for session diagnostics — mocked SDK, full async flow.

Tests verify that is_processing, event_log, and diagnostics behave correctly
across a complete send() lifecycle including mid-flight state observation.
"""
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response, ThinkingResult


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _result_msg(result: str = "Done"):
    from claude_agent_sdk import ResultMessage
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result=result,
    )


def _make_instant_client(messages: list) -> MagicMock:
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def _receive():  # type: ignore[return]
        for m in messages:
            yield m

    client.receive_response = _receive
    return client


# ──────────────────────────────────────────────────────────────────
# E2E tests
# ──────────────────────────────────────────────────────────────────


async def test_is_processing_transitions_false_true_false() -> None:
    """is_processing is False before send(), True during, False after."""
    session = ClaudeSession()
    captured: list[bool] = []

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def _receive():  # type: ignore[return]
        # Mid-flight: _processing must already be True
        captured.append(session.is_processing)
        yield _result_msg()

    client.receive_response = _receive

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        assert not session.is_processing          # before
        _ = [e async for e in session.send("hi")]
        assert not session.is_processing          # after

    assert captured == [True]                     # True during


async def test_diagnostics_fully_populated_after_send() -> None:
    """All diagnostics keys are present and correct after a completed send()."""
    session = ClaudeSession()
    client = _make_instant_client([_result_msg()])

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    d = session.diagnostics
    assert d["is_alive"] is True
    assert d["is_processing"] is False
    assert d["processing_seconds"] is None
    assert d["idle_seconds"] is not None and d["idle_seconds"] >= 0.0
    assert d["send_count"] == 1
    assert len(d["recent_events"]) >= 1  # at least the Response event
    assert isinstance(d["recent_events"], list)


async def test_event_log_records_full_event_sequence() -> None:
    """Event log captures ThinkingResult and Response in order."""
    from claude_agent_sdk import AssistantMessage, ThinkingBlock

    session = ClaudeSession()
    client = _make_instant_client([
        AssistantMessage(
            content=[ThinkingBlock(thinking="Hmm...", signature="sig")],
            model="m",
        ),
        _result_msg("Answer"),
    ])

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        _ = [e async for e in session.send("question")]

    log = list(session._event_log)
    # ThinkingBlock → ThinkingResult; ResultMessage → Response
    assert len(log) == 2
    event_types = [type(e) for _, e in log]
    assert event_types[0] == ThinkingResult
    assert event_types[1] == Response


async def test_two_sessions_track_state_independently() -> None:
    """Two ClaudeSession instances track send_count independently."""
    session1 = ClaudeSession()
    session2 = ClaudeSession()

    client1 = _make_instant_client([_result_msg("one")])
    client2 = _make_instant_client([_result_msg("two")])

    with patch("archon.ai.claude_session.ClaudeSDKClient") as MockClient:
        MockClient.side_effect = [client1, client2]
        await session1.start()
        await session2.start()

        _ = [e async for e in session1.send("p1")]

        # session2 unaffected by session1's send
        assert session1.send_count == 1
        assert session2.send_count == 0

        _ = [e async for e in session2.send("p2")]

        assert session1.send_count == 1
        assert session2.send_count == 1

    assert not session1.is_processing
    assert not session2.is_processing


async def test_event_log_grows_across_multiple_sends() -> None:
    """Each send() adds to the event log; log grows cumulatively."""
    session = ClaudeSession()

    batches = [[_result_msg("first")], [_result_msg("second")]]
    batch_iter = iter(batches)

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    def _receive():
        msgs = next(batch_iter, [])

        async def _gen():  # type: ignore[return]
            for m in msgs:
                yield m

        return _gen()

    client.receive_response = _receive

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        _ = [e async for e in session.send("first")]
        assert len(session._event_log) == 1
        _ = [e async for e in session.send("second")]
        assert len(session._event_log) == 2
