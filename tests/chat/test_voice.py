"""Tests for VoiceMessageHandler."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Audio, Voice

from archon.ai.tts import TTSConfig
from archon.chat.voice import VoiceMessageHandler


def _make_voice_handler(
    tts_auto: str = "off",
    text_handler=None,
) -> VoiceMessageHandler:
    """Create a VoiceMessageHandler with mocked dependencies."""
    sm = MagicMock()
    al = MagicMock()
    tts_cfg = TTSConfig(auto=tts_auto)
    return VoiceMessageHandler(
        session_manager=sm,
        agent_logger=al,
        stt_config={"model": "tiny"},
        tts_config=tts_cfg,
        text_handler=text_handler,
    )


# ──────────────────────────────────────────────────────────────────
# Construction — the crash that started it all
# ──────────────────────────────────────────────────────────────────


def test_voice_handler_constructs_with_tts_off() -> None:
    """TTSConfig(auto='off') must not crash in __init__ (is_enabled check)."""
    vmh = _make_voice_handler(tts_auto="off")
    assert vmh.tts is None


def test_voice_handler_constructs_with_tts_inbound() -> None:
    """TTSConfig(auto='inbound') must create a TTSHandler."""
    vmh = _make_voice_handler(tts_auto="inbound")
    assert vmh.tts is not None


def test_voice_handler_constructs_with_tts_always() -> None:
    """TTSConfig(auto='always') must create a TTSHandler."""
    vmh = _make_voice_handler(tts_auto="always")
    assert vmh.tts is not None


# ──────────────────────────────────────────────────────────────────
# Audio extension detection
# ──────────────────────────────────────────────────────────────────


def test_get_audio_extension_mp3() -> None:
    assert VoiceMessageHandler._get_audio_extension("audio/mpeg") == ".mp3"


def test_get_audio_extension_unknown() -> None:
    assert VoiceMessageHandler._get_audio_extension("video/mp4") == ".ogg"


def test_get_audio_extension_none() -> None:
    assert VoiceMessageHandler._get_audio_extension(None) == ".ogg"


# ──────────────────────────────────────────────────────────────────
# Voice message handling flow
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_voice_no_voice_attachment() -> None:
    """Message without voice attachment must send error."""
    vmh = _make_voice_handler()
    msg = MagicMock()
    msg.voice = None
    msg.answer = AsyncMock()

    await vmh.handle_voice_message(msg)
    msg.answer.assert_awaited_once()
    assert "No voice attachment" in msg.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_voice_transcribes_and_delegates() -> None:
    """Voice message must be transcribed and passed to text_handler."""
    text_handler = AsyncMock()
    vmh = _make_voice_handler(text_handler=text_handler)

    msg = MagicMock()
    msg.voice = MagicMock(spec=Voice)
    msg.voice.file_id = "abc123"
    msg.voice.duration = 5
    msg.from_user = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock(return_value=MagicMock())
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="voice/file.ogg"))
    msg.bot.download_file = AsyncMock()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value="hello world"):
        await vmh.handle_voice_message(msg)

    text_handler.assert_awaited_once_with(msg)
    assert msg.text == "hello world"


@pytest.mark.asyncio
async def test_handle_voice_empty_transcription() -> None:
    """Empty transcription must send error, not delegate."""
    text_handler = AsyncMock()
    vmh = _make_voice_handler(text_handler=text_handler)

    msg = MagicMock()
    msg.voice = MagicMock(spec=Voice)
    msg.voice.file_id = "abc123"
    msg.voice.duration = 2
    msg.from_user = MagicMock()
    msg.from_user.id = 42
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.get_file = AsyncMock(return_value=MagicMock(file_path="voice/file.ogg"))
    msg.bot.download_file = AsyncMock()

    with patch.object(vmh.stt, "transcribe_with_timeout", new_callable=AsyncMock, return_value=""):
        await vmh.handle_voice_message(msg)

    text_handler.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# Audio message handling
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_audio_no_attachment() -> None:
    """Message without audio attachment must send error."""
    vmh = _make_voice_handler()
    msg = MagicMock()
    msg.audio = None
    msg.answer = AsyncMock()

    await vmh.handle_audio_message(msg)
    msg.answer.assert_awaited_once()
    assert "No audio attachment" in msg.answer.call_args[0][0]


# ──────────────────────────────────────────────────────────────────
# Gateway registration order — voice handlers before generic handler
# ──────────────────────────────────────────────────────────────────


def test_voice_handlers_registered_before_generic_handler() -> None:
    """When voice is enabled, voice handlers must be registered BEFORE the generic handler."""
    from archon.chat.bot import create_dispatcher
    from archon.config.loader import (
        AccessConfig,
        Config,
        LoggingConfig,
        OutputConfig,
        SessionConfig,
        VoiceConfig,
        VoiceSTTConfig,
        VoiceTTSConfig,
    )
    from archon.gateway.gateway import _setup_dp

    cfg = Config(
        telegram_bot_token="12345:AAFakeToken",
        access=AccessConfig(allowed_user_ids=[100]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
        voice=VoiceConfig(
            enabled=True,
            stt=VoiceSTTConfig(model="tiny"),
            tts=VoiceTTSConfig(auto="off"),
        ),
    )
    dp = create_dispatcher()
    sm = MagicMock()
    sm.get_or_create = AsyncMock()

    _setup_dp(dp, cfg, sm)

    # Inspect registered message handlers — voice handlers must come before handle_message
    handlers = dp.message.handlers
    handler_names = []
    for h in handlers:
        cb = h.callback
        name = getattr(cb, "__name__", "") or getattr(cb, "__qualname__", "")
        handler_names.append(name)

    # Find positions
    voice_positions = [i for i, n in enumerate(handler_names) if "voice" in n.lower() or "audio" in n.lower()]
    text_positions = [i for i, n in enumerate(handler_names) if n == "handle_message"]

    assert voice_positions, f"Voice handlers not found in {handler_names}"
    assert text_positions, f"handle_message not found in {handler_names}"

    # All voice handlers must come before handle_message
    for vp in voice_positions:
        for tp in text_positions:
            assert vp < tp, (
                f"Voice handler at position {vp} must be before handle_message at position {tp}. "
                f"Handler order: {handler_names}"
            )
