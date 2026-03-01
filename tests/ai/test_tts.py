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
async def test_edge_tts_builds_correct_command(tmp_path: Path) -> None:
    """Edge TTS must build the correct subprocess command."""
    captured_cmd: list[str] = []

    async def fake_exec(*cmd, **_kw):
        captured_cmd.extend(cmd)
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    h = TTSHandler(TTSConfig(provider="edge", edge_voice="hu-HU-NoemiNeural"))

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await h.synthesize("hello", tmp_path / "out.ogg")

    assert "edge-tts" in captured_cmd
    assert "--voice" in captured_cmd
    idx = captured_cmd.index("--voice")
    assert captured_cmd[idx + 1] == "hu-HU-NoemiNeural"


@pytest.mark.asyncio
async def test_edge_tts_nonzero_exit_raises(tmp_path: Path) -> None:
    """Edge TTS non-zero exit must raise RuntimeError."""
    async def fake_exec(*cmd, **_kw):
        proc = MagicMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"error"))
        return proc

    h = TTSHandler(TTSConfig(provider="edge"))

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        with pytest.raises(RuntimeError, match="Edge TTS failed"):
            await h.synthesize("hello", tmp_path / "out.ogg")


@pytest.mark.asyncio
async def test_text_truncation_respects_max_length() -> None:
    """Text longer than max_text_length must be truncated before synthesis."""
    long_text = "a" * 5000
    captured_text: list[str] = []

    async def fake_exec(*cmd, **_kw):
        # Find the --text arg
        text_idx = cmd.index("--text") + 1
        captured_text.append(cmd[text_idx])
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"", b""))
        return proc

    h = TTSHandler(TTSConfig(provider="edge", max_text_length=100))

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await h.synthesize(long_text, Path("/tmp/out.ogg"))

    assert len(captured_text[0]) == 100
