"""Config loader — loads .env and config.toml into typed dataclasses."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from dotenv import load_dotenv


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass
class AccessConfig:
    allowed_user_ids: list[int]


@dataclass
class SessionConfig:
    working_directory: str
    inactivity_timeout_seconds: int = 1800


@dataclass
class OutputConfig:
    max_message_length: int = 4000
    truncation_strategy: str = "split"
    head_chars: int = 1500
    tail_chars: int = 1500


@dataclass
class LoggingConfig:
    log_file: str = "~/.archon/archon.log"
    log_level: str = "INFO"


@dataclass
class NotificationsConfig:
    show_thinking_result: bool = True
    brief_tool_output: bool = False
    concise_mode: bool = False


@dataclass
class Config:
    telegram_bot_token: str
    access: AccessConfig
    session: SessionConfig
    output: OutputConfig
    logging: LoggingConfig
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)


def load_config(
    env_file: str | Path = ".env",
    config_file: str | Path = "config.toml",
) -> Config:
    """Load config from env_file (.env) and config_file (config.toml).

    Raises ConfigError if required fields are missing.
    """
    load_dotenv(env_file)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is missing from environment or .env file")

    config_path = Path(config_file)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    try:
        access = AccessConfig(
            allowed_user_ids=data["access"]["allowed_user_ids"],
        )
        session = SessionConfig(
            working_directory=str(Path(data["session"]["working_directory"]).expanduser()),
            inactivity_timeout_seconds=data["session"].get("inactivity_timeout_seconds", 1800),
        )
    except KeyError as e:
        raise ConfigError(f"Missing required config key: {e}") from e

    if not access.allowed_user_ids:
        raise ConfigError("allowed_user_ids must not be empty")
    if session.inactivity_timeout_seconds <= 0:
        raise ConfigError("inactivity_timeout_seconds must be > 0")
    if not Path(session.working_directory).expanduser().exists():
        raise ConfigError(f"working_directory does not exist: {session.working_directory}")

    output_data = data.get("output", {})
    output = OutputConfig(
        max_message_length=output_data.get("max_message_length", 4000),
        truncation_strategy=output_data.get("truncation_strategy", "split"),
        head_chars=output_data.get("head_chars", 1500),
        tail_chars=output_data.get("tail_chars", 1500),
    )

    if output.max_message_length <= 0:
        raise ConfigError("max_message_length must be > 0")

    logging_data = data.get("logging", {})
    logging_cfg = LoggingConfig(
        log_file=logging_data.get("log_file", "~/.archon/archon.log"),
        log_level=logging_data.get("log_level", "INFO"),
    )

    notif_data = data.get("notifications", {})
    notifications = NotificationsConfig(
        show_thinking_result=notif_data.get("show_thinking_result", True),
        brief_tool_output=notif_data.get("brief_tool_output", False),
        concise_mode=notif_data.get("concise_mode", False),
    )

    return Config(
        telegram_bot_token=token,
        access=access,
        session=session,
        output=output,
        logging=logging_cfg,
        notifications=notifications,
    )


def save_notifications_config(
    notifications: NotificationsConfig,
    config_file: str | Path = "config.toml",
) -> None:
    """Persist notification settings to config.toml, preserving all other sections."""
    path = Path(config_file)
    with path.open("r", encoding="utf-8") as f:
        doc = tomlkit.load(f)

    if "notifications" not in doc:
        doc.add("notifications", tomlkit.table())

    doc["notifications"]["show_thinking_result"] = notifications.show_thinking_result  # type: ignore[index]
    doc["notifications"]["brief_tool_output"] = notifications.brief_tool_output  # type: ignore[index]
    doc["notifications"]["concise_mode"] = notifications.concise_mode  # type: ignore[index]

    with path.open("w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
