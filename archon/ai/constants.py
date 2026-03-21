"""Shared constants for the AI module."""

# Approximation for claude-* models; adjust if using models with a different context window.
CONTEXT_WINDOW_TOKENS = 200_000

# Pinned dated version for internal fast-model tasks (classifier, summarizer, compactor).
# The dated suffix locks behaviour; the alias form (claude-haiku-4-5) may silently upgrade.
DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"

# Canonical model list for the archon package.
# install.py duplicates these values (it can't import archon); keep both in sync.
# Update when Anthropic releases new model families.
DEFAULT_MODEL = "claude-sonnet-4-6"
AVAILABLE_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5"]

# Short aliases for /models command — lowercase keys, full model IDs as values.
# Aliases bypass available-list validation, so they work even for models not in AVAILABLE_MODELS.
# Update when Anthropic releases new model families.
MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5",
}
