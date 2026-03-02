"""Tests for TTS module — TTSConfig and TTSHandler."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.tts import TTSConfig, TTSHandler


# ──────────────────────────────────────────────────────────────────
# TTSConfig.is_enabled()
# ──────────────────────────────────────────────────────────────────


def test_tts_config_is_enabled_off() -> None:
    """auto='off' must return False."""
    assert TTSConfig(auto="off").is_enabled() is False


def test_tts_config_is_enabled_always() -> None:
    """auto='always' must return True."""
    assert TTSConfig(auto="always").is_enabled() is True


def test_tts_config_is_enabled_inbound() -> None:
    """auto='inbound' must return True."""
    assert TTSConfig(auto="inbound").is_enabled() is True


def test_tts_config_is_enabled_tagged() -> None:
    """auto='tagged' must return True."""
    assert TTSConfig(auto="tagged").is_enabled() is True


def test_tts_config_default_is_inbound() -> None:
    """Default TTSConfig must have auto='inbound' and is_enabled()=True."""
    cfg = TTSConfig()
    assert cfg.auto == "inbound"
    assert cfg.is_enabled() is True


# ──────────────────────────────────────────────────────────────────
# TTSHandler.is_enabled() / should_synthesize()
# ──────────────────────────────────────────────────────────────────


def test_handler_is_enabled_delegates_to_config() -> None:
    """TTSHandler.is_enabled() must reflect config.auto setting."""
    assert TTSHandler(TTSConfig(auto="always")).is_enabled() is True
    assert TTSHandler(TTSConfig(auto="off")).is_enabled() is False


def test_should_synthesize_always() -> None:
    h = TTSHandler(TTSConfig(auto="always"))
    assert h.should_synthesize(True) is True
    assert h.should_synthesize(False) is True


def test_should_synthesize_inbound() -> None:
    h = TTSHandler(TTSConfig(auto="inbound"))
    assert h.should_synthesize(True) is True
    assert h.should_synthesize(False) is False


def test_should_synthesize_off() -> None:
    h = TTSHandler(TTSConfig(auto="off"))
    assert h.should_synthesize(True) is False
    assert h.should_synthesize(False) is False


def test_should_synthesize_tagged() -> None:
    h = TTSHandler(TTSConfig(auto="tagged"))
    assert h.should_synthesize(True) is False
    assert h.should_synthesize(False) is False


# ──────────────────────────────────────────────────────────────────
# TTSHandler.synthesize — provider routing
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_synthesize_unknown_provider_raises() -> None:
    """Unknown TTS provider must raise ValueError."""
    h = TTSHandler(TTSConfig(provider="unknown"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Unknown TTS provider"):
        await h.synthesize("hello", Path("/tmp/out.ogg"))


@pytest.mark.asyncio
async def test_openai_tts_without_api_key_raises() -> None:
    """OpenAI TTS without API key must raise ValueError."""
    h = TTSHandler(TTSConfig(provider="openai", openai_api_key=None))
    h.openai_api_key = None  # ensure no env fallback
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        await h.synthesize("hello", Path("/tmp/out.ogg"))


@pytest.mark.asyncio
async def test_edge_tts_uses_correct_voice(tmp_path: Path) -> None:
    """Edge TTS must call Communicate with the configured voice."""
    output_file = tmp_path / "out.mp3"

    async def _fake_save(path: str) -> None:
        Path(path).write_bytes(b"\x00" * 64)

    h = TTSHandler(TTSConfig(provider="edge", edge_voice="hu-HU-NoemiNeural"))

    with patch("edge_tts.Communicate") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.save = AsyncMock(side_effect=_fake_save)
        mock_cls.return_value = mock_instance

        await h.synthesize("hello", output_file)

    mock_cls.assert_called_once()
    # voice is the second positional argument
    assert mock_cls.call_args[0][1] == "hu-HU-NoemiNeural"


@pytest.mark.asyncio
async def test_edge_tts_save_failure_raises(tmp_path: Path) -> None:
    """Exception from communicate.save() must be re-raised as RuntimeError."""
    h = TTSHandler(TTSConfig(provider="edge"))

    with patch("edge_tts.Communicate") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.save = AsyncMock(side_effect=Exception("network error"))
        mock_cls.return_value = mock_instance

        with pytest.raises(RuntimeError, match="Edge TTS failed"):
            await h.synthesize("hello", tmp_path / "out.mp3")


@pytest.mark.asyncio
async def test_text_truncation_respects_max_length(tmp_path: Path) -> None:
    """Text longer than max_text_length must be truncated before synthesis."""
    long_text = "a" * 5000
    captured_texts: list[str] = []

    async def _fake_save(path: str) -> None:
        Path(path).write_bytes(b"\x00" * 10)

    def _capture_communicate(text: str, voice: str, **kwargs: object) -> MagicMock:
        captured_texts.append(text)
        m = MagicMock()
        m.save = AsyncMock(side_effect=_fake_save)
        return m

    h = TTSHandler(TTSConfig(provider="edge", max_text_length=100))

    with patch("edge_tts.Communicate", side_effect=_capture_communicate):
        await h.synthesize(long_text, tmp_path / "out.mp3")

    assert len(captured_texts[0]) == 100
