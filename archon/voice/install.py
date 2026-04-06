"""VoiceInstaller — install, configure, and manage voice features (Task 1.2+)."""
from __future__ import annotations

import importlib
import logging
import shutil
import subprocess
import sys
from pathlib import Path

from archon.cli.console import Console


class VoiceInstaller:
    """Checks availability and manages installation of voice prerequisites."""

    def __init__(self, config_file: str | None = None, console: Console | None = None) -> None:
        self._console = console or Console()
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

    def check_torch(self) -> bool:
        """Return True if torch is importable (uses find_spec to avoid full runtime load)."""
        import importlib.util
        return importlib.util.find_spec("torch") is not None

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
    # Configuration
    # ------------------------------------------------------------------

    def configure_stt_model(self, model: str) -> None:
        """Write voice.stt.model to config file via set_config_value.

        Creates [voice] and [voice.stt] sections if absent.
        Logs a warning and returns silently if config file is missing or unreadable.
        """
        from archon.config.config_rw import set_config_value  # lazy import
        try:
            set_config_value("voice.stt.model", model, Path(self._config_file))
        except (FileNotFoundError, OSError) as exc:
            logging.getLogger("archon").warning(
                "Config file %s not found — skipping stt model config: %s",
                self._config_file, exc,
            )

    # ------------------------------------------------------------------
    # Run (full install flow)
    # ------------------------------------------------------------------

    def run(self, non_interactive: bool = False) -> int:
        """Run the full voice install flow. Returns 0 on success, 1 on abort or failure."""
        if not non_interactive:
            answer = input("Proceed with installation? [y/N] ").strip().lower()
            if answer != "y":
                self._console.warn("Installation aborted.")
                return 1

        # [1/3] Python dependencies
        if self.check_whisper():
            self._console.info("openai-whisper already installed — PyTorch already present, skipping download")
        else:
            if self.check_torch():
                self._console.info(
                    "Installing openai-whisper (PyTorch already installed — no large download needed)…"
                )
            else:
                self._console.info(
                    "Installing openai-whisper (PyTorch not installed — downloading ~2 GB; model weights download on first use)…"
                )
            try:
                self.install_deps()
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                self._console.error(f"Installation failed: {exc}")
                return 1
            self._console.success("openai-whisper installed.")

        # [2/3] ffmpeg check
        if self.check_ffmpeg():
            self._console.success("ffmpeg found on PATH (needed for audio decoding).")
        else:
            self._console.warn(
                "ffmpeg not found on PATH (needed for audio decoding).\n"
                "      Whisper requires ffmpeg for audio decoding. Install it:\n"
                "        macOS:   brew install ffmpeg\n"
                "        Ubuntu:  sudo apt install ffmpeg\n"
                "        Windows: https://ffmpeg.org/download.html\n"
                "      STT will not work until ffmpeg is on PATH."
            )

        # [3/3] Configure STT model
        if not non_interactive:
            print("      Model sizes (larger = more accurate, slower first-run download):")
            print("        tiny (~75 MB)   small (~466 MB)   medium (~1.5 GB)")
            model_input = input("  Speech-to-text model (Whisper) [tiny/small/medium] (default: medium): ").strip().lower()
            model = model_input if model_input in {"tiny", "small", "medium"} else "medium"
        else:
            model = "medium"
        self.configure_stt_model(model)
        self._console.success(f"Speech-to-text model set to '{model}'.")

        if not non_interactive:
            self._console.success("Voice support installed. Enable with: archon voice enable")
        return 0

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
