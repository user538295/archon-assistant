"""E2E tests for Bugs 20, 21, 22.

Bug 20 — Regression guards: BAM must not inject result preview into session context (fixed).
Bug 21 — Beacon Double-Send: two parallel agents send beacons simultaneously (xfail, unfixed).
Bug 22 — Regression guards: Pipeline RoutingEvent must carry a non-empty model string (fixed).
"""
import asyncio
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.background_agent_manager import BackgroundAgentManager, _AGENT_BEACON_WORDS
from archon.ai.event_mapper import Response, RoutingEvent, ToolStarted, ToolResult
from archon.ai.pipeline import Pipeline


# ── Helpers ───────────────────────────────────────────────────────────────────


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _sm() -> MagicMock:
    sm = MagicMock()
    sm.track_context = MagicMock()
    sm.inject_agent_context = MagicMock()
    sm.get_or_create = AsyncMock(return_value=MagicMock())
    return sm


def _instant_session(result: str = "agent output content") -> MagicMock:
    """Mock ClaudeSession that immediately yields a Response."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.inject_context = MagicMock()

    async def _send(prompt: str):  # type: ignore[return]
        yield Response(content=result)

    session.send = _send
    return session


def _slow_session(delay: float = 0.3, result: str = "slow output") -> MagicMock:
    """Mock ClaudeSession that sleeps *delay* seconds before yielding a Response."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.inject_context = MagicMock()

    async def _send(prompt: str):  # type: ignore[return]
        await asyncio.sleep(delay)
        yield Response(content=result)

    session.send = _send
    return session


async def _spawn_and_wait(manager: BackgroundAgentManager, **kwargs) -> "AgentRun":  # type: ignore[name-defined]
    from archon.ai.background_agent_manager import AgentRun
    run = await manager.spawn(**kwargs)
    if run._task_ref:
        await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=10.0)
    return run


# ══════════════════════════════════════════════════════════════════════════════
# Bug 20 — Message Shifting: result preview must NOT appear in injected context
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bug20_completion_context_must_not_contain_result_preview() -> None:
    """Regression guard: ensures the fix from Bug 20 is not re-introduced.

    Bug 20 was: BAM.inject_agent_context() leaked the agent result into the
    session context. The fix changed completion_ctx to a status-only note.

    Verifies that the distinctive result marker does NOT appear in the injected
    context text, and that the task content does not leak either.
    """
    DISTINCTIVE_RESULT = "UNIQUE_RESULT_MARKER_XYZZY_12345"
    bot = _bot()
    sm = _sm()
    session = _instant_session(result=DISTINCTIVE_RESULT)

    DISTINCTIVE_TASK = "UNIQUE_TASK_MARKER_BUG20_54321"

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm, beacon_interval_minutes=0)
        run = await _spawn_and_wait(manager, user_id=1, task=DISTINCTIVE_TASK, context="")

    assert run.status == "completed"
    sm.inject_agent_context.assert_called_once()
    injected: str = sm.inject_agent_context.call_args[0][1]

    # Regression guard: result must never appear in injected context.
    assert DISTINCTIVE_RESULT not in injected, (
        f"Bug 20: result preview leaked into session context.\n"
        f"Found '{DISTINCTIVE_RESULT}' in injected text:\n{injected!r}"
    )
    # Task content must NOT leak into injected context either.
    assert run.task not in injected, (
        f"Bug 20: run.task '{run.task}' leaked into injected context:\n{injected!r}"
    )
    assert run.name in injected, (
        f"Bug 20 regression: agent name '{run.name}' must appear in injected context.\n"
        f"Injected text:\n{injected!r}"
    )
    assert "BACKGROUND STATUS" in injected, (
        f"Bug 20 regression: '[BACKGROUND STATUS' framing must be in injected context.\n"
        f"Injected text:\n{injected!r}"
    )


@pytest.mark.asyncio
async def test_bug20_injected_context_must_be_status_only() -> None:
    """Regression guard: ensures the fix from Bug 20 is not re-introduced.

    Bug 20 was: the completion_ctx template included 'Result:' with a preview
    of the agent output. The fix changed it to a status-only note.

    Verifies that 'Result:' does NOT appear in the injected context text,
    and that the task content does not leak either.
    """
    bot = _bot()
    sm = _sm()
    session = _instant_session(result="some result content")

    DISTINCTIVE_TASK = "UNIQUE_TASK_MARKER_BUG20B_67890"

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm, beacon_interval_minutes=0)
        run = await _spawn_and_wait(manager, user_id=1, task=DISTINCTIVE_TASK, context="")

    assert run.status == "completed"
    sm.inject_agent_context.assert_called_once()
    injected: str = sm.inject_agent_context.call_args[0][1]

    # Regression guard: 'Result:' must never appear in injected context.
    assert "Result:" not in injected, (
        f"Bug 20: injected context contains 'Result:' — should be status-only.\n"
        f"Injected text:\n{injected!r}"
    )
    # Task content must NOT leak into injected context either.
    assert run.task not in injected, (
        f"Bug 20: run.task '{run.task}' leaked into injected context:\n{injected!r}"
    )
    assert run.name in injected, (
        f"Bug 20 regression: agent name '{run.name}' must appear in injected context.\n"
        f"Injected text:\n{injected!r}"
    )
    assert "BACKGROUND STATUS" in injected, (
        f"Bug 20 regression: '[BACKGROUND STATUS' framing must be in injected context.\n"
        f"Injected text:\n{injected!r}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Bug 21 — Beacon Double-Send: parallel agents send beacons close together
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="Bug 21 not yet fixed — per-agent beacons send separate messages when multiple agents run simultaneously")
async def test_bug21_parallel_agents_each_send_own_beacon() -> None:
    """Bug 21: when 2 agents run in the same wave, each fires its own beacon per interval.

    The user receives 2 beacon messages per interval instead of 1, making them
    appear as duplicates back-to-back.

    FAILS because with 2 parallel agents and beacon_interval_minutes>0, there will
    always be MORE than 1 beacon message (one per agent per interval).

    Expected fix: batch beacons from multiple agents into a single Telegram message
    per interval, or suppress per-agent beacons when multiple agents share a wave.
    """
    bot = _bot()
    sm = _sm()

    # Beacon fires after 0.05s; agents sleep 0.2s → beacon fires once per agent.
    session_factory_calls = []

    def _make_slow(**kwargs):  # noqa: ARG001
        s = _slow_session(delay=0.2)
        session_factory_calls.append(s)
        return s

    with patch(
        "archon.ai.background_agent_manager.ClaudeSession",
        side_effect=_make_slow,
    ):
        manager = BackgroundAgentManager(
            bot=bot, session_manager=sm, beacon_interval_minutes=0.001  # ~60ms
        )
        run1 = await manager.spawn(user_id=1, task="agent one task", context="")
        run2 = await manager.spawn(user_id=1, task="agent two task", context="")

        await asyncio.gather(
            asyncio.wait_for(asyncio.shield(run1._task_ref), timeout=10.0),
            asyncio.wait_for(asyncio.shield(run2._task_ref), timeout=10.0),
        )

    # Collect only beacon messages (not spawn, not completion).
    all_msgs = [call[0][1] for call in bot.send_message.call_args_list]
    beacon_msgs = [
        m for m in all_msgs
        if "working" in m.lower()
        or any(w in m.lower() for w in _AGENT_BEACON_WORDS)
    ]

    # After fix: each interval produces exactly 1 batched message (not 1 per agent).
    # With 2 agents, the old buggy code produced 2 messages per interval.
    # With the fix, each beacon message must mention BOTH agents (batched).
    assert len(beacon_msgs) >= 1, "Expected at least 1 beacon message"
    for msg in beacon_msgs:
        # Each beacon message must contain BOTH agent names — proving it is batched.
        assert run1.name in msg and run2.name in msg, (
            f"Bug 21: beacon message is not batched — it only covers one agent.\n"
            f"Expected both '{run1.name}' and '{run2.name}' in:\n{msg!r}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Bug 22 — Empty Model Field in Pipeline RoutingEvent
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_bug22_routing_event_model_must_not_be_empty_for_chat() -> None:
    """Bug 22 regression guard: RoutingEvent.model must carry the active model name.

    Reproduced for 'chat' routing path.

    Root cause: config.models.default was None when the [models] section omitted
    the ``default`` key. Fix: config-level fallback in loader.py (models.default
    falls back to available[0]). Pipeline.model delegates to Decomposer.model,
    and _routing_event() populates RoutingEvent.model from self.model.

    This test verifies the propagation chain: Decomposer.model → Pipeline.model
    → RoutingEvent.model is never empty.
    """
    from archon.ai.classification import Classification
    from archon.ai.classifier import ClassifierResult

    EXPECTED_MODEL = "claude-sonnet-4-6"

    mock_classifier = MagicMock()
    mock_classifier.start = AsyncMock()
    mock_classifier.stop = AsyncMock()
    mock_classifier.model = "claude-haiku-4-5-20251001"
    mock_classifier.usage_stats = None
    mock_classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent="chat", confidence=0.99),
        raw_response='{"intent":"chat","confidence":0.99}',
        duration_s=0.05,
        parse_error="",
        error="",
    ))

    mock_decomposer = MagicMock()
    mock_decomposer.start = AsyncMock()
    mock_decomposer.stop = AsyncMock()
    mock_decomposer.is_processing = False
    mock_decomposer.processing_seconds = None
    mock_decomposer.idle_seconds = 5.0
    mock_decomposer.send_count = 0
    mock_decomposer.usage_stats = None
    mock_decomposer.diagnostics = {"is_alive": True}
    mock_decomposer.is_alive = True
    mock_decomposer.flush_pending_context = MagicMock()
    mock_decomposer.recent_events = MagicMock(return_value=[])
    mock_decomposer.track_context = MagicMock()
    mock_decomposer.inject_context = MagicMock()
    mock_decomposer.activate_skill = MagicMock()
    mock_decomposer.context_summary = ""
    mock_decomposer.reminder = None
    # Model propagates correctly when config.models.default is set
    mock_decomposer.model = EXPECTED_MODEL

    async def _answer(prompt: str):
        yield Response(content="Pong.")

    mock_decomposer.answer = _answer

    with patch("archon.ai.pipeline.Classifier", return_value=mock_classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=mock_decomposer):
            pipeline = Pipeline(model=EXPECTED_MODEL)

    events = [e async for e in pipeline.send("Ping")]
    routing_events = [e for e in events if isinstance(e, RoutingEvent)]

    assert len(routing_events) == 1, f"Expected 1 RoutingEvent, got: {routing_events}"

    assert routing_events[0].model == EXPECTED_MODEL, (
        f"Bug 22: RoutingEvent.model='{routing_events[0].model}' "
        f"but expected '{EXPECTED_MODEL}'."
    )


@pytest.mark.asyncio
async def test_bug22_routing_event_model_must_not_be_empty_for_task() -> None:
    """Bug 22 regression guard: same model propagation check on the 'task_direct' routing path.

    Root cause and fix identical to the chat variant — config.models.default fallback
    in loader.py ensures Decomposer.model is never None.

    Verifies: Decomposer.model → Pipeline.model → RoutingEvent.model on task path.
    """
    import json
    from archon.ai.classification import Classification
    from archon.ai.classifier import ClassifierResult
    from archon.ai.decomposer import TaskOutput

    EXPECTED_MODEL = "claude-sonnet-4-6"

    mock_classifier = MagicMock()
    mock_classifier.start = AsyncMock()
    mock_classifier.stop = AsyncMock()
    mock_classifier.model = "claude-haiku-4-5-20251001"
    mock_classifier.usage_stats = None
    mock_classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent="task", confidence=0.95),
        raw_response=json.dumps({"intent": "task", "confidence": 0.95}),
        duration_s=0.05,
        parse_error="",
        error="",
    ))

    mock_decomposer = MagicMock()
    mock_decomposer.start = AsyncMock()
    mock_decomposer.stop = AsyncMock()
    mock_decomposer.is_processing = False
    mock_decomposer.processing_seconds = None
    mock_decomposer.idle_seconds = 5.0
    mock_decomposer.send_count = 0
    mock_decomposer.usage_stats = None
    mock_decomposer.diagnostics = {"is_alive": True}
    mock_decomposer.is_alive = True
    mock_decomposer.flush_pending_context = MagicMock()
    mock_decomposer.recent_events = MagicMock(return_value=[])
    mock_decomposer.track_context = MagicMock()
    mock_decomposer.inject_context = MagicMock()
    mock_decomposer.activate_skill = MagicMock()
    mock_decomposer.context_summary = ""
    mock_decomposer.reminder = None
    # Model propagates correctly when config.models.default is set
    mock_decomposer.model = EXPECTED_MODEL

    async def _answer(prompt: str):
        yield Response(content="Done.")

    mock_decomposer.answer = _answer
    mock_decomposer.route_task = AsyncMock(
        return_value=TaskOutput(scope="trivial", summary="Quick task", prompt="Do it")
    )

    with patch("archon.ai.pipeline.Classifier", return_value=mock_classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=mock_decomposer):
            pipeline = Pipeline(model=EXPECTED_MODEL)

    events = [e async for e in pipeline.send("Do something")]
    routing_events = [e for e in events if isinstance(e, RoutingEvent)]

    assert len(routing_events) == 1, f"Expected 1 RoutingEvent, got: {routing_events}"

    assert routing_events[0].model == EXPECTED_MODEL, (
        f"Bug 22: RoutingEvent.model='{routing_events[0].model}' "
        f"but expected '{EXPECTED_MODEL}' on task_direct path."
    )


@pytest.mark.asyncio
async def test_bug22_routing_event_model_non_empty_when_no_models_section() -> None:
    """Bug 22: when config.toml has no [models] section at all, Pipeline.model is None.

    _routing_event() must still produce a non-empty RoutingEvent.model — it should
    fall back to "(sdk-default)" instead of "".
    """
    from archon.ai.classification import Classification
    from archon.ai.classifier import ClassifierResult

    mock_classifier = MagicMock()
    mock_classifier.start = AsyncMock()
    mock_classifier.stop = AsyncMock()
    mock_classifier.model = "claude-haiku-4-5-20251001"
    mock_classifier.usage_stats = None
    mock_classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent="chat", confidence=0.99),
        raw_response='{"intent":"chat","confidence":0.99}',
        duration_s=0.05,
        parse_error="",
        error="",
    ))

    mock_decomposer = MagicMock()
    mock_decomposer.start = AsyncMock()
    mock_decomposer.stop = AsyncMock()
    mock_decomposer.is_processing = False
    mock_decomposer.processing_seconds = None
    mock_decomposer.idle_seconds = 5.0
    mock_decomposer.send_count = 0
    mock_decomposer.usage_stats = None
    mock_decomposer.diagnostics = {"is_alive": True}
    mock_decomposer.is_alive = True
    mock_decomposer.flush_pending_context = MagicMock()
    mock_decomposer.recent_events = MagicMock(return_value=[])
    mock_decomposer.track_context = MagicMock()
    mock_decomposer.inject_context = MagicMock()
    mock_decomposer.activate_skill = MagicMock()
    mock_decomposer.context_summary = ""
    mock_decomposer.reminder = None
    # No [models] section → model is None
    mock_decomposer.model = None

    async def _answer(prompt: str):
        yield Response(content="Pong.")

    mock_decomposer.answer = _answer

    with patch("archon.ai.pipeline.Classifier", return_value=mock_classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=mock_decomposer):
            pipeline = Pipeline(model=None)

    events = [e async for e in pipeline.send("Ping")]
    routing_events = [e for e in events if isinstance(e, RoutingEvent)]

    assert len(routing_events) == 1, f"Expected 1 RoutingEvent, got: {routing_events}"

    assert routing_events[0].model != "", (
        "Bug 22: RoutingEvent.model must not be empty when no [models] section exists."
    )
    assert routing_events[0].model == "(sdk-default)", (
        f"Bug 22: expected '(sdk-default)' fallback, got '{routing_events[0].model}'."
    )
