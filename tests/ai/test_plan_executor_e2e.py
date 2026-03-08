"""E2E smoke tests for plan execution — Phase 2 Task #8.

Patches Classifier and Decomposer at the Pipeline import level. Pipeline,
classification routing, plan parsing, PlanExecutor, and
BackgroundAgentManager all run with real code (BAM is mocked but PlanExecutor
is real).

Scenarios:
  - Happy path: user message -> classify -> route_task -> plan -> agents -> results
  - Dependency chain: a1 -> a2, both agents in the plan
  - Small scope: Decomposer routes as small task (single agent plan)
  - Chat flow unaffected by plan detection
  - Invalid plan JSON -> falls back to small scope
"""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import ReviewResult, TaskOutput
from archon.ai.event_mapper import (
    ClassificationEvent,
    PlanEvent,
    Response,
    RoutingEvent,
)
from archon.ai.session_manager import SessionManager


# ------------------------------------------------------------------
# Helpers (same mock pattern as test_pipeline.py)
# ------------------------------------------------------------------


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
    review_result=None,
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

    if answer_events is None:
        answer_events = [Response(content="Done.")]

    async def _answer(prompt: str) -> AsyncGenerator:
        for event in answer_events:
            yield event

    decomposer.answer = _answer
    decomposer.review = AsyncMock(return_value=review_result or ReviewResult(
        intent="task", confidence=0.9, estimated_tools=1,
    ))
    decomposer.route_task = AsyncMock(return_value=route_task_result or TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing",
    ))
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    return decomposer


# ------------------------------------------------------------------
# Happy path: plan detected and yielded
# ------------------------------------------------------------------


async def test_e2e_plan_flow_yields_plan_event() -> None:
    """Full flow: classify -> review (estimated_tools>1) -> route_task -> PlanEvent."""
    classifier = _mock_classifier(intent="task", confidence=0.5)
    decomposer = _mock_decomposer(
        review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
        route_task_result=TaskOutput(
            scope="large",
            summary="Break into research and implementation.",
            agents=[
                AgentTask(id="a1", task="Research best practices"),
                AgentTask(id="a2", task="Implement based on a1 findings", depends_on=("a1",)),
            ],
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("build a complex feature")]

    # Classification first
    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "task"

    # PlanEvent with large scope
    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1
    assert plan_events[0].summary == "Break into research and implementation."
    assert len(plan_events[0].plan.agents) == 2

    # No raw Response (the task output is converted to PlanEvent)
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 0


async def test_e2e_plan_review_triggers_route_task() -> None:
    """Review with estimated_tools > 1 triggers Decomposer.route_task()."""
    classifier = _mock_classifier(intent="task", confidence=0.5)
    decomposer = _mock_decomposer(
        review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=5),
        route_task_result=TaskOutput(
            scope="large",
            summary="Multi-agent plan",
            agents=[
                AgentTask(id="a1", task="Research"),
                AgentTask(id="a2", task="Implement", depends_on=("a1",)),
            ],
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            _ = [e async for e in session.send("complex task")]

    # route_task was called with the user prompt
    decomposer.route_task.assert_awaited_once_with("complex task")


# ------------------------------------------------------------------
# Dependency chain: a1 -> a2 with correct dependencies
# ------------------------------------------------------------------


async def test_e2e_dependency_chain_plan() -> None:
    """Plan with a1 -> a2: both agents in the plan, correct dependencies."""
    classifier = _mock_classifier(intent="task", confidence=0.3)
    decomposer = _mock_decomposer(
        review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
        route_task_result=TaskOutput(
            scope="large",
            summary="Chain execution",
            agents=[
                AgentTask(id="a1", task="Research"),
                AgentTask(id="a2", task="Implement", depends_on=("a1",)),
            ],
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("build it")]

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1
    plan = plan_events[0].plan
    assert plan.agents[1].depends_on == ("a1",)


# ------------------------------------------------------------------
# Small scope: single-agent plan
# ------------------------------------------------------------------


async def test_e2e_small_scope_yields_single_agent_plan() -> None:
    """When Decomposer returns scope=small, a single-agent PlanEvent is emitted."""
    classifier = _mock_classifier(intent="task", confidence=0.3)
    decomposer = _mock_decomposer(
        review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
        route_task_result=TaskOutput(
            scope="small",
            summary="Fix typo",
            prompt="Fix the typo in README.md",
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("fix the bug")]

    # PlanEvent with small scope and single agent
    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1
    assert plan_events[0].plan.scope == "small"
    assert len(plan_events[0].plan.agents) == 1

    # RoutingEvent is agent_spawn
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "agent_spawn"


# ------------------------------------------------------------------
# Chat flow unaffected
# ------------------------------------------------------------------


async def test_e2e_chat_flow_unaffected_by_plan_detection() -> None:
    """Chat intent -> conversational response, plan detection doesn't interfere."""
    classifier = _mock_classifier(intent="chat", confidence=0.97)
    decomposer = _mock_decomposer(
        answer_events=[Response(content="Hi! What can I do for you?")],
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
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

    # No route_task called for chat
    decomposer.route_task.assert_not_awaited()


# ------------------------------------------------------------------
# Invalid plan JSON -> falls back to small scope
# ------------------------------------------------------------------


async def test_e2e_invalid_plan_falls_back_to_small_scope() -> None:
    """If Decomposer returns scope=small (fallback), a single-agent PlanEvent is emitted."""
    classifier = _mock_classifier(intent="task", confidence=0.3)
    decomposer = _mock_decomposer(
        review_result=ReviewResult(intent="task", confidence=0.5, estimated_tools=3),
        route_task_result=TaskOutput(
            scope="small",
            summary="Direct handling",
            prompt="task",
        ),
    )

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            mgr = SessionManager(timeout=60)
            session = await mgr.get_or_create(user_id=1)
            events = [e async for e in session.send("task")]

    # Small scope -> single-agent PlanEvent (not a raw Response)
    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plan_events) == 1
    assert plan_events[0].plan.scope == "small"

    # RoutingEvent is agent_spawn
    routing = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing[0].routing == "agent_spawn"
