"""Tests for the config loader — S0.2."""
import pytest
from pathlib import Path

from archon.config.loader import ConfigError, load_config


VALID_TOML = """\
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "/tmp"
inactivity_timeout_seconds = 1800

[output]
max_message_length = 4000
truncation_strategy = "split"
head_chars = 1500
tail_chars = 1500

[logging]
log_file = "~/.archon/archon.log"
log_level = "INFO"
"""


def _env_file(tmp_path: Path, token: str = "test_token_abc") -> Path:
    p = tmp_path / ".env"
    p.write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    return p


def _config_file(tmp_path: Path, content: str = VALID_TOML) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


def test_valid_config_loads_correctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.telegram_bot_token == "test_token_abc"
    assert cfg.access.allowed_user_ids == [123456789]
    assert cfg.session.working_directory == "/tmp"
    assert cfg.session.inactivity_timeout_seconds == 1800
    assert cfg.output.max_message_length == 4000
    assert cfg.output.truncation_strategy == "split"
    assert cfg.output.head_chars == 1500
    assert cfg.output.tail_chars == 1500
    assert cfg.logging.log_file == "~/.archon/archon.log"
    assert cfg.logging.log_level == "INFO"


def test_missing_token_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(env_file=tmp_path / ".env", config_file=_config_file(tmp_path))


def test_missing_config_file_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(env_file=_env_file(tmp_path), config_file=tmp_path / "config.toml")


def test_missing_required_access_key_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = "[session]\nworking_directory = \"/tmp\"\n"
    with pytest.raises(ConfigError, match="Missing required config key"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_missing_required_session_key_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = "[access]\nallowed_user_ids = [1]\n"
    with pytest.raises(ConfigError, match="Missing required config key"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_optional_output_section_uses_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    minimal_toml = "[access]\nallowed_user_ids = [1]\n[session]\nworking_directory = \"/tmp\"\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, minimal_toml))

    assert cfg.output.max_message_length == 4000
    assert cfg.output.truncation_strategy == "split"
    assert cfg.logging.log_level == "INFO"


def test_module_getattr_raises_for_unknown_attribute() -> None:
    import archon.config as cfg_module

    with pytest.raises(AttributeError, match="has no attribute"):
        _ = cfg_module.nonexistent_attribute  # type: ignore[attr-defined]


def test_empty_allowed_user_ids_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = "[access]\nallowed_user_ids = []\n[session]\nworking_directory = \"/tmp\"\n"
    with pytest.raises(ConfigError, match="allowed_user_ids must not be empty"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_zero_inactivity_timeout_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = (
        "[access]\nallowed_user_ids = [1]\n"
        "[session]\nworking_directory = \"/tmp\"\ninactivity_timeout_seconds = 0\n"
    )
    with pytest.raises(ConfigError, match="inactivity_timeout_seconds must be > 0"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_negative_inactivity_timeout_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = (
        "[access]\nallowed_user_ids = [1]\n"
        "[session]\nworking_directory = \"/tmp\"\ninactivity_timeout_seconds = -1\n"
    )
    with pytest.raises(ConfigError, match="inactivity_timeout_seconds must be > 0"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_zero_max_message_length_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = (
        "[access]\nallowed_user_ids = [1]\n"
        "[session]\nworking_directory = \"/tmp\"\n"
        "[output]\nmax_message_length = 0\n"
    )
    with pytest.raises(ConfigError, match="max_message_length must be > 0"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_nonexistent_working_directory_raises_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    bad_toml = (
        "[access]\nallowed_user_ids = [1]\n"
        "[session]\nworking_directory = \"/nonexistent/path/that/does/not/exist\"\n"
    )
    with pytest.raises(ConfigError, match="working_directory does not exist"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, bad_toml))


def test_module_singleton_loaded_via_getattr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import archon.config as cfg_module

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env_file = _env_file(tmp_path)
    config_file = _config_file(tmp_path)

    # Inject a pre-loaded config into the module's private singleton
    loaded = load_config(env_file=env_file, config_file=config_file)
    cfg_module._config = loaded  # type: ignore[attr-defined]

    result = cfg_module.config
    assert result is loaded
    assert result.telegram_bot_token == "test_token_abc"
