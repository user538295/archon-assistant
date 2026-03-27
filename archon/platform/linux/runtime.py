"""Linux LinuxRuntime implementation."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from archon.platform.runtime import PlatformRuntime
from archon.platform.types import GpuType


class LinuxRuntime(PlatformRuntime):
    """Linux runtime — inherits POSIX signal/uptime from base."""

    def restart_process(self) -> None:
        """Restart via os.execv (same binary, same argv)."""
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def find_binary(
        self, name: str, extra_paths: list[Path] | None = None
    ) -> Path | None:
        """Find binary: shutil.which → ~/.local/bin → /usr/local/bin → extra_paths."""
        if not name:
            return None

        found = shutil.which(name)
        if found:
            return Path(found)

        candidates: list[Path] = []
        try:
            candidates.append(Path.home() / ".local" / "bin" / name)
        except RuntimeError:
            pass  # HOME unset (containers)
        candidates.append(Path("/usr/local/bin") / name)
        if extra_paths:
            candidates.extend(extra_paths)

        for p in candidates:
            if p.is_file() and os.access(p, os.X_OK):
                return p

        return None

    def detect_gpu_type(self) -> GpuType:
        """Return 'cuda' if nvidia-smi exits 0, 'none' otherwise."""
        try:
            result = subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
            if result.returncode == 0:
                return "cuda"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return "none"
