"""Classifier — wraps a per-call Haiku session to classify user prompts."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from archon.ai.classification import Classification, ClassificationResult, parse_classification
from archon.ai.claude_session import ClaudeSession
from archon.ai.constants import DEFAULT_FAST_MODEL
from archon.ai.event_mapper import Event, Response
from archon.ai.prompts import load_prompt

logger = logging.getLogger("archon")

_CLASSIFIER_MODEL = DEFAULT_FAST_MODEL


@dataclass
class ClassifierResult:
    """Rich result from classify() with timing and debug info."""

    classification: Classification
    raw_response: str = ""
    duration_s: float = 0.0
    parse_error: str = ""
    error: str = ""
    events: list[Event] = field(default_factory=list)


class Classifier:
    """Takes a prompt, returns a structured classification.

    Creates a fresh ClaudeSession (Haiku, no tools, max_turns=1) per classify() call.
    Non-Response events (ThinkingResult etc.) are collected and returned in `events`
    for debug-mode surfacing.
    """

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd
        self._prompt = load_prompt("classifier")
        # Accumulated cost/cache across calls.
        self._carried_cost_usd: float = 0.0
        self._carried_cache_creation: int = 0

    @property
    def model(self) -> str:
        return _CLASSIFIER_MODEL

    @property
    def usage_stats(self) -> "dict[str, Any] | None":
        if self._carried_cost_usd == 0.0 and self._carried_cache_creation == 0:
            return None
        return {
            "total_cost_usd": self._carried_cost_usd,
            "cumulative_cache_creation": self._carried_cache_creation,
        }

    def _create_session(self) -> ClaudeSession:
        """Create a fresh ClaudeSession for a single classify() call."""
        return ClaudeSession(
            cwd=self._cwd,
            model=_CLASSIFIER_MODEL,
            system_prompt=self._prompt,
            tools=[],
            max_turns=1,
            disable_thinking=True,
        )

    async def start(self) -> None:
        """No-op — no persistent session to start."""

    async def stop(self) -> None:
        """No-op — no persistent session to stop."""

    @staticmethod
    def _build_prompt(prompt: str, recent_context: list[str] | None) -> str:
        """Build enriched prompt with optional recent context block."""
        if not recent_context:
            return prompt
        n = len(recent_context)
        lines = [f"[Recent context — last {n} user message{'s' if n != 1 else ''}, oldest first]"]
        for i, msg in enumerate(recent_context, 1):
            clean = msg.replace("\n", " ").replace("\r", " ")
            lines.append(f"{i}. {clean[:200]}")
        lines.append(f"\nCurrent message: {prompt}")
        return "\n".join(lines)

    async def classify(self, prompt: str, recent_context: list[str] | None = None) -> ClassifierResult:
        """Classify a user prompt. Returns ClassifierResult with graceful fallback."""
        raw_response = ""
        error = ""
        result_events: list[Event] = []
        t0 = time.monotonic()

        enriched_prompt = self._build_prompt(prompt, recent_context)
        session = self._create_session()
        try:
            await session.start()
            try:
                async for event in session.send(enriched_prompt):
                    if isinstance(event, Response):
                        raw_response = event.content
                    else:
                        result_events.append(event)
            finally:
                try:
                    await session.stop()
                except Exception:
                    logger.warning("Classifier session stop failed", exc_info=True)
                self._carried_cost_usd += (session.usage_stats or {}).get("total_cost_usd", 0.0)
                self._carried_cache_creation += int(
                    (session.usage_stats or {}).get("cumulative_cache_creation", 0)
                )
        except Exception as exc:
            error = f"Classifier failed: {exc}"
            logger.error("Classifier failed — defaulting to task intent", exc_info=True)

        duration_s = round(time.monotonic() - t0, 1)

        result = parse_classification(raw_response)
        classification = result.classification
        parse_error = result.error or ""

        logger.info(
            "Classification: intent=%s confidence=%.2f duration=%.1fs",
            classification.intent, classification.confidence, duration_s,
        )

        return ClassifierResult(
            classification=classification,
            raw_response=raw_response,
            duration_s=duration_s,
            parse_error=parse_error,
            error=error,
            events=result_events,
        )
