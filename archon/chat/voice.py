"""Handle voice messages from Telegram — STT transcription + Claude processing + optional TTS reply."""
import asyncio
import html
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from aiogram.exceptions import TelegramRetryAfter

from aiogram.types import Audio, Message, Voice
from aiogram.types.input_file import FSInputFile

from archon.ai.event_mapper import PlanEvent, PromotionEvent, Response
from archon.ai.stt import STTHandler
from archon.ai.tts import TTSConfig, TTSHandler
from archon.chat.handler import format_event
from archon.config.loader import VoiceSTTConfig

if TYPE_CHECKING:
    from archon.ai.agent_logger import AgentLogger
    from archon.ai.background_agent_manager import BackgroundAgentManager
    from archon.ai.history_manager import HistoryManager
    from archon.ai.plan_executor import PlanExecutor
    from archon.ai.session_manager import SessionManager
    from archon.ai.truncation import TruncationStrategy
    from archon.config.loader import NotificationsConfig

logger = logging.getLogger("archon")

# Holds strong references to fire-and-forget tasks so they are not GC'd
# before completion.  Each task removes itself via done_callback.
_background_tasks: set["asyncio.Task[None]"] = set()

_MIME_EXT_MAP = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/flac": ".flac",
}


class VoiceMessageHandler:
    """Handles voice/audio messages: transcribe via STT, process via Claude, optionally reply with TTS."""

    def __init__(
        self,
        session_manager: "SessionManager",
        stt_config: Optional[dict[str, Any]] = None,
        tts_config: Optional[TTSConfig] = None,
        truncation: "TruncationStrategy | None" = None,
        max_len: int = 4000,
        notifications: "NotificationsConfig | None" = None,
        cwd: str = "",
        history_manager: "HistoryManager | None" = None,
        agent_logger: "AgentLogger | None" = None,
        background_agent_manager: "BackgroundAgentManager | None" = None,
    ):
        self.session_manager = session_manager
        self.truncation = truncation
        self.max_len = max_len
        self.notifications = notifications
        self.cwd = cwd
        self.history_manager = history_manager
        self.agent_logger = agent_logger
        self.background_agent_manager = background_agent_manager

        # STT setup
        stt_config = stt_config or {}
        self.stt = STTHandler(
            model=stt_config.get("model", VoiceSTTConfig.model),
            language=stt_config.get("language"),
        )

        # TTS setup
        self.tts_config = tts_config or TTSConfig(auto="off")
        self.tts = TTSHandler(self.tts_config) if self.tts_config.is_enabled() else None

        logger.info(
            "Voice handler initialized: STT model=%s, TTS provider=%s auto=%s",
            stt_config.get("model", VoiceSTTConfig.model),
            self.tts_config.provider,
            self.tts_config.auto,
        )

    async def handle_voice_message(self, message: Message) -> None:
        """Handle incoming voice message: download → transcribe → process → optional TTS reply."""
        if not message.voice:
            await message.answer("❌ No voice attachment found")
            return

        file_id = message.voice.file_id
        duration = message.voice.duration
        user_id = message.from_user.id if message.from_user else 0
        logger.info("Voice message from user %d, duration: %ds", user_id, duration)

        transcribed = await self._download_and_transcribe(message, file_id, ".ogg", timeout_sec=60.0)
        if transcribed is None:
            return

        await self._process_and_respond(message, transcribed, user_id)

    async def handle_audio_message(self, message: Message) -> None:
        """Handle incoming audio file (MP3, M4A, etc.): download → transcribe → process → optional TTS reply."""
        if not message.audio:
            await message.answer("❌ No audio attachment found")
            return

        file_id = message.audio.file_id
        ext = _MIME_EXT_MAP.get(message.audio.mime_type or "", ".ogg")
        user_id = message.from_user.id if message.from_user else 0
        logger.info("Audio message from user %d, file_id: %s", user_id, file_id)

        transcribed = await self._download_and_transcribe(message, file_id, ext, timeout_sec=120.0)
        if transcribed is None:
            return

        await self._process_and_respond(message, transcribed, user_id)

    # ── internal helpers ──────────────────────────────────────────

    async def _download_and_transcribe(
        self,
        message: Message,
        file_id: str,
        ext: str,
        timeout_sec: float,
    ) -> Optional[str]:
        """Download a Telegram file and transcribe it. Returns text or None on failure."""
        try:
            if message.bot is None:
                logger.warning("message.bot is None, skipping download")
                return None
            file_info = await message.bot.get_file(file_id)
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = Path(tmpdir) / f"audio_{file_id}{ext}"
                assert file_info.file_path is not None
                await message.bot.download_file(file_info.file_path, audio_path)
                logger.debug("Downloaded audio: %s (%d bytes)", audio_path.name, audio_path.stat().st_size)

                text = await self.stt.transcribe_with_timeout(audio_path, timeout_sec=timeout_sec)

            if not text or not text.strip():
                logger.warning("Whisper returned empty transcription")
                await message.answer("❌ Could not transcribe audio (empty result)")
                return None

            logger.info("Transcribed (%d chars): %s", len(text), text[:80])
            return text

        except FileNotFoundError as e:
            logger.error("Audio file download failed: %s", e)
            await message.answer(f"❌ Failed to download audio: {html.escape(str(e))}")
        except asyncio.TimeoutError:
            logger.error("Transcription timed out after %.0fs", timeout_sec)
            await message.answer("❌ Transcription timed out (audio too long?)")
        except Exception as e:
            logger.error("Error during transcription: %s", e, exc_info=True)
            await message.answer(f"❌ Error processing audio: {html.escape(str(e))}")
        return None

    async def _process_and_respond(self, message: Message, text: str, user_id: int) -> None:
        """Send transcribed text to Claude, stream events to Telegram, optionally reply with TTS."""
        # Show transcription
        preview = text[:200] + ("..." if len(text) > 200 else "")
        await message.answer(f"🎤 {preview}")

        # Record user message
        if self.history_manager:
            await self.history_manager.record_user_message(user_id, text, cwd=self.cwd)

        # Send typing indicator
        if message.bot is None:
            logger.warning("message.bot is None, skipping typing indicator")
        else:
            try:
                await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
            except Exception as exc:
                logger.warning("Failed to send typing indicator (%s)", type(exc).__name__)

        session = await self.session_manager.get_or_create(user_id)

        # Notify user immediately if the session is already processing a request.
        # The send() call below will wait behind the lock before starting (Bug.012).
        if session.is_processing:
            try:
                await message.answer(
                    "⏳ Previous request still processing — your message is queued"
                )
            except Exception as exc:
                logger.warning(
                    "Failed to send 'queued' notification to user %d (%s) — continuing",
                    user_id,
                    type(exc).__name__,
                )

        mode = self.notifications.mode if self.notifications else "debug"
        quiet_active = mode == "quiet"
        ack = "⏳ Working..." if quiet_active else "⏳ Processing..."
        try:
            await message.answer(ack)
        except Exception as exc:
            logger.warning(
                "Failed to send acknowledgement to user %d (%s) — continuing",
                user_id,
                type(exc).__name__,
            )

        response_text = ""

        try:
            async for event in session.send(text):
                # Sub-agent events → agent logger only
                if getattr(event, "source", "orchestrator") == "sub-agent":
                    if self.agent_logger:
                        await self.agent_logger.record_event(event)
                    continue

                if self.history_manager:
                    await self.history_manager.record_event(user_id, event)

                # Capture response text for TTS
                if isinstance(event, Response):
                    response_text = event.content

                # PlanEvent → launch PlanExecutor
                if isinstance(event, PlanEvent) and self.background_agent_manager is not None:
                    from archon.ai.plan_executor import PlanExecutor

                    executor = PlanExecutor(
                        bam=self.background_agent_manager,
                        bot=message.bot,
                        user_id=user_id,
                        cwd=self.cwd,
                        history_manager=self.history_manager,
                        context_summary=getattr(session, "context_summary", ""),
                    )
                    _task = asyncio.create_task(
                        executor.execute(event.plan),
                        name=f"plan-executor-{user_id}",
                    )
                    _background_tasks.add(_task)
                    _task.add_done_callback(_background_tasks.discard)

                # PromotionEvent → spawn background agent for promoted task
                if isinstance(event, PromotionEvent) and self.background_agent_manager is not None:
                    # Send the "handing off" notification BEFORE spawn() so it arrives
                    # before the "🤖 Agent X spawned." message that spawn() sends internally.
                    handoff_msg = f"🔄 Task is bigger than expected — handing off to a background agent ({event.tool_count} tools used)"
                    try:
                        await message.answer(handoff_msg, parse_mode="HTML")
                    except Exception as exc:
                        logger.warning(
                            "Failed to deliver promotion notification to user %d (%s) — continuing",
                            user_id, type(exc).__name__,
                        )
                    try:
                        run = await self.background_agent_manager.spawn(
                            user_id=user_id,
                            task=event.agent_prompt,
                            user_request=text,
                            context=getattr(session, "context_summary", ""),
                        )
                        logger.info(
                            "Voice task promoted to agent %r (user=%d, tools=%d)",
                            run.name,
                            user_id,
                            event.tool_count,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to spawn promoted agent for user %d: %s",
                            user_id,
                            exc,
                        )
                        try:
                            await message.answer(
                                f"⚠️ Task promotion failed — could not start background agent ({event.tool_count} tools used)",
                                parse_mode="HTML",
                            )
                        except Exception as notify_exc:
                            logger.warning(
                                "Failed to deliver spawn-failure notification to user %d (%s) — continuing",
                                user_id, type(notify_exc).__name__,
                            )
                    continue  # skip format_event for PromotionEvent

                # Format event and send to Telegram
                if self.truncation is not None:
                    for chunk in format_event(event, self.truncation, self.max_len, self.notifications):
                        try:
                            await message.answer(chunk, parse_mode="HTML")
                        except TelegramRetryAfter as exc:
                            await asyncio.sleep(exc.retry_after + 1)
                            try:
                                await message.answer(chunk, parse_mode="HTML")
                            except Exception as retry_exc:
                                logger.warning("Failed to deliver event after retry-after to user %d (%s)", user_id, type(retry_exc).__name__)
                        except Exception as exc:
                            logger.warning("Failed to deliver event to user %d (%s)", user_id, type(exc).__name__)

        except Exception as exc:
            logger.error("Error processing voice text for user %d: %s", user_id, exc, exc_info=True)
            try:
                await message.answer(f"❌ Error: {html.escape(str(exc))}")
            except Exception:
                logger.warning("Failed to send error notification to user %d", user_id, exc_info=True)
            return

        # TTS: generate voice note from response (only when no error occurred)
        if response_text and self.tts and self.tts.should_synthesize(True):
            await self._send_tts_response(message, response_text)

    async def _send_tts_response(self, message: Message, text: str) -> None:
        """Generate and send a TTS voice note."""
        assert self.tts is not None
        try:
            logger.info("Generating TTS response (%d chars)", len(text))
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = Path(tmpdir) / "response.ogg"
                await self.tts.synthesize(text, audio_path)

                if not audio_path.exists():
                    logger.error("TTS synthesis did not produce output file")
                    return

                logger.debug("TTS audio: %d bytes", audio_path.stat().st_size)
                await message.answer_voice(voice=FSInputFile(audio_path))

                logger.info("TTS voice response sent")
        except Exception as e:
            logger.error("TTS synthesis failed: %s", e, exc_info=True)
