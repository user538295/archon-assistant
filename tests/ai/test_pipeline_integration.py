"""Integration tests for Pipeline — Classifier + Decomposer routing flow.

Verifies that classification info flows correctly through the pipeline
to the Decomposer, using the new routing algorithm:
- Classification is yielded as ClassificationEvent
- chat + high confidence (>=0.8) → answer() directly
- Everything else (task, or chat below 0.8) → route_task()
- No review step
"""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import ClassificationEvent, PlanEvent, Response, RoutingEvent
from archon.ai.pipeline import Pipeline


# ------------------------------------------------------------------
# Helpers (same pattern as test_pipeline.py)
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
    decomposer.review = AsyncMock()
    decomposer.route_task = AsyncMock(return_value=route_task_result or TaskOutput(
        scope="small", summary="Quick task", prompt="Do the thing",
    ))
    decomposer.activate_skill = MagicMock()
    decomposer.inject_context = MagicMock()
    decomposer.recent_events = MagicMock(return_value=[])
    return decomposer


def _make_pipeline(classifier=None, decomposer=None):
    """Build a Pipeline with mocked Classifier and Decomposer."""
    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline()

    return pipeline, classifier, decomposer


async def _collect(pipeline, prompt="test"):
    return [e async for e in pipeline.send(prompt)]


# ------------------------------------------------------------------
# Classification info flows to the routing layer
# ------------------------------------------------------------------


async def test_high_confidence_task_routes_to_route_task() -> None:
    """High-confidence task → route_task() called; scope='small' → inline execution (no PlanEvent)."""
    pipeline, classifier, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.92),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Done inline.")],
            route_task_result=TaskOutput(scope="small", summary="Done.", prompt="write a test"),
        ),
    )
    events = await _collect(pipeline, "write a test")

    # Classifier was called with the user prompt
    classifier.classify.assert_awaited_once_with("write a test")

    # route_task IS called (task intent → always route_task)
    decomposer.route_task.assert_awaited_once()

    # No review — review is removed
    decomposer.review.assert_not_awaited()

    # scope="small" → inline execution → no PlanEvent
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0

    # Response IS yielded (inline)
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1


async def test_high_confidence_chat_routes_to_answer() -> None:
    """High-confidence chat classification triggers Decomposer.answer()."""
    pipeline, classifier, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Hello!")],
        ),
    )
    events = await _collect(pipeline, "hi")

    classifier.classify.assert_awaited_once_with("hi")
    decomposer.review.assert_not_awaited()
    decomposer.route_task.assert_not_awaited()

    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "Hello!"


async def test_low_confidence_task_routes_to_route_task() -> None:
    """Low-confidence task → route_task() called; scope='small' → inline execution (no PlanEvent)."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.5),
        decomposer=_mock_decomposer(
            answer_events=[Response(content="Done inline.")],
            route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="do something"),
        ),
    )
    events = await _collect(pipeline, "do something")

    # route_task IS called
    decomposer.route_task.assert_awaited_once()

    # No review
    decomposer.review.assert_not_awaited()

    # scope="small" → inline execution → no PlanEvent
    plans = [e for e in events if isinstance(e, PlanEvent)]
    assert len(plans) == 0


async def test_classification_event_yielded_for_task() -> None:
    """ClassificationEvent is yielded with correct intent and confidence."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.88),
    )
    events = await _collect(pipeline, "refactor the auth module")

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "task"
    assert classification_events[0].confidence == 0.88


async def test_classification_event_yielded_for_chat() -> None:
    """ClassificationEvent is yielded for chat classification."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.95),
    )
    events = await _collect(pipeline, "hi")

    classification_events = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(classification_events) == 1
    assert classification_events[0].intent == "chat"
    assert classification_events[0].confidence == 0.95


async def test_malformed_classifier_defaults_to_task_routes_to_route_task() -> None:
    """When classifier returns task(0.0), route_task is called (no review)."""
    pipeline, _, decomposer = _make_pipeline(
        classifier=_mock_classifier(intent="task", confidence=0.0),
        decomposer=_mock_decomposer(
            route_task_result=TaskOutput(scope="small", summary="Quick task", prompt="help"),
        ),
    )
    events = await _collect(pipeline, "do something")

    # Default classification event
    ce = [e for e in events if isinstance(e, ClassificationEvent)]
    assert len(ce) == 1
    assert ce[0].intent == "task"
    assert ce[0].confidence == 0.0

    # route_task called (task → always route_task)
    decomposer.route_task.assert_awaited_once()

    # No review
    decomposer.review.assert_not_awaited()


async def test_classification_event_is_first_event() -> None:
    """ClassificationEvent is always the first event yielded."""
    pipeline, _, _ = _make_pipeline(
        classifier=_mock_classifier(intent="chat", confidence=0.75),
    )
    events = await _collect(pipeline, "hey")

    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "chat"
    assert events[0].confidence == 0.75


# ------------------------------------------------------------------
# Bug 20: _pending_context consumed by ClaudeSession through Pipeline
# ------------------------------------------------------------------
# These tests exercise the full Pipeline → Decomposer → ClaudeSession
# chain with the SDK client mocked, verifying that inject_context()
# text actually reaches the prompt sent to the SDK.


def _make_sdk_client(result_text: str = "OK"):
    """Build a mock ClaudeSDKClient that records query() calls and yields a ResultMessage."""
    from claude_agent_sdk import ResultMessage

    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()
    client._transport = None

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=50,
        is_error=False,
        num_turns=1,
        session_id="test",
        result=result_text,
        total_cost_usd=0.001,
        usage={"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0},
    )

    async def _receive():
        yield result_msg

    client.receive_response = _receive
    return client


async def _make_real_pipeline(sdk_client):
    """Build a Pipeline with real Decomposer/ClaudeSession but mocked SDK + Classifier.

    Returns (pipeline, sdk_client) — sdk_client.query captures all prompts.
    """
    classifier = _mock_classifier(intent="chat", confidence=0.95)

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=sdk_client), \
         patch("archon.ai.pipeline.Classifier", return_value=classifier), \
         patch("archon.ai.decomposer.load_workspace_agents", new_callable=AsyncMock, return_value=None):
        pipeline = Pipeline()
        await pipeline.start()

    return pipeline, classifier


async def test_inject_context_reaches_sdk_prompt() -> None:
    """inject_context() text is prepended to the prompt that ClaudeSession sends to the SDK."""
    sdk_client = _make_sdk_client("Got it.")
    pipeline, _ = await _make_real_pipeline(sdk_client)

    try:
        pipeline.inject_context("Agent Atlas completed: refactored auth.py successfully.")
        events = [e async for e in pipeline.send("what happened?")]

        # Verify the SDK received a prompt containing the injected context
        # query() is called once by the main session's send()
        query_calls = [call.args[0] for call in sdk_client.query.call_args_list]
        assert len(query_calls) >= 1
        actual_prompt = query_calls[-1]  # last query() call is the user message
        assert "Agent Atlas completed: refactored auth.py successfully." in actual_prompt
        assert "what happened?" in actual_prompt

        # Response event was yielded
        responses = [e for e in events if isinstance(e, Response)]
        assert len(responses) == 1
    finally:
        await pipeline.stop()


async def test_inject_context_flushed_after_send() -> None:
    """After send(), the pending context is consumed — second send() gets no stale context."""
    sdk_client = _make_sdk_client("OK")
    pipeline, _ = await _make_real_pipeline(sdk_client)

    try:
        pipeline.inject_context("one-shot context")
        _ = [e async for e in pipeline.send("first message")]

        # Reset mock to track only the second call
        sdk_client.query.reset_mock()

        # Need fresh receive_response for the second send()
        from claude_agent_sdk import ResultMessage
        result_msg2 = ResultMessage(
            subtype="success",
            duration_ms=100,
            duration_api_ms=50,
            is_error=False,
            num_turns=1,
            session_id="test",
            result="OK2",
            total_cost_usd=0.001,
            usage={"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 0},
        )

        async def _receive2():
            yield result_msg2

        sdk_client.receive_response = _receive2

        _ = [e async for e in pipeline.send("second message")]

        query_calls = [call.args[0] for call in sdk_client.query.call_args_list]
        assert len(query_calls) >= 1
        second_prompt = query_calls[-1]
        assert "one-shot context" not in second_prompt
        assert "second message" in second_prompt
    finally:
        await pipeline.stop()


async def test_multiple_inject_context_all_consumed_on_send() -> None:
    """Multiple inject_context() calls queue up; all are prepended on the next send()."""
    sdk_client = _make_sdk_client("Done")
    pipeline, _ = await _make_real_pipeline(sdk_client)

    try:
        pipeline.inject_context("Agent Atlas completed: fixed bug in auth.py")
        pipeline.inject_context("Agent Nova completed: updated tests in test_auth.py")
        pipeline.inject_context("Agent Sage completed: refactored config loader")

        _ = [e async for e in pipeline.send("give me a summary")]

        query_calls = [call.args[0] for call in sdk_client.query.call_args_list]
        assert len(query_calls) >= 1
        actual_prompt = query_calls[-1]

        # All three context entries must be in the prompt
        assert "Agent Atlas completed: fixed bug in auth.py" in actual_prompt
        assert "Agent Nova completed: updated tests in test_auth.py" in actual_prompt
        assert "Agent Sage completed: refactored config loader" in actual_prompt
        assert "give me a summary" in actual_prompt
    finally:
        await pipeline.stop()
