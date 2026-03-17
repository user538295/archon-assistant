"""Classifier — wraps a Haiku session to classify user prompts."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from archon.ai.classification import Classification, ClassificationResult, parse_classification
from archon.ai.claude_session import ClaudeSession
from archon.ai.constants import DEFAULT_FAST_MODEL
from archon.ai.event_mapper import Response
from archon.ai.prompts import load_prompt

logger = logging.getLogger("archon")

_CLASSIFIER_MODEL = DEFAULT_FAST_MODEL
_CLASSIFIER_RESET_THRESHOLD = 50


@dataclass
class ClassifierResult:
    """Rich result from classify() with timing and debug info."""

    classification: Classification
    raw_response: str = ""
    duration_s: float = 0.0
    parse_error: str = ""
    error: str = ""


class Classifier:
    """Takes a prompt, returns a structured classification.

    Wraps ClaudeSession (Haiku, no tools, max_turns=1).
    No events yielded — just returns data. Pipeline creates events.
    """

    def __init__(self, cwd: str | None = None, qmd_url: str | None = None) -> None:
        self._cwd = cwd
        self._qmd_url = qmd_url
        prompt = load_prompt("classifier")
        self._prompt = prompt
        self._session = ClaudeSession(
            cwd=cwd,
            model=_CLASSIFIER_MODEL,
            system_prompt=prompt,
            qmd_url=qmd_url,
            tools=[],
            max_turns=1,
        )
        self._classify_call_count = 0
        # Accumulated cost/cache across session resets so usage_stats never loses history.
        self._carried_cost_usd: float = 0.0
        self._carried_cache_creation: int = 0

    @property
    def model(self) -> str:
        return _CLASSIFIER_MODEL

    @property
    def usage_stats(self) -> "dict[str, Any] | None":
        stats = self._session.usage_stats
        if stats is None and self._carried_cost_usd == 0.0 and self._carried_cache_creation == 0:
            return None
        base = stats or {}
        return {
            **base,
            "total_cost_usd": base.get("total_cost_usd", 0.0) + self._carried_cost_usd,
            "cumulative_cache_creation": (
                base.get("cumulative_cache_creation", 0) + self._carried_cache_creation
            ),
        }

    async def start(self) -> None:
        await self._session.start()

    async def stop(self) -> None:
        await self._session.stop()

    async def _reset_session(self) -> None:
        """Stop the current session, carry over accumulated stats, start a fresh one.

        Order: stop old session FIRST, then create and start the new one.
        """
        old_stats = self._session.usage_stats or {}
        self._carried_cost_usd += old_stats.get("total_cost_usd", 0.0)
        self._carried_cache_creation += old_stats.get("cumulative_cache_creation", 0)

        # Stop old session before creating the new one
        try:
            await self._session.stop()
        except Exception:
            logger.warning("Classifier: old session stop failed during reset", exc_info=True)

        self._session = ClaudeSession(
            cwd=self._cwd,
            model=_CLASSIFIER_MODEL,
            system_prompt=self._prompt,
            qmd_url=self._qmd_url,
            tools=[],
            max_turns=1,
        )
        await self._session.start()
        logger.debug("Classifier session recycled after %d calls", _CLASSIFIER_RESET_THRESHOLD)

    async def classify(self, prompt: str) -> ClassifierResult:
        """Classify a user prompt. Returns ClassifierResult with graceful fallback."""
        self._classify_call_count += 1
        if self._classify_call_count >= _CLASSIFIER_RESET_THRESHOLD:
            await self._reset_session()
            self._classify_call_count = 0

        raw_response = ""
        error = ""
        t0 = time.monotonic()

        try:
            async for event in self._session.send(prompt):
                if isinstance(event, Response):
                    raw_response = event.content
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
        )
