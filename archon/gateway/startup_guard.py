"""Crash-loop guard for startup notifications.

Prevents Telegram flooding when launchd/systemd keeps restarting a crashing Archon
by tracking the last startup timestamp and suppressing notifications if restarts
happen too rapidly (< 30s apart).
"""
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("archon")

_CRASH_LOOP_THRESHOLD_SECONDS = 30
_DEFAULT_STAMP_FILE = Path.home() / ".archon" / ".last_startup"


async def should_send_startup_notification(
    stamp_file: Path = _DEFAULT_STAMP_FILE,
) -> bool:
    """Return True if enough time has passed since the last startup.

    Writes the current timestamp to *stamp_file* on every call.
    Returns False (skip notification) if the previous startup was < 30s ago.
    Returns True on first-ever start (file missing) or corrupted content.
    """
    now = datetime.now(timezone.utc)
    should_send = True

    try:
        content = stamp_file.read_text().strip()
        last_start = datetime.fromisoformat(content)
        elapsed = (now - last_start).total_seconds()
        if elapsed < 0:
            logger.warning("Clock jumped backward (%.1fs) — treating as normal start", elapsed)
        elif elapsed < _CRASH_LOOP_THRESHOLD_SECONDS:
            logger.warning(
                "Crash-loop guard: last startup was %.1fs ago (< %ds), "
                "suppressing startup notification",
                elapsed,
                _CRASH_LOOP_THRESHOLD_SECONDS,
            )
            should_send = False
    except FileNotFoundError:
        logger.info("First startup — no previous timestamp found")
    except (ValueError, TypeError, OSError) as exc:
        logger.warning("Could not read last startup timestamp: %s", exc)

    # Always write current timestamp, even when suppressing.
    try:
        stamp_file.parent.mkdir(parents=True, exist_ok=True)
        stamp_file.write_text(now.isoformat())
    except OSError as exc:
        logger.warning("Could not write startup timestamp: %s", exc)

    return should_send
