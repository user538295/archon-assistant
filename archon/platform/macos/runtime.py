"""macOS MacRuntime implementation."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from archon.platform.runtime import PlatformRuntime
from archon.platform.types import GpuType

_HOMEBREW_BIN = Path("/opt/homebrew/bin")
_USR_LOCAL_BIN = Path("/usr/local/bin")


class MacRuntime(PlatformRuntime):
    """macOS runtime — inherits POSIX signal/uptime from base."""

    def restart_process(self) -> None:
        """Restart the current process via os.execv."""
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def find_binary(
        self, name: str, extra_paths: list[Path] | None = None
    ) -> Path | None:
        """Find a binary: which → /opt/homebrew/bin → /usr/local/bin → extra_paths."""
        if not name:
            return None

        # 1. PATH lookup
        found = shutil.which(name)
        if found:
            return Path(found)

        # 2. Hardcoded macOS paths
        for directory in (_HOMEBREW_BIN, _USR_LOCAL_BIN):
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate

        # 3. Caller-supplied extra paths
        for p in extra_paths or ():
            if p.is_file() and os.access(p, os.X_OK):
                return p

        return None

    def detect_gpu_type(self) -> GpuType:
        """Return 'apple_silicon' on arm64 Macs, 'none' on Intel."""
        if platform.machine() == "arm64":
            return "apple_silicon"
        return "none"
