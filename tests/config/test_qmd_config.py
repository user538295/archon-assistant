"""Unit tests for QmdConfig loading — FR.002 QMD integration."""
from pathlib import Path

import pytest

from archon.config.loader import QmdConfig, load_config


# ── helpers ──────────────────────────────────────────────────────────────────

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


# ── QmdConfig dataclass defaults ─────────────────────────────────────────────


def test_qmd_config_defaults() -> None:
    q = QmdConfig()
    assert q.enabled is False
    assert q.host == "localhost"
    assert q.port == 8181
    assert q.history_collection == "archon-history"
    assert q.binary_path == ""


# ── config.toml loading ───────────────────────────────────────────────────────


def test_qmd_defaults_when_section_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path)
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.enabled is False
    assert config.qmd.host == "localhost"
    assert config.qmd.port == 8181
    assert config.qmd.history_collection == "archon-history"


def test_qmd_enabled_true_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path, "\n[qmd]\nenabled = true\n")
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.enabled is True


def test_qmd_full_section_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[qmd]\n"
        "enabled = true\n"
        'host = "qmd.internal"\n'
        "port = 9090\n"
        'history_collection = "my-project"\n'
    )
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.enabled is True
    assert config.qmd.host == "qmd.internal"
    assert config.qmd.port == 9090
    assert config.qmd.history_collection == "my-project"


def test_qmd_port_only_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path, "\n[qmd]\nenabled = true\nport = 7777\n")
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.port == 7777
    assert config.qmd.host == "localhost"  # default unchanged


def test_qmd_host_only_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path, '\n[qmd]\nenabled = true\nhost = "remote.host"\n')
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.host == "remote.host"
    assert config.qmd.port == 8181  # default unchanged


def test_qmd_enabled_false_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path, "\n[qmd]\nenabled = false\n")
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.enabled is False


def test_qmd_localhost_127_treated_as_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """127.0.0.1 is also a local address — verify it round-trips from config."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path, '\n[qmd]\nenabled = true\nhost = "127.0.0.1"\n')
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.host == "127.0.0.1"


def test_qmd_binary_path_loaded_from_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[qmd]\nenabled = true\nbinary_path = "/home/user/.bun/bin/qmd"\n'
    env, cfg = _files(tmp_path, extra)
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.binary_path == "/home/user/.bun/bin/qmd"


def test_qmd_binary_path_defaults_empty_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env, cfg = _files(tmp_path, "\n[qmd]\nenabled = true\n")
    config = load_config(env_file=env, config_file=cfg)

    assert config.qmd.binary_path == ""
