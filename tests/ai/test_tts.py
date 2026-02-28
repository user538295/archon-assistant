"""Tests for TTS module — TTSConfig and TTSHandler."""
import pytest

from archon.ai.tts import TTSConfig, TTSHandler


# ──────────────────────────────────────────────────────────────────
# TTSConfig.is_enabled() — Bug fix: method was missing from dataclass
# ──────────────────────────────────────────────────────────────────


def test_tts_config_is_enabled_off() -> None:
    """auto='off' must return False."""
    cfg = TTSConfig(auto="off")
    assert cfg.is_enabled() is False


def test_tts_config_is_enabled_always() -> None:
    """auto='always' must return True."""
    cfg = TTSConfig(auto="always")
    assert cfg.is_enabled() is True


def test_tts_config_is_enabled_inbound() -> None:
    """auto='inbound' must return True."""
    cfg = TTSConfig(auto="inbound")
    assert cfg.is_enabled() is True


def test_tts_config_is_enabled_tagged() -> None:
    """auto='tagged' must return True."""
    cfg = TTSConfig(auto="tagged")
    assert cfg.is_enabled() is True


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
    h_on = TTSHandler(TTSConfig(auto="always"))
    h_off = TTSHandler(TTSConfig(auto="off"))
    assert h_on.is_enabled() is True
    assert h_off.is_enabled() is False


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
