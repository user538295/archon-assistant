"""Tests for archon_toolkit_voice — voice_status, voice_enable, voice_disable MCP tools (Task 2.1)."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

from archon.ai.archon_toolkit import ArchonToolkit
import archon.ai.archon_toolkit_voice as voice_module
from archon.ai.archon_toolkit_voice import (
    _handle_voice_status,
    _handle_voice_enable,
    _handle_voice_disable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_toolkit(config=None, config_file: str | None = None) -> ArchonToolkit:
    tk = ArchonToolkit(config=config)
    tk._config_file = config_file
    return tk


def _make_config(voice_enabled: bool = False) -> MagicMock:
    cfg = MagicMock()
    cfg.voice.enabled = voice_enabled
    return cfg


# ---------------------------------------------------------------------------
# voice_status tests
# ---------------------------------------------------------------------------


class TestVoiceStatus:
    async def test_voice_status_returns_json(self) -> None:
        """Patch VoiceInstaller.status(); assert all 4 JSON fields present with correct types."""
        toolkit = _make_toolkit(config=_make_config(voice_enabled=False))

        mock_status = {"whisper_installed": True, "ffmpeg_found": False, "edge_tts_installed": True}

        with patch("archon.ai.archon_toolkit_voice.VoiceInstaller") as mock_installer_cls:
            mock_installer_cls.return_value.status.return_value = mock_status
            result = await _handle_voice_status(toolkit, {})

        data = json.loads(result)
        assert "enabled" in data
        assert "whisper_installed" in data
        assert "ffmpeg_found" in data
        assert "edge_tts_installed" in data
        assert isinstance(data["enabled"], bool)
        assert isinstance(data["whisper_installed"], bool)
        assert isinstance(data["ffmpeg_found"], bool)
        assert isinstance(data["edge_tts_installed"], bool)
        assert data["whisper_installed"] is True
        assert data["ffmpeg_found"] is False
        assert data["edge_tts_installed"] is True

    async def test_voice_status_enabled_flag(self) -> None:
        """Config with voice.enabled=True → 'enabled': true in JSON."""
        toolkit = _make_toolkit(config=_make_config(voice_enabled=True))

        mock_status = {"whisper_installed": False, "ffmpeg_found": False, "edge_tts_installed": False}

        with patch("archon.ai.archon_toolkit_voice.VoiceInstaller") as mock_installer_cls:
            mock_installer_cls.return_value.status.return_value = mock_status
            result = await _handle_voice_status(toolkit, {})

        data = json.loads(result)
        assert data["enabled"] is True

    async def test_voice_status_no_config(self) -> None:
        """toolkit._config=None → 'enabled': false, no exception."""
        toolkit = _make_toolkit(config=None)

        mock_status = {"whisper_installed": False, "ffmpeg_found": False, "edge_tts_installed": True}

        with patch("archon.ai.archon_toolkit_voice.VoiceInstaller") as mock_installer_cls:
            mock_installer_cls.return_value.status.return_value = mock_status
            result = await _handle_voice_status(toolkit, {})

        data = json.loads(result)
        assert data["enabled"] is False


# ---------------------------------------------------------------------------
# voice_enable tests
# ---------------------------------------------------------------------------


class TestVoiceEnable:
    async def test_voice_enable_calls_set_config_value(self) -> None:
        """Assert set_config_value("voice.enabled","true", config_path) called via to_thread."""
        config_path = Path("/tmp/test_config.toml")
        toolkit = _make_toolkit(config_file=str(config_path))

        with patch("archon.ai.archon_toolkit_voice.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=None)
            with patch("archon.ai.archon_toolkit_voice.set_config_value") as mock_set:
                await _handle_voice_enable(toolkit, {})
                mock_asyncio.to_thread.assert_awaited_once_with(
                    mock_set, "voice.enabled", "true", config_path
                )

    async def test_voice_enable_returns_success_string(self) -> None:
        """Return value contains 'enabled'."""
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_voice.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=None)
            with patch("archon.ai.archon_toolkit_voice.set_config_value"):
                result = await _handle_voice_enable(toolkit, {})

        assert "enabled" in result.lower()

    async def test_voice_enable_uses_default_config_path_when_no_config_file(self) -> None:
        """When toolkit._config_file is None, use ~/.archon/config.toml."""
        toolkit = _make_toolkit(config_file=None)
        expected_path = Path.home() / ".archon" / "config.toml"

        with patch("archon.ai.archon_toolkit_voice.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=None)
            with patch("archon.ai.archon_toolkit_voice.set_config_value") as mock_set:
                await _handle_voice_enable(toolkit, {})
                mock_asyncio.to_thread.assert_awaited_once_with(
                    mock_set, "voice.enabled", "true", expected_path
                )


# ---------------------------------------------------------------------------
# voice_disable tests
# ---------------------------------------------------------------------------


class TestVoiceDisable:
    async def test_voice_disable_calls_set_config_value(self) -> None:
        """Assert set_config_value("voice.enabled","false", config_path) called."""
        config_path = Path("/tmp/test_config.toml")
        toolkit = _make_toolkit(config_file=str(config_path))

        with patch("archon.ai.archon_toolkit_voice.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=None)
            with patch("archon.ai.archon_toolkit_voice.set_config_value") as mock_set:
                await _handle_voice_disable(toolkit, {})
                mock_asyncio.to_thread.assert_awaited_once_with(
                    mock_set, "voice.enabled", "false", config_path
                )

    async def test_voice_disable_returns_success_string(self) -> None:
        """Return value contains 'disabled'."""
        toolkit = _make_toolkit()

        with patch("archon.ai.archon_toolkit_voice.asyncio") as mock_asyncio:
            mock_asyncio.to_thread = AsyncMock(return_value=None)
            with patch("archon.ai.archon_toolkit_voice.set_config_value"):
                result = await _handle_voice_disable(toolkit, {})

        assert "disabled" in result.lower()


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


class TestVoiceToolsRegistration:
    def test_voice_tools_registered_in_toolkit(self) -> None:
        """voice_status, voice_enable, voice_disable are in toolkit.tool_names after construction."""
        toolkit = ArchonToolkit(config=None)
        assert "voice_status" in toolkit.tool_names
        assert "voice_enable" in toolkit.tool_names
        assert "voice_disable" in toolkit.tool_names

    def test_rag_tools_still_registered_after_voice_added(self) -> None:
        """rag_status is still in toolkit.tool_names — voice registration doesn't break RAG."""
        toolkit = ArchonToolkit(config=None)
        assert "rag_status" in toolkit.tool_names
