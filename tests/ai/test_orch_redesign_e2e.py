"""E2E tests for the orchestration session redesign.

Regression tests for the bug where agents scanned the entire filesystem
when `_orch_session` had no history context (e.g. "rewrite the script from yesterday").

The fix:
1. `_orch_session` receives history context via `context_provider` at session start.
2. `_orch_session` has ArchonOrchestratorMCPServer tools.
3. Pipeline routing: `chat + confidence >= 0.8` → direct answer; everything else → route_task.
4. Dual-prompt format: when orch enriches the prompt, the agent task gets
   "[Original user request]: {orig}\n[Resolved context]: {resolved}".
"""

from __future__ import annotations

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.agent_plan import AgentTask
from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import (
    ClassificationEvent,
    PlanEvent,
    Response,
    RoutingEvent,
)
from archon.ai.session_manager import SessionManager


# ──────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing=False):
    """Build a mock ClaudeSession that yields given events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = is_processing
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.diagnostics = {"is_alive": True}
    session.model = "claude-sonnet-4-6"
    session.is_alive = True
    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.flush_pending_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    return session


def _make_decomposer(session_events=None, orch_events=None, summary_events=None, **kwargs):
    """Build a Decomposer with mocked main, orchestration, and summary sessions.

    Returns (decomposer, main_session, orch_session, summary_session).
    """
    from archon.ai.decomposer import Decomposer

    if session_events is None:
        session_events = [Response(content="Done.")]
    if orch_events is None:
        orch_events = [Response(content='{"scope":"small","summary":"Done","prompt":"Do the thing"}')]
    if summary_events is None:
        summary_events = [Response(content="User discussed topic X.")]

    main_session = _mock_session(*session_events)
    orch_session = _mock_session(*orch_events)
    summary_session = _mock_session(*summary_events)

    with patch(
        "archon.ai.decomposer.ClaudeSession",
        side_effect=[main_session, orch_session, summary_session],
    ):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    return decomposer, main_session, orch_session, summary_session


def _mock_classifier(intent="task", confidence=0.9, error="", parse_error="", raw=None):
    """Build a mock Classifier that returns a fixed ClassifierResult."""
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    if raw is None:
        raw = json.dumps({"intent": intent, "confidence": confidence})
    classifier.classify = AsyncMock(return_value=ClassifierResult(
        classification=Classification(intent=intent, confidence=confidence),
        raw_response=raw,
        duration_s=0.1,
        parse_error=parse_error,
        error=error,
    ))
    return classifier


def _mock_decomposer(
    answer_events=None,
    route_task_result=None,
    model="claude-sonnet-4-6",
):
    """Build a mock Decomposer."""
    decomposer = MagicMock()
    decomposer.start = AsyncMock()
    decomposer.stop = AsyncMock()
    decomposer.is_processing = False
    decomposer.processing_seconds = None
    decomposer.idle_seconds = 5.0
    decomposer.send_count = 0
    decomposer.usage_stats = None
    decomposer.diagnostics = {"is_alive": True}
    decomposer.model = model
    decomposer.is_alive = True
    decomposer.flush_pending_context = MagicMock()
    decomposer.reminder = None
    decomposer.context_summary = ""

    if answer_events is None:
        answer_events = [Response(content="Done.")]

    async def _answer(prompt: str) -> AsyncGenerator:
        for event in answer_events:
            yield event

    decomposer.answer = _answer
    decomposer.route_task = AsyncMock(return_value=route_task_result or TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing",
    ))
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.track_context = MagicMock()
    return decomposer


# ──────────────────────────────────────────────────────────────────
# Group 1: Pipeline-level e2e (mock Classifier + Decomposer)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_script_rewrite_routes_to_route_task() -> None:
    """'rewrite the script from yesterday' (task, 0.9) → route_task with dual-prompt format."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    resolved_prompt = "Rewrite /Users/manczg/projects/collect_bins.sh in Python and write tests"
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="small",
            summary="Rewrite bin collection script",
            prompt=resolved_prompt,
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("rewrite the script from yesterday")]

    # route_task was called with the original user prompt
    decomposer.route_task.assert_awaited_once_with("rewrite the script from yesterday")

    # A PlanEvent must be yielded
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1, f"Expected 1 PlanEvent, got {len(plans)}"

    agent_task = plans[0].plan.agents[0]

    # Dual-prompt format applied because resolved != original
    assert agent_task.task.startswith("[Original user request]:")
    assert "[Resolved context]:" in agent_task.task
    assert "/Users/manczg/projects/collect_bins.sh" in agent_task.task

    # answer() was NOT called (it's a task, not a high-confidence chat)
    # answer() is called via _task_direct_monitored which uses decomposer.answer
    # We can verify by checking that route_task was called (routing took the task branch)
    decomposer.route_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_script_rewrite_no_filesystem_scan() -> None:
    """Agent task for a script rewrite must be enriched (not equal to the original prompt)."""
    classifier = _mock_classifier(intent="task", confidence=0.9)
    original_prompt = "rewrite the script from yesterday"
    resolved_prompt = "Rewrite /Users/manczg/projects/collect_bins.sh in Python and write tests"
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="small",
            summary="Rewrite bin collection script",
            prompt=resolved_prompt,
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send(original_prompt)]

    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1
    agent_task_text = plans[0].plan.agents[0].task

    # The agent task was enriched — it differs from the original vague request
    assert agent_task_text != original_prompt

    # Resolved file path is present (enrichment added specifics)
    assert "/Users/manczg/projects/collect_bins.sh" in agent_task_text


@pytest.mark.asyncio
async def test_chat_high_confidence_does_not_call_route_task() -> None:
    """'what time is it?' (chat, 0.95) → direct answer, route_task NOT called."""
    classifier = _mock_classifier(intent="chat", confidence=0.95)
    decomposer = _mock_decomposer(
        answer_events=[Response(content="It depends on your timezone!")],
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("what time is it?")]

    # route_task was NOT called
    decomposer.route_task.assert_not_awaited()

    # answer() was used — there should be a Response event
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "timezone" in responses[0].content


@pytest.mark.asyncio
async def test_low_confidence_chat_routes_to_route_task() -> None:
    """'make that thing from last week' (chat, 0.6) → ambiguous → route_task called."""
    classifier = _mock_classifier(intent="chat", confidence=0.6)
    decomposer = _mock_decomposer(
        route_task_result=TaskOutput(
            scope="small",
            summary="Handle ambiguous request",
            prompt="make that thing from last week",
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("make that thing from last week")]

    # route_task WAS called (chat below threshold → orch decides)
    decomposer.route_task.assert_awaited_once()

    # A PlanEvent is yielded
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1


# ──────────────────────────────────────────────────────────────────
# Group 2: Decomposer-level e2e (mock ClaudeSession, real Decomposer logic)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_orch_session_receives_history_context_at_start() -> None:
    """Decomposer.start() injects history context into _orch_session."""
    history_content = "On 2026-03-08, user created /Users/manczg/projects/collect_bins.sh"

    mock_context_provider = MagicMock()
    mock_context_provider.startup_context_prompt = MagicMock(
        return_value="# History Structure\n## Sessions"
    )
    mock_context_provider.get_recent_context = MagicMock(return_value=history_content)

    decomposer, main_session, orch_session, summary_session = _make_decomposer(
        context_provider=mock_context_provider,
    )

    await decomposer.start()

    # inject_context was called on the orch session
    assert orch_session.inject_context.called, "_orch_session.inject_context was never called"

    # The injected text contains the history content with the file path
    all_injected = " ".join(
        str(call.args[0]) for call in orch_session.inject_context.call_args_list
    )
    assert "collect_bins.sh" in all_injected, (
        f"Expected 'collect_bins.sh' in injected context, got: {all_injected!r}"
    )


@pytest.mark.asyncio
async def test_route_task_uses_orch_enriched_prompt_for_dual_format() -> None:
    """Decomposer.route_task() returns the orch-resolved prompt in TaskOutput."""
    resolved = "Rewrite /Users/manczg/projects/collect_bins.sh in Python. Write tests in tests/."
    orch_response = json.dumps({
        "scope": "small",
        "summary": "Rewrite bin collection script",
        "prompt": resolved,
    })
    decomposer, _, orch_session, _ = _make_decomposer(
        orch_events=[Response(content=orch_response)],
    )

    result = await decomposer.route_task("rewrite the script from yesterday")

    assert result.scope == "small"
    assert result.prompt == resolved
    assert result.prompt != "rewrite the script from yesterday"


@pytest.mark.asyncio
async def test_route_task_original_prompt_unchanged_in_task_output() -> None:
    """The resolved prompt in TaskOutput differs from the original user request."""
    resolved = "Rewrite /Users/manczg/projects/collect_bins.sh in Python. Write tests in tests/."
    orch_response = json.dumps({
        "scope": "small",
        "summary": "Rewrite bin collection script",
        "prompt": resolved,
    })
    decomposer, _, _, _ = _make_decomposer(
        orch_events=[Response(content=orch_response)],
    )

    original_prompt = "rewrite the script from yesterday"
    result = await decomposer.route_task(original_prompt)

    # TaskOutput does not have an original_prompt field — verify via the prompt field
    assert result.prompt == resolved, (
        f"Expected resolved path in prompt, got: {result.prompt!r}"
    )
    # The resolved prompt contains the file path, not the vague original request
    assert "/Users/manczg/projects/collect_bins.sh" in result.prompt
    assert "yesterday" not in result.prompt


@pytest.mark.asyncio
async def test_orch_continues_working_after_reset() -> None:
    """Orch session is restarted + context re-injected at _ORCH_RESET_THRESHOLD, result still valid."""
    from archon.ai.decomposer import _ORCH_RESET_THRESHOLD

    history_content = "On 2026-03-08, user created /Users/manczg/projects/collect_bins.sh"
    mock_context_provider = MagicMock()
    mock_context_provider.startup_context_prompt = MagicMock(return_value="# History")
    mock_context_provider.get_recent_context = MagicMock(return_value=history_content)

    valid_json = json.dumps({
        "scope": "small",
        "summary": "Do a task",
        "prompt": "Perform the task",
    })

    # Each route_task call consumes one orch_session.send() — supply enough responses.
    # The orch session is reset at threshold, so stop/start are called; inject_context is
    # called again. We need the session to keep working after the reset.
    orch_responses = [Response(content=valid_json)] * (_ORCH_RESET_THRESHOLD + 1)
    decomposer, _, orch_session, _ = _make_decomposer(
        orch_events=orch_responses,
        context_provider=mock_context_provider,
    )

    await decomposer.start()

    # Record inject_context call count after start (initial context injection)
    inject_calls_after_start = orch_session.inject_context.call_count

    # Call route_task exactly _ORCH_RESET_THRESHOLD times — reset fires on this call
    for _ in range(_ORCH_RESET_THRESHOLD):
        result = await decomposer.route_task("do a task")
        assert result.scope == "small"

    # The reset fires on the _ORCH_RESET_THRESHOLD-th call: stop + start + inject_context
    assert orch_session.stop.call_count >= 1, "orch_session.stop should have been called on reset"
    assert orch_session.start.call_count >= 2, "orch_session.start should have been called on reset"

    # inject_context called again after reset (context re-injection)
    assert orch_session.inject_context.call_count > inject_calls_after_start, (
        "context_provider.get_recent_context should have been re-injected after orch reset"
    )

    # One more call after reset — still returns valid TaskOutput
    result = await decomposer.route_task("do another task")
    assert result.scope == "small"
    assert result.prompt == "Perform the task"


# ──────────────────────────────────────────────────────────────────
# Group 3: Full stack integration (real Decomposer + mocked ClaudeSession)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_stack_real_decomposer_script_rewrite() -> None:
    """Headline regression test: exercises real Decomposer._parse_task_output + Pipeline._yield_plan.

    Uses real Decomposer (not a mock), with only ClaudeSession mocked.
    Verifies the full chain: Pipeline.send → route_task → _parse_task_output → _yield_plan.
    """
    orch_json = json.dumps({
        "scope": "small",
        "summary": "Rewrite bin collection script",
        "prompt": (
            "Rewrite /Users/manczg/projects/collect_bins.sh in Python. "
            "Write tests in tests/test_collect_bins.py."
        ),
    })
    classifier = _mock_classifier(intent="task", confidence=0.9)
    main_session = _mock_session(Response(content="Done."))
    orch_session = _mock_session(Response(content=orch_json))
    summary_session = _mock_session(Response(content="User discussed a script rewrite."))

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch(
            "archon.ai.decomposer.ClaudeSession",
            side_effect=[main_session, orch_session, summary_session],
        ):
            with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
                mgr = SessionManager(timeout=60)
                session = await mgr.get_or_create(user_id=42)
                events = [e async for e in session.send("rewrite the script from yesterday")]

    # PlanEvent was yielded
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 1, f"Expected 1 PlanEvent, got {len(plans)}"

    agent_task = plans[0].plan.agents[0]

    # Dual-prompt format applied by real _yield_plan (resolved != original)
    assert "[Original user request]: rewrite the script from yesterday" in agent_task.task
    assert "[Resolved context]:" in agent_task.task
    assert "/Users/manczg/projects/collect_bins.sh" in agent_task.task

    # No filesystem scan commands in the resolved task
    assert "find /" not in agent_task.task
    assert "find ~" not in agent_task.task
    assert "-maxdepth" not in agent_task.task
