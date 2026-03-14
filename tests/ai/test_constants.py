"""Tests for archon/ai/constants.py — shared model constant."""

import logging
from unittest.mock import MagicMock, patch

from archon.ai.constants import AVAILABLE_MODELS, DEFAULT_FAST_MODEL, DEFAULT_MODEL


# ──────────────────────────────────────────────────────────────────
# DEFAULT_FAST_MODEL is importable and has the expected value
# ──────────────────────────────────────────────────────────────────


def test_default_fast_model_is_string() -> None:
    assert isinstance(DEFAULT_FAST_MODEL, str)


def test_default_fast_model_value() -> None:
    assert DEFAULT_FAST_MODEL == "claude-haiku-4-5-20251001"


def test_default_model_value() -> None:
    assert DEFAULT_MODEL == "claude-sonnet-4-6"


def test_available_models_contains_default() -> None:
    assert DEFAULT_MODEL in AVAILABLE_MODELS




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


# ──────────────────────────────────────────────────────────────────
# No auto-add: internal models are NOT appended to config.models.available
# (Bug 09 regression tests — updated: auto-add removed entirely)
# ──────────────────────────────────────────────────────────────────


def test_classifier_does_not_mutate_available_when_model_missing(caplog) -> None:
    """Classifier must NOT append to config.models.available when fast model is absent."""
    available: list[str] = ["claude-sonnet-4-5"]
    with patch("archon.ai.classifier.ClaudeSession"), \
         patch("archon.ai.classifier.load_prompt", return_value="prompt"):
        from archon.ai.classifier import Classifier
        with caplog.at_level(logging.INFO, logger="archon"):
            Classifier()
    assert DEFAULT_FAST_MODEL not in available
    assert not any("added automatically" in r.message for r in caplog.records)


def test_classifier_does_not_log_model_registration(caplog) -> None:
    """Classifier must produce no 'added automatically' log regardless of available list."""
    with patch("archon.ai.classifier.ClaudeSession"), \
         patch("archon.ai.classifier.load_prompt", return_value="prompt"):
        from archon.ai.classifier import Classifier
        with caplog.at_level(logging.INFO, logger="archon"):
            Classifier()
    assert not any("added automatically" in r.message for r in caplog.records)


def test_decomposer_does_not_mutate_available_when_model_missing(caplog) -> None:
    """Decomposer must NOT append to config.models.available when fast model is absent."""
    available: list[str] = ["claude-sonnet-4-5"]
    with patch("archon.ai.decomposer.ClaudeSession"), \
         patch("archon.ai.decomposer.load_prompt", return_value="prompt"), \
         patch("archon.ai.decomposer.load_workspace_agents", return_value={}):
        from archon.ai.decomposer import Decomposer
        with caplog.at_level(logging.INFO, logger="archon"):
            Decomposer()
    assert DEFAULT_FAST_MODEL not in available
    assert not any("added automatically" in r.message for r in caplog.records)


def test_decomposer_does_not_log_model_registration(caplog) -> None:
    """Decomposer must produce no 'added automatically' log regardless of available list."""
    with patch("archon.ai.decomposer.ClaudeSession"), \
         patch("archon.ai.decomposer.load_prompt", return_value="prompt"), \
         patch("archon.ai.decomposer.load_workspace_agents", return_value={}):
        from archon.ai.decomposer import Decomposer
        with caplog.at_level(logging.INFO, logger="archon"):
            Decomposer()
    assert not any("added automatically" in r.message for r in caplog.records)


def test_history_compactor_does_not_mutate_available_when_model_missing(tmp_path, caplog) -> None:
    """HistoryCompactor must NOT append to config.models.available when fast model is absent."""
    available: list[str] = ["claude-sonnet-4-5"]
    from archon.ai.history_compactor import HistoryCompactor
    with caplog.at_level(logging.INFO, logger="archon"):
        HistoryCompactor(history_dir=str(tmp_path))
    assert DEFAULT_FAST_MODEL not in available
    assert not any("added automatically" in r.message for r in caplog.records)


def test_history_compactor_does_not_log_model_registration(tmp_path, caplog) -> None:
    """HistoryCompactor must produce no 'added automatically' log regardless of available list."""
    from archon.ai.history_compactor import HistoryCompactor
    with caplog.at_level(logging.INFO, logger="archon"):
        HistoryCompactor(history_dir=str(tmp_path))
    assert not any("added automatically" in r.message for r in caplog.records)
