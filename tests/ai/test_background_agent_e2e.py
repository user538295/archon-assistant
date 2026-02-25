"""End-to-end tests for the Background Agent Execution feature — FR.014.

These tests verify the complete pipeline from spawning a background agent to the
result appearing as pending context on the main ClaudeSession:

  1. BackgroundAgentManager.spawn() → agent completes → inject_context() called on
     the real ClaudeSession → _pending_context is populated.

  2. After inject_context() has been called by BackgroundAgentManager, the injected
     content is actually prepended to the prompt in the next ClaudeSession.send()
     call, confirming the context survives to the next user interaction.

The ClaudeSession used for the background agent is mocked (to avoid real SDK
calls), while the *main* session is a real ClaudeSession instance so we can
observe its internal state transitions.
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


# ── Test 1: Full pipeline — spawn → complete → pending context ──────


async def test_full_background_agent_flow_e2e() -> None:
    """Full flow: BackgroundAgentManager spawns an agent; on completion inject_context()
    is called on the real main ClaudeSession, populating _pending_context."""
    # Real main session — not started; we only need inject_context() state tracking.
    main_session = ClaudeSession()

    # SessionManager mock returns the real main session from get_or_create().
    sm = MagicMock()
    sm.get_or_create = AsyncMock(return_value=main_session)

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

    # The background agent's result must have been injected into the main session.
    assert run.status == "completed"
    pending = list(main_session._pending_context)
    assert len(pending) == 1

    injected = pending[0]
    assert "42 issues found" in injected
    assert "analyse codebase for issues" in injected
    assert f"[Background agent {run.name} completed]" in injected
    assert f"[End agent {run.name}]" in injected


# ── Test 2: Injected context is prepended in the next send() call ───


async def test_context_prepended_in_next_send_after_agent_completion() -> None:
    """After BackgroundAgentManager calls inject_context(), the injected text must
    appear prepended in the prompt forwarded to the SDK on the next send() call.

    Strategy: start a real ClaudeSession against a mock SDK client; inject context
    the same way BackgroundAgentManager does; run send(); verify query() received
    a prompt that leads with the injected context block.
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

        # Simulate what BackgroundAgentManager._inject_result() does.
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
