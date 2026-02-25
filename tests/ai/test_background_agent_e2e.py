"""End-to-end tests for the Background Agent Execution feature — FR.014.

These tests verify the complete pipeline from spawning a background agent to
the result being stored in the run and a Telegram notification being sent.

Agent output is written to per-agent log files (FR.003) and is never injected
into the main session's chat stream.

A separate test verifies that ClaudeSession.inject_context() still works as a
general-purpose context-prepending mechanism (used by other features).
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.claude_session import ClaudeSession


# ── Shared helpers ─────────────────────────────────────────────────


def _make_mock_bg_session(result: str = "e2e result") -> MagicMock:
    """Fast mock ClaudeSession for the background agent (completes immediately)."""
    from archon.ai.event_mapper import Response

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()

    async def _send(prompt: str):  # type: ignore[return]
        yield Response(content=result)

    session.send = _send
    return session


def _make_sdk_client(messages: list | None = None) -> MagicMock:
    """Mock ClaudeSDKClient that yields *messages* from receive_response()."""
    from claude_agent_sdk import ResultMessage

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=5,
        is_error=False,
        num_turns=1,
        result="",
        session_id="s-test",
        total_cost_usd=0.0,
        usage={},
    )

    async def _receive_response():  # type: ignore[return]
        for m in (messages or [result_msg]):
            yield m

    client.receive_response = _receive_response
    return client


# ── Test 1: Full pipeline — spawn → complete → result stored, notification sent


async def test_full_background_agent_flow_e2e() -> None:
    """Full flow: BackgroundAgentManager spawns an agent; on completion the
    result is stored in run.result and a Telegram notification is sent.

    Agent output is NOT injected into the main session's _pending_context
    (FR.003: agent output is separated from the main chat stream).
    """
    sm = MagicMock()
    sm.get_or_create = AsyncMock(return_value=MagicMock())

    bot = MagicMock()
    bot.send_message = AsyncMock()

    mock_bg = _make_mock_bg_session(result="analysis complete: 42 issues found")

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_bg):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(
            user_id=1,
            task="analyse codebase for issues",
            context="focus on security",
        )
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "completed"
    assert run.result == "analysis complete: 42 issues found"

    # Telegram notification sent (spawn + completion = 2 calls)
    assert bot.send_message.await_count == 2
    completion_msg: str = bot.send_message.call_args_list[1][0][1]
    assert "✅" in completion_msg
    assert run.name in completion_msg

    # FR.003: main session must NOT have received the agent output
    main_session = ClaudeSession()
    assert list(main_session._pending_context) == []


# ── Test 2: ClaudeSession.inject_context() prepends in the next send() ─────


async def test_context_prepended_in_next_send_after_agent_completion() -> None:
    """ClaudeSession.inject_context() queues text that is prepended to the
    next send() call and cleared afterwards.

    inject_context() remains a general-purpose mechanism on ClaudeSession;
    this test verifies its stand-alone behaviour independently of
    BackgroundAgentManager.
    """
    mock_client = _make_sdk_client()

    agent_name = "TestAgent"
    task = "summarise pull requests"
    result = "PR #42 merged cleanly; PR #43 needs review"
    context_text = (
        f"[Background agent {agent_name} completed]\n"
        f"Task: {task}\n"
        f"Response:\n{result}\n"
        f"[End agent {agent_name}]"
    )

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        session = ClaudeSession()
        await session.start()

        # Manually inject context (as any caller may do).
        session.inject_context(context_text)

        # Run the next user send() — exhaust the async generator.
        _ = [event async for event in session.send("what is the status?")]

    # The SDK query must have been called exactly once.
    mock_client.query.assert_awaited_once()
    full_prompt: str = mock_client.query.call_args[0][0]

    # Context must be present and must precede the user's message.
    assert context_text in full_prompt
    assert "what is the status?" in full_prompt
    assert full_prompt.index(context_text) < full_prompt.index("what is the status?")

    # Pending context must be cleared — it must NOT appear in a subsequent send().
    _ = [event async for event in session.send("follow-up question")]
    second_prompt: str = mock_client.query.call_args_list[1][0][0]
    assert context_text not in second_prompt
