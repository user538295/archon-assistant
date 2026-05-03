"""Tests for SearchConfig — Task 7.7 client-only fields."""
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


def test_search_config_history_collection_field_removed() -> None:
    """SearchConfig no longer has a history_collection field."""
    r = SearchConfig()
    assert not hasattr(r, "history_collection"), (
        "history_collection must be removed from SearchConfig"
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
# Task 7.7 — client-only SearchConfig fields
# ---------------------------------------------------------------------------


def test_search_config_has_url_field() -> None:
    """SearchConfig has a url field with correct default."""
    r = SearchConfig()
    assert r.url == "http://127.0.0.1:8765"


def test_search_config_has_enabled_field() -> None:
    """SearchConfig has enabled field."""
    r = SearchConfig()
    assert r.enabled is False


def test_search_config_has_max_parallel_collections_field() -> None:
    """SearchConfig has max_parallel_collections field."""
    r = SearchConfig()
    assert r.max_parallel_collections == 3


def test_search_config_has_top_k_return_field() -> None:
    """SearchConfig has top_k_return field."""
    r = SearchConfig()
    assert r.top_k_return == 5


def test_search_config_deprecated_key_logs_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Old [search] db_path in TOML → WARNING logged about deprecated key."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\ndb_path = "~/.archon/search"\n'
    env, cfg = _files(tmp_path, extra)

    with caplog.at_level(logging.WARNING):
        load_config(env_file=env, config_file=cfg)

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("db_path" in msg and "no longer read by Archon" in msg for msg in warning_messages), (
        f"Expected deprecation warning for 'db_path', got: {warning_messages}"
    )


def test_search_config_no_longer_has_db_path() -> None:
    """SearchConfig no longer has a db_path attribute."""
    r = SearchConfig()
    with pytest.raises(AttributeError):
        _ = r.db_path  # type: ignore[attr-defined]


def test_search_url_loaded_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads url from [search] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\nurl = "http://10.0.0.1:9999"\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.url == "http://10.0.0.1:9999"


def test_search_max_parallel_collections_loaded_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """load_config reads max_parallel_collections from [search] section in TOML."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nmax_parallel_collections = 5\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.max_parallel_collections == 5


def test_search_config_url_invalid_scheme_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A url without http:// or https:// scheme raises ConfigError."""
    from archon.config.loader import ConfigError
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\nurl = "localhost:8765"\n'
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="url must start with http"):
        load_config(env_file=env, config_file=cfg)


def test_search_config_clean_config_no_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A config with only allowed search keys produces no deprecation warnings."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\nurl = "http://127.0.0.1:8765"\nenabled = false\n'
    env, cfg = _files(tmp_path, extra)

    with caplog.at_level(logging.WARNING):
        load_config(env_file=env, config_file=cfg)

    dep_warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING and "no longer read by Archon" in r.message]
    assert dep_warnings == [], f"Unexpected deprecation warnings: {dep_warnings}"

# ---------------------------------------------------------------------------
# SearchConfig.host_port property tests (C1-I-23 / C1-I-24)
# ---------------------------------------------------------------------------


def test_search_config_host_port_default() -> None:
    """SearchConfig().host_port returns ('127.0.0.1', 8765) by default."""
    r = SearchConfig()
    assert r.host_port == ("127.0.0.1", 8765)


def test_search_config_host_port_custom_url() -> None:
    """host_port correctly parses a custom url."""
    r = SearchConfig(url="http://10.0.0.2:9999")
    assert r.host_port == ("10.0.0.2", 9999)


def test_search_config_host_port_no_port() -> None:
    """URL without explicit port defaults to 8765."""
    r = SearchConfig(url="http://192.168.1.1")
    assert r.host_port == ("192.168.1.1", 8765)
