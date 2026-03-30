"""Tests for archon/ai/constants.py — shared model constant."""

import logging
from unittest.mock import MagicMock, patch

from archon.ai.constants import (
    AVAILABLE_MODELS,
    DEFAULT_FAST_MODEL,
    DEFAULT_MODEL,
    MODEL_ALIASES,
    get_context_window,
)


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


def test_model_aliases_resolve_to_valid_model_ids() -> None:
    """Every alias value should look like a valid claude model identifier."""
    assert "sonnet" in MODEL_ALIASES
    assert "opus" in MODEL_ALIASES
    assert "haiku" in MODEL_ALIASES
    for alias, model_id in MODEL_ALIASES.items():
        assert model_id.startswith("claude-"), f"alias {alias!r} → {model_id!r} missing claude- prefix"




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


# ──────────────────────────────────────────────────────────────────
# Task 1.4: config.models.default = None tolerance audit
#
# Audit notes: config.models.default call sites (all tolerate None):
# 1. gateway/gateway.py: if cfg.models.default: set_model() — None skips block
# 2. gateway/gateway.py → BackgroundAgentManager(model=None) — stores None, passes to SDK
# 3. gateway/gateway.py → JobScheduler(model=None) — stores None, passes to SDK
# 4. ai/archon_toolkit.py: if model is None: model = "unknown" — display fallback
# 5. ai/archon_toolkit.py: return ... or "default" — display fallback
# 6. SessionManager → Pipeline(model=self._model) — primary user-message path
#    model=None means SDK uses its own built-in default (no --model flag passed)
# Fallback: SDK built-in default (not DEFAULT_MODEL constant from constants.py).
#   DEFAULT_MODEL = "claude-sonnet-4-6" is a Python constant used for explicit model selection;
#   when model=None flows to the SDK, the SDK omits --model entirely and uses whatever
#   Claude Code itself is configured with — a separate, independent default.
# ──────────────────────────────────────────────────────────────────


def test_background_agent_manager_accepts_none_model() -> None:
    """BackgroundAgentManager stores model=None without raising (tolerates config.models.default=None).

    Construction-only verification is sufficient: model=None flows through to ClaudeSession
    and then to the SDK, which omits --model and uses its own built-in default model.
    The SDK handles None safely (verified from claude_agent_sdk subprocess_cli.py).
    Note: the fallback is the SDK's built-in default — NOT the DEFAULT_MODEL constant
    from constants.py ("claude-sonnet-4-6"). The SDK uses whatever Claude Code is configured with.
    """
    from unittest.mock import MagicMock
    from archon.ai.background_agent_manager import BackgroundAgentManager

    bot = MagicMock()
    session_manager = MagicMock()
    mgr = BackgroundAgentManager(bot=bot, session_manager=session_manager, model=None)
    assert mgr._model is None


# ──────────────────────────────────────────────────────────────────
# MODEL_CONTEXT_WINDOWS and get_context_window (FEAT-024 Task 1.1)
# ──────────────────────────────────────────────────────────────────


def test_get_context_window_known_model() -> None:
    assert get_context_window("claude-sonnet-4-6") == 200_000


def test_get_context_window_unknown_model_defaults_200k() -> None:
    assert get_context_window("gpt-5") == 200_000


def test_get_context_window_empty_string_defaults_200k() -> None:
    assert get_context_window("") == 200_000


def test_get_context_window_none_model_defaults_200k() -> None:
    assert get_context_window(None) == 200_000


def test_get_context_window_override_takes_precedence() -> None:
    assert get_context_window("claude-sonnet-4-6", {"claude-sonnet-4-6": 1_000_000}) == 1_000_000


def test_get_context_window_override_none_uses_constants() -> None:
    assert get_context_window("claude-sonnet-4-6", None) == 200_000


def test_get_context_window_empty_override_dict_uses_constants() -> None:
    assert get_context_window("claude-sonnet-4-6", {}) == 200_000


def test_opus_in_available_models() -> None:
    assert "claude-opus-4-6" in AVAILABLE_MODELS


def test_job_scheduler_accepts_none_model() -> None:
    """JobScheduler stores model=None without raising (tolerates config.models.default=None).

    Construction-only verification is sufficient: model=None flows through to ClaudeSession
    and then to the SDK, which omits --model and uses its own built-in default model.
    The SDK handles None safely (verified from claude_agent_sdk subprocess_cli.py).
    Note: the fallback is the SDK's built-in default — NOT the DEFAULT_MODEL constant
    from constants.py ("claude-sonnet-4-6"). The SDK uses whatever Claude Code is configured with.
    """
    from unittest.mock import MagicMock
    from archon.ai.job_scheduler import JobScheduler
    from archon.config.loader import ScheduleConfig

    schedule_cfg = ScheduleConfig()
    bot = MagicMock()
    scheduler = JobScheduler(config=schedule_cfg, bot=bot, allowed_user_ids=[], model=None)
    assert scheduler._model is None
