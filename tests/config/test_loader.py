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
log_file = "~/.archon/logs/archon.log"
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
    assert cfg.logging.log_file == "~/.archon/logs/archon.log"
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


# ──────────────────────────────────────────────────────────────────
# NotificationsConfig — S8.1 loading and persisting
# ──────────────────────────────────────────────────────────────────


def test_notifications_defaults_when_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.notifications.mode == "normal"
    assert cfg.notifications.interval_minutes == 2


def test_notifications_mode_loaded_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + '\n[notifications]\nmode = "quiet"\ninterval_minutes = 5\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.notifications.mode == "quiet"
    assert cfg.notifications.interval_minutes == 5


def test_notifications_all_modes_loadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    for mode in ("quiet", "normal", "verbose", "debug"):
        toml = VALID_TOML + f'\n[notifications]\nmode = "{mode}"\n'
        cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
        assert cfg.notifications.mode == mode


def test_notifications_migrate_concise_full_to_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old concise_mode='full' migrates to mode='quiet'."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + '\n[notifications]\nconcise_mode = "full"\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    assert cfg.notifications.mode == "quiet"


def test_notifications_migrate_concise_partial_to_normal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old concise_mode='partial' migrates to mode='normal', interval preserved."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + '\n[notifications]\nconcise_mode = "partial"\nconcise_interval_minutes = 5\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    assert cfg.notifications.mode == "normal"
    assert cfg.notifications.interval_minutes == 5


def test_notifications_migrate_concise_off_to_verbose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old concise_mode='off' migrates to mode='verbose'."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + '\n[notifications]\nconcise_mode = "off"\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    assert cfg.notifications.mode == "verbose"


def test_notifications_migrate_bool_true_to_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old concise_mode=true (boolean) migrates to mode='quiet'."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[notifications]\nconcise_mode = true\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    assert cfg.notifications.mode == "quiet"


def test_notifications_migrate_bool_false_to_verbose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Old concise_mode=false (boolean) migrates to mode='verbose'."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[notifications]\nconcise_mode = false\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))
    assert cfg.notifications.mode == "verbose"


def test_save_notifications_config_writes_mode_and_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from archon.config.loader import NotificationsConfig, save_notifications_config

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    config_file = _config_file(tmp_path)

    save_notifications_config(NotificationsConfig(mode="quiet", interval_minutes=5), config_file)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=config_file)

    assert cfg.notifications.mode == "quiet"
    assert cfg.notifications.interval_minutes == 5


def test_save_notifications_config_does_not_write_old_keys(tmp_path: Path) -> None:
    from archon.config.loader import NotificationsConfig, save_notifications_config

    config_file = _config_file(tmp_path)
    save_notifications_config(NotificationsConfig(mode="verbose"), config_file)
    content = config_file.read_text()

    assert "show_thinking_result" not in content
    assert "brief_tool_output" not in content
    assert "concise_mode" not in content
    assert "concise_interval_minutes" not in content


def test_save_notifications_config_preserves_other_sections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from archon.config.loader import NotificationsConfig, save_notifications_config

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    config_file = _config_file(tmp_path)

    save_notifications_config(NotificationsConfig(), config_file)

    cfg = load_config(env_file=_env_file(tmp_path), config_file=config_file)
    assert cfg.access.allowed_user_ids == [123456789]
    assert cfg.session.working_directory == "/tmp"
    assert cfg.output.max_message_length == 4000


def test_save_notifications_config_creates_section_if_missing(tmp_path: Path) -> None:
    from archon.config.loader import NotificationsConfig, save_notifications_config

    config_file = _config_file(tmp_path)  # VALID_TOML has no [notifications]
    save_notifications_config(NotificationsConfig(mode="debug"), config_file)
    content = config_file.read_text()

    assert "notifications" in content
    assert "debug" in content


# ──────────────────────────────────────────────────────────────────
# HistoryConfig — loading
# ──────────────────────────────────────────────────────────────────


def test_history_defaults_when_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.history.enabled is True
    assert cfg.history.directory == "~/.archon/history"


def test_history_loaded_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + '\n[history]\nenabled = false\ndirectory = "/custom/path"\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.history.enabled is False
    assert cfg.history.directory == "/custom/path"


# ──────────────────────────────────────────────────────────────────
# ModelsConfig — loading
# ──────────────────────────────────────────────────────────────────


def test_models_defaults_when_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.models.available == []
    assert cfg.models.default is None


def test_models_available_list_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + (
        '\n[models]\n'
        'available = ["claude-opus-4-5", "claude-sonnet-4-5"]\n'
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.models.available == ["claude-opus-4-5", "claude-sonnet-4-5"]
    assert cfg.models.default is None


def test_models_default_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + (
        '\n[models]\n'
        'available = ["claude-opus-4-5", "claude-sonnet-4-5"]\n'
        'default = "claude-sonnet-4-5"\n'
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.models.default == "claude-sonnet-4-5"


def test_models_empty_available_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + '\n[models]\navailable = []\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.models.available == []


# ──────────────────────────────────────────────────────────────────
# PluginsConfig — Critical gap: all three fields untested
# ──────────────────────────────────────────────────────────────────


def test_plugins_defaults_when_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.plugins.enabled is True
    assert cfg.plugins.plugins_dir == ""
    assert cfg.plugins.settings_path == ""


def test_plugins_enabled_false_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[plugins]\nenabled = false\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.plugins.enabled is False


def test_plugins_dir_and_settings_path_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + (
        "\n[plugins]\n"
        "enabled = true\n"
        'plugins_dir = "/custom/plugins"\n'
        'settings_path = "/custom/settings.json"\n'
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.plugins.enabled is True
    assert cfg.plugins.plugins_dir == "/custom/plugins"
    assert cfg.plugins.settings_path == "/custom/settings.json"


def test_plugins_enabled_true_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[plugins]\nenabled = true\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.plugins.enabled is True


# ──────────────────────────────────────────────────────────────────
# BackgroundAgentsConfig — S15.1
# ──────────────────────────────────────────────────────────────────


def test_background_agents_defaults_when_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.background_agents.spawn_rule == "auto"
    assert cfg.background_agents.max_parallel == 5
    assert cfg.background_agents.host == "localhost"
    assert cfg.background_agents.port == 18182
    assert cfg.background_agents.beacon_interval_minutes == 2


def test_background_agents_all_fields_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[background_agents]\n"
        'spawn_rule = "eager"\n'
        "max_parallel = 3\n"
        'host = "0.0.0.0"\n'
        "port = 9999\n"
        "beacon_interval_minutes = 5\n"
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.background_agents.spawn_rule == "eager"
    assert cfg.background_agents.max_parallel == 3
    assert cfg.background_agents.host == "0.0.0.0"
    assert cfg.background_agents.port == 9999
    assert cfg.background_agents.beacon_interval_minutes == 5


def test_background_agents_partial_fields_use_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[background_agents]\nspawn_rule = \"auto\"\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.background_agents.spawn_rule == "auto"
    assert cfg.background_agents.max_parallel == 5
    assert cfg.background_agents.host == "localhost"
    assert cfg.background_agents.port == 18182


def test_background_agents_spawn_rule_manual(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = '\n[background_agents]\nspawn_rule = "manual"\n'
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.background_agents.spawn_rule == "manual"


def test_background_agents_tool_promotion_threshold_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_promotion_threshold defaults to 10 when not set."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.background_agents.tool_promotion_threshold == 10


def test_background_agents_tool_promotion_threshold_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_promotion_threshold is read from [background_agents] section."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[background_agents]\ntool_promotion_threshold = 7\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.background_agents.tool_promotion_threshold == 7


def test_background_agents_tool_promotion_threshold_zero_is_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_promotion_threshold = 0 is valid and means promotion is disabled."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[background_agents]\ntool_promotion_threshold = 0\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.background_agents.tool_promotion_threshold == 0


def test_background_agents_tool_promotion_threshold_negative_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """tool_promotion_threshold = -1 must raise ConfigError."""
    from archon.config.loader import ConfigError

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[background_agents]\ntool_promotion_threshold = -1\n"
    with pytest.raises(ConfigError, match="tool_promotion_threshold"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))


# ──────────────────────────────────────────────────────────────────
# VoiceConfig — STT + TTS parsing
# ──────────────────────────────────────────────────────────────────


def test_voice_defaults_when_section_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.voice.enabled is False
    assert cfg.voice.stt.model == "medium"
    assert cfg.voice.stt.language is None
    assert cfg.voice.tts.provider == "openai"
    assert cfg.voice.tts.auto == "inbound"


def test_voice_all_fields_parsed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = (
        "\n[voice]\n"
        "enabled = true\n"
        "\n[voice.stt]\n"
        'model = "large"\n'
        'language = "hu"\n'
        "\n[voice.tts]\n"
        'provider = "edge"\n'
        'model = "tts-1-hd"\n'
        'voice = "alloy"\n'
        'auto = "always"\n'
        "max_text_length = 500\n"
        'edge_voice = "hu-HU-NoemiNeural"\n'
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.voice.enabled is True
    assert cfg.voice.stt.model == "large"
    assert cfg.voice.stt.language == "hu"
    assert cfg.voice.tts.provider == "edge"
    assert cfg.voice.tts.model == "tts-1-hd"
    assert cfg.voice.tts.voice == "alloy"
    assert cfg.voice.tts.auto == "always"
    assert cfg.voice.tts.max_text_length == 500
    assert cfg.voice.tts.edge_voice == "hu-HU-NoemiNeural"


def test_voice_partial_fields_use_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[voice]\nenabled = true\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, VALID_TOML + extra))

    assert cfg.voice.enabled is True
    assert cfg.voice.stt.model == "medium"
    assert cfg.voice.tts.provider == "openai"
    assert cfg.voice.tts.auto == "inbound"


# ──────────────────────────────────────────────────────────────────
# ReminderConfig — loading
# ──────────────────────────────────────────────────────────────────


def test_reminder_config_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default enabled is False — reminder is opt-in."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))

    assert cfg.reminder.enabled is False
    assert cfg.reminder.interval_messages == 20
    assert cfg.reminder.interval_tokens == 10000
    assert cfg.reminder.notify is False


def test_reminder_config_loads_from_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + (
        "\n[reminder]\n"
        "enabled = true\n"
        "interval_messages = 10\n"
        "interval_tokens = 5000\n"
        "notify = true\n"
    )
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.reminder.enabled is True
    assert cfg.reminder.interval_messages == 10
    assert cfg.reminder.interval_tokens == 5000
    assert cfg.reminder.notify is True


def test_reminder_config_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[reminder]\nenabled = false\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))

    assert cfg.reminder.enabled is False
    assert cfg.reminder.interval_messages == 20
    assert cfg.reminder.interval_tokens == 10000
    assert cfg.reminder.notify is False


def test_reminder_interval_messages_zero_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[reminder]\ninterval_messages = 0\n"
    with pytest.raises(ConfigError, match="interval_messages must be >= 1"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))


def test_reminder_interval_messages_negative_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[reminder]\ninterval_messages = -5\n"
    with pytest.raises(ConfigError, match="interval_messages must be >= 1"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))


def test_reminder_interval_tokens_zero_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    toml = VALID_TOML + "\n[reminder]\ninterval_tokens = 0\n"
    with pytest.raises(ConfigError, match="interval_tokens must be >= 1"):
        load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, toml))


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
