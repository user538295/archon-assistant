"""Coordinates graceful daemon restarts with rate limiting."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger("archon")

_RATE_LIMIT_SECONDS = 60.0


class RestartCoordinator:
    """Schedules, cancels, and rate-limits daemon restarts."""

    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None
        self._delay: float = 0.0
        self._task: asyncio.Task[None] | None = None

    # -- Scheduling ----------------------------------------------------------

    def schedule(self, reason: str, delay_seconds: float = 5.0) -> str:
        """Schedule a restart after *delay_seconds*.

        Returns a confirmation string.
        Raises ``RuntimeError`` if a restart is already scheduled.
        """
        if self._task is not None and not self._task.done():
            raise RuntimeError("Restart already scheduled")

        self._reason = reason
        self._delay = delay_seconds
        self._task = asyncio.get_running_loop().create_task(self._countdown())
        logger.info("Restart scheduled in %.1fs: %s", delay_seconds, reason)
        return f"Restart scheduled in {delay_seconds}s: {reason}"

    async def _countdown(self) -> None:
        await asyncio.sleep(self._delay)
        self._event.set()

    async def wait(self) -> tuple[str, float]:
        """Block until the restart event fires. Returns *(reason, delay)*."""
        await self._event.wait()
        return self._reason or "", self._delay

    @property
    def is_scheduled(self) -> bool:
        return self._task is not None and not self._task.done()

    def cancel(self) -> None:
        """Cancel a pending restart."""
        if self._task is not None:
            if self._task.done():
                logger.warning("Restart already fired, clearing event")
            else:
                self._task.cancel()
                logger.info("Scheduled restart cancelled")
        self._event.clear()
        self._task = None
        self._reason = None

    # -- Cross-process rate limiting -----------------------------------------

    @staticmethod
    def check_restart_allowed(restart_file: Path) -> bool:
        """Return True if enough time has passed since the last restart."""
        if not restart_file.exists():
            return True
        try:
            ts = float(restart_file.read_text().strip())
        except (ValueError, OSError):
            return True
        return (time.time() - ts) >= _RATE_LIMIT_SECONDS

    @staticmethod
    def write_restart_timestamp(restart_file: Path) -> None:
        """Write current timestamp to *restart_file*."""
        restart_file.parent.mkdir(parents=True, exist_ok=True)
        restart_file.write_text(str(time.time()))
