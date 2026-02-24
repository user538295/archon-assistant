"""Tests for ClaudeSession — S1.1 + S6.1."""
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response, ThinkingStarted, ThinkingResult, ToolStarted
from archon.ai.skill_loader import Skill


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_mock_client(messages: list = []):
    """Build a mock ClaudeSDKClient that yields given messages from receive_response."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

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


async def test_start_strips_claudecode_before_connect() -> None:
    """CLAUDECODE must not be set when connect() is called."""
    seen_during_connect: list[str | None] = []

    async def _connect_spy() -> None:
        seen_during_connect.append(os.environ.get("CLAUDECODE"))

    session = ClaudeSession()
    mock_client = _make_mock_client()
    mock_client.connect = _connect_spy
    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        original = os.environ.get("CLAUDECODE")
        os.environ["CLAUDECODE"] = "1"
        try:
            await session.start()
        finally:
            if original is None:
                os.environ.pop("CLAUDECODE", None)
            else:
                os.environ["CLAUDECODE"] = original

    assert seen_during_connect == [None]  # stripped during connect
    assert os.environ.get("CLAUDECODE") == original  # restored after


async def test_start_restores_claudecode_after_connect() -> None:
    """CLAUDECODE is restored even if connect() raises."""
    async def _failing_connect() -> None:
        raise RuntimeError("connect failed")

    session = ClaudeSession()
    mock_client = _make_mock_client()
    mock_client.connect = _failing_connect
    os.environ["CLAUDECODE"] = "sentinel"
    try:
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            with pytest.raises(RuntimeError):
                await session.start()
        assert os.environ.get("CLAUDECODE") == "sentinel"
    finally:
        os.environ.pop("CLAUDECODE", None)


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
    assert types == [ThinkingStarted, ThinkingResult, ToolStarted, Response]


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

    def test_is_stuck_false_when_not_processing(self) -> None:
        session = ClaudeSession()
        assert session.is_stuck() is False
        assert session.is_stuck(threshold_seconds=0.0) is False

    async def test_is_stuck_true_when_threshold_exceeded(self) -> None:
        """is_stuck(0.0) returns True while processing (any positive elapsed time > 0)."""
        session = ClaudeSession()
        captured: list[bool] = []

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()

        async def _receive():  # type: ignore[return]
            captured.append(session.is_stuck(threshold_seconds=0.0))
            yield _result_message()

        mock_client.receive_response = _receive
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("prompt")]

        assert captured == [True]

    async def test_is_stuck_false_when_under_threshold(self) -> None:
        """is_stuck(999999) returns False even while processing."""
        session = ClaudeSession()
        captured: list[bool] = []

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.query = AsyncMock()

        async def _receive():  # type: ignore[return]
            captured.append(session.is_stuck(threshold_seconds=999_999.0))
            yield _result_message()

        mock_client.receive_response = _receive
        with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
            await session.start()
            _ = [e async for e in session.send("prompt")]

        assert captured == [False]

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
