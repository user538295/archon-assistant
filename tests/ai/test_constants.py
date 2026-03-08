"""Tests for archon/ai/constants.py — shared model constant."""

from archon.ai.constants import DEFAULT_FAST_MODEL


# ──────────────────────────────────────────────────────────────────
# DEFAULT_FAST_MODEL is importable and has the expected value
# ──────────────────────────────────────────────────────────────────


def test_default_fast_model_is_string() -> None:
    assert isinstance(DEFAULT_FAST_MODEL, str)


def test_default_fast_model_value() -> None:
    assert DEFAULT_FAST_MODEL == "claude-haiku-4-5-20251001"


# ──────────────────────────────────────────────────────────────────
# All three modules import from the shared constant
# ──────────────────────────────────────────────────────────────────


def test_classifier_uses_shared_constant() -> None:
    from archon.ai.classifier import _CLASSIFIER_MODEL
    assert _CLASSIFIER_MODEL is DEFAULT_FAST_MODEL


def test_decomposer_uses_shared_constant() -> None:
    from archon.ai.decomposer import _SUMMARIZER_MODEL
    assert _SUMMARIZER_MODEL is DEFAULT_FAST_MODEL


def test_history_compactor_uses_shared_constant() -> None:
    from archon.ai.history_compactor import _HAIKU_MODEL
    assert _HAIKU_MODEL is DEFAULT_FAST_MODEL
