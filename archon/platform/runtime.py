"""PlatformRuntime abstract base class with shared POSIX logic."""
from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Callable

from archon.platform._run_mixin import RunMixin

logger = logging.getLogger("archon")


class PlatformRuntime(RunMixin, ABC):
    """ABC for platform-specific runtime operations.

    Concrete methods (shared POSIX logic):
        - register_signals: SIGTERM/SIGINT handlers with idempotent guard
        - process_uptime: ps-based elapsed time query

    Abstract methods (differ per platform):
        - restart_process: OS-level process restart
        - find_binary: binary discovery with platform-specific search paths
    """

    def __init__(self) -> None:
        super().__init__()
        self._shutdown_task: asyncio.Task[None] | None = None

    def register_signals(
        self,
        loop: Any,
        shutdown_callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Register SIGTERM and SIGINT with a double-signal guard.

        Re-triggers only if the previous shutdown task completed (or never started).
        Matches gateway.py's `_shutdown_task is None or _shutdown_task.done()` pattern.
        """

        def _handler() -> None:
            if self._shutdown_task is not None and not self._shutdown_task.done():
                return
            self._shutdown_task = loop.create_task(shutdown_callback())

        loop.add_signal_handler(signal.SIGTERM, _handler)
        logger.debug("Registered SIGTERM handler")
        loop.add_signal_handler(signal.SIGINT, _handler)
        logger.debug("Registered SIGINT handler")

    def process_uptime(self, pid: int) -> str | None:
        """Get process uptime string via ps. Returns None on failure."""
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "etime="],
                capture_output=True, text=True,
            )
            val = result.stdout.strip()
            return val if val else None
        except Exception:
            return None

    @abstractmethod
    def restart_process(self) -> None:
        """Restart the current process via os.execv."""

    @abstractmethod
    def find_binary(
        self, name: str, extra_paths: list[Path] | None = None
    ) -> Path | None:
        """Find a binary by name using platform-specific search paths."""
