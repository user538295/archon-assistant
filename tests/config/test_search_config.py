"""Tests for SearchConfig FEAT-021 changes — Task 2.1."""
import logging
from pathlib import Path

import pytest

from archon.config.loader import SearchConfig, load_config


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


def test_search_config_default_collections() -> None:
    """SearchConfig default collections is empty — system defaults live in pinned_collections."""
    r = SearchConfig()
    assert r.collections == []


def test_search_config_default_all_indexed_collections() -> None:
    """SearchConfig.all_indexed_collections defaults to history + workspace (from pinned_collections)."""
    r = SearchConfig()
    assert "~/.archon/history/sessions" in r.all_indexed_collections
    assert "~/.archon/workspace" in r.all_indexed_collections


def test_search_config_default_sync_timeout_is_zero() -> None:
    """SearchConfig default sync_timeout_seconds is 0 (no timeout)."""
    r = SearchConfig()
    assert r.sync_timeout_seconds == 0


def test_search_config_parses_collections_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads collections list from [search] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\ncollections = ["/data/docs", "/data/notes"]\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.collections == ["/data/docs", "/data/notes"]


def test_search_config_history_collection_field_removed() -> None:
    """SearchConfig no longer has a history_collection field."""
    r = SearchConfig()
    assert not hasattr(r, "history_collection"), (
        "history_collection must be removed from SearchConfig (FEAT-021 Task 2.1)"
    )


def test_search_config_warns_on_legacy_history_collection_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """load_config logs a WARNING when history_collection key is present in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\nhistory_collection = "my-old-collection"\n'
    env, cfg = _files(tmp_path, extra)

    with caplog.at_level(logging.WARNING):
        config = load_config(env_file=env, config_file=cfg)

    # The key is ignored — derived name is used, not the explicit value
    assert not hasattr(config.search, "history_collection")
    # A warning must be logged
    assert any("history_collection" in record.message for record in caplog.records)


def test_search_config_sets_deprecated_flag_on_history_collection_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config sets deprecated_history_collection=True when key is present in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\nhistory_collection = "my-old-collection"\n'
    env, cfg = _files(tmp_path, extra)

    config = load_config(env_file=env, config_file=cfg)

    assert config.search.deprecated_history_collection is True


def test_search_config_deprecated_flag_false_without_history_collection_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config leaves deprecated_history_collection=False when key is absent."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path)

    config = load_config(env_file=env, config_file=cfg)

    assert config.search.deprecated_history_collection is False


# ---------------------------------------------------------------------------
# Task 2.2 — routing parameters
# ---------------------------------------------------------------------------

def test_routing_defaults() -> None:
    """SearchConfig routing fields have correct defaults."""
    r = SearchConfig()
    assert r.max_parallel_collections == 3
    assert r.routing_confidence_threshold == 0.30
    assert r.routing_shortlist_size == 8


def test_pinned_collections_default() -> None:
    """SearchConfig pinned_collections defaults to history/sessions and workspace."""
    r = SearchConfig()
    assert r.pinned_collections == [
        "~/.archon/history/sessions",
        "~/.archon/workspace",
    ]


def test_routing_config_parsed_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads routing parameters from [search] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[search]\n"
        "max_parallel_collections = 5\n"
        "routing_confidence_threshold = 0.50\n"
        "routing_shortlist_size = 12\n"
    )
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.max_parallel_collections == 5
    assert config.search.routing_confidence_threshold == 0.50
    assert config.search.routing_shortlist_size == 12


def test_pinned_collections_parsed_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads pinned_collections from [search] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\npinned_collections = ["/custom/pinned"]\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.pinned_collections == ["/custom/pinned"]


def test_pinned_collections_empty_list_parsed_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """pinned_collections = [] disables pinned behaviour."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\npinned_collections = []\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.pinned_collections == []


def test_max_parallel_collections_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_parallel_collections = 0 raises ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nmax_parallel_collections = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="max_parallel_collections must be >= 1"):
        load_config(env_file=env, config_file=cfg)


# ---------------------------------------------------------------------------
# all_indexed_collections property
# ---------------------------------------------------------------------------


class TestAllIndexedCollections:
    def test_pinned_only_when_collections_empty(self) -> None:
        """If collections is empty, only pinned paths are indexed."""
        cfg = SearchConfig(collections=[], pinned_collections=["/pinned/docs"])
        assert cfg.all_indexed_collections == ["/pinned/docs"]

    def test_collections_only_when_pinned_empty(self) -> None:
        """If pinned_collections is empty, only user collections are indexed."""
        cfg = SearchConfig(collections=["/user/notes"], pinned_collections=[])
        assert cfg.all_indexed_collections == ["/user/notes"]

    def test_both_empty_returns_empty(self) -> None:
        """No paths configured → nothing to index."""
        cfg = SearchConfig(collections=[], pinned_collections=[])
        assert cfg.all_indexed_collections == []

    def test_union_deduplicates_shared_path(self) -> None:
        """A path in both lists appears only once in the result."""
        cfg = SearchConfig(collections=["/shared"], pinned_collections=["/shared"])
        assert cfg.all_indexed_collections == ["/shared"]

    def test_pinned_come_first(self) -> None:
        """Pinned collections precede user collections in the result."""
        cfg = SearchConfig(
            collections=["/user/a", "/user/b"],
            pinned_collections=["/pinned/sys"],
        )
        assert cfg.all_indexed_collections == ["/pinned/sys", "/user/a", "/user/b"]

    def test_shared_path_not_duplicated_pinned_first_ordering(self) -> None:
        """Shared path keeps its pinned-first position; unique user path appended."""
        cfg = SearchConfig(
            collections=["/shared", "/user/only"],
            pinned_collections=["/pinned/sys", "/shared"],
        )
        assert cfg.all_indexed_collections == ["/pinned/sys", "/shared", "/user/only"]


def test_routing_confidence_threshold_out_of_range_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """routing_confidence_threshold outside [0.0, 1.0] raises ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nrouting_confidence_threshold = 1.5\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="routing_confidence_threshold must be in"):
        load_config(env_file=env, config_file=cfg)


def test_routing_shortlist_size_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """routing_shortlist_size = 0 raises ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nrouting_shortlist_size = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="routing_shortlist_size must be >= 1"):
        load_config(env_file=env, config_file=cfg)


# ---------------------------------------------------------------------------
# Task 4.2 — auto_reindex_on_chunk_size_change
# ---------------------------------------------------------------------------

def test_search_auto_reindex_on_chunk_size_change_default() -> None:
    """SearchConfig.auto_reindex_on_chunk_size_change defaults to False when absent from TOML."""
    r = SearchConfig()
    assert r.auto_reindex_on_chunk_size_change is False


def test_search_auto_reindex_on_chunk_size_change_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config parses auto_reindex_on_chunk_size_change = true from [search] section."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nauto_reindex_on_chunk_size_change = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.auto_reindex_on_chunk_size_change is True


# ---------------------------------------------------------------------------
# Task 8.1 — watch field
# ---------------------------------------------------------------------------

def test_search_config_watch_defaults_false() -> None:
    """SearchConfig() defaults watch to False."""
    r = SearchConfig()
    assert r.watch is False


def test_search_config_watch_reads_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config parses watch = true from [search] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nwatch = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.watch is True


# ---------------------------------------------------------------------------
# C1-A — loader collections fallback must be [] not _DEFAULT_SEARCH_COLLECTIONS
# ---------------------------------------------------------------------------


def test_loader_collections_default_is_empty_when_absent_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When [search] section has no 'collections' key, loader must default to [] not system paths."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    # No collections key in TOML
    extra = "\n[search]\nenabled = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.collections == [], (
        "collections must default to [] — system defaults live in pinned_collections"
    )


def test_loader_pinned_collections_still_defaults_to_system_paths_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When 'pinned_collections' is absent from TOML, loader keeps _DEFAULT_SEARCH_COLLECTIONS."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nenabled = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert "~/.archon/history/sessions" in config.search.pinned_collections
    assert "~/.archon/workspace" in config.search.pinned_collections
