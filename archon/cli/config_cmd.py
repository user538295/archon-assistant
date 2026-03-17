"""Config view and edit commands for the Archon CLI."""
from __future__ import annotations
import json
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path

import tomlkit

from archon.config.loader import atomic_write, _file_lock, _file_unlock

_CONFIG_PATH = Path.home() / ".archon" / "config.toml"

_KNOWN_SECTIONS = frozenset({
    "access", "session", "output", "notifications", "logging",
    "history", "models", "plugins", "qmd", "schedule",
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
        with open(_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"Failed to parse config: {e}")
        return 1
    try:
        current: object = data
        for part in key.split("."):
            current = current[part]  # type: ignore[index]
        print(current)
        return 0
    except (KeyError, TypeError):
        print(f"Key not found: {key}")
        return 1


def _is_valid_toml_array(arr: list) -> bool:  # type: ignore[type-arg]
    """Validate that an array contains only homogeneous primitive types.

    Rejects: nested arrays, arrays of dicts/objects, mixed-type elements.
    Accepts: empty arrays, arrays where all elements share one primitive type.
    """
    if not arr:
        return True
    allowed = (int, float, str, bool)
    first_type = type(arr[0])
    if first_type not in allowed:
        return False
    return all(type(elem) is first_type for elem in arr)


def _coerce_value(value: str) -> int | float | bool | str | list:  # type: ignore[type-arg]
    # Try JSON first — handles arrays like [1, 2, 3] or ["a", "b"]
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                if not _is_valid_toml_array(parsed):
                    return value  # reject nested/mixed/object arrays — return raw string
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _run_set(key: str, value: str) -> int:
    if not _CONFIG_PATH.exists():
        print(f"Config not found: {_CONFIG_PATH}")
        return 1

    lock_file = _CONFIG_PATH.with_suffix(".toml.lock")
    lock_f = lock_file.open("w")
    try:
        _file_lock(lock_f)

        try:
            doc = tomlkit.parse(_CONFIG_PATH.read_text())
        except Exception as e:
            print(f"Failed to parse config: {e}")
            return 1

        original_content = _CONFIG_PATH.read_text()

        parts = key.split(".")
        # Warn if the top-level section is unknown (before auto-creation)
        top_section = parts[0]
        if len(parts) > 1 and top_section not in _KNOWN_SECTIONS and top_section not in doc:
            print(
                f"Warning: '{top_section}' is not a known config section. "
                f"Known sections: {', '.join(sorted(_KNOWN_SECTIONS))}"
            )
        container: object = doc
        for part in parts[:-1]:
            if not isinstance(container, dict):
                print(f"Cannot set {key}: intermediate key is not a table")
                return 1
            if part not in container:  # type: ignore[operator]
                container[part] = tomlkit.table()  # type: ignore[index]
            container = container[part]  # type: ignore[index]
        if not isinstance(container, dict):
            print(f"Cannot set {key}: intermediate key is not a table")
            return 1
        coerced = _coerce_value(value)
        container[parts[-1]] = coerced  # type: ignore[index]

        new_content = tomlkit.dumps(doc)

        # Round-trip validation: ensure the TOML we're about to write is loadable
        try:
            tomllib.loads(new_content)
        except Exception as e:
            # Restore original content
            atomic_write(_CONFIG_PATH, original_content)
            print(f"Round-trip validation failed, config restored: {e}")
            return 1

        atomic_write(_CONFIG_PATH, new_content)
        print(f"Set {key} = {coerced}")
        return 0
    finally:
        _file_unlock(lock_f)
        lock_f.close()
