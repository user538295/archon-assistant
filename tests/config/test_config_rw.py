"""Tests for archon.config.config_rw — pure config read/write library functions."""
from __future__ import annotations
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from archon.config.config_rw import get_config_value, set_config_value

SAMPLE_TOML = """\
[access]
allowed_user_ids = [123]

[notifications]
mode = "normal"
interval_minutes = 2

[session]
working_directory = "/tmp"
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(SAMPLE_TOML)
    return cfg


# ──────────────────────────────────────────────────────────────────
# get_config_value tests
# ──────────────────────────────────────────────────────────────────


def test_get_config_value_string(config_file: Path) -> None:
    result = get_config_value("notifications.mode", config_file)
    assert result == "normal"
    assert isinstance(result, str)


def test_get_config_value_int(config_file: Path) -> None:
    result = get_config_value("notifications.interval_minutes", config_file)
    assert result == 2
    assert isinstance(result, int)


def test_get_config_value_nested(config_file: Path) -> None:
    result = get_config_value("session.working_directory", config_file)
    assert result == "/tmp"


def test_get_config_value_missing_key(config_file: Path) -> None:
    with pytest.raises(KeyError):
        get_config_value("nonexistent.deep.key", config_file)


# ──────────────────────────────────────────────────────────────────
# set_config_value tests
# ──────────────────────────────────────────────────────────────────


def test_set_config_value_string(config_file: Path) -> None:
    set_config_value("notifications.mode", "quiet", config_file)
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["notifications"]["mode"] == "quiet"


def test_set_config_value_int_coercion(config_file: Path) -> None:
    set_config_value("notifications.interval_minutes", "42", config_file)
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["notifications"]["interval_minutes"] == 42
    assert isinstance(data["notifications"]["interval_minutes"], int)


def test_set_config_value_bool_coercion(config_file: Path) -> None:
    set_config_value("notifications.mode", "true", config_file)
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["notifications"]["mode"] is True


def test_set_config_value_list_coercion(config_file: Path) -> None:
    set_config_value("access.allowed_user_ids", "[1, 2, 3]", config_file)
    with open(config_file, "rb") as f:
        data = tomllib.load(f)
    assert data["access"]["allowed_user_ids"] == [1, 2, 3]
    assert isinstance(data["access"]["allowed_user_ids"], list)


def test_set_config_value_invalid_roundtrip(config_file: Path) -> None:
    original_content = config_file.read_text()
    with patch("archon.config.config_rw.tomllib.loads", side_effect=ValueError("bad toml")):
        with pytest.raises(ValueError):
            set_config_value("notifications.mode", "quiet", config_file)
    # Original file must be unchanged
    assert config_file.read_text() == original_content


def test_set_config_value_atomic(config_file: Path) -> None:
    """No .toml.tmp file must remain after a failed write."""
    tmp_file = config_file.with_suffix(".toml.tmp")
    with patch("archon.config.config_rw.tomllib.loads", side_effect=ValueError("bad toml")):
        with pytest.raises(ValueError):
            set_config_value("notifications.mode", "quiet", config_file)
    assert not tmp_file.exists(), f"Temp file {tmp_file} was not cleaned up"
