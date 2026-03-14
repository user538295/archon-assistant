"""Shared constants for the AI module."""

# Pinned dated version for internal fast-model tasks (classifier, summarizer, compactor).
# The dated suffix locks behaviour; the alias form (claude-haiku-4-5) may silently upgrade.
DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"

# Canonical model list for the archon package.
# install.py duplicates these values (it can't import archon); keep both in sync.
# Update when Anthropic releases new model families.
DEFAULT_MODEL = "claude-sonnet-4-6"
AVAILABLE_MODELS = ["claude-sonnet-4-6", "claude-haiku-4-5"]
