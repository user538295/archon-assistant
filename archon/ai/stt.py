"""Speech-to-Text using OpenAI Whisper."""
import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from archon.platform import get_runtime

logger = logging.getLogger("archon")


class STTHandler:
    """Handles speech-to-text transcription using Whisper."""

    SUPPORTED_FORMATS = {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".flac", ".webm"}

    def __init__(self, model: str = "medium", language: str | None = None):
        """
        Initialize STT handler.

        Args:
            model: Whisper model size (tiny, base, small, medium, large)
            language: Language code (en, hu, etc.) or None for auto-detect
        """
        self.model = model
        self.language = language
        if not shutil.which("ffmpeg"):
            logger.warning(
                "ffmpeg not found on PATH; Whisper requires ffmpeg for audio decoding"
            )
        found = get_runtime().find_binary("whisper")
        if found:
            self.whisper_bin: Path = found
            logger.debug("Found Whisper at %s", found)
        else:
            self.whisper_bin = Path("whisper")
            logger.warning(
                "Whisper binary not found; will attempt to use from PATH"
            )

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
            logger.warning("Unsupported audio format %s; Whisper may fail", suffix)

        cmd = [
            str(self.whisper_bin), str(audio_path),
            "--model", self.model,
            "--output_format", "txt",
            "--output_dir", str(audio_path.parent),
        ]

        if self.language:
            cmd.extend(["--language", self.language])

        logger.debug("Running: %s", " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            proc.kill()
            await proc.wait()
            logger.warning("Whisper subprocess killed due to cancellation")
            raise

        if proc.returncode != 0:
            error_msg = stderr.decode() if stderr else "Unknown error"
            logger.error("Whisper transcription failed: %s", error_msg)
            raise subprocess.CalledProcessError(proc.returncode or 1, cmd, stderr=error_msg.encode())

        # Whisper creates a .txt file; read it
        txt_file = audio_path.with_suffix(".txt")
        # Delete stale .txt file that predates the audio file to avoid returning stale content
        if txt_file.exists() and txt_file.stat().st_mtime < audio_path.stat().st_mtime:
            try:
                txt_file.unlink()
            except OSError as e:
                logger.warning("Failed to delete stale transcript file %s: %s", txt_file, e)
        if txt_file.exists():
            text = txt_file.read_text(encoding="utf-8").strip()
            try:
                txt_file.unlink()
            except OSError as e:
                logger.warning("Failed to delete temp transcript file %s: %s", txt_file, e)
            logger.info("Transcribed %s: %d characters", audio_path.name, len(text))
            return text

        # Fallback: use stdout
        text = stdout.decode().strip()
        logger.info("Transcribed %s: %d characters (from stdout)", audio_path.name, len(text))
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
            logger.error("Transcription timed out after %s seconds", timeout_sec)
            raise
