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
# Task 1.1: search_url NOT forwarded to ClaudeSession
# ──────────────────────────────────────────────────────────────────


def test_create_session_search_url_is_none() -> None:
    """_create_session() must not pass search_url kwarg to ClaudeSession."""
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        classifier._create_session()

    MockSession.assert_called_once()
    call_kwargs = MockSession.call_args.kwargs
    assert "search_url" not in call_kwargs


@pytest.mark.asyncio
async def test_create_session_does_not_forward_search_url() -> None:
    """Classifier constructed without search_url must not pass search_url to ClaudeSession."""
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()
    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        await classifier.classify("test")

    MockSession.assert_called_once()
    call_kwargs = MockSession.call_args.kwargs
    assert "search_url" not in call_kwargs


def test_classifier_init_rejects_search_url_kwarg() -> None:
    """Classifier.__init__() must reject search_url= as an unknown kwarg."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        with pytest.raises(TypeError, match="search_url"):
            Classifier(search_url="http://x")


def test_classifier_init_has_no_search_url_attribute() -> None:
    """Classifier.__init__() must not create a _search_url attribute."""
    from archon.ai.classifier import Classifier

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    assert not hasattr(classifier, "_search_url"), "_search_url must not exist after __init__"


def test_create_session_passes_only_expected_kwargs() -> None:
    """_create_session() must pass exactly the expected kwargs to ClaudeSession — no MCP-related extras."""
    from archon.ai.classifier import Classifier

    mock_session = _mock_session(Response(content='{"intent": "task", "confidence": 0.9}'))

    with patch("archon.ai.classifier.load_prompt", return_value="mock prompt"):
        classifier = Classifier()

    with patch("archon.ai.classifier.ClaudeSession", return_value=mock_session) as MockSession:
        classifier._create_session()

    call_kwargs = set(MockSession.call_args.kwargs.keys())
    expected = {"cwd", "model", "system_prompt", "tools", "max_turns", "disable_thinking"}
    assert call_kwargs == expected, f"Unexpected kwargs passed to ClaudeSession: {call_kwargs - expected}"


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
# Task 1.2: classifier system prompt content
# ──────────────────────────────────────────────────────────────────


def test_classifier_prompt_has_no_5_steps_line() -> None:
    """classifier.md must not contain any step-budget planning instruction."""
    import re
    from archon.ai.prompts import load_prompt

    prompt = load_prompt("classifier")
    # Catches "5 steps", "five steps", "5-step", "MUST start with thinking", etc.
    for pattern in [r"\b\d+\s*steps?\b", r"MUST start with thinking", r"plan the remaining"]:
        assert not re.search(pattern, prompt, re.IGNORECASE), f"Forbidden planning pattern found: {pattern!r}"


def test_classifier_prompt_has_fallback_rule() -> None:
    """classifier.md must contain a fallback rule referencing recent context for ambiguous messages."""
    from archon.ai.prompts import load_prompt

    # Reads real classifier.md — does NOT mock load_prompt.
    prompt = load_prompt("classifier")
    assert "ambiguous" in prompt
    assert "recent context" in prompt  # must reference injected context, not just mention ambiguity
    assert prompt.index("ambiguous") < prompt.index("If unsure")  # rule must precede the catch-all default


def test_classifier_prompt_preserves_core_directives() -> None:
    """classifier.md must retain all required directives after Task 1.2 edits."""
    from archon.ai.prompts import load_prompt

    # Reads real classifier.md — does NOT mock load_prompt.
    prompt = load_prompt("classifier")
    for required in ["Output ONLY", "no code fences", "If unsure", '"chat"', '"task"']:
        assert required in prompt, f"Required directive missing from classifier.md: {required!r}"


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


# ──────────────────────────────────────────────────────────────────
# Task 2.1: recent_context parameter
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_with_context_includes_context_block() -> None:
    """classify() with recent_context must prepend a labeled context block."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("do that", recent_context=["write tests", "ok done"])

    assert len(sent_prompts) == 1
    enriched = sent_prompts[0]
    assert "[Recent context" in enriched
    assert "last 2 user messages" in enriched
    assert "oldest first]" in enriched
    assert "1. write tests" in enriched
    assert "2. ok done" in enriched
    assert "\nCurrent message: do that" in enriched


@pytest.mark.asyncio
async def test_classify_context_header_n_matches_count() -> None:
    """Context block header must show the count of messages passed."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("next", recent_context=["a", "b", "c"])

    assert "last 3 user messages" in sent_prompts[0]


@pytest.mark.asyncio
async def test_classify_without_context_no_context_block() -> None:
    """classify() with recent_context=None must pass the original prompt unchanged."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("hello", recent_context=None)

    assert sent_prompts[0] == "hello"


@pytest.mark.asyncio
async def test_classify_context_empty_list_no_context_block() -> None:
    """classify() with recent_context=[] must pass the original prompt unchanged."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("hello", recent_context=[])

    assert sent_prompts[0] == "hello"


@pytest.mark.asyncio
async def test_classify_context_truncates_long_messages() -> None:
    """Context messages longer than 200 chars must be truncated to 200 chars."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    long_msg = "x" * 300
    await classifier.classify("check", recent_context=[long_msg])

    enriched = sent_prompts[0]
    # The truncated message (200 x's) must appear, not the full 300 x's
    assert "x" * 200 in enriched
    assert "x" * 201 not in enriched


@pytest.mark.asyncio
async def test_classify_context_oldest_first() -> None:
    """Context messages must appear in oldest-first order, numbered 1, 2, 3."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("go", recent_context=["a", "b", "c"])

    enriched = sent_prompts[0]
    pos_1a = enriched.index("1. a")
    pos_2b = enriched.index("2. b")
    pos_3c = enriched.index("3. c")
    assert pos_1a < pos_2b < pos_3c


@pytest.mark.asyncio
async def test_classify_context_single_message_singular_header() -> None:
    """Context block header uses singular 'message' when exactly one context message is passed."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("go", recent_context=["only one message"])

    assert "last 1 user message" in sent_prompts[0]
    assert "last 1 user messages" not in sent_prompts[0]


@pytest.mark.asyncio
async def test_classify_context_newlines_in_messages_sanitized() -> None:
    """Messages containing newlines must have them replaced with spaces to preserve numbered-list format."""
    classifier, mock_session = _make_classifier()
    sent_prompts: list[str] = []

    original_send = mock_session.send

    async def _capturing_send(prompt: str):
        sent_prompts.append(prompt)
        async for event in original_send(prompt):
            yield event

    mock_session.send = _capturing_send

    await classifier.classify("check", recent_context=["line1\nline2\nline3"])

    enriched = sent_prompts[0]
    assert "line1 line2 line3" in enriched
    assert "line1\nline2" not in enriched
