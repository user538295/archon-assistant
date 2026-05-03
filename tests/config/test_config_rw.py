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


# ──────────────────────────────────────────────────────────────────
# config_collections_append / config_collections_remove tests
# ──────────────────────────────────────────────────────────────────

from archon.config.config_rw import config_collections_append, config_collections_remove  # noqa: E402


TOML_WITH_RAG = """\
[search]
collections = ["/existing/path"]
"""

TOML_WITHOUT_RAG = """\
[access]
allowed_user_ids = [123]
"""


@pytest.fixture
def rag_config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(TOML_WITH_RAG)
    return cfg


@pytest.fixture
def no_rag_config_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(TOML_WITHOUT_RAG)
    return cfg


def test_config_collections_append_adds_path(rag_config_file: Path) -> None:
    config_collections_append(rag_config_file, "/new/path")
    with open(rag_config_file, "rb") as f:
        data = tomllib.load(f)
    assert "/new/path" in data["search"]["collections"]


def test_config_collections_remove_removes_path(rag_config_file: Path) -> None:
    config_collections_remove(rag_config_file, "/existing/path")
    with open(rag_config_file, "rb") as f:
        data = tomllib.load(f)
    resolved = str(Path("/existing/path").expanduser().resolve())
    remaining = [str(Path(p).expanduser().resolve()) for p in data["search"]["collections"]]
    assert resolved not in remaining


def test_config_collections_remove_noop_if_missing_section(no_rag_config_file: Path) -> None:
    # Must not raise even when [search] section is absent
    config_collections_remove(no_rag_config_file, "/some/path")


def test_config_collections_append_uses_file_lock(rag_config_file: Path) -> None:
    with patch("archon.config.config_rw._file_lock") as mock_lock, \
         patch("archon.config.config_rw._file_unlock") as mock_unlock:
        config_collections_append(rag_config_file, "/new/path")
    mock_lock.assert_called_once()
    mock_unlock.assert_called_once()


def test_config_collections_remove_uses_file_lock(rag_config_file: Path) -> None:
    with patch("archon.config.config_rw._file_lock") as mock_lock, \
         patch("archon.config.config_rw._file_unlock") as mock_unlock:
        config_collections_remove(rag_config_file, "/existing/path")
    mock_lock.assert_called_once()
    mock_unlock.assert_called_once()


def test_config_collections_remove_noop_if_missing_collections_key(tmp_path: Path) -> None:
    """remove noop when [search] section exists but 'collections' key is absent."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("[search]\nenabled = true\n")
    config_collections_remove(cfg, "/some/path")  # must not raise


def test_config_collections_append_creates_missing_collections_key(tmp_path: Path) -> None:
    """append creates collections array when [search] exists but collections is absent."""
    import tomllib  # noqa: PLC0415
    cfg = tmp_path / "config.toml"
    cfg.write_text("[search]\nenabled = true\n")
    config_collections_append(cfg, "/new/path")
    with open(cfg, "rb") as f:
        data = tomllib.load(f)
    assert "/new/path" in data["search"]["collections"]


def test_rag_cmd_functions_importable_after_extraction() -> None:
    from archon.config.config_rw import config_collections_append as append_fn  # noqa: PLC0415
    from archon.config.config_rw import config_collections_remove as remove_fn  # noqa: PLC0415
    from archon_search.sync import manifest_remove_entry  # noqa: PLC0415

    assert callable(append_fn)
    assert callable(remove_fn)
    assert callable(manifest_remove_entry)
