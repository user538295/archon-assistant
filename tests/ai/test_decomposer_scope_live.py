"""Live tests — Decomposer scope decision with the real Claude SDK.

These tests call the REAL Decomposer session (same model as production) and
verify it correctly distinguishes large-scope tasks (→ PlanEvent) from small
ones (→ direct Response).

BUG-3 FIXED (FIX-032): scope rubric and extended thinking enabled in router session.

  BUG-3 (scope misjudgement): The Decomposer handled the 06:44:12 UTC
      "Make a comprehensive plan to refactor pipeline, classifier, decomposer,
      gateway..." message as scope=small (direct response). The history log
      confirmed this via RoutingEvent: Decision: direct response.

      Root cause: The test helper was using decomposer.md (main session prompt)
      instead of orchestrator.md + route_task.md (actual router session path).
      The production fix (Tasks 1.1/2.1) added the scope rubric and extended
      thinking budget to the router session, which now correctly identifies
      large-scope tasks.

Objective measurable thresholds used in these tests:

  LARGE scope triggers (must emit PlanEvent):
    - ≥ 3 distinct module/file names mentioned AND
    - requires creating/writing ≥ 1 output file
    → Measurable: count distinct module names in prompt + check for "save"/"write"

  LARGE scope triggers (must emit PlanEvent):
    - Requires reading multiple independent sources before synthesising output
    → Measurable: investigation verbs (investigate, analyse, check, read) +
      multiple targets mentioned

  SMALL scope (must emit direct Response, no PlanEvent):
    - Single question, no file operations, no multiple targets
    - Pure conversational exchange
    → Measurable: no action verbs targeting file system, ≤ 1 subject

Real messages are verbatim from the 2026-03-01 history log.

Run:  uv run pytest -m live tests/ai/test_decomposer_scope_live.py -v
"""

import asyncio
import shutil

import pytest

from archon.ai.decomposer import Decomposer, TaskOutput
from archon.ai.event_mapper import RoutingEvent
from archon.ai.pipeline import Pipeline

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_TIMEOUT = 200.0  # Extended thinking may take 60-90s


# ──────────────────────────────────────────────────────────────────
# Helper — run the real Decomposer.route_task() in isolation
# Uses the actual production code path so scope rubric and extended
# thinking budget are applied exactly as in production.
# ──────────────────────────────────────────────────────────────────

async def _call_decomposer_direct(prompt: str) -> tuple[list, TaskOutput]:
    """Run the real Decomposer.route_task() and return (events, task_output).

    Uses the actual production routing path (Decomposer.route_task) with
    orchestrator.md as system prompt and route_task.md as instruction.
    Returns all intermediate events and the final TaskOutput sentinel.

    Args:
        prompt: The raw user message.
    """
    decomposer = Decomposer()
    await decomposer.start()
    events: list = []
    task_output: TaskOutput | None = None
    try:
        async with asyncio.timeout(_TIMEOUT):
            async for item in decomposer.route_task(prompt):
                if isinstance(item, TaskOutput):
                    task_output = item
                else:
                    events.append(item)
    finally:
        await decomposer.stop()
    assert task_output is not None, "route_task() did not yield a TaskOutput sentinel"
    return events, task_output


# ──────────────────────────────────────────────────────────────────
# BUG-3: Large-scope tasks must produce PlanEvent
# ──────────────────────────────────────────────────────────────────

async def test_decomposer_emits_plan_for_multimodule_refactoring_request() -> None:
    """BUG-3 FIXED: Refactoring plan request (from 06:44:12 UTC) must route as large scope.

    This message meets all three large-scope criteria:
      ✓ 4 distinct modules named: pipeline, classifier, decomposer, gateway
      ✓ Requires writing output file: "Save the result into md file"
      ✓ Investigation before output: must read each module before writing plan

    Measurable thresholds:
      - TaskOutput.scope == "large"
      - TaskOutput.agents length ≥ 2 (at minimum: investigation agent + synthesis agent)
    """
    prompt = (
        "Make a comprehensive plan how to refactor the pipeline, classifier, decomposer, "
        "gateway to have clean code. Save the result into md file. The plan also contains "
        "a task list as well. You must be clear and easy to understand in the documentation "
        "even for a medior developer can understand. Be very precise, the plan contains clear "
        "and small steps to be able to easy to follow the changes. Be very very precise and accurate."
    )
    _events, task_output = await _call_decomposer_direct(prompt)

    assert task_output.scope == "large", (
        f"BUG-3: Decomposer handled a multi-module refactoring request "
        f"(4 modules + file output) as scope={task_output.scope!r}.\n"
        f"Expected scope='large'.\n"
        f"is_fallback={task_output.is_fallback}, fallback_reason={task_output.fallback_reason!r}"
    )

    agents = task_output.agents or []
    assert len(agents) >= 2, (
        f"Plan has too few agents: {len(agents)}. "
        f"Expected ≥ 2 (at minimum: investigation + synthesis).\n"
        f"Agents: {[a.id for a in agents]}"
    )


async def test_decomposer_emits_plan_for_multi_target_investigation() -> None:
    """BUG-3 FIXED: Investigation of multiple independent code paths must route as large scope.

    This message has ≥ 2 independent investigation targets and an output artifact.

    Measurable thresholds:
      - TaskOutput.scope == "large"
      - TaskOutput.agents ≥ 2
    """
    prompt = (
        "Investigate why the Classifier always returns confidence=0.0 in production. "
        "Check the history log for patterns, read the classification.py parser, "
        "and read the pipeline.py to trace how the raw response is captured. "
        "Write a bug report summarising your findings."
    )
    _events, task_output = await _call_decomposer_direct(prompt)

    assert task_output.scope == "large", (
        f"Decomposer routed multi-target investigation as scope={task_output.scope!r}.\n"
        f"Investigation targets: history log, classification.py, pipeline.py (3 sources).\n"
        f"is_fallback={task_output.is_fallback}, fallback_reason={task_output.fallback_reason!r}"
    )
    agents = task_output.agents or []
    assert len(agents) >= 2, (
        f"Plan has only {len(agents)} agent(s); expected ≥ 2."
    )


# ──────────────────────────────────────────────────────────────────
# Baseline: small-scope tasks must NOT produce PlanEvent
# These establish that the fix does not break simple tasks.
# ──────────────────────────────────────────────────────────────────


async def test_decomposer_direct_response_for_single_question() -> None:
    """Simple single question must route as small scope, not large.

    Measurable threshold:
      - TaskOutput.scope != "large"
      - TaskOutput.scope in ("small", "trivial")
    """
    prompt = "What is the purpose of the Pipeline class in this codebase?"
    _events, task_output = await _call_decomposer_direct(prompt)

    assert task_output.scope in ("small", "trivial"), (
        f"Decomposer routed a simple single question as scope={task_output.scope!r}.\n"
        f"This is scope=small — should not trigger agent plan.\n"
        f"agents: {task_output.agents}"
    )


async def test_decomposer_direct_response_for_single_file_fix() -> None:
    """Single-file change must route as small scope, not large.

    Measurable threshold:
      - TaskOutput.scope in ("small", "trivial") (single file, no dependencies, one action)
    """
    prompt = (
        "In archon/ai/classification.py, rename the variable 'log' to 'logger' "
        "to match the naming convention used in all other modules."
    )
    _events, task_output = await _call_decomposer_direct(prompt)

    assert task_output.scope in ("small", "trivial"), (
        f"Decomposer routed a single-file rename as scope={task_output.scope!r}.\n"
        f"This is clearly scope=small — one file, one change, no dependencies.\n"
        f"agents: {task_output.agents}"
    )


# ──────────────────────────────────────────────────────────────────
# RoutingEvent: verify routing decision is logged correctly
# ──────────────────────────────────────────────────────────────────


async def test_routing_event_reports_agent_plan_when_plan_emitted() -> None:
    """BUG-3 FIXED: When a plan is emitted, RoutingEvent must report routing='agent_plan'.

    This test uses the full Pipeline (Classifier + Decomposer) so RoutingEvent
    is produced by pipeline.send() — the same path used in production.

    Measurable threshold:
      - RoutingEvent.routing == 'agent_plan' (not 'direct')
      - RoutingEvent is the last event in the stream
    """
    prompt = (
        "Make a comprehensive plan how to refactor the pipeline, classifier, decomposer, "
        "gateway to have clean code. Save the result into md file. The plan also contains "
        "a task list as well. You must be clear and easy to understand in the documentation "
        "even for a medior developer can understand. Be very precise, the plan contains clear "
        "and small steps to be able to easy to follow the changes. Be very very precise and accurate."
    )

    # Use the full Pipeline to get RoutingEvent (only Pipeline emits it)
    pipeline = Pipeline()
    await pipeline.start()
    events: list = []
    try:
        async with asyncio.timeout(_TIMEOUT):
            async for event in pipeline.send(prompt):
                events.append(event)
    finally:
        await pipeline.stop()

    routing_events = [e for e in events if isinstance(e, RoutingEvent)]
    assert routing_events, "No RoutingEvent emitted by Pipeline."

    routing = routing_events[0]
    assert routing.routing == "agent_plan", (
        f"BUG-3 CONFIRMED via RoutingEvent: Decision was {routing.routing!r}, "
        f"expected 'agent_plan'.\n"
        f"This matches the history log entry: Decision: direct response\n"
        f"Model: {routing.model!r}"
    )
    assert isinstance(events[-1], RoutingEvent), (
        f"RoutingEvent must be the last event in the stream.\n"
        f"Last event was: {type(events[-1]).__name__!r}"
    )
