"""Voice-related MCP tools for ArchonToolkit (FEAT-025)."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("archon")

if TYPE_CHECKING:
    from archon.ai.archon_toolkit import ArchonToolkit

# VoiceInstaller and set_config_value are imported lazily so this module remains
# importable even when openai-whisper is not installed.
try:
    from archon.voice.install import VoiceInstaller
    from archon.config.config_rw import set_config_value
except ImportError:  # pragma: no cover
    VoiceInstaller = None  # type: ignore[misc,assignment]
    set_config_value = None  # type: ignore[assignment]


_VOICE_STATUS_SCHEMA: dict[str, Any] = {
    "name": "voice_status",
    "description": (
        "Check voice feature status — whether voice is enabled in config and "
        "which dependencies (Whisper, ffmpeg, edge-tts) are installed."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_voice_status(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Return voice status as a JSON string."""
    import archon.ai.archon_toolkit_voice as _self  # noqa: PLC0415

    installer = _self.VoiceInstaller()
    status = installer.status()

    enabled: bool = False
    if toolkit._config is not None:
        enabled = bool(toolkit._config.voice.enabled)

    return json.dumps({
        "enabled": enabled,
        "whisper_installed": bool(status.get("whisper_installed", False)),
        "ffmpeg_found": bool(status.get("ffmpeg_found", False)),
        "edge_tts_installed": bool(status.get("edge_tts_installed", False)),
    })


_VOICE_ENABLE_SCHEMA: dict[str, Any] = {
    "name": "voice_enable",
    "description": "Enable voice features in config. Restart Archon to apply.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_voice_enable(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Enable voice in config and return a success string."""
    import archon.ai.archon_toolkit_voice as _self  # noqa: PLC0415

    config_path = (
        Path(toolkit._config_file)
        if toolkit._config_file
        else Path.home() / ".archon" / "config.toml"
    )
    await _self.asyncio.to_thread(_self.set_config_value, "voice.enabled", "true", config_path)
    return "Voice enabled in config. Restart Archon to apply."


_VOICE_DISABLE_SCHEMA: dict[str, Any] = {
    "name": "voice_disable",
    "description": "Disable voice features in config. Restart Archon to apply.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_voice_disable(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Disable voice in config and return a success string."""
    import archon.ai.archon_toolkit_voice as _self  # noqa: PLC0415

    config_path = (
        Path(toolkit._config_file)
        if toolkit._config_file
        else Path.home() / ".archon" / "config.toml"
    )
    await _self.asyncio.to_thread(_self.set_config_value, "voice.enabled", "false", config_path)
    return "Voice disabled in config. Restart Archon to apply."


def _register_voice_tools(toolkit: "ArchonToolkit") -> None:
    """Register voice-related tools into the given toolkit instance."""
    toolkit.register_tool(
        "voice_status",
        _VOICE_STATUS_SCHEMA,
        functools.partial(_handle_voice_status, toolkit),
    )
    toolkit.register_tool(
        "voice_enable",
        _VOICE_ENABLE_SCHEMA,
        functools.partial(_handle_voice_enable, toolkit),
    )
    toolkit.register_tool(
        "voice_disable",
        _VOICE_DISABLE_SCHEMA,
        functools.partial(_handle_voice_disable, toolkit),
    )
