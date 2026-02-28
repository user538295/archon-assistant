"""Speech-to-Text using OpenAI Whisper."""
import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("archon")


class STTHandler:
    """Handles speech-to-text transcription using Whisper."""

    SUPPORTED_FORMATS = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".webm"}

    def __init__(self, model: str = "medium", language: Optional[str] = None):
        """
        Initialize STT handler.

        Args:
            model: Whisper model size (tiny, base, small, medium, large)
            language: Language code (en, hu, etc.) or None for auto-detect
        """
        self.model = model
        self.language = language
        self._find_whisper_binary()

    def _find_whisper_binary(self) -> None:
        """Find whisper binary in common locations."""
        common_paths = [
            Path("/opt/homebrew/bin/whisper"),  # macOS Homebrew
            Path("/usr/local/bin/whisper"),     # Linux/Intel macOS
            Path("/usr/bin/whisper"),           # System PATH
        ]

        self.whisper_bin = None
        for path in common_paths:
            if path.exists():
                self.whisper_bin = path
                logger.debug(f"Found Whisper at {path}")
                return

        # If not found in standard locations, assume it's in PATH
        self.whisper_bin = Path("whisper")
        logger.warning("Whisper binary not found in standard locations; will attempt to use from PATH")

    async def transcribe(self, audio_path: Path) -> str:
        """
        Transcribe audio file to text.

        Args:
            audio_path: Path to audio file (ogg, mp3, wav, m4a, etc.)

        Returns:
            Transcribed text

        Raises:
            subprocess.CalledProcessError: If transcription fails
            FileNotFoundError: If audio file not found
            ValueError: If file format is unsupported
        """
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        suffix = audio_path.suffix.lower()
        if suffix not in self.SUPPORTED_FORMATS:
            logger.warning(f"Unsupported audio format {suffix}; Whisper may fail")

        cmd = [str(self.whisper_bin), str(audio_path), "--model", self.model, "--output_format", "txt"]

        if self.language:
            cmd.extend(["--language", self.language])

        logger.debug(f"Running: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error(f"Whisper transcription failed: {error_msg}")
            raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=error_msg.encode())

        # Whisper creates a .txt file; read it
        txt_file = audio_path.with_suffix(".txt")
        if txt_file.exists():
            text = txt_file.read_text().strip()
            txt_file.unlink()  # Clean up
            logger.info(f"Transcribed {audio_path.name}: {len(text)} characters")
            return text

        # Fallback: use stdout
        text = stdout.decode().strip()
        logger.info(f"Transcribed {audio_path.name}: {len(text)} characters (from stdout)")
        return text

    async def transcribe_with_timeout(self, audio_path: Path, timeout_sec: float = 60.0) -> str:
        """
        Transcribe audio file with timeout.

        Args:
            audio_path: Path to audio file
            timeout_sec: Timeout in seconds

        Returns:
            Transcribed text

        Raises:
            asyncio.TimeoutError: If transcription exceeds timeout
        """
        try:
            return await asyncio.wait_for(self.transcribe(audio_path), timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.error(f"Transcription timed out after {timeout_sec} seconds")
            raise
