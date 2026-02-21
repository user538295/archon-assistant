"""S5.7 — Live unit tests: config loader with real files, no mocks."""
import os
import pytest
from pathlib import Path

from archon.config.loader import ConfigError, load_config

pytestmark = pytest.mark.live

VALID_TOML = """\
[access]
allowed_user_ids = [999888777]

[session]
working_directory = "/tmp"
inactivity_timeout_seconds = 600

[output]
max_message_length = 3000
truncation_strategy = "split"
"""


@pytest.fixture(autouse=True)
def _isolate_token_env() -> None:
    """Save and restore TELEGRAM_BOT_TOKEN around each test without monkeypatch."""
    saved = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    yield
    if saved is not None:
        os.environ["TELEGRAM_BOT_TOKEN"] = saved
    else:
        os.environ.pop("TELEGRAM_BOT_TOKEN", None)


def test_live_happy_path_reads_real_files(tmp_path: Path) -> None:
    """load_config reads real .env and config.toml with real file I/O."""
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=live_test_token_xyz\n")
    (tmp_path / "config.toml").write_text(VALID_TOML)

    cfg = load_config(env_file=tmp_path / ".env", config_file=tmp_path / "config.toml")

    assert cfg.telegram_bot_token == "live_test_token_xyz"
    assert cfg.access.allowed_user_ids == [999888777]
    assert cfg.session.working_directory == "/tmp"
    assert cfg.session.inactivity_timeout_seconds == 600
    assert cfg.output.max_message_length == 3000


def test_live_missing_config_file_error_contains_real_path(tmp_path: Path) -> None:
    """ConfigError message contains the real absolute path of the missing file."""
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=live_test_token_xyz\n")
    missing = tmp_path / "config.toml"  # deliberately not created

    with pytest.raises(ConfigError) as exc_info:
        load_config(env_file=tmp_path / ".env", config_file=missing)

    assert str(missing) in str(exc_info.value)


def test_live_empty_env_file_raises_missing_token(tmp_path: Path) -> None:
    """Empty .env file with no env var set raises ConfigError about missing token."""
    (tmp_path / ".env").write_text("")
    (tmp_path / "config.toml").write_text(VALID_TOML)

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_config(env_file=tmp_path / ".env", config_file=tmp_path / "config.toml")


def test_live_all_sections_parsed_from_real_toml(tmp_path: Path) -> None:
    """Full config.toml with all optional sections is parsed correctly on real fs."""
    (tmp_path / ".env").write_text("TELEGRAM_BOT_TOKEN=full_live_token\n")
    (tmp_path / "config.toml").write_text("""\
[access]
allowed_user_ids = [111, 222, 333]

[session]
working_directory = "/var/tmp"
inactivity_timeout_seconds = 300

[output]
max_message_length = 2000
truncation_strategy = "split"
head_chars = 800
tail_chars = 800

[logging]
log_file = "/tmp/test_archon.log"
log_level = "DEBUG"
""")

    cfg = load_config(env_file=tmp_path / ".env", config_file=tmp_path / "config.toml")

    assert cfg.access.allowed_user_ids == [111, 222, 333]
    assert cfg.session.inactivity_timeout_seconds == 300
    assert cfg.output.head_chars == 800
    assert cfg.output.tail_chars == 800
    assert cfg.logging.log_level == "DEBUG"
    assert cfg.logging.log_file == "/tmp/test_archon.log"
