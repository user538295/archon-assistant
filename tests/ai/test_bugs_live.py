"""Live e2e tests for Bugs 20, 21, 22 — NO mocks, real systems only.

Uses real BackgroundAgentManager + real ClaudeSession (Claude API), plus
simple Python recording classes instead of unittest.mock objects.

All tests are expected to FAIL with the current buggy code and PASS after fixes.

Run with:
  uv run pytest tests/ai/test_bugs_live.py -m live -v
"""
import asyncio
import os
import time

import pytest

from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import RoutingEvent

# Bug 22 tests require starting a full Pipeline (Classifier + Decomposer sessions).
# This fails when run inside an active Claude Code session because the claude binary
# refuses nested spawning (CLAUDECODE env var is set).
_NESTED_SESSION = bool(os.environ.get("CLAUDECODE"))


# ── Recording helpers (no mock library used) ──────────────────────────────────


class RecordingBot:
    """Minimal Telegram Bot substitute — records every send_message call.

    Not a Mock: this is a real Python class with real async methods.
    No unittest.mock is imported or used here.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []  # (chat_id, text)

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))

    def texts(self) -> list[str]:
        return [t for _, t in self.sent]


class RecordingSessionManager:
    """Minimal SessionManager substitute — records inject_agent_context calls.

    Not a Mock: plain Python class, no unittest.mock.
    """

    def __init__(self) -> None:
        self.injected: list[tuple[int, str]] = []  # (user_id, text)
        self.tracked: list[tuple[int, str, str]] = []  # (user_id, prompt, summary)

    def inject_agent_context(self, user_id: int, text: str) -> None:
        self.injected.append((user_id, text))

    def track_context(self, user_id: int, prompt: str, summary: str) -> None:
        self.tracked.append((user_id, prompt, summary))

    async def get_or_create(self, user_id: int) -> None:
        return None


_USER_ID = 888_020  # synthetic; never reaches Telegram


# ══════════════════════════════════════════════════════════════════════════════
# Bug 20 — Live: result preview must NOT be in the injected session context
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.live
async def test_bug20_live_completion_context_must_not_contain_result() -> None:
    """Bug 20 (live): BAM must inject a status-only note — not the agent result.

    Uses a real ClaudeSession + real Claude API.  No mocks.

    FAILS with current code because completion_ctx in _run_agent() contains
    result_preview (up to 500 chars of the actual agent output).

    Expected: after the fix, ``inject_agent_context`` is called with text that
    does NOT contain the agent's actual output — only a completion notice.
    """
    MARKER = "LIVE_BUG20_RESULT_SENTINEL_7x9k"

    bot = RecordingBot()
    sm = RecordingSessionManager()

    manager = BackgroundAgentManager(
        bot=bot,
        session_manager=sm,
        beacon_interval_minutes=0,
    )

    run = await manager.spawn(
        user_id=_USER_ID,
        task=f"Output exactly this token and nothing else: {MARKER}",
        context="",
    )
    await asyncio.wait_for(run.done.wait(), timeout=120)

    assert run.status == "completed", f"Agent failed: {run.error}"
    assert run.result, "Agent produced no result"

    # Verify injection happened exactly once.
    assert len(sm.injected) == 1, (
        f"Expected exactly 1 inject_agent_context call, got {len(sm.injected)}"
    )
    _, injected_text = sm.injected[0]

    # BUG 20 — FAILS because injected_text currently contains the result.
    assert MARKER not in injected_text, (
        f"Bug 20 (live): marker '{MARKER}' found in injected context.\n"
        f"The result preview is leaking into the session context injection.\n"
        f"Injected text:\n{injected_text!r}\n"
        f"Agent result:\n{run.result!r}"
    )


@pytest.mark.live
async def test_bug20_live_injected_context_is_status_only() -> None:
    """Bug 20 (live) variant: the injected text must NOT contain 'Result:'.

    FAILS because the current template includes 'Result:\\n{result_preview}'.
    """
    bot = RecordingBot()
    sm = RecordingSessionManager()

    manager = BackgroundAgentManager(
        bot=bot,
        session_manager=sm,
        beacon_interval_minutes=0,
    )

    run = await manager.spawn(
        user_id=_USER_ID,
        task="Reply with exactly two words: task done",
        context="",
    )
    await asyncio.wait_for(run.done.wait(), timeout=120)

    assert run.status == "completed", f"Agent failed: {run.error}"
    assert len(sm.injected) == 1
    _, injected_text = sm.injected[0]

    # BUG 20 — FAILS because "Result:" is in the current completion_ctx template.
    assert "Result:" not in injected_text, (
        f"Bug 20 (live): injected context contains 'Result:' — should be status-only.\n"
        f"Injected text:\n{injected_text!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Bug 21 — Live: parallel agents must not send simultaneous duplicate beacons
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.live
async def test_bug21_live_parallel_agents_send_duplicate_beacons() -> None:
    """Bug 21 (live): two agents in parallel must produce only ONE batched beacon per interval.

    The fix replaced per-agent beacon tasks with a single per-user beacon task that
    batches all running agents into one Telegram message per interval.

    Uses real BackgroundAgentManager + real ClaudeSession (real Claude API).
    No mocks.

    Verifies: every beacon message contains BOTH agent names (proving batching).
    If the old code were in place, each message would contain only one agent name.
    """
    bot = RecordingBot()
    sm = RecordingSessionManager()

    # 1-second beacon interval — fires during a 10+ second task.
    manager = BackgroundAgentManager(
        bot=bot,
        session_manager=sm,
        beacon_interval_minutes=0.016,  # ~1 second
    )

    # Spawn two agents simultaneously (simulating wave 1 with 2 agents).
    run1 = await manager.spawn(
        user_id=_USER_ID,
        task="Silently count to 10 in your head, then reply: agent1 done",
        context="",
    )
    run2 = await manager.spawn(
        user_id=_USER_ID,
        task="Silently count to 10 in your head, then reply: agent2 done",
        context="",
    )

    await asyncio.gather(
        asyncio.wait_for(run1.done.wait(), timeout=120),
        asyncio.wait_for(run2.done.wait(), timeout=120),
    )

    # Filter beacon messages (exclude spawn and completion notifications).
    beacon_texts = [
        t for t in bot.texts()
        if "working" in t.lower()
        or any(w in t.lower() for w in [
            "pondering", "deliberating", "cogitating", "noodling",
            "mulling", "brewing", "percolating", "scheming",
        ])
    ]

    # Fix verified: each interval sends exactly ONE batched message.
    # Multiple intervals may fire during a long run, so len >= 1 is fine.
    # The key proof of the fix: every beacon message contains BOTH agent names.
    assert len(beacon_texts) >= 1, (
        "Expected at least one beacon message to have fired during the run."
    )
    for msg in beacon_texts:
        assert run1.name in msg and run2.name in msg, (
            f"Bug 21 (live): beacon is not batched — contains only one agent.\n"
            f"Expected both '{run1.name}' and '{run2.name}' in:\n{msg!r}\n"
            f"All beacons: {beacon_texts}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Bug 22 — Live: Pipeline RoutingEvent must carry non-empty model name
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.live
@pytest.mark.skipif(_NESTED_SESSION, reason="Cannot start nested Claude sessions (CLAUDECODE env set)")
async def test_bug22_live_routing_event_model_is_populated_for_chat() -> None:
    """Bug 22 (live): RoutingEvent.model must contain the active model name.

    Tests the 'chat' routing path (intent='chat', confidence≥0.8).

    Uses a real Pipeline with real Classifier + Decomposer (real Claude API).
    No mocks.

    FAILS because ClaudeSession._model is None when the model comes from config
    (not passed explicitly), so Decomposer.model → None → RoutingEvent.model = "".

    The history log shows 'Model: ' (empty) for every pipeline routing entry.
    """
    from archon.ai.pipeline import Pipeline

    pipeline = Pipeline(model="claude-sonnet-4-6")
    await pipeline.start()
    try:
        events = [e async for e in pipeline.send("ping")]
    finally:
        await pipeline.stop()

    routing_events = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing_events) >= 1, (
        f"No RoutingEvent found in pipeline output. Events: {events}"
    )

    # BUG 22 — FAILS because RoutingEvent.model is "" (empty string).
    assert routing_events[0].model, (
        f"Bug 22 (live): RoutingEvent.model is empty.\n"
        f"Expected a non-empty model name like 'claude-sonnet-4-6'.\n"
        f"Root cause: Pipeline._routing_event() uses self.model or '' and "
        f"self._decomposer.model returns None (ClaudeSession._model is None "
        f"when model is set via config/SDK default, not passed to ClaudeSession constructor)."
    )


@pytest.mark.live
@pytest.mark.skipif(_NESTED_SESSION, reason="Cannot start nested Claude sessions (CLAUDECODE env set)")
async def test_bug22_live_routing_event_model_is_populated_for_task() -> None:
    """Bug 22 (live): same empty-model bug on the 'task_direct' routing path.

    Sends a clearly task-like prompt to trigger the task routing path.
    FAILS for the same root cause as the chat variant.
    """
    from archon.ai.pipeline import Pipeline

    pipeline = Pipeline(model="claude-sonnet-4-6")
    await pipeline.start()
    try:
        events = [e async for e in pipeline.send("List the files in the current directory")]
    finally:
        await pipeline.stop()

    routing_events = [e for e in events if isinstance(e, RoutingEvent)]
    assert len(routing_events) >= 1, (
        f"No RoutingEvent found in pipeline output. Events: {events}"
    )

    # BUG 22 — FAILS because RoutingEvent.model is "" (empty string).
    assert routing_events[0].model, (
        f"Bug 22 (live): RoutingEvent.model is empty on task_direct path.\n"
        f"RoutingEvent: {routing_events[0]}"
    )
