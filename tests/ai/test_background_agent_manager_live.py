"""Live e2e tests for BackgroundAgentManager — S15.6.

Run manually with:
  uv run pytest tests/ai/test_background_agent_manager_live.py -m live -v

Uses a real BackgroundAgentManager backed by real ClaudeSession instances.
The aiogram Bot is stubbed so no Telegram messages are sent during the test.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.claude_session import ClaudeSession


_USER_ID = 999_001  # synthetic; never reaches Telegram


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _stub_bot() -> MagicMock:
    """Telegram Bot stub — captures calls but sends nothing."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _stub_session_manager(main_session: ClaudeSession) -> MagicMock:
    """Minimal SessionManager stub that returns *main_session* for any user."""
    sm = MagicMock()
    sm.get_or_create = AsyncMock(return_value=main_session)
    return sm


# ──────────────────────────────────────────────────────────────────
# Live tests
# ──────────────────────────────────────────────────────────────────


@pytest.mark.live
async def test_live_spawn_status_transitions_running_to_completed() -> None:
    """spawn() immediately returns status='running'; after task finishes it is 'completed'."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with exactly three words: background agent done",
        )

        assert run.status == "running"

        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)

        assert run.status == "completed"
    finally:
        await main_session.stop()


@pytest.mark.live
async def test_live_spawn_result_is_non_empty() -> None:
    """Completed run has a non-empty result string."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with one word: ok",
        )

        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)

        assert run.result  # non-empty string
    finally:
        await main_session.stop()


@pytest.mark.live
async def test_live_spawn_injects_context_into_main_session() -> None:
    """After completion, inject_context() queues result text on the main session."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with one word: done",
        )

        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)

        # inject_context() accumulates text in _pending_context (one-shot queue)
        assert len(main_session._pending_context) == 1
        ctx = main_session._pending_context[0]
        assert run.name in ctx
        assert run.task in ctx
        assert run.result in ctx
    finally:
        await main_session.stop()


@pytest.mark.live
async def test_live_spawn_sends_telegram_notification_with_agent_name() -> None:
    """Completion triggers bot.send_message once with a ✅ and the agent name."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with one word: hi",
        )

        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)

        bot.send_message.assert_called_once()
        call_user_id, call_text = bot.send_message.call_args.args
        assert call_user_id == _USER_ID
        assert "✅" in call_text
        assert run.name in call_text
    finally:
        await main_session.stop()


@pytest.mark.live
async def test_live_name_released_after_completion() -> None:
    """Agent name is returned to the pool once the task finishes."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with one word: done",
        )
        assigned_name = run.name
        assert assigned_name in manager._active_names

        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)

        assert assigned_name not in manager._active_names
    finally:
        await main_session.stop()


@pytest.mark.live
async def test_live_get_run_reflects_final_state() -> None:
    """get_run() returns the same AgentRun object with completed status and result."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with one word: hello",
        )

        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)

        fetched = manager.get_run(run.run_id)
        assert fetched is run
        assert fetched.status == "completed"
        assert fetched.result
    finally:
        await main_session.stop()


# ──────────────────────────────────────────────────────────────────
# Cancellation — user interrupts a running agent
# ──────────────────────────────────────────────────────────────────


@pytest.mark.live
async def test_live_cancel_sets_status_cancelled_and_cleans_up() -> None:
    """cancel() on an in-flight real agent: status→cancelled, name released,
    no Telegram notification, no inject_context on the main session."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            # Long task so Claude won't finish before we cancel.
            task="Write a detailed 1000-word essay about the history of computing",
        )
        assigned_name = run.name
        assert run.status == "running"
        assert assigned_name in manager._active_names

        # Let the asyncio task start and enter the real SDK (connect + send).
        await asyncio.sleep(0.5)

        result = await manager.cancel(run.run_id)
        assert result is True

        # Wait for _run_agent's CancelledError handler to finish fully.
        assert run._task_ref is not None
        done, _ = await asyncio.wait({run._task_ref}, timeout=10.0)
        assert run._task_ref in done, "Task didn't finish after cancellation"

        assert run.status == "cancelled"
        assert assigned_name not in manager._active_names   # name released
        bot.send_message.assert_not_called()                # no Telegram notification
        assert len(main_session._pending_context) == 0      # no inject_context
    finally:
        await main_session.stop()


@pytest.mark.live
async def test_live_stop_all_cancels_in_flight_agents() -> None:
    """stop_all() cancels every running agent; all transition to 'cancelled'."""
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task="Write a detailed 1000-word essay about the history of computing",
        )
        assert run.status == "running"

        await asyncio.sleep(0.5)  # let task enter the SDK before stopping
        await manager.stop_all()

        assert run.status == "cancelled"
    finally:
        await main_session.stop()


# ──────────────────────────────────────────────────────────────────
# Mid-stream cancellation — cancel while Claude is actively streaming
# ──────────────────────────────────────────────────────────────────


@pytest.mark.live
async def test_live_cancel_while_claude_is_streaming() -> None:
    """Cancel an agent *after* Claude has already started yielding events.

    The previous cancel tests sleep 0.5s and cancel during session.start()
    (subprocess connection phase).  This test waits for the first real event
    to arrive before cancelling, proving that mid-stream cancellation works.

    Strategy: wrap ClaudeSession.send() so it sets an asyncio.Event on the
    first yielded event, then signals us to cancel — without touching any
    production code path.
    """
    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()

    first_event_received = asyncio.Event()

    # Wrap the real ClaudeSession.send() to set our event on first yield.
    original_send = ClaudeSession.send

    async def _instrumented_send(self, prompt: str):  # type: ignore[return]
        async for event in original_send(self, prompt):
            first_event_received.set()  # signal: Claude is now streaming
            yield event

    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        with patch.object(ClaudeSession, "send", _instrumented_send):
            run = await manager.spawn(
                user_id=_USER_ID,
                # Long task — must not finish before we can cancel.
                task="Write a detailed 2000-word essay about the history of computing",
            )

            assert run.status == "running"

            # Wait until the first real streaming event arrives (up to 30s).
            await asyncio.wait_for(first_event_received.wait(), timeout=30.0)

            # Claude is provably mid-stream right now — cancel it.
            result = await manager.cancel(run.run_id)
            assert result is True

        # Wait for the cancellation handler to finish.
        assert run._task_ref is not None
        done, _ = await asyncio.wait({run._task_ref}, timeout=10.0)
        assert run._task_ref in done, "Task didn't finish after mid-stream cancellation"

        assert run.status == "cancelled"
        bot.send_message.assert_not_called()           # no Telegram notification
        assert len(main_session._pending_context) == 0  # no inject_context
    finally:
        await main_session.stop()


# ──────────────────────────────────────────────────────────────────
# Non-blocking: orchestrator must respond while background agent runs
# ──────────────────────────────────────────────────────────────────


@pytest.mark.live
async def test_live_orchestrator_not_blocked_while_background_agent_runs() -> None:
    """Core non-blocking property: the main session responds to a new message
    *immediately* while a background agent is still working on a long task.

    Scenario (mirrors real Telegram usage):
      1. Spawn a long background agent (write a 500-word essay — takes ~30-90s).
      2. Let it get started, then confirm it is still running.
      3. Send a short message to the main session ("tell me a short joke").
      4. Assert the Response arrives while the background agent is still running.

    Failure mode caught: any regression where the main session's _send_lock is
    somehow held or waited on during background agent execution would cause the
    joke request to queue until the essay finishes (~60s+), and the status check
    at step 4 would show status == 'completed', failing the assertion.
    """
    import time

    from archon.ai.event_mapper import Response

    bot = _stub_bot()
    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
        )

        # Step 1: Spawn a genuinely long background task.
        run = await manager.spawn(
            user_id=_USER_ID,
            task=(
                "Write a detailed 500-word essay about the history of artificial "
                "intelligence, covering the 1950s to present day."
            ),
        )
        assert run.status == "running"

        # Step 2: Give the agent time to start so it is provably in-flight.
        await asyncio.sleep(3)
        assert run.status == "running", (
            "Background agent finished in < 3s — pick a longer task for this test"
        )

        # Step 3: Send a short message to the MAIN session.
        t0 = time.monotonic()
        events: list = []
        async for event in main_session.send("Tell me a one-line joke."):
            events.append(event)
        elapsed = time.monotonic() - t0

        # Step 4: A Response must have arrived — and the background agent must
        # still be running (proves the main session did NOT wait for the agent).
        responses = [e for e in events if isinstance(e, Response)]
        assert responses, f"Main session returned no Response event; events={events}"

        assert run.status == "running", (
            f"Background agent already finished after {elapsed:.1f}s — the test "
            "is inconclusive; try a longer background task or a shorter joke prompt"
        )

    finally:
        await manager.stop_all()
        await main_session.stop()
