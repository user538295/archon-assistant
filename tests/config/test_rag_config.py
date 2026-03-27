"""Tests for RagConfig FEAT-021 changes — Task 2.1."""
import logging
from pathlib import Path

import pytest

from archon.config.loader import RagConfig, load_config


_BASE_TOML = """\
[access]
allowed_user_ids = [1]

[session]
working_directory = "/tmp"
"""

_BASE_ENV = "TELEGRAM_BOT_TOKEN=fake_token\n"


def _files(tmp_path: Path, toml_extra: str = "") -> tuple[Path, Path]:
    env = tmp_path / ".env"
    env.write_text(_BASE_ENV)
    cfg = tmp_path / "config.toml"
    cfg.write_text(_BASE_TOML + toml_extra)
    return env, cfg


def test_rag_config_default_collections() -> None:
    """RagConfig default collections list contains history and workspace paths."""
    r = RagConfig()
    assert "~/.archon/history/sessions" in r.collections
    assert "~/.archon/workspace" in r.collections


def test_rag_config_default_sync_timeout() -> None:
    """RagConfig default sync_timeout_seconds is 30."""
    r = RagConfig()
    assert r.sync_timeout_seconds == 30


def test_rag_config_parses_collections_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads collections list from [rag] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[rag]\ncollections = ["/data/docs", "/data/notes"]\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.collections == ["/data/docs", "/data/notes"]


def test_rag_config_history_collection_field_removed() -> None:
    """RagConfig no longer has a history_collection field."""
    r = RagConfig()
    assert not hasattr(r, "history_collection"), (
        "history_collection must be removed from RagConfig (FEAT-021 Task 2.1)"
    )


def test_rag_config_warns_on_legacy_history_collection_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """load_config logs a WARNING when history_collection key is present in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[rag]\nhistory_collection = "my-old-collection"\n'
    env, cfg = _files(tmp_path, extra)

    with caplog.at_level(logging.WARNING):
        config = load_config(env_file=env, config_file=cfg)

    # The key is ignored — derived name is used, not the explicit value
    assert not hasattr(config.rag, "history_collection")
    # A warning must be logged
    assert any("history_collection" in record.message for record in caplog.records)


def test_rag_config_sets_deprecated_flag_on_history_collection_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config sets deprecated_history_collection=True when key is present in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[rag]\nhistory_collection = "my-old-collection"\n'
    env, cfg = _files(tmp_path, extra)

    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.deprecated_history_collection is True


def test_rag_config_deprecated_flag_false_without_history_collection_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config leaves deprecated_history_collection=False when key is absent."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path)

    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.deprecated_history_collection is False
