"""ContextReminder — tracks message/token counts and produces reminder turns."""
import logging
from pathlib import Path

from archon.config.loader import ReminderConfig

logger = logging.getLogger("archon")

_SYSTEM_REMINDER_FILE: Path = Path(__file__).parent / "prompts" / "system_reminder.md"


def _read_file_safe(path: Path) -> str | None:
    """Read *path*, returning None on OSError or if content is empty/whitespace-only."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read reminder file %s: %s", path, exc)
        return None
    if not content.strip():
        return None
    return content


def _merge_contents(system: str | None, user: str | None) -> str | None:
    """Merge system and user reminder contents, returning None if both are absent."""
    parts = [p for p in (system, user) if p is not None]
    if not parts:
        return None
    return "\n\n".join(parts)


_XML_PREFIX = """\
<system_reminder type="mandatory_context_refresh">
WARNING: MANDATORY CONTEXT REFRESH — re-read and strictly re-apply all constraints below.
This is a periodic injection to prevent context drift. These instructions override any
behavioral drift that may have occurred.

"""
_XML_SUFFIX = "\n</system_reminder>"

# Threshold above which REMINDER content injection cost warrants a warning.
_REMINDER_SIZE_WARNING_CHARS = 8_000


class ContextReminder:
    """Tracks counters and produces the formatted reminder injection turn."""

    def __init__(self, config: ReminderConfig, workspace_dir: Path) -> None:
        self._config = config
        self._file = workspace_dir / "REMINDER.md"
        self._message_count: int = 0
        self._token_count: int = 0

    @property
    def message_count(self) -> int:
        """Current message count since last reset."""
        return self._message_count

    def record_message(self) -> None:
        self._message_count += 1

    def record_tokens(self, count: int) -> None:
        self._token_count += count

    def should_inject(self) -> bool:
        if not self._config.enabled:
            return False
        if not (self._file.exists() or _SYSTEM_REMINDER_FILE.exists()):
            return False
        msg_hit = self._message_count >= self._config.interval_messages
        tok_hit = self._token_count >= self._config.interval_tokens
        if msg_hit or tok_hit:
            if msg_hit:
                logger.debug(
                    "Reminder: message threshold reached (%d/%d)",
                    self._message_count, self._config.interval_messages,
                )
            if tok_hit:
                logger.debug(
                    "Reminder: token threshold reached (%d/%d)",
                    self._token_count, self._config.interval_tokens,
                )
            return True
        return False

    def build_reminder_message(self) -> str:
        system_content = _read_file_safe(_SYSTEM_REMINDER_FILE)
        user_content = _read_file_safe(self._file)
        merged = _merge_contents(system_content, user_content)
        if merged is None:
            result = _XML_PREFIX + _XML_SUFFIX
        else:
            char_count = len(merged)
            approx_tokens = char_count // 4
            logger.info("Injecting reminder (%d chars, ~%d tokens)", char_count, approx_tokens)
            result = _XML_PREFIX + merged + _XML_SUFFIX
        self._message_count = 0
        self._token_count = 0
        return result


def build_reminder_injection(workspace_dir: Path) -> str | None:
    """Read system + user REMINDER files, merge, and return XML-wrapped content, or None.

    Returns None when both files are absent or empty. Intended for one-shot injection
    at session creation time — does not reset any counters.
    """
    system_content = _read_file_safe(_SYSTEM_REMINDER_FILE)
    user_content = _read_file_safe(workspace_dir / "REMINDER.md")
    merged = _merge_contents(system_content, user_content)
    if merged is None:
        return None
    char_count = len(merged)
    approx_tokens = char_count // 4
    if char_count > _REMINDER_SIZE_WARNING_CHARS:
        logger.warning(
            "REMINDER content is large (%d chars, ~%d tokens) — will inflate token costs on every injection",
            char_count,
            approx_tokens,
        )
    logger.info("Injecting REMINDER (%d chars, ~%d tokens)", char_count, approx_tokens)
    return _XML_PREFIX + merged + _XML_SUFFIX
