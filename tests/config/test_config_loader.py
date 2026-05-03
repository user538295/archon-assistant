"""Tests for SearchConfig (client-only, Task 7.7) and ModelsConfig — Task 2.1 (FEAT-029)."""
import logging
from pathlib import Path

import pytest

from archon.config.loader import ConfigError, ModelsConfig, SearchConfig, load_config


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


def test_search_config_defaults() -> None:
    r = SearchConfig()
    assert r.enabled is False
    assert r.url == "http://127.0.0.1:8765"
    assert r.max_parallel_collections == 3
    assert r.top_k_return == 5
    # Server-side fields must not be present
    assert not hasattr(r, "host")
    assert not hasattr(r, "port")
    assert not hasattr(r, "db_path")
    assert not hasattr(r, "embedding_model")


def test_search_config_client_fields_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[search]\n"
        "enabled = true\n"
        'url = "http://search.internal:9000"\n'
        "max_parallel_collections = 5\n"
        "top_k_return = 10\n"
    )
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.enabled is True
    assert config.search.url == "http://search.internal:9000"
    assert config.search.max_parallel_collections == 5
    assert config.search.top_k_return == 10


def test_search_config_deprecated_fields_log_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Old server-side fields in [search] TOML emit deprecation warnings."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[search]\n"
        "enabled = true\n"
        'host = "rag.internal"\n'
        "port = 9999\n"
        'db_path = "/data/rag"\n'
        'embedding_model = "some/embed-model"\n'
        'reranker_model = "some/reranker"\n'
        "top_k_retrieve = 30\n"
        "chunk_size = 256\n"
    )
    env, cfg = _files(tmp_path, extra)
    with caplog.at_level(logging.WARNING):
        load_config(env_file=env, config_file=cfg)

    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    deprecated_keys = {"host", "port", "db_path", "embedding_model", "reranker_model", "top_k_retrieve", "chunk_size"}
    for key in deprecated_keys:
        assert any(key in msg and "no longer read by Archon" in msg for msg in warnings), (
            f"Expected deprecation warning for '{key}'"
        )


def test_rag_config_top_k_return_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\ntop_k_return = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="top_k_return"):
        load_config(env_file=env, config_file=cfg)


def test_config_has_search_attribute_not_legacy_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Config must expose 'search' attribute (SearchConfig), never the removed legacy section."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path)
    config = load_config(env_file=env, config_file=cfg)
    assert hasattr(config, "search"), "config.search must exist"
    assert isinstance(config.search, SearchConfig)


def test_rag_config_missing_optional_uses_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A [search] section with only enabled = true should use all other defaults."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nenabled = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.enabled is True
    assert config.search.url == "http://127.0.0.1:8765"
    assert config.search.max_parallel_collections == 3
    assert config.search.top_k_return == 5


# --- FEAT-029 Task 2.1: ModelsConfig.available is dict[str, int] ---


def test_models_config_context_windows_attr_removed() -> None:
    assert not hasattr(ModelsConfig(), "context_windows")


def test_models_config_available_defaults_empty_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path)
    config = load_config(env_file=env, config_file=cfg)
    assert config.models.available == {}


def test_models_config_available_loaded_as_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models.available]\n"claude-opus-4-6" = 1_000_000\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)
    assert config.models.available == {"claude-opus-4-6": 1_000_000}


def test_models_config_available_list_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models]\navailable = ["claude-sonnet-4-6"]\n'
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="TOML table"):
        load_config(env_file=env, config_file=cfg)


def test_models_config_available_scalar_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[models]\navailable = 42\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="TOML table"):
        load_config(env_file=env, config_file=cfg)


@pytest.mark.parametrize("val", [0, -1])
def test_models_config_available_nonpositive_raises(
    val: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = f'\n[models.available]\n"bad-model" = {val}\n'
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="non-positive"):
        load_config(env_file=env, config_file=cfg)


def test_models_config_available_bool_value_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models.available]\n"bad-model" = true\n'
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="integers"):
        load_config(env_file=env, config_file=cfg)


def test_models_config_default_uses_first_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models.available]\n"claude-opus-4-6" = 1_000_000\n"claude-sonnet-4-6" = 200_000\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)
    assert config.models.default == "claude-opus-4-6"


def test_models_config_context_windows_section_logs_deprecation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models.context_windows]\n"my-model" = 1_000_000\n'
    env, cfg = _files(tmp_path, extra)
    with caplog.at_level(logging.WARNING, logger="archon"):
        load_config(env_file=env, config_file=cfg)
    assert any("context_windows" in r.message and "no longer used" in r.message for r in caplog.records)
