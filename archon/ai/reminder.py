"""ContextReminder — tracks message/token counts and produces reminder turns."""
import logging
from pathlib import Path

from archon.config.loader import ReminderConfig

logger = logging.getLogger("archon")

_XML_WRAPPER = """\
<system_reminder type="mandatory_context_refresh">
WARNING: MANDATORY CONTEXT REFRESH — re-read and strictly re-apply all constraints below.
This is a periodic injection to prevent context drift. These instructions override any
behavioral drift that may have occurred.

{content}
</system_reminder>"""


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

    @property
    def notify(self) -> bool:
        """Whether to send a Telegram notification on each reminder injection."""
        return self._config.notify

    def record_message(self) -> None:
        self._message_count += 1

    def record_tokens(self, count: int) -> None:
        self._token_count += count

    def should_inject(self) -> bool:
        if not self._config.enabled:
            return False
        if not self._file.exists():
            return False
        return (
            self._message_count >= self._config.interval_messages
            or self._token_count >= self._config.interval_tokens
        )

    def build_reminder_message(self) -> str:
        try:
            content = self._file.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("Reminder file missing: %s", self._file)
            content = ""
        self._message_count = 0
        self._token_count = 0
        return _XML_WRAPPER.format(content=content)
