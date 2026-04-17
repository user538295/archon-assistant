"""Tests for Classifier — standalone classification wrapper."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.classification import Classification, ClassificationResult
from archon.ai.event_mapper import Response, ThinkingResult


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing=False):
    """Build a mock ClaudeSession that yields given events from send()."""
    from tests.conftest import _mock_session_factory

    return _mock_session_factory(*events, is_processing=is_processing)


def _make_classifier(session_events=None):
    """Build a Classifier with a mocked session injected via _create_session()."""
    from archon.ai.classifier import Classifier

    if session_events is None:
        session_events = [Response(content='{"intent": "task", "confidence": 0.9}')]

    mock_session = _mock_session(*session_events)

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    # Override _create_session so classify() gets the mock without SDK calls
    classifier._create_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]

    return classifier, mock_session


# ──────────────────────────────────────────────────────────────────
# Task 1.1: No persistent session in __init__
# ──────────────────────────────────────────────────────────────────


def test_init_has_no_session() -> None:
    """Classifier.__init__() must not create a _session attribute."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    assert not hasattr(classifier, "_session"), "_session must not exist after __init__"


@pytest.mark.asyncio
async def test_start_is_noop() -> None:
    """await classifier.start() must complete without creating or starting any session."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    with patch("archon.ai.classifier.ClaudeSession") as MockSession:
        await classifier.start()

    MockSession.assert_not_called()


@pytest.mark.asyncio
async def test_stop_is_noop() -> None:
    """await classifier.stop() must complete without calling any session method."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    with patch("archon.ai.classifier.ClaudeSession") as MockSession:
        await classifier.stop()

    MockSession.assert_not_called()


def test_usage_stats_returns_carried_zeros_initially() -> None:
    """Fresh Classifier usage_stats must return None (no cost yet)."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    # No calls yet — no stats to report
    assert classifier.usage_stats is None


# ──────────────────────────────────────────────────────────────────
# classify() returns valid ClassificationResult
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_valid_classification() -> None:
    classifier, _ = _make_classifier(
        session_events=[Response(content='{"intent": "chat", "confidence": 0.85}')],
    )
    result = await classifier.classify("hello")
    assert result.classification == Classification(intent="chat", confidence=0.85)


@pytest.mark.asyncio
async def test_classify_returns_task_for_task_intent() -> None:
    classifier, _ = _make_classifier(
        session_events=[Response(content='{"intent": "task", "confidence": 0.95}')],
    )
    result = await classifier.classify("refactor the auth module")
    assert result.classification.intent == "task"
    assert result.classification.confidence == 0.95


# ──────────────────────────────────────────────────────────────────
# classify() defaults on bad JSON
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_default_on_bad_json() -> None:
    classifier, _ = _make_classifier(
        session_events=[Response(content="I cannot classify this")],
    )
    result = await classifier.classify("test")
    assert result.classification == Classification(intent="task", confidence=0.0)
    assert result.parse_error != ""


@pytest.mark.asyncio
async def test_classify_returns_default_when_no_response() -> None:
    """When session yields events but no Response, default to task."""
    classifier, _ = _make_classifier(
        session_events=[ThinkingResult(content="hmm")],
    )
    result = await classifier.classify("test")
    assert result.classification == Classification(intent="task", confidence=0.0)


# ──────────────────────────────────────────────────────────────────
# classify() defaults on crash
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_returns_default_on_crash() -> None:
    from archon.ai.classifier import Classifier

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()

    async def _crashing_send(prompt: str):
        raise RuntimeError("SDK connection lost")
        yield  # makes this an async generator

    mock_session.send = _crashing_send

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    classifier._create_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]

    result = await classifier.classify("test")
    assert result.classification == Classification(intent="task", confidence=0.0)
    assert result.error != ""


# ──────────────────────────────────────────────────────────────────
# classify() includes timing
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_includes_timing() -> None:
    classifier, _ = _make_classifier()
    result = await classifier.classify("test")
    assert result.duration_s >= 0.0


@pytest.mark.asyncio
async def test_classify_includes_raw_response() -> None:
    raw = '{"intent": "chat", "confidence": 0.85}'
    classifier, _ = _make_classifier(
        session_events=[Response(content=raw)],
    )
    result = await classifier.classify("hi")
    assert result.raw_response == raw


@pytest.mark.asyncio
async def test_classify_raw_response_empty_on_crash() -> None:
    from archon.ai.classifier import Classifier

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()

    async def _crashing_send(prompt: str):
        raise RuntimeError("boom")
        yield  # makes this an async generator

    mock_session.send = _crashing_send

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    classifier._create_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]

    result = await classifier.classify("test")
    assert result.raw_response == ""


# ──────────────────────────────────────────────────────────────────
# Session created with Haiku, no tools, max_turns=1
# (now via _create_session, called per classify() invocation)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_created_with_haiku_model() -> None:
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        await classifier.classify("hi")

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


@pytest.mark.asyncio
async def test_session_created_with_no_tools() -> None:
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        await classifier.classify("hi")

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs["tools"] == []


@pytest.mark.asyncio
async def test_session_created_with_max_turns_one() -> None:
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        await classifier.classify("hi")

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs["max_turns"] == 1


# ──────────────────────────────────────────────────────────────────
# Lifecycle — start/stop are no-ops (Task 1.1)
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_does_not_delegate_to_session() -> None:
    """start() must not call session.start() — there is no persistent session."""
    classifier, mock_session = _make_classifier()
    await classifier.start()
    mock_session.start.assert_not_called()


@pytest.mark.asyncio
async def test_stop_does_not_delegate_to_session() -> None:
    """stop() must not call session.stop() — there is no persistent session."""
    classifier, mock_session = _make_classifier()
    await classifier.stop()
    mock_session.stop.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# model property
# ──────────────────────────────────────────────────────────────────


def test_model_returns_haiku() -> None:
    from archon.ai.classifier import Classifier, _CLASSIFIER_MODEL
    classifier, _ = _make_classifier()
    assert classifier.model == _CLASSIFIER_MODEL


# ──────────────────────────────────────────────────────────────────
# usage_stats with carried cost
# ──────────────────────────────────────────────────────────────────


def test_usage_stats_reflect_carried_cost() -> None:
    """usage_stats must return carried cost after it has been accumulated."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    classifier._carried_cost_usd = 0.05
    classifier._carried_cache_creation = 100

    stats = classifier.usage_stats
    assert stats is not None
    assert stats["total_cost_usd"] == 0.05
    assert stats["cumulative_cache_creation"] == 100


@pytest.mark.asyncio
async def test_classify_creates_fresh_session_per_call() -> None:
    """Each classify() call must create a new ClaudeSession instance."""
    from archon.ai.classifier import Classifier

    sessions_created: list[MagicMock] = []

    def _session_factory(**kwargs):
        mock = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))
        mock.usage_stats = {"total_cost_usd": 0.0, "cumulative_cache_creation": 0}
        sessions_created.append(mock)
        return mock

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", side_effect=_session_factory):
        await classifier.classify("first")
        await classifier.classify("second")

    assert len(sessions_created) == 2, "Expected 2 separate session instances"


@pytest.mark.asyncio
async def test_classify_accumulates_cost_across_calls() -> None:
    """usage_stats['total_cost_usd'] must sum costs from all classify() calls."""
    from archon.ai.classifier import Classifier

    def _session_factory(**kwargs):
        mock = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))
        mock.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 0}
        return mock

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", side_effect=_session_factory):
        await classifier.classify("first")
        await classifier.classify("second")

    stats = classifier.usage_stats
    assert stats is not None
    assert abs(stats["total_cost_usd"] - 0.02) < 1e-9


@pytest.mark.asyncio
async def test_usage_stats_survive_session_reset() -> None:
    """Cumulative cost must accumulate correctly across multiple classify() calls."""
    from archon.ai.classifier import Classifier

    call_count = 0

    def _session_factory(**kwargs):
        nonlocal call_count
        mock = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))
        if call_count == 0:
            mock.usage_stats = {"total_cost_usd": 0.05, "cumulative_cache_creation": 100}
        else:
            mock.usage_stats = {"total_cost_usd": 0.01, "cumulative_cache_creation": 10}
        call_count += 1
        return mock

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", side_effect=_session_factory):
        await classifier.classify("first")
        await classifier.classify("second")

    stats = classifier.usage_stats
    assert stats is not None
    assert abs(stats["total_cost_usd"] - 0.06) < 1e-9
    assert stats["cumulative_cache_creation"] == 110


@pytest.mark.asyncio
async def test_classify_stops_session_on_exception() -> None:
    """session.stop() must be called even when session.send() raises."""
    from archon.ai.classifier import Classifier

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock()
    mock_session.usage_stats = {"total_cost_usd": 0.0, "cumulative_cache_creation": 0}

    async def _crashing_send(prompt: str):
        raise RuntimeError("network error")
        yield  # makes this an async generator

    mock_session.send = _crashing_send

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    classifier._create_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]

    await classifier.classify("test")
    mock_session.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_accumulates_cost_even_when_stop_raises() -> None:
    """Cost must be accumulated even if session.stop() raises."""
    from archon.ai.classifier import Classifier

    mock_session = MagicMock()
    mock_session.start = AsyncMock()
    mock_session.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    mock_session.usage_stats = {"total_cost_usd": 0.03, "cumulative_cache_creation": 50}

    async def _send(prompt: str):
        yield Response(content='{"intent": "task", "confidence": 0.9}')

    mock_session.send = _send

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    classifier._create_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]

    result = await classifier.classify("test")
    # stop() raised but cost must still be accumulated
    assert classifier._carried_cost_usd == pytest.approx(0.03)
    assert classifier._carried_cache_creation == 50


@pytest.mark.asyncio
async def test_pipeline_start_stop_with_stateless_classifier() -> None:
    """Pipeline.start() / Pipeline.stop() must complete without error with stateless classifier."""
    from archon.ai.classifier import Classifier
    from archon.ai.pipeline import Pipeline

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    mock_decomposer = MagicMock()
    mock_decomposer.start = AsyncMock()
    mock_decomposer.stop = AsyncMock()

    with (
        patch("archon.ai.pipeline.Classifier", return_value=classifier),
        patch("archon.ai.pipeline.Decomposer", return_value=mock_decomposer),
    ):
        pipeline = Pipeline(cwd="/tmp")
        await pipeline.start()
        await pipeline.stop()

    # Classifier.start()/stop() are no-ops — no session calls; decomposer is invoked
    mock_decomposer.start.assert_awaited_once()
    mock_decomposer.stop.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# Task 2.2: search_url — Classifier stores _search_url internally
# ──────────────────────────────────────────────────────────────────


def test_classifier_uses_search_url_attribute() -> None:
    """Classifier must store search_url as _search_url internally."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier(search_url="http://localhost:6333")

    assert hasattr(classifier, "_search_url"), "_search_url must exist"
    assert classifier._search_url == "http://localhost:6333"


@pytest.mark.asyncio
async def test_create_session_passes_search_url() -> None:
    """_create_session must pass search_url= to ClaudeSession."""
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier(search_url="http://localhost:6333")
    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        await classifier.classify("test")

    call_kwargs = MockSession.call_args.kwargs
    assert call_kwargs.get("search_url") == "http://localhost:6333"


# ──────────────────────────────────────────────────────────────────
# Task 3.1: events field on ClassifierResult
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classifier_preserves_non_response_events() -> None:
    """Non-Response events (e.g. ThinkingResult) must be collected in result.events."""
    classifier, _ = _make_classifier(
        session_events=[
            ThinkingResult(content="thinking..."),
            Response(content='{"intent": "task", "confidence": 0.9}'),
        ],
    )
    result = await classifier.classify("test")
    assert result.events == [ThinkingResult(content="thinking...")]


@pytest.mark.asyncio
async def test_classifier_empty_events_on_response_only() -> None:
    """When session yields only a Response, result.events must be empty."""
    classifier, _ = _make_classifier(
        session_events=[Response(content='{"intent": "chat", "confidence": 0.8}')],
    )
    result = await classifier.classify("hi")
    assert result.events == []


def test_classifier_result_events_field_type() -> None:
    """ClassifierResult.events must default to an empty list (default_factory)."""
    from archon.ai.classifier import ClassifierResult
    from archon.ai.classification import Classification

    result = ClassifierResult(classification=Classification(intent="task", confidence=1.0))
    assert isinstance(result.events, list)
    assert result.events == []


@pytest.mark.asyncio
async def test_classifier_multiple_non_response_events_collected() -> None:
    """Multiple non-Response events must all be collected in order in result.events."""
    classifier, _ = _make_classifier(
        session_events=[
            ThinkingResult(content="t1"),
            ThinkingResult(content="t2"),
            Response(content='{"intent": "task", "confidence": 0.9}'),
        ],
    )
    result = await classifier.classify("test")
    assert result.events == [ThinkingResult(content="t1"), ThinkingResult(content="t2")]


@pytest.mark.asyncio
async def test_classifier_response_excluded_from_events() -> None:
    """Response event must never appear in result.events."""
    classifier, _ = _make_classifier(
        session_events=[Response(content='{"intent": "task", "confidence": 0.9}')],
    )
    result = await classifier.classify("test")
    assert result.events == []
    assert not any(isinstance(e, Response) for e in result.events)


# ──────────────────────────────────────────────────────────────────
# Task 3.3: classifier prompt content
# ──────────────────────────────────────────────────────────────────


def test_classifier_prompt_forbids_reasoning() -> None:
    """classifier.md must contain anti-reasoning directives."""
    import pathlib

    prompt_path = pathlib.Path(__file__).parents[2] / "archon" / "ai" / "prompts" / "classifier.md"
    content = prompt_path.read_text()
    assert "Do NOT evaluate" in content
    assert "ONLY classify it" in content
    assert "Do NOT respond to the content" in content


# ──────────────────────────────────────────────────────────────────
# Task 3.4: SDK regression guard — thinking={"type": "disabled"}
# ──────────────────────────────────────────────────────────────────


def test_sdk_supports_thinking_disabled_config() -> None:
    """ClaudeAgentOptions must accept thinking={'type': 'disabled'} without error.

    This is a regression guard: if the SDK removes or renames the thinking
    parameter, this test will fail and alert us before the classifier breaks
    at runtime.
    """
    from claude_agent_sdk.types import ClaudeAgentOptions

    opts = ClaudeAgentOptions(thinking={"type": "disabled"})
    assert opts.thinking is not None
    assert opts.thinking.get("type") == "disabled"  # type: ignore[union-attr]


# ──────────────────────────────────────────────────────────────────
# Task 3.5: Classifier session constructed with disable_thinking=True
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classifier_session_constructed_with_thinking_disabled() -> None:
    """classify() must construct ClaudeSession with disable_thinking=True."""
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        await classifier.classify("hi")

    MockSession.assert_called_once()
    _, kwargs = MockSession.call_args
    assert kwargs.get("disable_thinking") is True
