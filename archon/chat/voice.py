"""Handle voice messages from Telegram."""
import asyncio
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from aiogram.types import Audio, Message, Voice

from archon.ai.stt import STTHandler
from archon.ai.tts import TTSConfig, TTSHandler

if TYPE_CHECKING:
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.session_manager import SessionManager

logger = logging.getLogger("archon")


class VoiceMessageHandler:
    """Handles incoming voice messages: transcribe, process, optionally reply with voice."""

    def __init__(
        self,
        session_manager: "SessionManager",
        agent_logger: "AgentLogger",
        stt_config: Optional[dict] = None,
        tts_config: Optional[TTSConfig] = None,
        text_handler=None,
    ):
        """
        Initialize voice message handler.

        Args:
            session_manager: Session manager for message processing
            agent_logger: Agent logger for history
            stt_config: Speech-to-text config (model, language)
            tts_config: Text-to-speech config
            text_handler: Function to call to process text (handle_message_internal)
        """
        self.session_manager = session_manager
        self.agent_logger = agent_logger
        self.text_handler = text_handler

        # STT setup (Whisper)
        stt_config = stt_config or {}
        self.stt = STTHandler(
            model=stt_config.get("model", "medium"),
            language=stt_config.get("language"),
        )

        # TTS setup (OpenAI or Edge)
        self.tts_config = tts_config or TTSConfig(auto="off")
        self.tts = TTSHandler(self.tts_config) if self.tts_config.is_enabled() else None

        logger.info(f"Voice handler initialized: STT model={stt_config.get('model', 'medium')}, TTS provider={self.tts_config.provider}")

    async def handle_voice_message(self, message: Message) -> None:
        """
        Handle incoming voice message.

        Flow:
        1. Download voice file from Telegram
        2. Transcribe using Whisper (STT)
        3. Update message text with transcription
        4. Process with text handler (existing Claude flow)
        5. Optionally synthesize response with TTS

        Args:
            message: Telegram message with voice attachment
        """
        if not message.voice:
            logger.error("Message has no voice attachment")
            await message.answer("❌ No voice attachment found")
            return

        voice: Voice = message.voice
        user_id = message.from_user.id if message.from_user else "unknown"

        logger.info(f"Voice message from user {user_id}, duration: {voice.duration}s, file_id: {voice.file_id}")

        # Step 1: Download voice file
        try:
            file_info = await message.bot.get_file(voice.file_id)
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = Path(tmpdir) / f"voice_{voice.file_id}.ogg"
                await message.bot.download_file(file_info.file_path, audio_path)

                logger.debug(f"Downloaded voice file: {audio_path}, size: {audio_path.stat().st_size} bytes")

                # Step 2: Transcribe audio
                logger.info(f"Transcribing voice message (duration: {voice.duration}s)")
                transcribed_text = await self.stt.transcribe_with_timeout(audio_path, timeout_sec=60.0)

                if not transcribed_text or transcribed_text.strip() == "":
                    logger.warning("Whisper transcribed empty text")
                    await message.answer("❌ Could not transcribe voice message (empty result)")
                    return

                logger.info(f"Transcribed: {transcribed_text[:100]}...")

                # Show transcription to user
                transcription_msg = await message.answer(f"🎤 Transcribed: {transcribed_text[:200]}...\n\n⏳ Processing...")

                # Step 3: Update message text and let text handler process it
                message.text = transcribed_text
                message.voice = None  # Mark as processed

                # Step 4: Process with text handler
                if self.text_handler:
                    logger.debug("Delegating to text message handler")
                    await self.text_handler(message)
                else:
                    logger.warning("No text handler registered; cannot process transcribed text")
                    await message.answer("❌ Text handler not configured")

        except FileNotFoundError as e:
            logger.error(f"Audio file download failed: {e}")
            await message.answer(f"❌ Failed to download voice file: {e}")
        except asyncio.TimeoutError:
            logger.error("Voice transcription timed out")
            await message.answer("❌ Voice transcription timed out (audio too long?)")
        except Exception as e:
            logger.error(f"Error handling voice message: {e}", exc_info=True)
            await message.answer(f"❌ Error processing voice message: {e}")

    async def handle_audio_message(self, message: Message) -> None:
        """
        Handle incoming audio file message (MP3, M4A, etc.).

        Similar to voice message but for audio files sent as attachments.

        Args:
            message: Telegram message with audio attachment
        """
        if not message.audio:
            logger.error("Message has no audio attachment")
            await message.answer("❌ No audio attachment found")
            return

        audio: Audio = message.audio
        user_id = message.from_user.id if message.from_user else "unknown"

        logger.info(f"Audio message from user {user_id}, file_id: {audio.file_id}, mime_type: {audio.mime_type}")

        try:
            file_info = await message.bot.get_file(audio.file_id)
            with tempfile.TemporaryDirectory() as tmpdir:
                # Determine extension from MIME type
                ext = self._get_audio_extension(audio.mime_type)
                audio_path = Path(tmpdir) / f"audio_{audio.file_id}{ext}"

                await message.bot.download_file(file_info.file_path, audio_path)

                logger.debug(f"Downloaded audio file: {audio_path}, size: {audio_path.stat().st_size} bytes")

                logger.info("Transcribing audio message")
                transcribed_text = await self.stt.transcribe_with_timeout(audio_path, timeout_sec=120.0)

                if not transcribed_text or transcribed_text.strip() == "":
                    logger.warning("Whisper transcribed empty text")
                    await message.answer("❌ Could not transcribe audio (empty result)")
                    return

                logger.info(f"Transcribed: {transcribed_text[:100]}...")

                # Update message and process
                message.text = transcribed_text
                message.audio = None

                if self.text_handler:
                    await self.text_handler(message)
                else:
                    await message.answer("❌ Text handler not configured")

        except asyncio.TimeoutError:
            logger.error("Audio transcription timed out")
            await message.answer("❌ Audio transcription timed out (file too long?)")
        except Exception as e:
            logger.error(f"Error handling audio message: {e}", exc_info=True)
            await message.answer(f"❌ Error processing audio: {e}")

    @staticmethod
    def _get_audio_extension(mime_type: Optional[str]) -> str:
        """Get audio file extension from MIME type."""
        mime_map = {
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/opus": ".opus",
            "audio/flac": ".flac",
        }
        return mime_map.get(mime_type or "", ".ogg")

    async def maybe_send_voice_response(
        self,
        message: Message,
        response_text: str,
    ) -> bool:
        """
        Optionally send response as voice note instead of text.

        Args:
            message: Original message (to check if it was voice)
            response_text: Response text from Claude

        Returns:
            True if voice was sent, False if text should be sent instead
        """
        if not self.tts or not self.tts.is_enabled():
            return False

        # Check if we should synthesize this response
        message_had_voice = message.voice is not None or message.audio is not None
        should_voice = self.tts.should_synthesize(message_had_voice)

        if not should_voice:
            logger.debug(f"TTS disabled for this response (auto={self.tts_config.auto}, had_voice={message_had_voice})")
            return False

        try:
            logger.info(f"Generating voice response ({len(response_text)} chars)")

            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = Path(tmpdir) / "response.ogg"
                await self.tts.synthesize(response_text, audio_path)

                if not audio_path.exists():
                    logger.error("TTS synthesis did not create output file")
                    return False

                file_size = audio_path.stat().st_size
                logger.debug(f"Generated audio file: {file_size} bytes")

                # Send as voice note (round bubble in Telegram)
                with open(audio_path, "rb") as f:
                    await message.answer_voice(voice=f)

                logger.info("Voice response sent successfully")
                return True

        except Exception as e:
            logger.error(f"Error generating voice response: {e}", exc_info=True)
            return False
