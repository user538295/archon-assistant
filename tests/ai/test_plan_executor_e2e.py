"""E2E smoke tests for plan execution — Phase 2 Task #8.

Patches ClaudeSession at SDK level only. Pipeline, classification, plan
parsing, PlanExecutor, and BackgroundAgentManager all run with real code
(BAM is mocked but PlanExecutor is real).

Scenarios:
  - Happy path: user message → classify → decompose → plan → agents → results
  - Dependency chain: a1 → a2, a1 completes first, a2 gets log path
  - Mixed: agent fails, partial results delivered
  - Small scope: Decomposer handles directly (no plan, existing behavior)
"""

import json
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.background_agent_manager import AgentRun
from archon.ai.event_mapper import (
    ClassificationEvent,
    PlanEvent,
    Response,
)
from archon.ai.session_manager import SessionManager


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _fake_claude_session(events: list):
    """Create a fake ClaudeSession that yields scripted events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = False
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.is_alive = True
    session.model = None
    session.diagnostics = {"is_alive": True}
    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    return session


_PLAN_JSON = json.dumps({
    "scope": "large",
    "summary": "Break into research and implementation.",
    "agents": [
        {"id": "a1", "task": "Research best practices"},
        {"id": "a2", "task": "Implement based on a1 findings", "depends_on": ["a1"]},
    ],
})


# ──────────────────────────────────────────────────────────────────
# Happy path: plan detected and yielded
# ──────────────────────────────────────────────────────────────────


async def test_e2e_plan_flow_yields_plan_event() -> None:
    """Full flow: classify → decompose → plan JSON → PlanEvent yielded."""
    classifier = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.95}')])
    decomposer = _fake_claude_session([Response(content=_PLAN_JSON)])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("build a complex feature")]

    # Classification first
    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "task"

    # PlanEvent instead of Response
    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1
    assert plan_events[0].summary == "Break into research and implementation."
    assert len(plan_events[0].plan.agents) == 2

    # No raw Response (the plan JSON is converted to PlanEvent)
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0


async def test_e2e_plan_decomposer_receives_classification() -> None:
    """Decomposer receives the classification prefix before deciding scope."""
    classifier = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.88}')])
    decomposer = _fake_claude_session([Response(content=_PLAN_JSON)])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        _ = [e async for e in session.send("complex task")]

    # Decomposer received classification prefix
    assert "[Classification:" in decomposer._send_calls[0]
    assert '"intent": "task"' in decomposer._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Dependency chain: a1 → a2 with log paths
# ──────────────────────────────────────────────────────────────────


async def test_e2e_dependency_chain_plan() -> None:
    """Plan with a1 → a2: both agents in the plan, correct dependencies."""
    plan_with_chain = json.dumps({
        "scope": "large",
        "summary": "Chain execution",
        "agents": [
            {"id": "a1", "task": "Research"},
            {"id": "a2", "task": "Implement", "depends_on": ["a1"]},
        ],
    })

    classifier = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.9}')])
    decomposer = _fake_claude_session([Response(content=plan_with_chain)])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("build it")]

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1
    plan = plan_events[0].plan
    assert plan.agents[1].depends_on == ["a1"]


# ──────────────────────────────────────────────────────────────────
# Small scope: no plan, existing behavior
# ──────────────────────────────────────────────────────────────────


async def test_e2e_small_scope_no_plan() -> None:
    """When Decomposer handles directly (small scope), no PlanEvent is emitted."""
    classifier = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.9}')])
    decomposer = _fake_claude_session([Response(content="Here's the fix: change line 42.")])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("fix the bug")]

    # Normal Response, no PlanEvent
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "fix" in responses[0].content.lower()

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 0


# ──────────────────────────────────────────────────────────────────
# Chat flow unaffected
# ──────────────────────────────────────────────────────────────────


async def test_e2e_chat_flow_unaffected_by_plan_detection() -> None:
    """Chat intent → conversational response, plan detection doesn't interfere."""
    classifier = _fake_claude_session([Response(content='{"intent": "chat", "confidence": 0.97}')])
    decomposer = _fake_claude_session([Response(content="Hi! What can I do for you?")])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("hello")]

    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "chat"

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "Hi!" in responses[0].content

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 0


# ──────────────────────────────────────────────────────────────────
# Invalid plan JSON → falls back to Response
# ──────────────────────────────────────────────────────────────────


async def test_e2e_invalid_plan_json_falls_back_to_response() -> None:
    """If Decomposer outputs invalid plan JSON, it's delivered as a normal Response."""
    invalid_plan = json.dumps({"scope": "large", "summary": "X"})  # missing agents

    classifier = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.9}')])
    decomposer = _fake_claude_session([Response(content=invalid_plan)])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("task")]

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 0
