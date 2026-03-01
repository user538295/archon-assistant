"""Tests for Decomposer — the brain that evaluates, answers, and plans."""

import json
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted


# ──────────────────────────────────────────────────────────────────
# Helpers
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
    session.recent_events = MagicMock(return_value=[])
    return session


def _make_decomposer(session_events=None, **kwargs):
    """Build a Decomposer with a mocked session."""
    from archon.ai.decomposer import Decomposer

    if session_events is None:
        session_events = [Response(content="Done.")]

    mock_session = _mock_session(*session_events)

    with patch("archon.ai.decomposer.ClaudeSession", return_value=mock_session):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    return decomposer, mock_session


# ──────────────────────────────────────────────────────────────────
# review() — re-evaluates low-confidence classification
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_returns_updated_classification() -> None:
    from archon.ai.classification import Classification
    from archon.ai.decomposer import Decomposer

    review_json = json.dumps({"intent": "task", "confidence": 0.9, "estimated_tools": 3})
    decomposer, session = _make_decomposer(
        session_events=[Response(content=review_json)],
    )
    classification = Classification(intent="chat", confidence=0.5)
    result = await decomposer.review("refactor auth", classification)

    assert result.intent == "task"
    assert result.confidence == 0.9
    assert result.estimated_tools == 3


@pytest.mark.asyncio
async def test_review_graceful_fallback_on_parse_error() -> None:
    from archon.ai.classification import Classification
    from archon.ai.decomposer import Decomposer

    decomposer, _ = _make_decomposer(
        session_events=[Response(content="I think this is a task")],
    )
    classification = Classification(intent="chat", confidence=0.5)
    result = await decomposer.review("test", classification)

    # Should fall back to original classification values
    assert result.intent == "chat"
    assert result.confidence == 0.5
    assert result.estimated_tools == 0


@pytest.mark.asyncio
async def test_review_sends_review_prompt_to_session() -> None:
    from archon.ai.classification import Classification

    review_json = json.dumps({"intent": "task", "confidence": 0.85, "estimated_tools": 2})
    decomposer, session = _make_decomposer(
        session_events=[Response(content=review_json)],
    )
    classification = Classification(intent="chat", confidence=0.5)
    await decomposer.review("hello there", classification)

    # Session should have been called once
    assert len(session._send_calls) == 1
    # The prompt should contain the review instruction
    assert "hello there" in session._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# answer() — streams events from session
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_streams_events() -> None:
    decomposer, _ = _make_decomposer(
        session_events=[
            ThinkingResult(content="thinking..."),
            Response(content="Here is the answer"),
        ],
    )
    events = [e async for e in decomposer.answer("hello")]

    assert len(events) == 2
    assert isinstance(events[0], ThinkingResult)
    assert isinstance(events[1], Response)


@pytest.mark.asyncio
async def test_answer_sends_prompt_to_session() -> None:
    decomposer, session = _make_decomposer()
    _ = [e async for e in decomposer.answer("what is 2+2")]

    assert len(session._send_calls) == 1
    assert "what is 2+2" in session._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# route_task() — returns small or large TaskOutput
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_task_returns_small_scope() -> None:
    small_json = json.dumps({
        "scope": "small",
        "summary": "Fix the typo",
        "prompt": "Fix the typo in README.md line 5",
    })
    decomposer, _ = _make_decomposer(
        session_events=[Response(content=small_json)],
    )
    result = await decomposer.route_task("fix the typo in readme")

    assert result.scope == "small"
    assert result.summary == "Fix the typo"
    assert result.prompt == "Fix the typo in README.md line 5"
    assert result.agents is None


@pytest.mark.asyncio
async def test_route_task_returns_large_scope() -> None:
    large_json = json.dumps({
        "scope": "large",
        "summary": "Refactor auth module",
        "agents": [
            {"id": "a1", "task": "Extract middleware"},
            {"id": "a2", "task": "Update imports", "depends_on": ["a1"]},
        ],
    })
    decomposer, _ = _make_decomposer(
        session_events=[Response(content=large_json)],
    )
    result = await decomposer.route_task("refactor the auth module")

    assert result.scope == "large"
    assert result.summary == "Refactor auth module"
    assert result.prompt is None
    assert result.agents is not None
    assert len(result.agents) == 2


@pytest.mark.asyncio
async def test_route_task_graceful_fallback_on_bad_json() -> None:
    decomposer, _ = _make_decomposer(
        session_events=[Response(content="Let me handle this directly")],
    )
    result = await decomposer.route_task("do something")

    # Fallback: treat as small task with the prompt as-is
    assert result.scope == "small"
    assert result.prompt is not None


@pytest.mark.asyncio
async def test_route_task_sends_prompt_to_session() -> None:
    small_json = json.dumps({"scope": "small", "summary": "test", "prompt": "do it"})
    decomposer, session = _make_decomposer(
        session_events=[Response(content=small_json)],
    )
    await decomposer.route_task("big task here")

    assert len(session._send_calls) == 1
    assert "big task here" in session._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Duck-typing surface delegates to session
# ──────────────────────────────────────────────────────────────────


def test_is_processing_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.is_processing = True
    assert decomposer.is_processing is True


def test_model_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.model = "claude-sonnet-4-6"
    assert decomposer.model == "claude-sonnet-4-6"


def test_diagnostics_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.diagnostics = {"is_alive": True, "send_count": 5}
    assert decomposer.diagnostics == {"is_alive": True, "send_count": 5}


def test_usage_stats_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.usage_stats = {"total_cost_usd": 0.05}
    assert decomposer.usage_stats == {"total_cost_usd": 0.05}


def test_activate_skill_delegates() -> None:
    decomposer, session = _make_decomposer()
    skill = MagicMock()
    decomposer.activate_skill(skill)
    session.activate_skill.assert_called_once_with(skill)


def test_inject_context_delegates() -> None:
    decomposer, session = _make_decomposer()
    decomposer.inject_context("some context")
    session.inject_context.assert_called_once_with("some context")


def test_is_alive_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.is_alive = True
    assert decomposer.is_alive is True


def test_send_count_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.send_count = 3
    assert decomposer.send_count == 3


def test_processing_seconds_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.processing_seconds = 12.5
    assert decomposer.processing_seconds == 12.5


def test_idle_seconds_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.idle_seconds = 3.0
    assert decomposer.idle_seconds == 3.0


def test_recent_events_delegates() -> None:
    decomposer, session = _make_decomposer()
    session.recent_events.return_value = [(1.0, Response(content="hi"))]
    assert len(decomposer.recent_events(5)) == 1


# ──────────────────────────────────────────────────────────────────
# Lifecycle
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_starts_session() -> None:
    decomposer, session = _make_decomposer()
    await decomposer.start()
    session.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_stops_session() -> None:
    decomposer, session = _make_decomposer()
    await decomposer.stop()
    session.stop.assert_awaited_once()
