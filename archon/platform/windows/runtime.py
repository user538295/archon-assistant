"""Windows runtime — signal handling via signal.signal (no POSIX add_signal_handler)."""
from __future__ import annotations

import os
import shutil
import signal
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Callable

from archon.platform.runtime import PlatformRuntime


class WindowsRuntime(PlatformRuntime):
    """Windows runtime — uses signal.signal instead of loop.add_signal_handler."""

    def register_signals(
        self,
        loop: Any,
        shutdown_callback: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        """Register shutdown signals via signal.signal (Windows compatible).

        Windows doesn't support loop.add_signal_handler, so we use signal.signal
        and bridge into the asyncio loop via call_soon_threadsafe.
        """

        def _handler(signum: int, frame: Any) -> None:
            if self._shutdown_task is not None and not self._shutdown_task.done():
                return

            def _schedule() -> None:
                self._shutdown_task = loop.create_task(shutdown_callback())

            loop.call_soon_threadsafe(_schedule)

        signal.signal(signal.SIGINT, _handler)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, _handler)  # type: ignore[attr-defined,unused-ignore]

    def restart_process(self) -> None:
        """Restart via os.execv."""
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def find_binary(
        self, name: str, extra_paths: list[Path] | None = None
    ) -> Path | None:
        """Find a binary via shutil.which only (no platform-specific paths)."""
        if not name:
            return None

        found = shutil.which(name)
        if found:
            return Path(found)

        for p in extra_paths or ():
            if p.is_file() and os.access(p, os.X_OK):
                return p

        return None

    def process_uptime(self, pid: int) -> str | None:
        """Not supported on Windows."""
        return None
