"""Classification schema and parser for the multi-agent pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("archon")

_VALID_INTENTS = ("chat", "task")
_DEFAULT = None  # sentinel; built lazily to avoid mutable default


@dataclass(frozen=True, slots=True)
class Classification:
    """Structured output from the Classifier (Haiku) session."""

    intent: Literal["chat", "task"]
    confidence: float


def _default() -> Classification:
    return Classification(intent="task", confidence=0.0)


def parse_classification(raw: str) -> Classification:
    """Parse a JSON string into a Classification.

    On any failure (malformed JSON, missing/invalid fields) returns
    the default Classification(intent="task", confidence=0.0) and logs
    a warning.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("Classification parse failed: malformed JSON: %s", raw[:120])
        return _default()

    if not isinstance(data, dict):
        log.warning("Classification parse failed: expected object, got %s", type(data).__name__)
        return _default()

    intent = data.get("intent")
    confidence = data.get("confidence")

    if intent not in _VALID_INTENTS or confidence is None:
        parts = []
        if intent not in _VALID_INTENTS:
            parts.append(f"invalid intent={intent!r}")
        if confidence is None:
            parts.append("missing confidence")
        log.warning("Classification parse failed: %s", ", ".join(parts))
        return _default()

    confidence = max(0.0, min(1.0, float(confidence)))
    return Classification(intent=intent, confidence=confidence)
