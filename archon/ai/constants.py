"""Shared constants for the AI module."""

# Pinned dated version for internal fast-model tasks (classifier, summarizer, compactor).
# The dated suffix locks behaviour; the alias form (claude-haiku-4-5) may silently upgrade.
DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"

# Canonical model registry: model ID → context window size (tokens).
# Each model entry is the single source of truth for both availability and context window.
# update_models.py keeps this dict current on every release.
# Fallback: 200_000 (safe for all current Claude models without an explicit entry).
DEFAULT_MODEL = "claude-sonnet-4-6"
AVAILABLE_MODELS: dict[str, int] = {
    "claude-3-haiku-20240307": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    "claude-opus-4-1-20250805": 200_000,
    "claude-opus-4-20250514": 200_000,
    "claude-opus-4-5-20251101": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-sonnet-4-20250514": 200_000,
    "claude-sonnet-4-5-20250929": 200_000,
    "claude-sonnet-4-6": 200_000,
}


def get_context_window(model: str | None, overrides: dict[str, int] | None = None) -> int:
    """Return the context window size for *model*.

    Lookup order: config overrides → AVAILABLE_MODELS → 200_000 default.
    """
    if overrides and model in overrides:
        return overrides[model]
    return AVAILABLE_MODELS.get(model or "", 200_000)

# Short aliases for /models command — lowercase keys, full model IDs as values.
# Aliases bypass available-list validation, so they work even for models not in AVAILABLE_MODELS.
# Update when Anthropic releases new model families.
MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
}
