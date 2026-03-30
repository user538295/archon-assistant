"""archon voice — CLI subcommand for managing voice features (Task 1.6)."""
from __future__ import annotations

import argparse
from pathlib import Path

from archon.voice.install import VoiceInstaller

_CONFIG_PATH = Path.home() / ".archon" / "config.toml"


def run_voice(
    args: argparse.Namespace,
    voice_parser: argparse.ArgumentParser | None = None,
) -> int:
    """Dispatch to the appropriate voice sub-action."""
    dispatch = {
        "install": _run_install,
        "status": _run_status,
        "enable": _run_enable,
        "disable": _run_disable,
    }
    cmd = getattr(args, "voice_command", None)
    if cmd in (None, "help"):
        if voice_parser:
            voice_parser.print_help()
        return 0
    handler = dispatch.get(cmd)
    if handler is None:
        print(f"Unknown voice command: {cmd}")
        return 1
    return handler(args)


def _run_install(args: argparse.Namespace) -> int:
    return VoiceInstaller().run(non_interactive=getattr(args, "non_interactive", False))


def _run_status(args: argparse.Namespace) -> int:
    s = VoiceInstaller().status()
    print(f"openai-whisper : {'installed'   if s['whisper_installed'] else 'not installed'}")
    print(f"edge-tts       : {'installed'   if s['edge_tts_installed'] else 'not installed'}")
    print(f"ffmpeg         : {'found'       if s['ffmpeg_found']       else 'not found'}")
    return 0


def _run_enable(args: argparse.Namespace) -> int:
    from archon.config.config_rw import set_config_value
    set_config_value("voice.enabled", "true", _CONFIG_PATH)
    print("voice.enabled = true")
    print("Run 'archon restart' to apply.")
    return 0


def _run_disable(args: argparse.Namespace) -> int:
    from archon.config.config_rw import set_config_value
    set_config_value("voice.enabled", "false", _CONFIG_PATH)
    print("voice.enabled = false")
    print("Run 'archon restart' to apply.")
    return 0
