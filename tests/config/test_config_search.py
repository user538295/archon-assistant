"""Suite 11 — Archon-side SearchConfig edge cases (C11.4, C11.6).

Tests that invalid string values for integer SearchConfig fields raise ConfigError.
"""
from pathlib import Path

import pytest

from archon.config.loader import ConfigError, load_config


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


def test_c11_4_max_parallel_collections_string_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C11.4: max_parallel_collections = 'many' (string) raises ConfigError, not ValueError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\nmax_parallel_collections = "many"\n'
    env, cfg = _files(tmp_path, extra)

    with pytest.raises(ConfigError, match="max_parallel_collections"):
        load_config(env_file=env, config_file=cfg)


def test_c11_6_top_k_return_string_raises_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C11.6: top_k_return = 'few' (string) raises ConfigError, not ValueError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[search]\ntop_k_return = "few"\n'
    env, cfg = _files(tmp_path, extra)

    with pytest.raises(ConfigError, match="top_k_return"):
        load_config(env_file=env, config_file=cfg)
