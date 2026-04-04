"""Tests for SearchConfig — Task 1.2 (FEAT-019)."""
from pathlib import Path

import pytest

from archon.config.loader import ConfigError, SearchConfig, load_config


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
    assert r.host == "localhost"
    assert r.port == 8282
    assert r.db_path == "~/.archon/search"
    assert r.embedding_model == "BAAI/bge-small-en-v1.5"
    assert r.providers == []
    assert r.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert r.top_k_retrieve == 20
    assert r.top_k_return == 5
    assert r.chunk_size == 512


def test_rag_config_all_fields_parsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        "top_k_return = 10\n"
        "chunk_size = 256\n"
    )
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.enabled is True
    assert config.search.host == "rag.internal"
    assert config.search.port == 9999
    assert config.search.db_path == "/data/rag"
    assert config.search.embedding_model == "some/embed-model"
    assert config.search.reranker_model == "some/reranker"
    assert config.search.top_k_retrieve == 30
    assert config.search.top_k_return == 10
    assert config.search.chunk_size == 256


def test_rag_config_invalid_port_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # port = 0
    env, cfg = _files(tmp_path, "\n[search]\nport = 0\n")
    with pytest.raises(ConfigError, match="port"):
        load_config(env_file=env, config_file=cfg)

    # port = 65536
    cfg.write_text(_BASE_TOML + "\n[search]\nport = 65536\n")
    with pytest.raises(ConfigError, match="port"):
        load_config(env_file=env, config_file=cfg)


def test_rag_config_top_k_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """top_k_return >= top_k_retrieve must raise ConfigError (strict greater required)."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    # return > retrieve
    extra = "\n[search]\ntop_k_retrieve = 5\ntop_k_return = 10\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="top_k"):
        load_config(env_file=env, config_file=cfg)

    # return == retrieve (equal also rejected — retrieve must be strictly greater)
    cfg.write_text(_BASE_TOML + "\n[search]\ntop_k_retrieve = 5\ntop_k_return = 5\n")
    with pytest.raises(ConfigError, match="top_k"):
        load_config(env_file=env, config_file=cfg)


def test_rag_config_top_k_retrieve_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\ntop_k_retrieve = 0\ntop_k_return = 5\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="top_k_retrieve"):
        load_config(env_file=env, config_file=cfg)


def test_rag_config_negative_values_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    cfg_pairs = [
        "\n[search]\ntop_k_return = -1\n",
        "\n[search]\ntop_k_retrieve = -1\ntop_k_return = 1\n",
        "\n[search]\nchunk_size = -1\n",
    ]
    for extra in cfg_pairs:
        env, cfg = _files(tmp_path, extra)
        with pytest.raises(ConfigError):
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


def test_rag_config_chunk_size_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nchunk_size = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="chunk_size"):
        load_config(env_file=env, config_file=cfg)


def test_rag_config_top_k_return_zero_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\ntop_k_return = 0\n"
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="top_k_return"):
        load_config(env_file=env, config_file=cfg)


def test_rag_config_missing_optional_uses_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A [search] section with only enabled = true should use all other defaults."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[search]\nenabled = true\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.search.enabled is True
    assert config.search.host == "localhost"
    assert config.search.port == 8282
    assert config.search.db_path == "~/.archon/search"
    assert config.search.embedding_model == "BAAI/bge-small-en-v1.5"
    assert config.search.reranker_model == "BAAI/bge-reranker-v2-m3"
    assert config.search.providers == []
    assert config.search.top_k_retrieve == 20
    assert config.search.top_k_return == 5
    assert config.search.chunk_size == 512


# --- FEAT-024 Task 1.2: context_windows in ModelsConfig ---


def test_models_config_context_windows_loaded_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models.context_windows]\n"my-model" = 1000000\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)
    assert config.models.context_windows == {"my-model": 1_000_000}


def test_models_config_context_windows_defaults_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[models]\navailable = []\n"
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)
    assert config.models.context_windows == {}


@pytest.mark.parametrize("val", [0, -1])
def test_models_config_context_windows_rejects_nonpositive(
    val: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = f'\n[models.context_windows]\n"bad-model" = {val}\n'
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="context_windows"):
        load_config(env_file=env, config_file=cfg)


def test_models_config_context_windows_rejects_float(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[models.context_windows]\n"bad-model" = 3.14\n'
    env, cfg = _files(tmp_path, extra)
    with pytest.raises(ConfigError, match="context_windows"):
        load_config(env_file=env, config_file=cfg)
