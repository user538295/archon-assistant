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


# ── Test 3: FR.15 e2e — beacon counts tool/thinking events and edits


def _make_mock_bg_session_with_tools(
    tool_names: list[str],
    thinking_count: int = 1,
    pause_secs: float = 0.15,
) -> MagicMock:
    """Mock session that yields ToolStarted + ThinkingStarted events, pauses, then Response."""
    from archon.ai.event_mapper import Response, ThinkingStarted, ToolStarted

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()

    async def _send(prompt: str):  # type: ignore[return]
        for name in tool_names:
            yield ToolStarted(name=name)
        for _ in range(thinking_count):
            yield ThinkingStarted()
        await asyncio.sleep(pause_secs)  # beacon fires during pause
        yield Response(content="tool counting done")

    session.send = _send
    return session


async def test_beacon_e2e_counts_tool_and_thinking_events() -> None:
    """E2E: beacon text reflects real event counts from the agent session.

    The mock session yields 3 ToolStarted + 2 ThinkingStarted events, then
    pauses so the beacon can fire.  We verify edit_message_text is called and
    the beacon text contains the correct counts.
    """
    from archon.ai.background_agent_manager import BackgroundAgentManager

    sent_msg = MagicMock()
    sent_msg.message_id = 55555

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock()

    sm = MagicMock()
    sm.get_or_create = AsyncMock(return_value=MagicMock())

    tool_session = _make_mock_bg_session_with_tools(
        tool_names=["Read", "Write", "Bash"],
        thinking_count=2,
        pause_secs=0.20,
    )

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=tool_session):
        # beacon_interval_minutes=0.001 → fires during the 200ms pause
        manager = BackgroundAgentManager(
            bot=bot, session_manager=sm, beacon_interval_minutes=0.001
        )
        run = await manager.spawn(
            user_id=1,
            task="tool count e2e",
            context="",
        )
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "completed"
    assert bot.edit_message_text.await_count >= 1

    # Inspect the last beacon edit — it should have the highest counts
    last_text: str = bot.edit_message_text.call_args_list[-1][1].get("text", "")
    assert run.name in last_text
    assert "3 tools" in last_text
    assert "2 thinking" in last_text


async def test_beacon_e2e_no_counts_shown_when_no_tool_events() -> None:
    """E2E: when the agent has no tool/thinking events, beacon shows only verb with no counts."""
    from archon.ai.background_agent_manager import BackgroundAgentManager

    sent_msg = MagicMock()
    sent_msg.message_id = 11111

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock()

    sm = MagicMock()
    sm.get_or_create = AsyncMock(return_value=MagicMock())

    # Session yields only a pause then a Response — no tool/thinking events
    async def _no_tools_send(prompt: str):  # type: ignore[return]
        from archon.ai.event_mapper import Response
        await asyncio.sleep(0.15)
        yield Response(content="no tools")

    no_tools_session = MagicMock()
    no_tools_session.start = AsyncMock()
    no_tools_session.stop = AsyncMock()
    no_tools_session.send = _no_tools_send

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=no_tools_session):
        manager = BackgroundAgentManager(
            bot=bot, session_manager=sm, beacon_interval_minutes=0.001
        )
        run = await manager.spawn(user_id=1, task="no tools e2e")
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "completed"
    assert bot.edit_message_text.await_count >= 1
    last_text: str = bot.edit_message_text.call_args_list[-1][1].get("text", "")
    # No counts → no parentheses in beacon text
    assert "(" not in last_text
    assert ")" not in last_text
