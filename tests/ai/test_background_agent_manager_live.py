"""Live e2e tests for BackgroundAgentManager — S15.6 + FR.15.

Run manually with:
  uv run pytest tests/ai/test_background_agent_manager_live.py -m live -v

Uses a real BackgroundAgentManager backed by real ClaudeSession instances.
The aiogram Bot is stubbed so no Telegram messages are sent during the test.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.background_agent_manager import BackgroundAgentManager, _AGENT_BEACON_WORDS
from archon.ai.claude_session import ClaudeSession


_USER_ID = 999_001  # synthetic; never reaches Telegram


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _stub_bot(message_id: int = 99901) -> MagicMock:
    """Telegram Bot stub — captures calls but sends nothing.

    send_message returns a fake Message with a real integer .message_id so
    BackgroundAgentManager can capture it for the beacon task.
    """
    sent = MagicMock()
    sent.message_id = message_id

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent)
    bot.edit_message_text = AsyncMock()
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

        # spawn notification + completion notification = 2 calls
        assert bot.send_message.await_count == 2
        completion_call = bot.send_message.call_args_list[1]
        call_user_id, call_text = completion_call.args
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
        # Only the spawn notification — no cancellation notification
        assert bot.send_message.await_count == 1
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
        # Only the spawn notification — no cancellation notification
        assert bot.send_message.await_count == 1
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


# ──────────────────────────────────────────────────────────────────
# FR.15 — Per-agent working beacon (live)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.live
async def test_live_beacon_fires_new_messages() -> None:
    """Real agent + short beacon interval: every beacon fire sends a NEW message.

    Design (sleep-first, send-always):
    - Beacon sleeps beacon_interval_minutes × 60 s before every action.
    - Every fire: send_message (new message → push notification).
    - No in-place edits are ever made.

    We use 0.016 min ≈ 1 s interval with a long task so the beacon fires multiple
    times.  The bot is fully stubbed — no real Telegram API calls are made.
    """
    sent_msg = MagicMock()
    sent_msg.message_id = 99001

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock()

    main_session = ClaudeSession()
    await main_session.start()
    try:
        # 0.016 min ≈ 1 second interval — fires at least once during a ~20 s task
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
            beacon_interval_minutes=0.016,
        )

        run = await manager.spawn(
            user_id=_USER_ID,
            task=(
                "Write a 300-word essay about the history of computing, "
                "using at least 3 separate tool calls."
            ),
        )
        assert run.status == "running"

        # Wait for at least one beacon send_message beyond the spawn notification
        # (send_message.await_count starts at 1 from spawn; we wait for ≥ 2)
        deadline = asyncio.get_event_loop().time() + 10.0
        while bot.send_message.await_count < 2:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(0.2)

        # Gather final state
        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=90.0)

        assert run.status == "completed"
    finally:
        await main_session.stop()

    # Beacon's first fire sends a new message (spawn is index 0, beacon is index 1+)
    assert bot.send_message.await_count >= 2, (
        "Beacon never fired (expected at least spawn + 1 beacon send_message)"
    )
    first_beacon_text: str = bot.send_message.call_args_list[1][0][1]
    assert run.name in first_beacon_text
    assert "🤖" in first_beacon_text
    has_verb = "working" in first_beacon_text or any(
        w in first_beacon_text for w in _AGENT_BEACON_WORDS
    )
    assert has_verb
    # Beacon never edits — every fire is a new send_message call.
    assert bot.edit_message_text.await_count == 0, (
        "Beacon must never call edit_message_text — send-always design."
    )


@pytest.mark.live
async def test_live_beacon_disabled_when_interval_zero() -> None:
    """With beacon_interval_minutes=0 a real agent never calls edit_message_text."""
    sent_msg = MagicMock()
    sent_msg.message_id = 12345

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock()

    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
            beacon_interval_minutes=0,
        )
        run = await manager.spawn(
            user_id=_USER_ID,
            task="Reply with exactly two words: beacon disabled",
        )
        assert run._task_ref is not None
        await asyncio.wait_for(run._task_ref, timeout=60.0)
    finally:
        await main_session.stop()

    bot.edit_message_text.assert_not_called()


@pytest.mark.live
async def test_live_beacon_cancelled_when_agent_cancelled() -> None:
    """Cancelling a real agent also cancels its beacon task cleanly."""
    sent_msg = MagicMock()
    sent_msg.message_id = 55555

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent_msg)
    bot.edit_message_text = AsyncMock()

    main_session = ClaudeSession()
    await main_session.start()
    try:
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=_stub_session_manager(main_session),
            beacon_interval_minutes=2,
        )
        run = await manager.spawn(
            user_id=_USER_ID,
            task="Write a detailed 1000-word essay about the history of computing",
        )
        # Let the agent start
        await asyncio.sleep(1.0)

        result = await manager.cancel(run.run_id)
        assert result is True

        assert run._task_ref is not None
        done, _ = await asyncio.wait({run._task_ref}, timeout=10.0)
        assert run._task_ref in done

        assert run.status == "cancelled"
        # Beacon interval is 2 min so it never fired
        bot.edit_message_text.assert_not_called()
    finally:
        await main_session.stop()
