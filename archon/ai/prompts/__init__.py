"""Prompt file loader for the multi-agent pipeline."""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Load a prompt file by name (without extension).

    Raises FileNotFoundError if the prompt file does not exist.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Required prompt '{name}.md' not found in {_PROMPTS_DIR}"
        ) from exc
