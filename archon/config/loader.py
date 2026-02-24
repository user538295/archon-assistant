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
class HistoryConfig:
    enabled: bool = True
    directory: str = "~/.archon/history"


@dataclass
class NotificationsAgentsConfig:
    """Per-agent notification level.

    mode=None → inherit from the parent NotificationsConfig.mode at runtime.
    Any explicit value pins agent lifecycle events to that level regardless of
    what the orchestrator's mode is set to.
    """
    mode: str | None = None  # None = inherit from orchestrator


@dataclass
class NotificationsConfig:
    mode: str = "normal"        # "quiet" | "normal" | "verbose" | "debug"
    interval_minutes: int = 2   # beacon interval (quiet mode only); 0 = no beacon
    agents: NotificationsAgentsConfig = field(default_factory=NotificationsAgentsConfig)


@dataclass
class ModelsConfig:
    available: list[str] = field(default_factory=list)
    default: str | None = None


@dataclass
class PluginsConfig:
    enabled: bool = True
    plugins_dir: str = ""       # empty = use default (~/.claude/plugins/)
    settings_path: str = ""     # empty = use default (~/.claude/settings.json)


@dataclass
class AgentDefinitionConfig:
    """Single custom agent definition (maps to SDK AgentDefinition)."""
    name: str
    description: str
    prompt: str
    tools: list[str] = field(default_factory=list)
    model: str | None = None    # "sonnet" | "haiku" | "opus" | "inherit" | None


@dataclass
class AgentsConfig:
    """Config for custom agent team definitions."""
    enabled: bool = True
    definitions: list[AgentDefinitionConfig] = field(default_factory=list)


@dataclass
class Config:
    telegram_bot_token: str
    access: AccessConfig
    session: SessionConfig
    output: OutputConfig
    logging: LoggingConfig
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    agents: AgentsConfig = field(default_factory=AgentsConfig)


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
    if "mode" in notif_data:
        # New-style config
        notif_mode = str(notif_data["mode"])
        notif_interval = int(notif_data.get("interval_minutes", 2))
    elif "concise_mode" in notif_data:
        # Migrate old-style keys
        raw = notif_data["concise_mode"]
        if isinstance(raw, bool):
            notif_mode = "quiet" if raw else "verbose"
        elif raw == "full":
            notif_mode = "quiet"
        elif raw == "partial":
            notif_mode = "normal"
        else:  # "off" or anything unrecognised
            notif_mode = "verbose"
        notif_interval = int(notif_data.get("concise_interval_minutes", 2))
    else:
        # No notifications section or no recognised keys → use defaults
        notif_mode = "normal"
        notif_interval = 2

    # Parse [notifications.agents] subsection (may be absent → mode=None = inherit)
    agents_notif_data = notif_data.get("agents", {})
    raw_agent_mode = agents_notif_data.get("mode", None)
    notif_agents = NotificationsAgentsConfig(
        mode=str(raw_agent_mode) if raw_agent_mode is not None else None,
    )
    notifications = NotificationsConfig(
        mode=notif_mode,
        interval_minutes=notif_interval,
        agents=notif_agents,
    )

    history_data = data.get("history", {})
    history = HistoryConfig(
        enabled=history_data.get("enabled", True),
        directory=history_data.get("directory", "~/.archon/history"),
    )

    models_data = data.get("models", {})
    models = ModelsConfig(
        available=list(models_data.get("available", [])),
        default=models_data.get("default") or None,
    )

    plugins_data = data.get("plugins", {})
    plugins = PluginsConfig(
        enabled=plugins_data.get("enabled", True),
        plugins_dir=plugins_data.get("plugins_dir", ""),
        settings_path=plugins_data.get("settings_path", ""),
    )

    agents_data = data.get("agents", {})
    agents_definitions: list[AgentDefinitionConfig] = []
    for defn in agents_data.get("definitions", []):
        agents_definitions.append(AgentDefinitionConfig(
            name=str(defn["name"]),
            description=str(defn.get("description", "")),
            prompt=str(defn["prompt"]),
            tools=list(defn.get("tools", [])),
            model=defn.get("model") or None,
        ))
    agents = AgentsConfig(
        enabled=agents_data.get("enabled", True),
        definitions=agents_definitions,
    )

    return Config(
        telegram_bot_token=token,
        access=access,
        session=session,
        output=output,
        logging=logging_cfg,
        notifications=notifications,
        history=history,
        models=models,
        plugins=plugins,
        agents=agents,
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

    # Write only new-style keys; remove legacy keys if present
    notif = doc["notifications"]  # type: ignore[index]
    for old_key in ("show_thinking_result", "brief_tool_output", "concise_mode", "concise_interval_minutes"):
        if old_key in notif:
            del notif[old_key]  # type: ignore[attr-defined]
    notif["mode"] = notifications.mode  # type: ignore[index]
    notif["interval_minutes"] = notifications.interval_minutes  # type: ignore[index]

    # Persist [notifications.agents] subsection
    if notifications.agents.mode is not None:
        # Ensure the subsection exists and write the mode key
        if "agents" not in notif:
            notif.add("agents", tomlkit.table())  # type: ignore[attr-defined]
        notif["agents"]["mode"] = notifications.agents.mode  # type: ignore[index]
    else:
        # agents.mode=None → remove the mode key if it exists; leave subsection otherwise empty
        if "agents" in notif and "mode" in notif["agents"]:  # type: ignore[index]
            del notif["agents"]["mode"]  # type: ignore[index]

    with path.open("w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
