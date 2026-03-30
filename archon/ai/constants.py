"""Shared constants for the AI module."""

# Pinned dated version for internal fast-model tasks (classifier, summarizer, compactor).
# The dated suffix locks behaviour; the alias form (claude-haiku-4-5) may silently upgrade.
DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"

# Canonical model list for the archon package.
# Update when Anthropic releases new model families.
DEFAULT_MODEL = "claude-sonnet-4-6"
AVAILABLE_MODELS = ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]

# Per-model context window sizes (tokens).
# Maintained manually — the Anthropic API does not expose context window sizes.
# Fallback: 200_000 (safe for all current Claude models).
MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "claude-opus-4-6":           200_000,
    "claude-sonnet-4-6":         200_000,
    "claude-haiku-4-5":          200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


def get_context_window(model: str | None, overrides: dict[str, int] | None = None) -> int:
    """Return the context window size for *model*.

    Lookup order: config overrides → MODEL_CONTEXT_WINDOWS → 200_000 default.
    """
    if overrides and model in overrides:
        return overrides[model]
    return MODEL_CONTEXT_WINDOWS.get(model or "", 200_000)

# Short aliases for /models command — lowercase keys, full model IDs as values.
# Aliases bypass available-list validation, so they work even for models not in AVAILABLE_MODELS.
# Update when Anthropic releases new model families.
MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
}
