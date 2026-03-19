"""Pure config read/write library functions shared by CLI and MCP toolkit."""
from __future__ import annotations
import json
import tomllib
from pathlib import Path
from typing import Any

import tomlkit

from archon.config.loader import atomic_write, _file_lock, _file_unlock


def get_config_value(path: str, config_file: Path) -> Any:
    """Navigate dot-notation *path* in a tomllib-parsed dict.

    Raises ``KeyError`` if any segment of the path is not found.
    """
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    current: Any = data
    for part in path.split("."):
        current = current[part]  # raises KeyError if missing
    return current


def set_config_value(path: str, value: str, config_file: Path) -> None:
    """Write a coerced value at dot-notation *path* in *config_file*.

    - Acquires a file lock around read-modify-write.
    - Runs round-trip validation via ``tomllib.loads()`` before writing.
    - Writes atomically via ``atomic_write``.

    Raises ``ValueError`` if round-trip validation fails (original file unchanged).
    """
    lock_file = config_file.with_suffix(".toml.lock")
    lock_f = lock_file.open("w")
    try:
        _file_lock(lock_f)

        original_content = config_file.read_text()
        doc = tomlkit.parse(original_content)

        parts = path.split(".")
        container: Any = doc
        for part in parts[:-1]:
            if not isinstance(container, dict):
                raise ValueError(f"Cannot set {path}: intermediate key is not a table")
            if part not in container:
                container[part] = tomlkit.table()
            container = container[part]
        if not isinstance(container, dict):
            raise ValueError(f"Cannot set {path}: intermediate key is not a table")

        coerced = _coerce_value(value)
        container[parts[-1]] = coerced

        new_content = tomlkit.dumps(doc)

        # Round-trip validation: ensure the TOML we're about to write is loadable
        try:
            tomllib.loads(new_content)
        except Exception as e:
            raise ValueError(f"Round-trip validation failed: {e}") from e

        atomic_write(config_file, new_content)
    finally:
        _file_unlock(lock_f)
        lock_f.close()


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
