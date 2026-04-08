"""Tests for attachment config fields in SessionConfig."""
import logging
from pathlib import Path

import pytest

from archon.config.loader import ConfigError, load_config


VALID_TOML = """\
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "/tmp/test_workspace"
"""


def _env_file(tmp_path: Path, token: str = "test_token_abc") -> Path:
    p = tmp_path / ".env"
    p.write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    return p


def _config_file(tmp_path: Path, content: str = VALID_TOML) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


def _make_workspace(tmp_path: Path) -> Path:
    """Create and return a workspace directory that exists on disk."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


def _toml_with_workspace(ws: Path) -> str:
    return (
        "[access]\n"
        "allowed_user_ids = [123456789]\n\n"
        "[session]\n"
        f'working_directory = "{ws}"\n'
    )


def test_default_attachments_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When attachments_dir not set in TOML, defaults to {working_directory}/attachments."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    toml = _toml_with_workspace(ws)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.session.attachments_dir == f"{ws}/attachments"


def test_explicit_attachments_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When set explicitly, uses that value."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    custom_dir = tmp_path / "custom_attachments"
    custom_dir.mkdir()
    toml = _toml_with_workspace(ws) + f'attachments_dir = "{custom_dir}"\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.session.attachments_dir == str(custom_dir)


def test_cleanup_hours_default_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When not set, defaults to 0 (disabled)."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    toml = _toml_with_workspace(ws)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.session.attachments_cleanup_hours == 12.5


def test_cleanup_hours_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When set to 12.5, loads correctly."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    toml = _toml_with_workspace(ws) + "attachments_cleanup_hours = 12.5\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.session.attachments_cleanup_hours == 12.5


def test_symlink_resolved_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Create a symlink, verify it's resolved and warning logged."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    real_dir = tmp_path / "real_attachments"
    real_dir.mkdir()
    symlink_dir = tmp_path / "link_attachments"
    symlink_dir.symlink_to(real_dir)

    toml = _toml_with_workspace(ws) + f'attachments_dir = "{symlink_dir}"\n'

    with caplog.at_level(logging.WARNING, logger="archon"):
        cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.session.attachments_dir == str(real_dir.resolve())
    assert "symlink" in caplog.text.lower()


def test_missing_parent_dir_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Use a non-existent parent path, verify warning logged."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    nonexistent = tmp_path / "nonexistent_parent" / "attachments"
    toml = _toml_with_workspace(ws) + f'attachments_dir = "{nonexistent}"\n'

    with caplog.at_level(logging.WARNING, logger="archon"):
        cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.session.attachments_dir == str(nonexistent)
    assert "parent directory" in caplog.text.lower()


def test_negative_cleanup_hours_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative cleanup hours should raise ConfigError."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    ws = _make_workspace(tmp_path)
    toml = _toml_with_workspace(ws) + "attachments_cleanup_hours = -5.0\n"
    with pytest.raises(ConfigError, match="attachments_cleanup_hours must be >= 0"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))


def test_symlink_resolved_parent_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """After symlink resolution, parent of resolved path should be checked."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Create symlink pointing to non-existent directory
    target = tmp_path / "nonexistent" / "deep" / "attachments"
    link = tmp_path / "att_link"
    link.symlink_to(target)
    toml = (
        "[access]\n"
        "allowed_user_ids = [123]\n\n"
        "[session]\n"
        f'working_directory = "{workspace}"\n'
        f'attachments_dir = "{link}"\n'
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    # Both warnings should be present
    assert any("symlink" in r.message for r in caplog.records)
    assert any("Parent directory" in r.message for r in caplog.records)


def test_tilde_expansion_explicit_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit attachments_dir with ~ should be expanded."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    toml = (
        "[access]\n"
        "allowed_user_ids = [123]\n\n"
        "[session]\n"
        f'working_directory = "{workspace}"\n'
        'attachments_dir = "~/my_attachments"\n'
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    assert "~" not in cfg.session.attachments_dir
    assert cfg.session.attachments_dir.endswith("my_attachments")
