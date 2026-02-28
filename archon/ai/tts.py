"""Text-to-Speech using OpenAI or Edge TTS."""
import asyncio
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Literal, Optional

try:
    import httpx
except ImportError:
    httpx = None

logger = logging.getLogger("archon")


@dataclass
class TTSConfig:
    """TTS configuration."""

    provider: Literal["openai", "edge"] = "openai"
    model: str = "tts-1"  # or "tts-1-hd"
    voice: str = "nova"  # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
    auto: Literal["always", "inbound", "tagged", "off"] = "inbound"
    max_text_length: int = 3000
    timeout_ms: int = 30000
    openai_api_key: Optional[str] = None
    edge_voice: Optional[str] = None
    edge_output_format: str = "audio-24khz-48kbitrate-mono-mp3"
    edge_rate: str = "+0%"
    edge_pitch: str = "+0%"

    def is_enabled(self) -> bool:
        """Check if TTS is enabled based on auto mode."""
        return self.auto != "off"


class TTSHandler:
    """Handles text-to-speech using OpenAI TTS or Edge TTS."""

    OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

    def __init__(self, config: TTSConfig):
        """Initialize TTS handler."""
        self.config = config
        self.openai_api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")

        if config.provider == "openai" and not self.openai_api_key:
            logger.warning("OpenAI provider selected but OPENAI_API_KEY not set")

        logger.info(f"TTS initialized with provider={config.provider}, model={config.model}, voice={config.voice}")

    async def synthesize(self, text: str, output_path: Path) -> Path:
        """
        Generate speech from text.

        Args:
            text: Text to synthesize
            output_path: Where to save the audio file

        Returns:
            Path to generated audio file

        Raises:
            ValueError: If provider is unknown or config invalid
            RuntimeError: If synthesis fails
        """
        if self.config.provider == "openai":
            return await self._openai_tts(text, output_path)
        elif self.config.provider == "edge":
            return await self._edge_tts(text, output_path)
        else:
            raise ValueError(f"Unknown TTS provider: {self.config.provider}")

    async def _openai_tts(self, text: str, output_path: Path) -> Path:
        """Use OpenAI TTS API."""
        if not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not set; cannot use OpenAI TTS")

        if not httpx:
            raise ImportError("httpx is required for OpenAI TTS; install with: pip install httpx")

        if self.config.voice not in self.OPENAI_VOICES:
            logger.warning(f"Voice {self.config.voice} not in known voices; using anyway")

        text_to_synthesize = text[: self.config.max_text_length]

        url = "https://api.openai.com/v1/audio/speech"
        headers = {"Authorization": f"Bearer {self.openai_api_key}"}
        payload = {
            "model": self.config.model,
            "input": text_to_synthesize,
            "voice": self.config.voice,
            "response_format": "opus",  # Telegram voice note format
        }

        timeout_sec = self.config.timeout_ms / 1000.0

        try:
            async with httpx.AsyncClient(timeout=timeout_sec) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()

            output_path.write_bytes(response.content)
            logger.info(f"OpenAI TTS generated audio: {output_path.name} ({len(response.content)} bytes)")
            return output_path

        except httpx.HTTPError as e:
            logger.error(f"OpenAI TTS API error: {e}")
            raise RuntimeError(f"OpenAI TTS failed: {e}")
        except asyncio.TimeoutError:
            logger.error(f"OpenAI TTS timed out after {timeout_sec} seconds")
            raise RuntimeError(f"OpenAI TTS timed out after {timeout_sec} seconds")

    async def _edge_tts(self, text: str, output_path: Path) -> Path:
        """Use Edge TTS (free alternative)."""
        text_to_synthesize = text[: self.config.max_text_length]

        # Build edge-tts command
        voice = self.config.edge_voice or "en-US-MichelleNeural"

        cmd = [
            "npx",
            "edge-tts",
            "--text",
            text_to_synthesize,
            "--voice",
            voice,
            "--write-media",
            str(output_path),
            "--rate",
            self.config.edge_rate,
            "--pitch",
            self.config.edge_pitch,
        ]

        timeout_sec = self.config.timeout_ms / 1000.0

        try:
            logger.debug(f"Running Edge TTS: {' '.join(cmd)}")

            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)

            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(f"Edge TTS failed: {error_msg}")
                raise RuntimeError(f"Edge TTS failed: {error_msg}")

            logger.info(f"Edge TTS generated audio: {output_path.name}")
            return output_path

        except asyncio.TimeoutError:
            logger.error(f"Edge TTS timed out after {timeout_sec} seconds")
            raise RuntimeError(f"Edge TTS timed out after {timeout_sec} seconds")

    def is_enabled(self) -> bool:
        """Check if TTS is enabled."""
        return self.config.auto != "off"

    def should_synthesize(self, message_has_voice: bool) -> bool:
        """
        Determine if response should be synthesized.

        Args:
            message_has_voice: Whether original message was voice

        Returns:
            True if response should be voice
        """
        if self.config.auto == "off":
            return False
        if self.config.auto == "always":
            return True
        if self.config.auto == "inbound":
            return message_has_voice
        if self.config.auto == "tagged":
            # TODO: Check for [[tts:...]] tags in response
            return False
        return False
