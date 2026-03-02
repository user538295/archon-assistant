"""Classification schema and parser for the multi-agent pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("archon")

_VALID_INTENTS = ("chat", "task")


@dataclass(frozen=True, slots=True)
class Classification:
    """Structured output from the Classifier (Haiku) session."""

    intent: Literal["chat", "task"]
    confidence: float
    estimated_tools: int = 0


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Result of parse_classification: the parsed Classification plus any error."""

    classification: Classification
    error: str | None = None


def _default() -> Classification:
    return Classification(intent="task", confidence=0.0)


def extract_json_object(text: str) -> str | None:
    """Try to find a JSON object ``{...}`` in mixed text.

    Handles markdown fences (```json ... ```) and preamble/trailing prose.
    Returns the extracted JSON string or None if no object found.
    """
    # Strip markdown fences first
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove opening fence (with optional language tag) and closing fence
        lines = stripped.splitlines()
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # drop closing fence
        stripped = "\n".join(lines).strip()

    # Try to find a JSON object by locating the first '{' and matching '}'
    start = stripped.find("{")
    if start < 0:
        return None
    # Walk forward tracking brace depth
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(stripped)):
        ch = stripped[i]
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            if in_string:
                escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : i + 1]
    return None


def parse_classification(raw: str) -> ClassificationResult:
    """Parse a JSON string into a Classification.

    Tries direct ``json.loads`` first.  On failure, attempts to extract a
    JSON object from mixed text (markdown fences, preamble prose, etc.).
    On any failure (malformed JSON, missing/invalid fields) returns
    the default Classification(intent="task", confidence=0.0) and logs
    a warning.

    Returns a ``ClassificationResult`` with the parsed classification and
    an optional error message (``None`` on success).
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Try to extract JSON object from mixed text
        extracted = extract_json_object(raw)
        if extracted is None:
            error = "no JSON object found in response"
            log.warning("Classification parse failed: %s", error)
            return ClassificationResult(_default(), error=error)
        try:
            data = json.loads(extracted)
        except (json.JSONDecodeError, TypeError):
            error = "malformed JSON in response"
            log.warning("Classification parse failed: %s", error)
            return ClassificationResult(_default(), error=error)

    if not isinstance(data, dict):
        error = f"expected object, got {type(data).__name__}"
        log.warning("Classification parse failed: %s", error)
        return ClassificationResult(_default(), error=error)

    intent = data.get("intent")
    confidence = data.get("confidence")

    if intent not in _VALID_INTENTS or confidence is None:
        parts = []
        if intent not in _VALID_INTENTS:
            parts.append(f"invalid intent={intent!r}")
        if confidence is None:
            parts.append("missing confidence")
        error = ", ".join(parts)
        log.warning("Classification parse failed: %s", error)
        return ClassificationResult(_default(), error=error)

    confidence = max(0.0, min(1.0, float(confidence)))

    raw_tools = data.get("estimated_tools", 0)
    try:
        estimated_tools = max(0, int(raw_tools))
    except (TypeError, ValueError):
        estimated_tools = 0

    return ClassificationResult(
        Classification(intent=intent, confidence=confidence, estimated_tools=estimated_tools)
    )
