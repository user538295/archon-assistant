"""Classifier — wraps a Haiku session to classify user prompts."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from archon.ai.classification import Classification, ClassificationResult, parse_classification
from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response
from archon.ai.prompts import load_prompt

logger = logging.getLogger("archon")

_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"


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
        prompt = load_prompt("classifier")
        self._session = ClaudeSession(
            cwd=cwd,
            model=_CLASSIFIER_MODEL,
            system_prompt=prompt,
            qmd_url=qmd_url,
            tools=[],
            max_turns=1,
        )

    @property
    def model(self) -> str:
        return _CLASSIFIER_MODEL

    async def start(self) -> None:
        await self._session.start()

    async def stop(self) -> None:
        await self._session.stop()

    async def classify(self, prompt: str) -> ClassifierResult:
        """Classify a user prompt. Returns ClassifierResult with graceful fallback."""
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
