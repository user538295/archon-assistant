"""Live tests — Classifier (Haiku) model behaviour with the real Claude SDK.

These tests call the REAL claude-haiku model via ClaudeSession and verify
that the Classifier produces calibrated, parseable classification output.

CURRENT STATUS:
  BUG-2 (parse failure) is fixed: parse_classification() now handles markdown
      code fences via extract_json_object(), so confidence=0.0 no longer occurs
      from a parse failure alone. BUG-2 xfail markers have been removed.

  BUG-1 (raw format) is still informational: the model may still wrap JSON in
      markdown fences, but this no longer causes a failure — kept as xfail(strict=False).

Real messages used are verbatim from history log 2026-03-01.md.

Run:  uv run pytest -m live tests/ai/test_classifier_live.py -v
"""

import asyncio
import json
import shutil

import pytest

from archon.ai.classification import parse_classification
from archon.ai.classifier import Classifier
from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response
from archon.ai.prompts import load_prompt

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 30.0


# ──────────────────────────────────────────────────────────────────
# Real messages verbatim from history log 2026-03-01.md
# All were classified as {"intent": "task", "confidence": 0.0}.
# The expected minimum confidence is what a working classifier SHOULD return.
# ──────────────────────────────────────────────────────────────────

# (user_message, expected_intent, expected_min_confidence)
_REAL_TASK_MESSAGES = [
    (
        # 06:44:12 UTC — multi-module analysis + document creation
        # 4 modules mentioned, "save result into md file" = action on file system
        (
            "Make a comprehensive plan how to refactor the pipeline, classifier, decomposer, "
            "gateway to have clean code. Save the result into md file. The plan also contains "
            "a task list as well. You must be clear and easy to understand in the documentation "
            "even for a medior developer can understand. Be very precise, the plan contains clear "
            "and small steps to be able to easy to follow the changes. Be very very precise and accurate."
        ),
        "task",
        0.8,  # unambiguous action request: refactor + save file
    ),
    (
        # 07:11:30 UTC — investigate + read external resource
        "Why didn't you spawn an agent for the previous task? Investigate it and check history log too",
        "task",
        0.6,  # investigation/analysis request
    ),
    (
        # 07:30 UTC — file creation + testing request
        (
            "The tests for the classifier and decomposer is bad. They don't handle these issues. "
            "I want live tests for these. Use real examples from the log. Verify the bug via tests "
            "but don't fix them yet. Give objective thresholds examples here. Measurable signals."
        ),
        "task",
        0.8,  # explicit "I want tests" + "use examples" = file creation task
    ),
]

# (user_message, expected_intent, expected_min_confidence)
_REAL_CHAT_MESSAGES = [
    ("hello", "chat", 0.7),
    ("thanks, that's great!", "chat", 0.7),
    ("what time is it?", "chat", 0.6),
    ("how are you?", "chat", 0.7),
]


# ──────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────


async def _call_classifier(prompt: str) -> str:
    """Call the real Classifier model and return the raw Response content string.

    Returns empty string if no Response event was received (e.g. model failed).
    """
    session = ClaudeSession(
        model=_CLASSIFIER_MODEL,
        system_prompt=load_prompt("classifier"),
    )
    await session.start()
    raw = ""
    try:
        async with asyncio.timeout(_TIMEOUT):
            async for event in session.send(prompt):
                if isinstance(event, Response):
                    raw = event.content
    finally:
        await session.stop()
    return raw


# ──────────────────────────────────────────────────────────────────
# BUG-1: Raw response format
# These tests verify the Classifier output is raw JSON, not markdown-wrapped.
# Expected to FAIL if the Haiku model wraps its answer in code fences.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.xfail(reason="BUG-1: Haiku may wrap JSON in markdown fences", strict=False)
@pytest.mark.parametrize("prompt,_intent,_min_confidence", _REAL_TASK_MESSAGES)
async def test_classifier_raw_response_contains_no_markdown_fences(
    prompt: str,
    _intent: str,
    _min_confidence: float,
) -> None:
    """BUG-1: Raw Classifier response must be pure JSON — no ```json ... ``` wrapping.

    Wrapping causes json.loads() to raise JSONDecodeError, which causes
    parse_classification() to silently return the default (confidence=0.0).
    This test will FAIL if the model adds markdown fences to its output.

    Threshold: zero occurrences of triple-backtick in the raw response.
    """
    raw = await _call_classifier(prompt)

    assert raw, f"Classifier returned an empty response for prompt: {prompt[:80]!r}"
    assert "```" not in raw, (
        f"BUG-1 CONFIRMED: Classifier wrapped its JSON in markdown code fences.\n"
        f"parse_classification() cannot parse this and silently returns confidence=0.0.\n"
        f"Raw response (first 300 chars): {raw[:300]!r}"
    )


@pytest.mark.xfail(reason="BUG-1: Haiku may return non-JSON responses", strict=False)
@pytest.mark.parametrize("prompt,_intent,_min_confidence", _REAL_TASK_MESSAGES)
async def test_classifier_raw_response_is_directly_json_parseable(
    prompt: str,
    _intent: str,
    _min_confidence: float,
) -> None:
    """BUG-1: json.loads() must succeed on the raw Classifier response without preprocessing.

    If json.loads() raises, parse_classification() falls back to (task, 0.0),
    which is indistinguishable from a genuine 0.0 confidence result.

    Threshold: json.loads(raw) must not raise JSONDecodeError.
    """
    raw = await _call_classifier(prompt)

    assert raw, f"Empty response for: {prompt[:80]!r}"
    try:
        json.loads(raw)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"BUG-1 CONFIRMED: Classifier response is not valid JSON.\n"
            f"json.loads() raised: {exc}\n"
            f"Raw response: {raw[:300]!r}\n"
            f"NOTE: This causes parse_classification() to silently return confidence=0.0."
        )


# ──────────────────────────────────────────────────────────────────
# BUG-2: Confidence calibration
# These tests verify the Classifier returns non-zero, calibrated confidence.
# Expected to FAIL if BUG-1 is present (parse failure → confidence=0.0).
# Also fails if the model genuinely returns 0.0 without a parse failure.
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("prompt,expected_intent,min_confidence", _REAL_TASK_MESSAGES)
async def test_classifier_task_message_confidence_above_threshold(
    prompt: str,
    expected_intent: str,
    min_confidence: float,
) -> None:
    """BUG-2: Classifier must return confidence ≥ threshold for unambiguous task messages.

    All three messages from the 2026-03-01 log are unambiguously task-oriented.
    A working classifier should express high confidence.

    Measurable thresholds per message:
      - "Make a comprehensive plan to refactor..." → task, confidence ≥ 0.8
        (4 named modules + "save to file" = unambiguous multi-step task)
      - "Investigate it and check history log" → task, confidence ≥ 0.6
        (explicit investigation action)
      - "I want live tests ... Use real examples" → task, confidence ≥ 0.8
        (explicit creation request with artifacts)
    """
    raw = await _call_classifier(prompt)
    result = parse_classification(raw)
    classification = result.classification

    assert classification.intent == expected_intent, (
        f"Wrong intent: expected {expected_intent!r}, got {classification.intent!r}\n"
        f"Raw response: {raw[:200]!r}"
    )
    assert classification.confidence >= min_confidence, (
        f"BUG-2 CONFIRMED: Classifier returned confidence={classification.confidence} "
        f"for a clearly task-oriented message.\n"
        f"Expected: confidence ≥ {min_confidence}\n"
        f"Prompt: {prompt[:120]!r}\n"
        f"Raw response: {raw[:200]!r}\n"
        f"NOTE: confidence=0.0 almost always indicates a silent parse failure (BUG-1)."
    )


@pytest.mark.parametrize("prompt,expected_intent,min_confidence", _REAL_CHAT_MESSAGES)
async def test_classifier_chat_message_confidence_above_threshold(
    prompt: str,
    expected_intent: str,
    min_confidence: float,
) -> None:
    """Classifier must return correct intent with confidence ≥ threshold for chat messages.

    Measurable thresholds:
      - "hello"              → chat, confidence ≥ 0.7
      - "thanks, that's great!" → chat, confidence ≥ 0.7
      - "what time is it?"  → chat, confidence ≥ 0.6
      - "how are you?"      → chat, confidence ≥ 0.7
    """
    raw = await _call_classifier(prompt)
    result = parse_classification(raw)
    classification = result.classification

    assert classification.intent == expected_intent, (
        f"Wrong intent for chat message {prompt!r}: "
        f"expected {expected_intent!r}, got {classification.intent!r}\n"
        f"Raw response: {raw[:200]!r}"
    )
    assert classification.confidence >= min_confidence, (
        f"Confidence too low for chat message {prompt!r}: "
        f"got {classification.confidence}, expected ≥ {min_confidence}\n"
        f"Raw response: {raw[:200]!r}"
    )


@pytest.mark.xfail(reason="BUG-1: markdown fences cause JSONDecodeError", strict=False)
async def test_classifier_confidence_is_never_zero_for_deterministic_input() -> None:
    """BUG-2: confidence=0.0 must not appear in a successful Classifier call.

    0.0 is ONLY produced by parse_classification()'s fallback path — it is
    the default when JSON parsing fails or required fields are missing.
    A model that answers at all should be able to express some confidence.

    Measurable threshold: confidence > 0.0 for any response where intent
    is correctly parsed (i.e., json.loads() did not raise).

    Uses "hello" — the simplest, most unambiguous chat message possible.
    """
    raw = await _call_classifier("hello")

    # Only check confidence if the response parsed at all
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        pytest.fail(
            f"Classifier response is not valid JSON (BUG-1 likely): {raw[:200]!r}"
        )

    confidence = data.get("confidence", None)
    assert confidence is not None, f"'confidence' field missing from response: {data}"
    assert confidence > 0.0, (
        f"BUG-2 CONFIRMED: Classifier returned confidence=0.0 for 'hello'.\n"
        f"This is only valid as a fallback default — a working model must express "
        f"some confidence even for simple inputs.\n"
        f"Full response: {raw!r}"
    )


# ──────────────────────────────────────────────────────────────────
# FEAT-036 — Context injection live test
# ──────────────────────────────────────────────────────────────────


async def test_classifier_live_context_injection_disambiguates() -> None:
    """'continue' with task context is classified as 'task' with no parse error.

    An ambiguous follow-up message ('continue') that is unclassifiable on its
    own should be correctly resolved to 'task' intent when recent context
    (a prior task-oriented message) is injected.
    """
    classifier = Classifier()
    await classifier.start()
    try:
        result = await classifier.classify(
            "continue",
            recent_context=["write tests for the pipeline"],
        )
    finally:
        await classifier.stop()

    assert result.parse_error == "", (
        f"Parse error occurred: {result.parse_error!r}\n"
        f"Raw response: {result.raw_response!r}"
    )
    assert result.classification.intent == "task", (
        f"Expected 'task' intent for 'continue' with task context, "
        f"got {result.classification.intent!r} (confidence={result.classification.confidence})\n"
        f"Raw response: {result.raw_response!r}"
    )
