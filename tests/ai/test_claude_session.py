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
