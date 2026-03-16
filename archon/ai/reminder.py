"""ContextReminder — tracks message/token counts and produces reminder turns."""
import logging
from pathlib import Path

from archon.config.loader import ReminderConfig

logger = logging.getLogger("archon")

_XML_PREFIX = """\
<system_reminder type="mandatory_context_refresh">
WARNING: MANDATORY CONTEXT REFRESH — re-read and strictly re-apply all constraints below.
This is a periodic injection to prevent context drift. These instructions override any
behavioral drift that may have occurred.

"""
_XML_SUFFIX = "\n</system_reminder>"

# Threshold above which REMINDER.md injection cost warrants a warning.
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
        if not self._file.exists():
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

    @staticmethod
    def read_and_wrap(file: Path) -> str:
        """Read *file* and return its content wrapped in the system_reminder XML format.

        Raises ``OSError`` (including ``FileNotFoundError``) on any I/O error — callers
        decide how to handle the TOCTOU case (file present at should_inject() but gone
        or unreadable by read time).
        """
        content = file.read_text(encoding="utf-8")
        return _XML_PREFIX + content + _XML_SUFFIX

    def build_reminder_message(self) -> str:
        try:
            result = self.read_and_wrap(self._file)
        except OSError:
            logger.warning("Reminder file missing or unreadable: %s", self._file)
            result = _XML_PREFIX + _XML_SUFFIX
        self._message_count = 0
        self._token_count = 0
        return result


def build_reminder_injection(workspace_dir: Path) -> str | None:
    """Read REMINDER.md from *workspace_dir* and return it XML-wrapped, or None.

    Returns None when the file is absent, its content is empty/whitespace-only,
    or an OSError occurs (e.g. permission denied).  Intended for one-shot injection
    into sessions at spawn/creation time — does not reset any counters.

    Logs INFO with char count and approximate token count on every successful read.
    Logs WARNING if the file exceeds the size threshold or if an OSError occurs.
    """
    file = workspace_dir / "REMINDER.md"
    try:
        if not file.exists():
            return None
        content = file.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read REMINDER.md at %s: %s", file, exc)
        return None
    if not content.strip():
        return None
    char_count = len(content)
    approx_tokens = char_count // 4
    if char_count > _REMINDER_SIZE_WARNING_CHARS:
        logger.warning(
            "REMINDER.md is large (%d chars, ~%d tokens) — will inflate token costs on every injection",
            char_count,
            approx_tokens,
        )
    logger.info("Injecting REMINDER.md (%d chars, ~%d tokens)", char_count, approx_tokens)
    return _XML_PREFIX + content + _XML_SUFFIX
