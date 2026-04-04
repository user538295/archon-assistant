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


def test_rag_config_default_sync_timeout_is_zero() -> None:
    """RagConfig default sync_timeout_seconds is 0 (no timeout)."""
    r = RagConfig()
    assert r.sync_timeout_seconds == 0


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


# ---------------------------------------------------------------------------
# Task 2.2 — routing parameters
# ---------------------------------------------------------------------------

def test_routing_defaults() -> None:
    """RagConfig routing fields have correct defaults."""
    r = RagConfig()
    assert r.max_parallel_collections == 3
    assert r.routing_confidence_threshold == 0.30
    assert r.routing_shortlist_size == 8


def test_pinned_collections_default() -> None:
    """RagConfig pinned_collections defaults to history/sessions and workspace."""
    r = RagConfig()
    assert r.pinned_collections == [
        "~/.archon/history/sessions",
        "~/.archon/workspace",
    ]


def test_routing_config_parsed_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads routing parameters from [rag] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[rag]\n"
        "max_parallel_collections = 5\n"
        "routing_confidence_threshold = 0.50\n"
        "routing_shortlist_size = 12\n"
    )
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.max_parallel_collections == 5
    assert config.rag.routing_confidence_threshold == 0.50
    assert config.rag.routing_shortlist_size == 12


def test_pinned_collections_parsed_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads pinned_collections from [rag] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[rag]\npinned_collections = ["/custom/pinned"]\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.pinned_collections == ["/custom/pinned"]


def test_pinned_collections_empty_list_parsed_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pinned_collections = [] disables pinned behaviour."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[rag]\npinned_collections = []\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.pinned_collections == []


def test_max_parallel_collections_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_parallel_collections = 0 raises ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[rag]\nmax_parallel_collections = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="max_parallel_collections must be >= 1"):
        load_config(env_file=env, config_file=cfg)


def test_routing_confidence_threshold_out_of_range_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """routing_confidence_threshold outside [0.0, 1.0] raises ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[rag]\nrouting_confidence_threshold = 1.5\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="routing_confidence_threshold must be in"):
        load_config(env_file=env, config_file=cfg)


def test_routing_shortlist_size_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """routing_shortlist_size = 0 raises ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[rag]\nrouting_shortlist_size = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="routing_shortlist_size must be >= 1"):
        load_config(env_file=env, config_file=cfg)


# ---------------------------------------------------------------------------
# Task 4.2 — auto_reindex_on_chunk_size_change
# ---------------------------------------------------------------------------

def test_rag_auto_reindex_on_chunk_size_change_default() -> None:
    """RagConfig.auto_reindex_on_chunk_size_change defaults to False when absent from TOML."""
    r = RagConfig()
    assert r.auto_reindex_on_chunk_size_change is False


def test_rag_auto_reindex_on_chunk_size_change_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config parses auto_reindex_on_chunk_size_change = true from [rag] section."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[rag]\nauto_reindex_on_chunk_size_change = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.auto_reindex_on_chunk_size_change is True


# ---------------------------------------------------------------------------
# Task 8.1 — watch field
# ---------------------------------------------------------------------------

def test_rag_config_watch_defaults_false() -> None:
    """RagConfig() defaults watch to False."""
    r = RagConfig()
    assert r.watch is False


def test_rag_config_watch_reads_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config parses watch = true from [rag] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[rag]\nwatch = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.rag.watch is True
