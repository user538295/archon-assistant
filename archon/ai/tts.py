"""Text-to-Speech using OpenAI or Edge TTS."""
import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

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
    openai_api_key: str | None = None
    edge_voice: str = "en-US-MichelleNeural"
    edge_output_format: str = "audio-24khz-48kbitrate-mono-mp3"
    edge_rate: str = "+0%"
    edge_pitch: str = "+0Hz"

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

        logger.info(
            "TTS initialized with provider=%s, model=%s, voice=%s",
            config.provider, config.model, config.voice,
        )

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
            raise ImportError("httpx is required for OpenAI TTS; install with: uv add httpx")

        if self.config.voice not in self.OPENAI_VOICES:
            logger.warning("Voice %s not in known voices; using anyway", self.config.voice)

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
            logger.info(
                "OpenAI TTS generated audio: %s (%d bytes)",
                output_path.name, len(response.content),
            )
            return output_path

        except httpx.HTTPError as e:
            logger.error("OpenAI TTS API error: %s", e)
            raise RuntimeError(f"OpenAI TTS failed: {e}")
        except httpx.TimeoutException:
            logger.error("OpenAI TTS timed out after %s seconds", timeout_sec)
            raise RuntimeError(f"OpenAI TTS timed out after {timeout_sec} seconds")

    async def _edge_tts(self, text: str, output_path: Path) -> Path:
        """Use edge-tts Python library (no Node.js required)."""
        try:
            import edge_tts as _edge_tts_lib
        except ImportError as exc:
            raise ImportError(
                "edge-tts is required for Edge TTS; install with: uv add edge-tts"
            ) from exc

        text_to_synthesize = text[: self.config.max_text_length]
        voice = self.config.edge_voice or TTSConfig.edge_voice
        timeout_sec = self.config.timeout_ms / 1000.0

        logger.debug("Edge TTS: voice=%s, chars=%d", voice, len(text_to_synthesize))

        try:
            communicate = _edge_tts_lib.Communicate(
                text_to_synthesize,
                voice,
                rate=self.config.edge_rate,
                pitch=self.config.edge_pitch,
            )
            await asyncio.wait_for(communicate.save(str(output_path)), timeout=timeout_sec)
            logger.info(
                "Edge TTS generated audio: %s (%d bytes)",
                output_path.name, output_path.stat().st_size,
            )
            return output_path

        except asyncio.TimeoutError:
            logger.error("Edge TTS timed out after %s seconds", timeout_sec)
            raise RuntimeError(f"Edge TTS timed out after {timeout_sec} seconds")
        except Exception as exc:
            logger.error("Edge TTS failed: %s", exc)
            raise RuntimeError(f"Edge TTS failed: {exc}") from exc

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
            raise NotImplementedError("tagged TTS mode is not yet implemented")
        return False
