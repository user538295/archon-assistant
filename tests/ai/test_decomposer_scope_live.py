"""Live tests — Decomposer scope decision with the real Claude SDK.

These tests call the REAL Decomposer session (same model as production) and
verify it correctly distinguishes large-scope tasks (→ PlanEvent) from small
ones (→ direct Response).

CURRENT STATUS: BUG-3 tests are EXPECTED TO FAIL — they expose the confirmed
scope misjudgement from the 2026-03-01 history log.

  BUG-3 (scope misjudgement): The Decomposer handled the 06:44:12 UTC
      "Make a comprehensive plan to refactor pipeline, classifier, decomposer,
      gateway..." message as scope=small (direct response). The history log
      confirmed this via RoutingEvent: Decision: direct response.

      The task met ALL THREE large-scope criteria from the decomposer prompt:
        ✓ Multiple steps where output of one feeds the next
          (read files → analyse → write document)
        ✓ External investigation before implementation
          (14 files were read before the document was written)
        ✓ Multiple independent sub-tasks (each module is independent)

      Root cause (confirmed in chat): The Decomposer anchored on "single output
      file = small" instead of "investigation breadth = large".

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

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import PlanEvent, Response, RoutingEvent
from archon.ai.pipeline import Pipeline
from archon.ai.prompts import load_prompt

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_TIMEOUT = 120.0  # Decomposer may think for a while


# ──────────────────────────────────────────────────────────────────
# Helper — run just the Decomposer session in isolation
# We bypass the Classifier here to test scope logic in isolation.
# The Decomposer prompt is pre-injected with a task classification
# matching what the real Classifier SHOULD have sent.
# ──────────────────────────────────────────────────────────────────

async def _call_decomposer_direct(
    prompt: str,
    inject_classification: str = '{"intent": "task", "confidence": 0.9}',
) -> list:
    """Run the real Decomposer session and return all events.

    Injects a fake (but correct) classification prefix so the Decomposer
    receives the same format it would from the full pipeline, but without
    depending on the (potentially buggy) Classifier.

    Args:
        prompt: The raw user message.
        inject_classification: The classification JSON to prefix.
            Defaults to high-confidence task (what a working classifier
            should return for the messages used in these tests).
    """
    full_prompt = f"[Classification: {inject_classification}]\n\n{prompt}"
    session = ClaudeSession(
        system_prompt=load_prompt("decomposer"),
    )
    await session.start()
    events: list = []
    try:
        async with asyncio.timeout(_TIMEOUT):
            async for event in session.send(full_prompt):
                events.append(event)
    finally:
        await session.stop()
    return events


# ──────────────────────────────────────────────────────────────────
# BUG-3: Large-scope tasks must produce PlanEvent
# ──────────────────────────────────────────────────────────────────

@pytest.mark.xfail(reason="BUG-3: Decomposer routes multi-module tasks as small scope", strict=False)
async def test_decomposer_emits_plan_for_multimodule_refactoring_request() -> None:
    """BUG-3: Refactoring plan request (from 06:44:12 UTC) must produce PlanEvent.

    This message meets all three large-scope criteria:
      ✓ 4 distinct modules named: pipeline, classifier, decomposer, gateway
      ✓ Requires writing output file: "Save the result into md file"
      ✓ Investigation before output: must read each module before writing plan

    Measurable thresholds:
      - PlanEvent MUST be emitted (routing = agent_plan)
      - plan.agents length ≥ 2 (at minimum: investigation agent + synthesis agent)
      - No direct Response event (the agent plan replaces it)

    EXPECTED TO FAIL in current code: history log confirms this was routed
    as "direct response" on 2026-03-01.
    """
    prompt = (
        "Make a comprehensive plan how to refactor the pipeline, classifier, decomposer, "
        "gateway to have clean code. Save the result into md file. The plan also contains "
        "a task list as well. You must be clear and easy to understand in the documentation "
        "even for a medior developer can understand. Be very precise, the plan contains clear "
        "and small steps to be able to easy to follow the changes. Be very very precise and accurate."
    )
    events = await _call_decomposer_direct(prompt)

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    direct_responses = [e for e in events if isinstance(e, Response)]

    assert plan_events, (
        f"BUG-3 CONFIRMED: Decomposer handled a multi-module refactoring request "
        f"(4 modules + file output) as scope=small (direct response).\n"
        f"Expected: PlanEvent with ≥ 2 agents.\n"
        f"Got: {[type(e).__name__ for e in events]}\n"
        f"Direct response text (first 200 chars): "
        f"{direct_responses[0].content[:200] if direct_responses else '(none)'!r}"
    )

    # Validate plan structure
    plan = plan_events[0].plan
    assert len(plan.agents) >= 2, (
        f"Plan has too few agents: {len(plan.agents)}. "
        f"Expected ≥ 2 (at minimum: investigation + synthesis).\n"
        f"Agents: {[a.id for a in plan.agents]}"
    )

    # The original Response must NOT be passed through when a plan is emitted
    assert not direct_responses, (
        f"Both PlanEvent AND Response were emitted — plan detection is not suppressing Response.\n"
        f"Response content: {direct_responses[0].content[:200]!r}"
    )


@pytest.mark.xfail(reason="BUG-3: Decomposer routes multi-target investigation as small scope", strict=False)
async def test_decomposer_emits_plan_for_multi_target_investigation() -> None:
    """BUG-3: Investigation of multiple independent code paths must produce PlanEvent.

    This message has ≥ 2 independent investigation targets and an output artifact.

    Measurable thresholds:
      - PlanEvent MUST be emitted
      - plan.agents ≥ 2
    """
    prompt = (
        "Investigate why the Classifier always returns confidence=0.0 in production. "
        "Check the history log for patterns, read the classification.py parser, "
        "and read the pipeline.py to trace how the raw response is captured. "
        "Write a bug report summarising your findings."
    )
    events = await _call_decomposer_direct(prompt)

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert plan_events, (
        f"Decomposer did not emit PlanEvent for a multi-target investigation + report.\n"
        f"Investigation targets: history log, classification.py, pipeline.py (3 sources).\n"
        f"Got events: {[type(e).__name__ for e in events]}"
    )
    assert len(plan_events[0].plan.agents) >= 2, (
        f"Plan has only {len(plan_events[0].plan.agents)} agent(s); expected ≥ 2."
    )


# ──────────────────────────────────────────────────────────────────
# Baseline: small-scope tasks must NOT produce PlanEvent
# These establish that the fix does not break simple tasks.
# ──────────────────────────────────────────────────────────────────


async def test_decomposer_direct_response_for_single_question() -> None:
    """Simple single question must produce direct Response, not PlanEvent.

    Measurable threshold:
      - NO PlanEvent emitted
      - Exactly 1 Response emitted
      - Response content is non-empty
    """
    prompt = "What is the purpose of the Pipeline class in this codebase?"
    events = await _call_decomposer_direct(
        prompt,
        inject_classification='{"intent": "chat", "confidence": 0.9}',
    )

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    responses = [e for e in events if isinstance(e, Response)]

    assert not plan_events, (
        f"Decomposer emitted a PlanEvent for a simple single question.\n"
        f"This is scope=small — should be a direct response.\n"
        f"Plan summary: {plan_events[0].summary if plan_events else '(none)'}"
    )
    assert responses, "Decomposer returned no Response for a simple question."
    assert responses[0].content, "Response content is empty."


async def test_decomposer_direct_response_for_single_file_fix() -> None:
    """Single-file change must produce direct Response, not PlanEvent.

    Measurable threshold:
      - NO PlanEvent emitted (single file, no dependencies, one action)
      - 1 Response emitted
    """
    prompt = (
        "In archon/ai/classification.py, rename the variable 'log' to 'logger' "
        "to match the naming convention used in all other modules."
    )
    events = await _call_decomposer_direct(prompt)

    plan_events = [e for e in events if isinstance(e, PlanEvent)]
    assert not plan_events, (
        f"Decomposer emitted PlanEvent for a single-file rename.\n"
        f"This is clearly scope=small — one file, one change, no dependencies.\n"
        f"Plan: {plan_events[0].summary if plan_events else '(none)'}"
    )


# ──────────────────────────────────────────────────────────────────
# RoutingEvent: verify routing decision is logged correctly
# ──────────────────────────────────────────────────────────────────


@pytest.mark.xfail(reason="BUG-3: depends on Decomposer scope fix", strict=False)
async def test_routing_event_reports_agent_plan_when_plan_emitted() -> None:
    """When a plan is emitted, RoutingEvent must report routing='agent_plan'.

    This test uses the full Pipeline (Classifier + Decomposer) so RoutingEvent
    is produced by pipeline.send() — the same path used in production.

    Measurable threshold:
      - RoutingEvent.routing == 'agent_plan' (not 'direct')
      - RoutingEvent is the last event in the stream

    Note: This test depends on BUG-3 being fixed first. It will fail until
    the Decomposer correctly emits a plan for the given prompt.
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
