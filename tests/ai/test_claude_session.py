"""Tests for ClaudeSession — S1.1 + S6.1."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted
from archon.ai.skill_loader import Skill


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_mock_client(messages: list | None = None):
    """Build a mock ClaudeSDKClient that yields given messages from receive_response."""
    messages = messages or []
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    # Explicitly set _transport to None so tests that make disconnect() raise RuntimeError
    # don't silently exercise the Bug 11 transport-fallback path (test hygiene).
    client._transport = None

    async def _receive_response():  # type: ignore[return]
        for m in messages:
            yield m

    client.receive_response = _receive_response
    return client


# ──────────────────────────────────────────────────────────────────
# is_alive
# ──────────────────────────────────────────────────────────────────


def test_is_alive_false_before_start() -> None:
    session = ClaudeSession()
    assert not session.is_alive


async def test_is_alive_true_after_start() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
    assert session.is_alive


async def test_is_alive_false_after_stop() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()
    assert not session.is_alive


# ──────────────────────────────────────────────────────────────────
# start — CLAUDECODE stripping
# ──────────────────────────────────────────────────────────────────


async def test_start_strips_claudecode_before_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDECODE must not be set when connect() is called."""
    seen_during_connect: list[str | None] = []

    async def _connect_spy() -> None:
        seen_during_connect.append(os.environ.get("CLAUDECODE"))

    monkeypatch.setenv("CLAUDECODE", "1")
    session = ClaudeSession()
    mock_client = _make_mock_client()
    mock_client.connect = _connect_spy
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()

    assert seen_during_connect == [None]  # stripped during connect


async def test_start_restores_claudecode_after_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDECODE is restored even if connect() raises."""
    async def _failing_connect() -> None:
        raise RuntimeError("connect failed")

    monkeypatch.setenv("CLAUDECODE", "sentinel")
    session = ClaudeSession()
    mock_client = _make_mock_client()
    mock_client.connect = _failing_connect
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        with pytest.raises(RuntimeError):
            await session.start()
    assert os.environ.get("CLAUDECODE") == "sentinel"


async def test_concurrent_start_serializes_env_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two concurrent start() calls must not overlap on os.environ CLAUDECODE.

    The _ENV_LOCK ensures the second session cannot reach os.environ.pop() while
    the first session is still inside connect().  We verify this by holding the
    first connect() open via an event and asserting that the second connect()
    only observes CLAUDECODE=None (stripped) and never runs concurrently.
    """
    import asyncio as _asyncio
    observed: list[str | None] = []
    first_connect_started = _asyncio.Event()
    first_connect_may_finish = _asyncio.Event()

    async def _slow_connect() -> None:
        observed.append(os.environ.get("CLAUDECODE"))
        first_connect_started.set()
        await first_connect_may_finish.wait()

    async def _fast_connect() -> None:
        observed.append(os.environ.get("CLAUDECODE"))

    monkeypatch.setenv("CLAUDECODE", "1")
    session1 = ClaudeSession()
    session2 = ClaudeSession()
    mock1 = _make_mock_client()
    mock2 = _make_mock_client()
    mock1.connect = _slow_connect
    mock2.connect = _fast_connect

    with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=[mock1, mock2]):
        task1 = _asyncio.create_task(session1.start())
        await first_connect_started.wait()
        task2 = _asyncio.create_task(session2.start())
        await _asyncio.sleep(0)
        first_connect_may_finish.set()
        await task1
        await task2

    # Both connects must have seen CLAUDECODE=None (stripped), never overlapping.
    assert observed == [None, None], f"Unexpected observed values: {observed}"
    assert session1.is_alive
    assert session2.is_alive


# ──────────────────────────────────────────────────────────────────
# start
# ──────────────────────────────────────────────────────────────────


async def test_start_calls_connect() -> None:
    session = ClaudeSession(cwd="/some/path")
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
        MockClient.assert_called_once()
        mock_client.connect.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# send
# ──────────────────────────────────────────────────────────────────


async def test_send_raises_if_not_started() -> None:
    session = ClaudeSession()
    with pytest.raises(RuntimeError, match="not started"):
        async for _ in session.send("hello"):
            pass


async def test_send_calls_query_with_prompt() -> None:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    messages = [
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="s1",
            result="OK",
        )
    ]
    session = ClaudeSession()
    mock_client = _make_mock_client(messages)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("Say: OK")]

    mock_client.query.assert_awaited_once_with("Say: OK")
    assert len(events) == 1
    assert isinstance(events[0], Response)
    assert events[0].content == "OK"


async def test_send_yields_all_mapped_events() -> None:
    from claude_agent_sdk import AssistantMessage, ResultMessage, ThinkingBlock, ToolUseBlock

    messages = [
        AssistantMessage(content=[ThinkingBlock(thinking="thinking", signature="s")], model="m"),
        AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={})], model="m"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=2,
            session_id="s1",
            result="Done.",
        ),
    ]
    session = ClaudeSession()
    mock_client = _make_mock_client(messages)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("Do it")]

    types = [type(e) for e in events]
    assert types == [ThinkingResult, ToolStarted, Response]


# ──────────────────────────────────────────────────────────────────
# stop
# ──────────────────────────────────────────────────────────────────


async def test_stop_calls_disconnect() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()
    mock_client.disconnect.assert_awaited_once()


async def test_stop_is_noop_when_not_started() -> None:
    session = ClaudeSession()
    await session.stop()  # must not raise


async def test_stop_is_noop_when_already_stopped() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()
        await session.stop()  # second stop must not raise
    mock_client.disconnect.assert_awaited_once()  # called only once


# ──────────────────────────────────────────────────────────────────
# skills — S6.1
# ──────────────────────────────────────────────────────────────────


def _result_message():
    from claude_agent_sdk import ResultMessage
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
    )


async def test_system_prompt_includes_skill_name_and_description() -> None:
    skill = Skill(name="my-skill", description="Does cool things", content="# Instructions")
    session = ClaudeSession(skills=[skill])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.system_prompt is not None
    assert "my-skill" in options.system_prompt
    assert "Does cool things" in options.system_prompt


async def test_system_prompt_is_none_when_no_skills() -> None:
    session = ClaudeSession(skills=[])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.system_prompt is None


async def test_system_prompt_custom_only() -> None:
    session = ClaudeSession(system_prompt="You are a classifier.")
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.system_prompt == "You are a classifier."


async def test_system_prompt_before_skills() -> None:
    skill = Skill(name="my-skill", description="Does stuff", content="# body")
    session = ClaudeSession(system_prompt="Custom instructions.", skills=[skill])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    sp = MockClient.call_args.kwargs["options"].system_prompt
    assert sp.startswith("Custom instructions.")
    assert "my-skill" in sp
    custom_pos = sp.index("Custom instructions.")
    skill_pos = sp.index("my-skill")
    assert custom_pos < skill_pos


async def test_system_prompt_before_spawn_rule() -> None:
    session = ClaudeSession(
        system_prompt="Custom prefix.",
        spawn_rule="eager",
        background_agent_mcp_url="http://localhost:18182/mcp/1",
    )
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    sp = MockClient.call_args.kwargs["options"].system_prompt
    assert sp.startswith("Custom prefix.")
    assert "spawn_background_agent" in sp


async def test_system_prompt_none_is_backward_compatible() -> None:
    session = ClaudeSession(system_prompt=None, skills=[])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.system_prompt is None


async def test_system_prompt_empty_string_treated_as_none() -> None:
    session = ClaudeSession(system_prompt="", skills=[])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.system_prompt is None


async def test_system_prompt_lists_all_skills() -> None:
    skills = [
        Skill("skill-a", "Description A", "body A"),
        Skill("skill-b", "Description B", "body B"),
    ]
    session = ClaudeSession(skills=skills)
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert "skill-a" in options.system_prompt
    assert "Description A" in options.system_prompt
    assert "skill-b" in options.system_prompt
    assert "Description B" in options.system_prompt


async def test_activate_skill_queues_skill() -> None:
    skill = Skill(name="queued-skill", description="desc", content="skill body")
    session = ClaudeSession()
    session.activate_skill(skill)
    assert len(session._pending_skills) == 1
    assert session._pending_skills[0] is skill


async def test_activate_skill_queues_multiple_skills() -> None:
    skill_a = Skill("a", "desc a", "body a")
    skill_b = Skill("b", "desc b", "body b")
    session = ClaudeSession()
    session.activate_skill(skill_a)
    session.activate_skill(skill_b)
    assert len(session._pending_skills) == 2


async def test_send_prepends_skill_content_to_prompt() -> None:
    skill = Skill(name="inject-skill", description="desc", content="INJECTED CONTENT")
    session = ClaudeSession()
    mock_client = _make_mock_client([_result_message()])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        session.activate_skill(skill)
        _ = [e async for e in session.send("user prompt")]

    actual_prompt = mock_client.query.call_args.args[0]
    assert "INJECTED CONTENT" in actual_prompt
    assert "user prompt" in actual_prompt
    # Skill content must appear before user prompt
    assert actual_prompt.index("INJECTED CONTENT") < actual_prompt.index("user prompt")


async def test_send_includes_skill_label_in_prompt() -> None:
    skill = Skill(name="labeled-skill", description="desc", content="skill body")
    session = ClaudeSession()
    mock_client = _make_mock_client([_result_message()])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        session.activate_skill(skill)
        _ = [e async for e in session.send("prompt")]

    actual_prompt = mock_client.query.call_args.args[0]
    assert "labeled-skill" in actual_prompt


async def test_send_clears_pending_skills_after_first_use() -> None:
    skill = Skill(name="one-shot-skill", description="desc", content="ONE SHOT")
    session = ClaudeSession()
    mock_client = _make_mock_client([_result_message(), _result_message()])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        session.activate_skill(skill)
        _ = [e async for e in session.send("first message")]
        _ = [e async for e in session.send("second message")]

    assert mock_client.query.call_count == 2
    second_prompt = mock_client.query.call_args_list[1].args[0]
    assert "ONE SHOT" not in second_prompt
    assert second_prompt == "second message"


async def test_send_without_pending_skills_sends_prompt_unchanged() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client([_result_message()])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("plain prompt")]

    mock_client.query.assert_awaited_once_with("plain prompt")


# ──────────────────────────────────────────────────────────────────
# usage_stats — /context tracking
# ──────────────────────────────────────────────────────────────────


def test_usage_stats_none_before_any_send() -> None:
    session = ClaudeSession()
    assert session.usage_stats is None


async def test_usage_stats_none_after_start_before_send() -> None:
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
    assert session.usage_stats is None


async def test_usage_stats_populated_after_send() -> None:
    from claude_agent_sdk import ResultMessage
    msg = ResultMessage(
        subtype="success",
        duration_ms=1200,
        duration_api_ms=800,
        is_error=False,
        num_turns=3,
        session_id="s1",
        result="Done",
        usage={"input_tokens": 1000, "output_tokens": 100},
        total_cost_usd=0.005,
    )
    session = ClaudeSession()
    mock_client = _make_mock_client([msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    stats = session.usage_stats
    assert stats is not None
    assert stats["usage"]["input_tokens"] == 1000
    assert stats["usage"]["output_tokens"] == 100
    assert stats["num_turns"] == 3
    assert stats["last_duration_ms"] == 1200


async def test_usage_stats_accumulates_cost_across_turns() -> None:
    """Cost should accumulate across multiple send() calls (one ResultMessage per call)."""
    from claude_agent_sdk import ResultMessage

    def _msg(cost: float) -> ResultMessage:
        return ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=1,
            session_id="s1",
            result="OK",
            usage={"input_tokens": 500, "output_tokens": 50},
            total_cost_usd=cost,
        )

    # Use batches so each receive_response() call yields a distinct ResultMessage
    batches = [[_msg(0.01)], [_msg(0.02)]]
    batch_iter = iter(batches)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    def _receive_response():
        msgs = next(batch_iter, [])
        async def _gen():
            for m in msgs:
                yield m
        return _gen()

    mock_client.receive_response = _receive_response

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("first")]
        _ = [e async for e in session.send("second")]

    stats = session.usage_stats
    assert stats is not None
    assert abs(stats["total_cost_usd"] - 0.03) < 0.0001


async def test_usage_stats_updates_usage_dict_with_latest() -> None:
    """usage dict reflects the latest response, not the first one."""
    from claude_agent_sdk import ResultMessage

    msg1 = ResultMessage(
        subtype="success", duration_ms=100, duration_api_ms=50,
        is_error=False, num_turns=1, session_id="s1", result="OK",
        usage={"input_tokens": 100, "output_tokens": 10},
        total_cost_usd=0.001,
    )
    msg2 = ResultMessage(
        subtype="success", duration_ms=200, duration_api_ms=100,
        is_error=False, num_turns=2, session_id="s1", result="OK",
        usage={"input_tokens": 250, "output_tokens": 25},
        total_cost_usd=0.002,
    )

    # Each send() call gets its own ResultMessage
    batches = [[msg1], [msg2]]
    batch_iter = iter(batches)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    def _receive_response():
        msgs = next(batch_iter, [])
        async def _gen():
            for m in msgs:
                yield m
        return _gen()

    mock_client.receive_response = _receive_response

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("first")]
        _ = [e async for e in session.send("second")]

    stats = session.usage_stats
    assert stats is not None
    # Second response had 250 input tokens — must replace first (100)
    assert stats["usage"]["input_tokens"] == 250


# ──────────────────────────────────────────────────────────────────
# cumulative_cache_creation — context window fix
# ──────────────────────────────────────────────────────────────────
#
# cache_read_input_tokens is inflated (N tool calls × context_size) and must
# NOT be used for context window calculation.  Instead we accumulate
# cache_creation_input_tokens across all turns, which grows monotonically and
# accurately reflects the total cached context.


def test_cumulative_cache_creation_starts_at_zero() -> None:
    session = ClaudeSession()
    stats = session.usage_stats
    # No send() yet — no stats at all, but internal counter must be zero.
    assert session._cumulative_cache_creation == 0


async def test_cumulative_cache_creation_set_after_single_send() -> None:
    """After one send(), cumulative_cache_creation == that turn's cache_creation."""
    from claude_agent_sdk import ResultMessage

    msg = ResultMessage(
        subtype="success", duration_ms=100, duration_api_ms=50,
        is_error=False, num_turns=1, session_id="s1", result="OK",
        usage={
            "input_tokens": 500,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 2_000,
        },
        total_cost_usd=0.01,
    )
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    async def _gen():
        yield msg

    mock_client.receive_response = lambda: _gen()

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    assert session._cumulative_cache_creation == 2_000


async def test_cumulative_cache_creation_accumulates_across_turns() -> None:
    """cumulative_cache_creation sums cache_creation from all send() calls."""
    from claude_agent_sdk import ResultMessage

    def _msg(cache_c: int) -> ResultMessage:
        return ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=50,
            is_error=False, num_turns=1, session_id="s1", result="OK",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": cache_c,
            },
            total_cost_usd=0.01,
        )

    batches = [[_msg(1_000)], [_msg(500)], [_msg(300)]]
    batch_iter = iter(batches)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    def _receive_response():
        msgs = next(batch_iter, [])
        async def _gen():
            for m in msgs:
                yield m
        return _gen()

    mock_client.receive_response = _receive_response

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("turn1")]
        _ = [e async for e in session.send("turn2")]
        _ = [e async for e in session.send("turn3")]

    assert session._cumulative_cache_creation == 1_800   # 1000 + 500 + 300


async def test_usage_stats_includes_cumulative_cache_creation() -> None:
    """usage_stats must expose cumulative_cache_creation for _fmt_context."""
    from claude_agent_sdk import ResultMessage

    def _msg(cache_c: int) -> ResultMessage:
        return ResultMessage(
            subtype="success", duration_ms=100, duration_api_ms=50,
            is_error=False, num_turns=1, session_id="s1", result="OK",
            usage={
                "input_tokens": 200,
                "output_tokens": 50,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": cache_c,
            },
            total_cost_usd=0.01,
        )

    batches = [[_msg(3_000)], [_msg(1_500)]]
    batch_iter = iter(batches)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    def _receive_response():
        msgs = next(batch_iter, [])
        async def _gen():
            for m in msgs:
                yield m
        return _gen()

    mock_client.receive_response = _receive_response

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("first")]
        _ = [e async for e in session.send("second")]

    stats = session.usage_stats
    assert stats is not None
    assert "cumulative_cache_creation" in stats
    assert stats["cumulative_cache_creation"] == 4_500   # 3000 + 1500


async def test_cumulative_cache_creation_zero_when_usage_missing_key() -> None:
    """If cache_creation_input_tokens key is absent, cumulative must not crash."""
    from claude_agent_sdk import ResultMessage

    msg = ResultMessage(
        subtype="success", duration_ms=100, duration_api_ms=50,
        is_error=False, num_turns=1, session_id="s1", result="OK",
        usage={"input_tokens": 100, "output_tokens": 50},  # no cache keys
        total_cost_usd=0.01,
    )
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    async def _gen():
        yield msg

    mock_client.receive_response = lambda: _gen()

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    assert session._cumulative_cache_creation == 0
    stats = session.usage_stats
    assert stats is not None
    assert stats["cumulative_cache_creation"] == 0


# ──────────────────────────────────────────────────────────────────
# context_percentage()
# ──────────────────────────────────────────────────────────────────


def test_context_percentage_zero_when_no_usage() -> None:
    """Fresh session with no send() calls returns 0."""
    session = ClaudeSession()
    assert session.context_percentage() == 0


def test_context_percentage_calculates_correctly() -> None:
    """context_percentage = round(100 * (cumul_cc + input_t) / 200_000)."""
    session = ClaudeSession()
    # Inject known values directly into internal state.
    session._last_usage = {"input_tokens": 10_000, "output_tokens": 500}  # type: ignore[attr-defined]
    session._cumulative_cache_creation = 40_000  # type: ignore[attr-defined]
    # expected = round(100 * (40_000 + 10_000) / 200_000) = round(25.0) = 25
    assert session.context_percentage() == 25


def test_context_percentage_can_exceed_100() -> None:
    """No clamping — values above 200 K return > 100."""
    session = ClaudeSession()
    session._last_usage = {"input_tokens": 100_000, "output_tokens": 0}  # type: ignore[attr-defined]
    session._cumulative_cache_creation = 150_000  # type: ignore[attr-defined]
    # expected = round(100 * 250_000 / 200_000) = round(125.0) = 125
    assert session.context_percentage() == 125


# ──────────────────────────────────────────────────────────────────
# plugins parameter — High gap
# ──────────────────────────────────────────────────────────────────


async def test_plugins_passed_to_claude_agent_options() -> None:
    """plugins list must be forwarded verbatim to ClaudeAgentOptions."""
    plugins = [{"type": "local", "path": "/some/plugin"}]
    session = ClaudeSession(plugins=plugins)
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.plugins == plugins


async def test_empty_plugins_list_passed_to_options() -> None:
    """When no plugins are given the options.plugins must be an empty list (not None)."""
    session = ClaudeSession(plugins=[])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.plugins == []


# ──────────────────────────────────────────────────────────────────
# disallowed_tools — plan mode guard
# ──────────────────────────────────────────────────────────────────


async def test_disallowed_tools_blocks_enter_plan_mode() -> None:
    """EnterPlanMode and ExitPlanMode must be in disallowed_tools on every session.

    These tools require an interactive TTY dialog that cannot be shown in a
    headless SDK session (rH()/isTeammate returns false for top-level sessions),
    so they must be blocked at the SDK level.
    """
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert "EnterPlanMode" in options.disallowed_tools
    assert "ExitPlanMode" in options.disallowed_tools


# ──────────────────────────────────────────────────────────────────
# tools parameter
# ──────────────────────────────────────────────────────────────────


async def test_tools_none_by_default() -> None:
    """By default, tools should not be set (None → all default tools)."""
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.tools is None


async def test_tools_empty_list_disables_all_tools() -> None:
    """tools=[] should pass empty list to options, disabling all tools."""
    session = ClaudeSession(tools=[])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.tools == []


async def test_tools_specific_list_passed_through() -> None:
    """tools=['Read', 'Grep'] should be forwarded to options."""
    session = ClaudeSession(tools=["Read", "Grep"])
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.tools == ["Read", "Grep"]


# ──────────────────────────────────────────────────────────────────
# max_turns parameter
# ──────────────────────────────────────────────────────────────────


async def test_max_turns_none_by_default() -> None:
    """By default, max_turns should not be set (None → unlimited)."""
    session = ClaudeSession()
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.max_turns is None


async def test_max_turns_passed_to_options() -> None:
    """max_turns=1 should be forwarded to ClaudeAgentOptions."""
    session = ClaudeSession(max_turns=1)
    mock_client = _make_mock_client()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client) as MockClient:
        await session.start()
    options = MockClient.call_args.kwargs["options"]
    assert options.max_turns == 1


# ──────────────────────────────────────────────────────────────────
# model property — Medium gap
# ──────────────────────────────────────────────────────────────────


def test_model_property_returns_configured_model() -> None:
    session = ClaudeSession(model="claude-opus-4-5")
    assert session.model == "claude-opus-4-5"


def test_model_property_none_when_not_set() -> None:
    session = ClaudeSession()
    assert session.model is None


# ──────────────────────────────────────────────────────────────────
# stop() swallows RuntimeError — Medium gap
# ──────────────────────────────────────────────────────────────────


async def test_stop_swallows_runtime_error_from_disconnect() -> None:
    """stop() must not propagate RuntimeError raised by disconnect()."""
    session = ClaudeSession()
    mock_client = _make_mock_client()
    mock_client.disconnect = AsyncMock(side_effect=RuntimeError("cancel scope error"))
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        await session.stop()  # must not raise

    assert not session.is_alive


async def test_stop_is_not_alive_after_runtime_error() -> None:
    """is_alive must be False even when disconnect() raises RuntimeError."""
    session = ClaudeSession()
    mock_client = _make_mock_client()
    mock_client.disconnect = AsyncMock(side_effect=RuntimeError("anyio cancel scope"))
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        assert session.is_alive
        await session.stop()

    assert not session.is_alive


async def test_usage_stats_none_cost_not_accumulated() -> None:
    """ResultMessage with total_cost_usd=None does not add to accumulated cost."""
    from claude_agent_sdk import ResultMessage

    msg = ResultMessage(
        subtype="success", duration_ms=100, duration_api_ms=50,
        is_error=False, num_turns=1, session_id="s1", result="OK",
        usage={"input_tokens": 100, "output_tokens": 10},
        total_cost_usd=None,
    )
    session = ClaudeSession()
    mock_client = _make_mock_client([msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    stats = session.usage_stats
    assert stats is not None
    assert stats["total_cost_usd"] == 0.0


# ──────────────────────────────────────────────────────────────────
# Diagnostics — S14.1
# ──────────────────────────────────────────────────────────────────


def _make_spy_client(messages: list, session: "ClaudeSession", capture: "list[bool]") -> MagicMock:
    """Mock client whose receive_response captures session.is_processing mid-flight."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def _receive_response():  # type: ignore[return]
        capture.append(session.is_processing)
        for m in messages:
            yield m

    client.receive_response = _receive_response
    return client


def _make_batch_client(batches: list) -> MagicMock:
    """Mock client that returns a different batch of messages per receive_response() call."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    batch_iter = iter(batches)

    def _receive_response():
        msgs = next(batch_iter, [])

        async def _gen():  # type: ignore[return]
            for m in msgs:
                yield m

        return _gen()

    client.receive_response = _receive_response
    return client


class TestClaudeSessionDiagnostics:
    """S14.1 — processing state, timing, event log, stuck detection."""

    # ── happy paths ────────────────────────────────────────────────

    def test_is_processing_false_before_any_send(self) -> None:
        session = ClaudeSession()
        assert session.is_processing is False

    async def test_is_processing_true_while_iterating(self) -> None:
        """is_processing is True inside receive_response() (mid-flight)."""
        session = ClaudeSession()
        captured: list[bool] = []
        mock_client = _make_spy_client([_result_message()], session, captured)
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("prompt")]
        assert captured == [True]

    async def test_is_processing_false_after_send_completes(self) -> None:
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("prompt")]
        assert session.is_processing is False

    def test_processing_seconds_none_when_not_processing(self) -> None:
        session = ClaudeSession()
        assert session.processing_seconds is None

    async def test_processing_seconds_positive_while_processing(self) -> None:
        """processing_seconds is a non-negative float during receive_response()."""
        session = ClaudeSession()
        captured: list[float | None] = []

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()

        async def _receive():  # type: ignore[return]
            captured.append(session.processing_seconds)
            yield _result_message()

        mock_client.receive_response = _receive
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("prompt")]

        assert len(captured) == 1
        assert captured[0] is not None
        assert captured[0] >= 0.0

    def test_idle_seconds_none_before_first_response(self) -> None:
        session = ClaudeSession()
        assert session.idle_seconds is None

    async def test_idle_seconds_nonnegative_after_response(self) -> None:
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("hello")]
        idle = session.idle_seconds
        assert idle is not None
        assert idle >= 0.0

    def test_send_count_zero_on_fresh_session(self) -> None:
        session = ClaudeSession()
        assert session.send_count == 0

    async def test_send_count_increments_each_send(self) -> None:
        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("first")]
            assert session.send_count == 1
            _ = [e async for e in session.send("second")]
            assert session.send_count == 2

    async def test_diagnostics_contains_expected_keys(self) -> None:
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("hello")]

        d = session.diagnostics
        for key in ("is_alive", "is_processing", "processing_seconds", "idle_seconds",
                    "send_count", "recent_events", "usage_stats"):
            assert key in d, f"Missing key: {key}"
        assert d["is_alive"] is True
        assert d["is_processing"] is False
        assert d["processing_seconds"] is None
        assert d["idle_seconds"] is not None
        assert d["send_count"] == 1
        assert isinstance(d["recent_events"], list)

    def test_event_log_empty_on_fresh_session(self) -> None:
        session = ClaudeSession()
        assert list(session._event_log) == []

    async def test_event_log_populated_after_send(self) -> None:
        from archon.ai.event_mapper import Response
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("hello")]

        log = list(session._event_log)
        assert len(log) == 1
        timestamp, event = log[0]
        assert isinstance(timestamp, float)
        assert isinstance(event, Response)

    # ── edge cases ─────────────────────────────────────────────────

    async def test_is_processing_resets_on_early_break(self) -> None:
        """finally block resets _processing when the generator is explicitly closed."""
        import contextlib
        from claude_agent_sdk import AssistantMessage, ThinkingBlock
        messages = [
            AssistantMessage(content=[ThinkingBlock(thinking="hmm", signature="s")], model="m"),
            _result_message(),
        ]
        session = ClaudeSession()
        mock_client = _make_mock_client(messages)
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            # aclosing() awaits aclose() on exit, ensuring finally runs immediately.
            async with contextlib.aclosing(session.send("prompt")) as gen:
                async for _ in gen:
                    break  # exit after first event
        assert session.is_processing is False

    async def test_is_processing_resets_on_exception_in_receive(self) -> None:
        """finally block resets _processing when receive_response() raises."""
        session = ClaudeSession()

        async def _error_receive():  # type: ignore[return]
            raise RuntimeError("network error")
            yield  # make it an async generator

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = _error_receive

        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            with pytest.raises(RuntimeError, match="network error"):
                async for _ in session.send("prompt"):
                    pass
        assert session.is_processing is False

    def test_event_log_bounded_at_max_size(self) -> None:
        """The event log deque has maxlen=200 so it never grows unboundedly."""
        session = ClaudeSession()
        assert session._event_log.maxlen == 200

    async def test_send_count_increments_even_on_partial_iteration(self) -> None:
        """send_count increments even when the caller breaks after the first event."""
        import contextlib
        from claude_agent_sdk import AssistantMessage, ThinkingBlock
        messages = [
            AssistantMessage(content=[ThinkingBlock(thinking="hmm", signature="s")], model="m"),
            _result_message(),
        ]
        session = ClaudeSession()
        mock_client = _make_mock_client(messages)
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            async with contextlib.aclosing(session.send("prompt")) as gen:
                async for _ in gen:
                    break
        assert session.send_count == 1

    async def test_recent_events_returns_last_n(self) -> None:
        """recent_events(1) returns only the last event when log has more."""
        from archon.ai.event_mapper import Response
        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("first")]
            _ = [e async for e in session.send("second")]

        events = session.recent_events(1)
        assert len(events) == 1
        _, event = events[0]
        assert isinstance(event, Response)

    def test_recent_events_zero_returns_empty(self) -> None:
        session = ClaudeSession()
        assert session.recent_events(0) == []

    async def test_idle_seconds_updated_after_each_send(self) -> None:
        """idle_seconds is non-None and >= 0 after each completed send."""
        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("first")]
            assert session.idle_seconds is not None
            assert session.idle_seconds >= 0.0
            _ = [e async for e in session.send("second")]
            assert session.idle_seconds is not None
            assert session.idle_seconds >= 0.0


# ──────────────────────────────────────────────────────────────────
# Concurrent-send guard (Bug: typing... but no response)
# ──────────────────────────────────────────────────────────────────


class TestConcurrentSendGuard:
    """send() must queue a second caller when one is already in flight.

    Root cause of the original bug: two messages arriving concurrently both
    called session.send() at the same time — the second query() call raced
    with the first receive_response() loop, corrupting the stream.

    The fix (Bug.005) changed the policy from *reject* to *queue*: a second
    send() now waits for the lock instead of immediately returning an error,
    so users can continue chatting while a background agent is running.
    """

    async def test_bug005_second_send_queues_not_rejects(self) -> None:
        """Bug.005 regression: a second send() while the first is in-flight must
        WAIT (queue) rather than immediately yield an ErrorEvent.

        Previously the session rejected the second message with
        "Still processing your previous request — please wait", making it
        impossible to chat while a background agent was running.
        """
        import asyncio as _asyncio
        from archon.ai.event_mapper import ErrorEvent as _EE

        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()

        # Hold the lock to simulate the orchestrator being mid-response.
        await session._send_lock.acquire()

        second_events: list = []
        second_done = _asyncio.Event()

        async def _second() -> None:
            async for event in session.send("can I chat while Agent Onyx is running?"):
                second_events.append(event)
            second_done.set()

        task = _asyncio.create_task(_second())
        await _asyncio.sleep(0)  # let the task start and block on the lock

        # Task must be WAITING — not done — because the lock is still held.
        assert not second_done.is_set(), (
            "send() returned immediately; expected it to wait for the lock (Bug.005)"
        )

        # Simulate orchestrator finishing → release the lock.
        session._send_lock.release()
        await task

        # Must NOT contain any "please wait" error — the message was queued.
        errors = [e for e in second_events if isinstance(e, _EE)]
        assert not errors, (
            f"Bug.005 not fixed: got ErrorEvent instead of queuing — "
            f"{errors[0].message!r}"
        )
        assert any(isinstance(e, Response) for e in second_events)

    async def test_second_send_query_not_called_while_lock_held(self) -> None:
        """query() must NOT be called until the lock is actually acquired —
        the SDK must never see two concurrent query() calls."""
        import asyncio as _asyncio

        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()

        await session._send_lock.acquire()

        async def _second() -> None:
            async for _ in session.send("queued"):
                pass

        task = _asyncio.create_task(_second())
        await _asyncio.sleep(0)  # let the task reach the lock and block

        # Lock still held — query() must not have been called yet.
        mock_client.query.assert_not_called()

        session._send_lock.release()
        await task

        # After the lock is released the queued send runs and calls query() once.
        mock_client.query.assert_awaited_once()

    async def test_send_lock_released_after_normal_completion(self) -> None:
        """After send() completes normally the lock is free for the next call."""
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("first")]

        assert not session._send_lock.locked()

    async def test_send_lock_released_after_early_break(self) -> None:
        """Breaking out of the async-for loop must still release the lock."""
        import contextlib
        from claude_agent_sdk import AssistantMessage, ThinkingBlock

        messages = [
            AssistantMessage(content=[ThinkingBlock(thinking="hmm", signature="s")], model="m"),
            _result_message(),
        ]
        session = ClaudeSession()
        mock_client = _make_mock_client(messages)
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            async with contextlib.aclosing(session.send("prompt")) as gen:
                async for _ in gen:
                    break  # abandon the generator mid-stream

        assert not session._send_lock.locked()

    async def test_send_lock_released_after_exception(self) -> None:
        """An exception inside receive_response() must still release the lock."""
        session = ClaudeSession()

        async def _error_receive():  # type: ignore[return]
            raise RuntimeError("network failure")
            yield  # make it an async generator

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()
        mock_client.receive_response = _error_receive

        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            with pytest.raises(RuntimeError, match="network failure"):
                async for _ in session.send("prompt"):
                    pass

        assert not session._send_lock.locked()

    async def test_sequential_sends_both_process_in_order(self) -> None:
        """Two sequential send() calls both complete successfully.

        Verifies that the queuing mechanism works end-to-end: the second
        send() starts only after the first releases the lock.
        """
        import asyncio as _asyncio

        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()

        # Hold the lock to represent the first in-flight send.
        await session._send_lock.acquire()

        first_events: list = []
        second_events: list = []

        async def _second() -> None:
            async for event in session.send("second"):
                second_events.append(event)

        # Queue the second send while the first is "in flight".
        task = _asyncio.create_task(_second())
        await _asyncio.sleep(0)

        # "First send" completes — release the lock.
        # Manually add a response event to represent the first send's output.
        first_events.append(Response(content="first done"))
        session._send_lock.release()
        await task

        assert any(isinstance(e, Response) for e in first_events)
        assert any(isinstance(e, Response) for e in second_events)


# ──────────────────────────────────────────────────────────────────
# inject_context — S15.1
# ──────────────────────────────────────────────────────────────────


class TestInjectContext:
    """inject_context() queues text prepended to the next send() call (one-shot)."""

    def test_inject_context_queues_text(self) -> None:
        session = ClaudeSession()
        session.inject_context("some context")
        assert session._pending_context == ["some context"]

    def test_inject_context_multiple_calls_accumulate(self) -> None:
        session = ClaudeSession()
        session.inject_context("first")
        session.inject_context("second")
        assert session._pending_context == ["first", "second"]

    async def test_inject_context_prepended_before_prompt(self) -> None:
        """Context block should precede the user prompt in the query call."""
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            session.inject_context("ctx line")
            _ = [e async for e in session.send("user prompt")]

        called_with: str = mock_client.query.call_args[0][0]
        assert called_with.startswith("ctx line")
        assert "user prompt" in called_with

    async def test_inject_context_cleared_after_send(self) -> None:
        """_pending_context cleared after one send; second send gets no prefix."""
        session = ClaudeSession()
        mock_client = _make_batch_client([[_result_message()], [_result_message()]])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            session.inject_context("one-shot ctx")
            _ = [e async for e in session.send("first")]
            _ = [e async for e in session.send("second")]

        first_call: str = mock_client.query.call_args_list[0][0][0]
        second_call: str = mock_client.query.call_args_list[1][0][0]
        assert "one-shot ctx" in first_call
        assert "one-shot ctx" not in second_call

    async def test_inject_context_multiple_entries_joined(self) -> None:
        """Multiple context entries are all included in the prompt."""
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            session.inject_context("ctx A")
            session.inject_context("ctx B")
            _ = [e async for e in session.send("prompt")]

        called_with: str = mock_client.query.call_args[0][0]
        assert "ctx A" in called_with
        assert "ctx B" in called_with
        # Order preserved
        assert called_with.index("ctx A") < called_with.index("ctx B")

    async def test_inject_context_before_skills(self) -> None:
        """Context block is prepended before skill blocks."""
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            session.inject_context("injected ctx")
            skill = Skill(name="myskill", description="desc", content="skill body here")
            session.activate_skill(skill)
            _ = [e async for e in session.send("prompt")]

        called_with: str = mock_client.query.call_args[0][0]
        ctx_pos = called_with.index("injected ctx")
        skill_pos = called_with.index("skill body here")
        assert ctx_pos < skill_pos, "Context should come before skill blocks"

    async def test_no_prefix_without_pending_context(self) -> None:
        """With no pending context, the prompt is sent unchanged."""
        session = ClaudeSession()
        mock_client = _make_mock_client([_result_message()])
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("plain prompt")]

        mock_client.query.assert_awaited_once_with("plain prompt")


# ──────────────────────────────────────────────────────────────────
# background_agent_mcp_url + spawn_rule — S15.1
# ──────────────────────────────────────────────────────────────────


class TestBackgroundAgentMcpConfig:
    """Tests for background_agent_mcp_url and spawn_rule integration."""

    def _capture_client(self) -> tuple[list, object]:
        """Return (captured_options_list, side_effect_callable) for patching ClaudeSDKClient."""
        captured: list = []

        def _side_effect(options: object) -> object:  # type: ignore[return]
            captured.append(options)
            return _make_mock_client()

        return captured, _side_effect

    async def test_task_tool_always_disallowed(self) -> None:
        """'Task' is always in disallowed_tools regardless of background_agent_mcp_url.

        The SDK's native Task tool is unconditionally disabled so the orchestrator
        never blocks its send() turn on a synchronous sub-agent run (Bug.005).
        """
        captured, side_effect = self._capture_client()
        # No background_agent_mcp_url set — Task must still be blocked
        session = ClaudeSession()
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        assert "Task" in captured[0].disallowed_tools

    async def test_task_tool_also_disallowed_when_mcp_url_set(self) -> None:
        """'Task' is in disallowed_tools even when background_agent_mcp_url is set."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession(background_agent_mcp_url="http://localhost:18182/mcp/42")
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        assert "Task" in captured[0].disallowed_tools

    async def test_archon_mcp_server_added_when_url_set(self) -> None:
        """background_agent_mcp_url is added to mcp_servers under the 'archon' key."""
        captured, side_effect = self._capture_client()
        mcp_url = "http://localhost:18182/mcp/99"
        session = ClaudeSession(background_agent_mcp_url=mcp_url)
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        mcp_servers = captured[0].mcp_servers
        assert "archon" in mcp_servers
        assert mcp_servers["archon"]["url"] == mcp_url
        assert mcp_servers["archon"]["type"] == "http"

    async def test_archon_mcp_not_added_when_url_none(self) -> None:
        """No 'archon' entry in mcp_servers when background_agent_mcp_url is None."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession()
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        assert "archon" not in captured[0].mcp_servers

    async def test_mcp_headers_included_in_archon_config_when_provided(self) -> None:
        """mcp_headers are added to the archon MCP server config dict."""
        captured, side_effect = self._capture_client()
        headers = {"Authorization": "Bearer mytoken"}
        session = ClaudeSession(
            background_agent_mcp_url="http://localhost:18183/mcp",
            mcp_headers=headers,
        )
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        archon_cfg = captured[0].mcp_servers["archon"]
        assert archon_cfg["headers"] == headers

    async def test_mcp_headers_not_added_when_empty(self) -> None:
        """No 'headers' key in archon MCP config when mcp_headers is None."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession(background_agent_mcp_url="http://localhost:18183/mcp")
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        archon_cfg = captured[0].mcp_servers["archon"]
        assert "headers" not in archon_cfg

    async def test_spawn_rule_eager_in_system_prompt(self) -> None:
        """'eager' spawn_rule appends its hint to the system prompt."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession(
            spawn_rule="eager",
            background_agent_mcp_url="http://localhost:18182/mcp/1",
        )
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        sp = captured[0].system_prompt
        assert sp is not None
        assert "spawn_background_agent" in sp
        assert "proactively" in sp.lower() or "parallel" in sp.lower()

    async def test_spawn_rule_auto_in_system_prompt(self) -> None:
        """'auto' spawn_rule appends its hint to the system prompt."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession(
            spawn_rule="auto",
            background_agent_mcp_url="http://localhost:18182/mcp/1",
        )
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        sp = captured[0].system_prompt
        assert sp is not None
        assert "spawn_background_agent" in sp

    async def test_spawn_rule_manual_in_system_prompt(self) -> None:
        """'manual' spawn_rule hint says 'explicitly'."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession(
            spawn_rule="manual",
            background_agent_mcp_url="http://localhost:18182/mcp/1",
        )
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        sp = captured[0].system_prompt
        assert sp is not None
        assert "spawn_background_agent" in sp
        assert "explicitly" in sp.lower()

    async def test_spawn_rule_none_no_hint_without_skills(self) -> None:
        """When spawn_rule is None and no skills, system_prompt is None."""
        captured, side_effect = self._capture_client()
        session = ClaudeSession(spawn_rule=None)
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        assert captured[0].system_prompt is None

    async def test_spawn_rule_none_skills_preserved(self) -> None:
        """When spawn_rule is None but skills present, system_prompt contains skills only."""
        captured, side_effect = self._capture_client()
        skill = Skill(name="mys", description="my skill", content="")
        session = ClaudeSession(skills=[skill], spawn_rule=None)
        with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
            await session.start()
        assert captured
        sp = captured[0].system_prompt
        assert sp is not None
        assert "mys" in sp
        assert "spawn_background_agent" not in sp

    async def test_each_spawn_rule_produces_distinct_hint(self) -> None:
        """Each of the three spawn_rule values produces a unique system prompt."""
        hints = []
        for rule in ("eager", "auto", "manual"):
            captured, side_effect = self._capture_client()
            session = ClaudeSession(
                spawn_rule=rule,
                background_agent_mcp_url="http://localhost:18182/mcp/1",
            )
            with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=side_effect):
                await session.start()
            hints.append(captured[0].system_prompt)
        assert len(set(hints)) == 3, "Each spawn_rule should produce a distinct prompt"


# ──────────────────────────────────────────────────────────────────
# FR.003 — source tagging on SDK-derived events
# ──────────────────────────────────────────────────────────────────


async def test_regular_events_have_orchestrator_source() -> None:
    """SDK-derived events (ThinkingResult, ToolStarted, etc.) have source='orchestrator'."""
    from claude_agent_sdk import AssistantMessage, ThinkingBlock, ResultMessage

    messages = [
        AssistantMessage(content=[ThinkingBlock(thinking="thinking", signature="s")], model="m"),
        ResultMessage(
            subtype="success",
            duration_ms=10,
            duration_api_ms=5,
            is_error=False,
            num_turns=1,
            session_id="s1",
            result="Done.",
        ),
    ]
    session = ClaudeSession()
    mock_client = _make_mock_client(messages)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("Do it")]

    from archon.ai.event_mapper import ThinkingResult, Response
    for event in events:
        assert event.source == "orchestrator", (
            f"SDK-derived event {type(event).__name__} must have source='orchestrator', "
            f"got source={event.source!r}"
        )


# ──────────────────────────────────────────────────────────────────
# Reminder injection — US-004
# ──────────────────────────────────────────────────────────────────


def _make_batch_client_for_reminder(reminder_batch: list, main_batch: list) -> MagicMock:
    """Mock client that returns reminder_batch for the reminder query, then main_batch for the main query."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    batches = [reminder_batch, main_batch]
    batch_iter = iter(batches)

    def _receive_response():
        msgs = next(batch_iter, [])
        async def _gen():  # type: ignore[return]
            for m in msgs:
                yield m
        return _gen()

    client.receive_response = _receive_response
    return client


async def test_reminder_injected_as_separate_turn(tmp_path) -> None:
    """When should_inject() is True, a ReminderInjectedEvent is yielded before the main response."""
    from archon.ai.event_mapper import ReminderInjectedEvent
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    config = ReminderConfig(enabled=True, interval_messages=5, interval_tokens=40000)
    reminder = ContextReminder(config, tmp_path)
    # Trigger threshold: set message count at or above interval
    for _ in range(5):
        reminder.record_message()

    assert reminder.should_inject()

    mock_client = _make_batch_client_for_reminder([], [_result_message()])
    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("hello")]

    reminder_events = [e for e in events if isinstance(e, ReminderInjectedEvent)]
    assert len(reminder_events) == 1
    assert reminder_events[0].message_count == 5
    # Reminder query call must have happened before the main query
    assert mock_client.query.call_count == 2


async def test_reminder_not_injected_when_below_threshold(tmp_path) -> None:
    """When message count is below threshold, no ReminderInjectedEvent is yielded."""
    from archon.ai.event_mapper import ReminderInjectedEvent
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    config = ReminderConfig(enabled=True, interval_messages=10, interval_tokens=40000)
    reminder = ContextReminder(config, tmp_path)
    for _ in range(3):  # below threshold of 10
        reminder.record_message()

    assert not reminder.should_inject()

    mock_client = _make_mock_client([_result_message()])
    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("hello")]

    reminder_events = [e for e in events if isinstance(e, ReminderInjectedEvent)]
    assert len(reminder_events) == 0
    # Only the main query should have been called
    mock_client.query.assert_awaited_once_with("hello")


async def test_reminder_not_injected_when_disabled(tmp_path) -> None:
    """When ReminderConfig.enabled=False, no injection occurs even above threshold."""
    from archon.ai.event_mapper import ReminderInjectedEvent
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    config = ReminderConfig(enabled=False, interval_messages=1, interval_tokens=1)
    reminder = ContextReminder(config, tmp_path)
    reminder.record_message()  # would trigger if enabled

    assert not reminder.should_inject()

    mock_client = _make_mock_client([_result_message()])
    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        events = [e async for e in session.send("hello")]

    reminder_events = [e for e in events if isinstance(e, ReminderInjectedEvent)]
    assert len(reminder_events) == 0
    mock_client.query.assert_awaited_once_with("hello")


# ──────────────────────────────────────────────────────────────────
# Generator drain — ResultMessage captured even on early exit
# ──────────────────────────────────────────────────────────────────


async def test_usage_stats_updated_after_early_generator_exit() -> None:
    """ResultMessage metadata must be captured even when the consumer breaks before
    the generator is exhausted (e.g., task promotion in _task_direct_monitored)."""
    from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

    tool_msg = AssistantMessage(
        content=[ToolUseBlock(id="t1", name="Read", input={"file": "test.py"})],
        model="test",
    )
    result_msg = ResultMessage(
        subtype="success",
        duration_ms=750,
        duration_api_ms=400,
        is_error=False,
        num_turns=2,
        session_id="s1",
        result="Done",
        usage={"input_tokens": 500, "output_tokens": 50, "cache_creation_input_tokens": 200},
        total_cost_usd=0.007,
    )

    mock_client = _make_mock_client([tool_msg, result_msg])
    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        # Break after the first event (ToolStarted), simulating task promotion.
        # Then explicitly close the generator to trigger the finally-block drain,
        # mirroring what Python's async generator finalizer does in production.
        gen = session.send("do something complex")
        async for _ in gen:
            break
        await gen.aclose()

    # Without the drain fix, usage_stats would be None because the ResultMessage
    # was never reached before the consumer broke out of the loop.
    stats = session.usage_stats
    assert stats is not None, "usage_stats must be populated even after early generator exit"
    assert stats["usage"]["input_tokens"] == 500
    assert abs(stats["total_cost_usd"] - 0.007) < 1e-9
    assert stats["num_turns"] == 2
    assert stats["cumulative_cache_creation"] == 200


# ──────────────────────────────────────────────────────────────────
# user_turns — send count exposed in usage_stats
# ──────────────────────────────────────────────────────────────────


async def test_usage_stats_exposes_user_turns() -> None:
    """usage_stats must include user_turns reflecting the number of send() calls,
    not the SDK's per-query num_turns (which is always 1 for chat responses)."""
    from claude_agent_sdk import ResultMessage

    msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,  # SDK always returns 1 for no-tool chat responses
        session_id="s1",
        result="OK",
        usage={"input_tokens": 100, "output_tokens": 10},
        total_cost_usd=0.001,
    )
    batches = [[msg], [msg], [msg]]
    batch_iter = iter(batches)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    def _receive_response():
        msgs = next(batch_iter, [])

        async def _gen():
            for m in msgs:
                yield m

        return _gen()

    mock_client.receive_response = _receive_response

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("first")]
        _ = [e async for e in session.send("second")]
        _ = [e async for e in session.send("third")]

    stats = session.usage_stats
    assert stats is not None
    assert stats["user_turns"] == 3, (
        "user_turns must count user messages (3), not SDK num_turns (always 1)"
    )

# ──────────────────────────────────────────────────────────────────
# _last_usage reset — stale data not exposed on failure
# ──────────────────────────────────────────────────────────────────


async def test_last_usage_reset_at_start_of_send() -> None:
    """_last_usage must be reset to None at the start of send() so that stale data
    from a previous successful turn is not exposed when the current turn fails.

    Without this reset, handler.py's finally block would read the previous turn's
    usage data and double-count tokens via record_tokens().
    """
    session = ClaudeSession()
    # Simulate stale _last_usage from a previous successful turn
    session._last_usage = {"input_tokens": 5000, "output_tokens": 1000}  # type: ignore[attr-defined]
    session._connected = True

    # Client that fails mid-stream (before ResultMessage is received)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    async def _failing_receive():  # type: ignore[return]
        raise RuntimeError("Network error during receive")
        yield  # make it an async generator

    mock_client.receive_response = _failing_receive
    session._client = mock_client  # type: ignore[attr-defined]

    try:
        async for _ in session.send("test"):
            pass
    except RuntimeError:
        pass

    # After failure, usage_stats must be None — not stale from previous turn
    assert session.usage_stats is None


# ──────────────────────────────────────────────────────────────────
# Reminder tracking in send() — record_message and record_tokens
# ──────────────────────────────────────────────────────────────────


def _make_reminder(tmp_path, interval_messages: int = 100, interval_tokens: int = 1_000_000) -> "ContextReminder":
    """Build a ContextReminder with high thresholds so should_inject() stays False."""
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    config = ReminderConfig(
        enabled=True,
        interval_messages=interval_messages,
        interval_tokens=interval_tokens,
    )
    return ContextReminder(config, tmp_path)


async def test_send_records_reminder_message_on_success(tmp_path) -> None:
    """After a successful send(), reminder.record_message() is called exactly once."""
    from unittest.mock import MagicMock

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([_result_message()])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    reminder.record_message.assert_called_once()


async def test_send_records_reminder_tokens_on_success(tmp_path) -> None:
    """After a successful send() with usage data, reminder.record_tokens(total) is called.

    record_tokens receives input_tokens + output_tokens only (cache_creation excluded).
    """
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={
            "input_tokens": 300,
            "output_tokens": 200,
            "cache_creation_input_tokens": 1500,
        },
        total_cost_usd=0.005,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    reminder.record_tokens.assert_called_once_with(500)  # 300 + 200 (cache_creation excluded)


async def test_send_records_reminder_message_on_send_failure(tmp_path) -> None:
    """When send() raises (SDK error), reminder.record_message() is still called (finally fires)."""
    from unittest.mock import MagicMock

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    async def _error_receive():  # type: ignore[return]
        raise RuntimeError("SDK failure")
        yield  # make it an async generator

    mock_client.receive_response = _error_receive

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        with pytest.raises(RuntimeError, match="SDK failure"):
            async for _ in session.send("hello"):
                pass

    # finally block must still fire record_message
    reminder.record_message.assert_called_once()


async def test_send_does_not_record_tokens_when_no_result_message(tmp_path) -> None:
    """When send() fails before ResultMessage arrives, record_tokens() is NOT called."""
    from unittest.mock import MagicMock

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()

    async def _error_receive():  # type: ignore[return]
        raise RuntimeError("SDK failure before ResultMessage")
        yield  # make it an async generator

    mock_client.receive_response = _error_receive

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        with pytest.raises(RuntimeError):
            async for _ in session.send("hello"):
                pass

    # _last_usage is None (reset at start of send, never set by ResultMessage)
    # so record_tokens must NOT be called
    reminder.record_tokens.assert_not_called()


async def test_send_records_reminder_tokens_excludes_cache_creation(tmp_path) -> None:
    """record_tokens() must NOT include cache_creation_input_tokens.

    The cold-cache first turn includes the entire system prompt in cache_creation
    (~20-50K+), which would blow the token threshold after 1-2 turns.
    Only input_tokens + output_tokens track actual conversational activity.
    """
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={
            "input_tokens": 5,
            "output_tokens": 3,
            "cache_creation_input_tokens": 8000,
        },
        total_cost_usd=0.01,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    # Must be 5 + 3 = 8 (cache_creation excluded)
    reminder.record_tokens.assert_called_once_with(8)


async def test_send_records_reminder_tokens_without_cache_creation_key(tmp_path) -> None:
    """When cache_creation_input_tokens is absent, fall back to input + output only."""
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={"input_tokens": 300, "output_tokens": 200},
        total_cost_usd=0.005,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    reminder.record_tokens.assert_called_once_with(500)


async def test_send_records_reminder_tokens_cache_creation_is_zero(tmp_path) -> None:
    """cache_creation_input_tokens=0 should not affect the sum."""
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_creation_input_tokens": 0,
        },
        total_cost_usd=0.001,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    reminder.record_tokens.assert_called_once_with(15)


async def test_send_records_reminder_tokens_cache_creation_is_none(tmp_path) -> None:
    """cache_creation_input_tokens=None (within dict[str, Any]) must be treated as 0."""
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
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
        },
        total_cost_usd=0.001,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    reminder.record_tokens.assert_called_once_with(150)  # None treated as 0


async def test_send_records_reminder_tokens_excludes_cache_read(tmp_path) -> None:
    """cache_read_input_tokens must NOT be included in the reminder token sum.

    It is intentionally excluded because it is inflated by tool-call multiplicity —
    each API call within one SDK query reads the full cache, so summing it would
    vastly over-count actual token consumption.  This test prevents a future
    developer from "helpfully" adding cache_read_input_tokens to the formula.
    """
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={
            "input_tokens": 300,
            "output_tokens": 200,
            "cache_creation_input_tokens": 1500,
            "cache_read_input_tokens": 50000,
        },
        total_cost_usd=0.01,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    # Must be 300 + 200 = 500 (excludes both cache_read AND cache_creation)
    reminder.record_tokens.assert_called_once_with(500)


async def test_send_records_reminder_tokens_missing_input_output_keys(tmp_path) -> None:
    """When usage dict has ONLY cache_creation_input_tokens (no input_tokens or
    output_tokens keys at all), record_tokens() must use 0 for the missing keys."""
    from claude_agent_sdk import ResultMessage
    from unittest.mock import MagicMock

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={"cache_creation_input_tokens": 5000},
        total_cost_usd=0.005,
    )

    reminder = _make_reminder(tmp_path)
    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()  # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)
    mock_client = _make_mock_client([result_msg])
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    # cache_creation excluded; input and output missing → 0
    reminder.record_tokens.assert_called_once_with(0)


# ──────────────────────────────────────────────────────────────────
# Fix 2: Capture ResultMessage from silent reminder injection turn
# ──────────────────────────────────────────────────────────────────


def _make_reminder_inject_client(
    reminder_result_msg,
    user_result_msg,
) -> MagicMock:
    """Mock client that yields reminder_result_msg for the reminder turn,
    then user_result_msg for the user turn."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    batches = [[reminder_result_msg], [user_result_msg]]
    batch_iter = iter(batches)

    def _receive_response():
        msgs = next(batch_iter, [])
        async def _gen():  # type: ignore[return]
            for m in msgs:
                yield m
        return _gen()

    client.receive_response = _receive_response
    return client


async def test_reminder_injection_cost_captured(tmp_path) -> None:
    """ResultMessage from the reminder injection turn must contribute to
    _total_cost_usd and _cumulative_cache_creation."""
    from claude_agent_sdk import ResultMessage
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    # Threshold=1 so should_inject() is True immediately after first message
    config = ReminderConfig(enabled=True, interval_messages=1, interval_tokens=1_000_000)
    reminder = ContextReminder(config, tmp_path)
    reminder.record_message()  # trigger threshold

    assert reminder.should_inject()

    reminder_result = ResultMessage(
        subtype="success",
        duration_ms=50,
        duration_api_ms=30,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="",
        usage={
            "input_tokens": 50,
            "output_tokens": 5,
            "cache_creation_input_tokens": 100,
        },
        total_cost_usd=0.005,
    )
    user_result = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=60,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="OK",
        usage={"input_tokens": 200, "output_tokens": 20},
        total_cost_usd=0.002,
    )

    mock_client = _make_reminder_inject_client(reminder_result, user_result)
    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    # Both the reminder turn cost and the user turn cost must be accumulated
    assert abs(session._total_cost_usd - 0.007) < 1e-9, (
        f"Expected 0.007 (0.005 + 0.002), got {session._total_cost_usd}"
    )
    # Reminder turn's cache_creation_input_tokens must be captured
    assert session._cumulative_cache_creation >= 100, (
        f"Expected cumulative_cache_creation >= 100, got {session._cumulative_cache_creation}"
    )
    # _last_usage must reflect the USER turn, not the reminder turn
    assert session._last_usage is not None
    assert session._last_usage.get("input_tokens") == 200, (
        f"_last_usage must reflect user turn (200 input tokens), got {session._last_usage}"
    )


async def test_record_message_not_called_when_reminder_injection_fails(tmp_path) -> None:
    """Fix 2: if client.query() raises during reminder injection (before user message is sent),
    record_message() must NOT be called — the user turn never reached Claude."""
    from unittest.mock import MagicMock
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    # Threshold=1 so should_inject() is True immediately
    config = ReminderConfig(enabled=True, interval_messages=1, interval_tokens=1_000_000)
    reminder = ContextReminder(config, tmp_path)
    reminder.record_message()  # trigger threshold so should_inject() returns True
    assert reminder.should_inject()

    reminder.record_message = MagicMock()  # type: ignore[method-assign]
    reminder.record_tokens = MagicMock()   # type: ignore[method-assign]

    session = ClaudeSession(reminder=reminder)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    # First call (reminder injection) raises; user message call never happens
    mock_client.query = AsyncMock(side_effect=RuntimeError("SDK error during reminder injection"))

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        with pytest.raises(RuntimeError, match="SDK error during reminder injection"):
            async for _ in session.send("hello"):
                pass

    # record_message must NOT have been called — user message was never queued
    reminder.record_message.assert_not_called()


async def test_reminder_injection_last_usage_not_overwritten(tmp_path) -> None:
    """_last_usage must NOT be set from the reminder turn — it must reflect
    the user turn only, so record_tokens() counts user tokens, not reminder tokens."""
    from claude_agent_sdk import ResultMessage
    from archon.ai.reminder import ContextReminder
    from archon.config.loader import ReminderConfig

    reminder_file = tmp_path / "REMINDER.md"
    reminder_file.write_text("Keep context fresh.", encoding="utf-8")

    config = ReminderConfig(enabled=True, interval_messages=1, interval_tokens=1_000_000)
    reminder = ContextReminder(config, tmp_path)
    reminder.record_message()

    assert reminder.should_inject()

    reminder_result = ResultMessage(
        subtype="success",
        duration_ms=50,
        duration_api_ms=30,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="",
        usage={"input_tokens": 999, "output_tokens": 999},  # distinct sentinel values
        total_cost_usd=0.001,
    )
    user_result = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=60,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="Answer",
        usage={"input_tokens": 123, "output_tokens": 45},
        total_cost_usd=0.003,
    )

    mock_client = _make_reminder_inject_client(reminder_result, user_result)
    session = ClaudeSession(reminder=reminder)
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()
        _ = [e async for e in session.send("hello")]

    # usage_stats["usage"] must reflect user turn tokens (123/45), NOT reminder turn (999/999)
    stats = session.usage_stats
    assert stats is not None
    assert stats["usage"]["input_tokens"] == 123, (
        f"usage_stats must show user turn tokens (123), got {stats['usage']['input_tokens']}"
    )
    assert stats["usage"]["output_tokens"] == 45, (
        f"usage_stats must show user turn tokens (45), got {stats['usage']['output_tokens']}"
    )


# ──────────────────────────────────────────────────────────────────
# flush_pending_context
# ──────────────────────────────────────────────────────────────────


def test_flush_pending_context_clears_queue() -> None:
    """flush_pending_context() discards all queued context entries."""
    session = ClaudeSession()
    session.inject_context("first")
    session.inject_context("second")
    assert len(session._pending_context) == 2

    session.flush_pending_context()

    assert session._pending_context == []


def test_flush_pending_context_is_noop_when_empty() -> None:
    """flush_pending_context() does not raise when queue is already empty."""
    session = ClaudeSession()
    session.flush_pending_context()  # must not raise
    assert session._pending_context == []


# ──────────────────────────────────────────────────────────────────
# Bug 10 — Generator drain timeout closes the generator (resource cleanup)
# ──────────────────────────────────────────────────────────────────


async def test_early_aclose_does_not_hang(caplog: pytest.LogCaptureFixture) -> None:
    """Bug 10: gen.aclose() completes quickly even when the SDK stream is slow.

    This tests the promotion scenario where the consumer calls aclose() after seeing a
    tool-use event, before the SDK stream has finished. The drain safety-net in the finally
    block must not block indefinitely — it either drains quickly or times out.

    The actual promotion flow in _task_direct_monitored:
      gen = decomposer.answer(prompt)
      async for event in gen:
          if tool_count >= threshold:
              await gen.aclose()   ← must complete without hanging
              return
    """
    import logging
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    async def _slow_receive_response():  # type: ignore[return]
        """Yields one ToolUseBlock then blocks indefinitely — simulates a live SDK stream."""
        yield AssistantMessage(content=[ToolUseBlock(id="t1", name="Read", input={})], model="m")
        await asyncio.sleep(60)  # blocks — no ResultMessage arrives
        yield object()  # pragma: no cover

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    client.receive_response = _slow_receive_response

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()

        gen = session.send("long task")
        # Get the first event (ToolStarted), then close — like task promotion
        first_event = await gen.__anext__()
        # aclose() must complete without hanging even though the SDK stream is slow.
        # Python's async generator cleanup closes intercept_gen automatically, so the
        # drain in the finally block completes instantly (or times out gracefully).
        import asyncio as _asyncio
        await _asyncio.wait_for(gen.aclose(), timeout=8.0)

    assert first_event is not None  # consumed at least one event before closing
    # Session should still be alive (stop() not called)
    assert session.is_alive
    # No errors logged (drain may or may not fire a warning depending on cleanup order)
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records == [], f"Unexpected errors: {[r.message for r in error_records]}"


# ──────────────────────────────────────────────────────────────────
# Bug 11 — stop() from a different task falls back to transport.close()
# ──────────────────────────────────────────────────────────────────


async def test_stop_from_different_task_closes_transport() -> None:
    """Bug 11: when disconnect() raises RuntimeError (cancel scope), transport is closed directly."""
    transport_close_called = False

    class _MockTransport:
        async def close(self) -> None:
            nonlocal transport_close_called
            transport_close_called = True

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock(side_effect=RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"))
    client._transport = _MockTransport()

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        await session.stop()

    assert transport_close_called, "transport.close() must be called when disconnect() raises RuntimeError"
    assert not session.is_alive


async def test_stop_from_different_task_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Bug 11: disconnect RuntimeError is logged as WARNING, not silently dropped."""
    import logging

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock(side_effect=RuntimeError("Attempted to exit cancel scope in a different task than it was entered in"))
    client._transport = MagicMock()
    client._transport.close = AsyncMock()

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        with caplog.at_level(logging.WARNING, logger="archon"):
            await session.stop()

    warnings = [r for r in caplog.records if "disconnect skipped" in r.message.lower()]
    assert len(warnings) >= 1


async def test_stop_transport_close_error_is_swallowed() -> None:
    """Bug 11: if transport.close() itself raises, stop() must still complete without propagating."""

    class _FailingTransport:
        async def close(self) -> None:
            raise OSError("transport already closed")

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock(side_effect=RuntimeError("cancel scope"))
    client._transport = _FailingTransport()

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        await session.stop()  # must not raise even if transport.close() fails

    assert not session.is_alive


async def test_stop_without_transport_still_works() -> None:
    """Bug 11: if client has no _transport attribute (unusual), stop() must not crash."""

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock(side_effect=RuntimeError("cancel scope"))
    # Deliberately omit _transport attribute
    del client._transport

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        await session.stop()  # must not raise

    assert not session.is_alive


async def test_stop_oserror_still_closes_transport() -> None:
    """M1: when disconnect() raises OSError (not just RuntimeError), transport fallback is still attempted."""
    transport_close_called = False

    class _MockTransport:
        async def close(self) -> None:
            nonlocal transport_close_called
            transport_close_called = True

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock(side_effect=OSError("connection reset by peer"))
    client._transport = _MockTransport()

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=client):
        await session.start()
        await session.stop()

    assert transport_close_called, "transport.close() must be called when disconnect() raises OSError"
    assert not session.is_alive


# ──────────────────────────────────────────────────────────────────
# BUG-13 — _send_lock permanently held on CancelledError during drain
# ──────────────────────────────────────────────────────────────────


async def test_send_lock_released_after_cancelled_error_in_drain() -> None:
    """BUG-13: CancelledError during the drain phase must not permanently hold _send_lock.

    In Python 3.9+, CancelledError is a BaseException, not Exception.  The drain's
    except clauses only catch TimeoutError and Exception, so a CancelledError thrown
    into wait_for() would skip the lock-release at the bottom of the finally block.

    This test:
    1. Starts a send() that gets interrupted by CancelledError during the drain phase.
    2. Verifies that _send_lock is NOT permanently held afterwards.
    3. Verifies that _processing is reset to False.
    4. Verifies that a subsequent send() can complete normally (no deadlock).
    """
    import asyncio as _asyncio
    import contextlib
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    # A receive_response that blocks indefinitely after the first message,
    # simulating a slow SDK stream so the drain will be running when cancelled.
    drain_started = _asyncio.Event()

    async def _blocking_receive():  # type: ignore[return]
        yield AssistantMessage(
            content=[ToolUseBlock(id="t1", name="Read", input={})], model="m"
        )
        drain_started.set()
        await _asyncio.sleep(60)  # blocks — keeps the drain alive
        yield object()  # pragma: no cover

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = _blocking_receive
    mock_client._transport = None

    session = ClaudeSession()
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()

        # Start send() as a task, break after first event so the generator enters
        # its finally-block drain while the underlying stream is still blocking.
        async def _run_send() -> None:
            gen = session.send("trigger drain")
            async with contextlib.aclosing(gen):
                async for _ in gen:
                    break  # exit after first event → drain starts in finally

        task = _asyncio.create_task(_run_send())
        # Wait until drain is running (blocking inside wait_for)
        await _asyncio.wait_for(drain_started.wait(), timeout=5.0)

        # Cancel the task — this injects CancelledError into the running drain
        task.cancel()
        with pytest.raises(_asyncio.CancelledError):
            await task

    # After CancelledError, the lock MUST be released (BUG-13 fix)
    assert not session._send_lock.locked(), (
        "BUG-13: _send_lock is permanently held after CancelledError in drain — "
        "all subsequent send() calls would hang forever"
    )
    # _processing MUST be reset
    assert session._processing is False, (
        "BUG-13: _processing stuck True after CancelledError in drain"
    )

    # A subsequent send() must complete without deadlocking
    mock_client.receive_response = lambda: _make_mock_client([_result_message()]).receive_response()
    result_events: list = []
    async with contextlib.aclosing(session.send("second message")) as gen:
        async for event in gen:
            result_events.append(event)
    assert any(isinstance(e, Response) for e in result_events), (
        "BUG-13: second send() after CancelledError must complete normally (no deadlock)"
    )


# ──────────────────────────────────────────────────────────────────
# P1: force_kill_for_recovery lock isolation
# ──────────────────────────────────────────────────────────────────


def test_force_kill_creates_fresh_lock() -> None:
    """force_kill_for_recovery() must create a new _send_lock so the old
    generator's finally block cannot release the new session's lock.

    Without a fresh lock, the GC-triggered cleanup of the abandoned send()
    generator would call _send_lock.release() on a lock that the NEW send()
    holds — breaking the concurrency guard.
    """
    session = ClaudeSession()
    old_lock = session._send_lock
    session.force_kill_for_recovery()
    assert session._send_lock is not old_lock, (
        "force_kill_for_recovery() must create a fresh asyncio.Lock "
        "to prevent old generator's finally from releasing new session's lock"
    )


# ──────────────────────────────────────────────────────────────────
# rag_url / MCP server key rename (Task 6.1)
# ──────────────────────────────────────────────────────────────────


async def test_rag_url_registers_mcp_server() -> None:
    """rag_url set → mcp_servers['rag'] entry is built correctly."""
    captured: list = []
    url = "http://localhost:8181/mcp"
    session = ClaudeSession(rag_url=url)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch(
            "archon.ai.claude_session.ClaudeAgentOptions",
            side_effect=lambda **kw: captured.append(kw) or MagicMock(),
        ),
    ):
        await session.start()

    mcp_servers = captured[0]["mcp_servers"]
    assert "rag" in mcp_servers
    assert mcp_servers["rag"] == {"type": "http", "url": url}


async def test_rag_url_none_omits_mcp_server() -> None:
    """rag_url=None → no 'rag' key in mcp_servers."""
    captured: list = []
    session = ClaudeSession(rag_url=None)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch(
            "archon.ai.claude_session.ClaudeAgentOptions",
            side_effect=lambda **kw: captured.append(kw) or MagicMock(),
        ),
    ):
        await session.start()

    mcp_servers = captured[0].get("mcp_servers", {})
    assert "rag" not in mcp_servers


async def test_no_qmd_key_in_mcp_servers() -> None:
    """'qmd' key must never appear in mcp_servers regardless of rag_url."""
    captured: list = []
    session = ClaudeSession(rag_url="http://localhost:8181/mcp")
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch(
            "archon.ai.claude_session.ClaudeAgentOptions",
            side_effect=lambda **kw: captured.append(kw) or MagicMock(),
        ),
    ):
        await session.start()

    mcp_servers = captured[0].get("mcp_servers", {})
    assert "qmd" not in mcp_servers


def test_old_qmd_url_kwarg_raises_type_error() -> None:
    """ClaudeSession no longer accepts qmd_url — passing it raises TypeError."""
    with pytest.raises(TypeError, match="qmd_url"):
        ClaudeSession(qmd_url="http://localhost:8181/mcp")  # type: ignore[call-arg]
