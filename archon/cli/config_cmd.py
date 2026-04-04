"""Config view and edit commands for the Archon CLI."""
from __future__ import annotations
import os
import re
import shlex
import subprocess
from pathlib import Path

from archon.config.loader import _file_lock, _file_unlock
from archon.config.config_rw import (
    get_config_value,
    set_config_value,
    _coerce_value,
    _is_valid_toml_array,
)

_CONFIG_PATH = Path.home() / ".archon" / "config.toml"

_KNOWN_SECTIONS = frozenset({
    "access", "session", "output", "notifications", "logging",
    "history", "models", "plugins", "search", "schedule",
    "background_agents", "voice", "reminder",
})


def run_config(args: object) -> int:
    cmd = getattr(args, "config_command", None) or "show"
    if cmd == "show":
        return _run_show()
    if cmd == "edit":
        return _run_edit()
    if cmd == "get":
        return _run_get(args.key)  # type: ignore[attr-defined]
    if cmd == "set":
        return _run_set(args.key, args.value)  # type: ignore[attr-defined]
    print(f"Unknown config command: {cmd}")
    return 1


def _run_show() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Config not found: {_CONFIG_PATH}")
        return 1
    toml_str = _CONFIG_PATH.read_text()
    redacted = re.sub(r'(?i)(token|password|secret|key)\s*=\s*"[^"]*"', r'\1 = "***"', toml_str)
    print(f"# {_CONFIG_PATH}")
    print(redacted)
    return 0


def _run_edit() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Config not found: {_CONFIG_PATH}")
        return 1
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    editor_var = (
        "EDITOR" if os.environ.get("EDITOR")
        else "VISUAL" if os.environ.get("VISUAL")
        else "EDITOR"
    )
    try:
        cmd = shlex.split(editor) + [str(_CONFIG_PATH)]
    except ValueError:
        print(f"Invalid {editor_var} value: {editor}")
        return 1
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print(f"Editor not found: {editor}")
        return 1
    return result.returncode


def _run_get(key: str) -> int:
    if not _CONFIG_PATH.exists():
        print(f"Config not found: {_CONFIG_PATH}")
        return 1
    try:
        value = get_config_value(key, _CONFIG_PATH)
        print(value)
        return 0
    except KeyError:
        print(f"Key not found: {key}")
        return 1
    except Exception as e:
        print(f"Failed to parse config: {e}")
        return 1


def _run_set(key: str, value: str) -> int:
    if not _CONFIG_PATH.exists():
        print(f"Config not found: {_CONFIG_PATH}")
        return 1

    # Warn if the top-level section is unknown (CLI-specific behaviour)
    parts = key.split(".")
    top_section = parts[0]
    if len(parts) > 1 and top_section not in _KNOWN_SECTIONS:
        # Peek at existing doc to check whether the section already exists
        try:
            import tomlkit as _tomlkit
            existing_doc = _tomlkit.parse(_CONFIG_PATH.read_text())
        except Exception:
            existing_doc = {}
        if top_section not in existing_doc:
            print(
                f"Warning: '{top_section}' is not a known config section. "
                f"Known sections: {', '.join(sorted(_KNOWN_SECTIONS))}"
            )

    try:
        coerced = _coerce_value(value)
        set_config_value(key, value, _CONFIG_PATH)
        print(f"Set {key} = {coerced}")
        return 0
    except ValueError as e:
        msg = str(e)
        if "intermediate key is not a table" in msg:
            print(f"Cannot set {key}: intermediate key is not a table")
        else:
            print(f"Round-trip validation failed, config restored: {e}")
        return 1
    except Exception as e:
        print(f"Failed to parse config: {e}")
        return 1
