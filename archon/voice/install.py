"""VoiceInstaller — install, configure, and manage voice features (Task 1.2+)."""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path


class VoiceInstaller:
    """Checks availability and manages installation of voice prerequisites."""

    def __init__(self, config_file: str | None = None) -> None:
        self._config_file = config_file or str(Path.home() / ".archon" / "config.toml")

    # ------------------------------------------------------------------
    # Dependency checks (pure — no subprocess, no side effects)
    # ------------------------------------------------------------------

    def check_whisper(self) -> bool:
        """Return True if openai-whisper is importable and has load_model."""
        try:
            module = importlib.import_module("whisper")
            return hasattr(module, "load_model")
        except ImportError:
            return False

    def check_ffmpeg(self) -> bool:
        """Return True if ffmpeg binary is on PATH."""
        return shutil.which("ffmpeg") is not None

    def check_edge_tts(self) -> bool:
        """Return True if edge_tts is importable."""
        try:
            importlib.import_module("edge_tts")
            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def install_deps(self) -> None:
        """Install openai-whisper using uv pip into the current Python environment."""
        subprocess.run(
            ["uv", "pip", "install", "--python", sys.executable, "openai-whisper"],
            check=True,
        )

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, bool]:
        """Return availability of all voice prerequisites."""
        return {
            "whisper_installed": self.check_whisper(),
            "ffmpeg_found": self.check_ffmpeg(),
            "edge_tts_installed": self.check_edge_tts(),
        }
