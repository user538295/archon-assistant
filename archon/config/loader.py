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
class NotificationsConfig:
    mode: str = "normal"        # "normal" | "verbose" | "debug"


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
class QmdConfig:
    enabled: bool = False               # disabled until user explicitly opts in
    host: str = "localhost"             # QMD MCP daemon host
    port: int = 8181                    # QMD MCP daemon port
    history_collection: str = "archon-history"  # collection name for ~/.archon/history


@dataclass
class CronPipelineStep:
    """One step in a cron job pipeline.

    Exactly one of ``tool`` or ``prompt`` should be set:
    - ``tool``   — a bash command/script whose stdout is passed to the next step
    - ``prompt`` — a Claude prompt; ``{input}`` is replaced with the previous step's output
    """
    tool: str | None = None
    prompt: str | None = None


@dataclass
class CronJobConfig:
    """Configuration for a single scheduled cron job."""
    name: str
    schedule: str                           # standard cron expression (5 fields)
    pipeline: list[CronPipelineStep]
    notify_user_id: int | None = None       # Telegram user ID to notify on completion
    timeout_seconds: float = 60.0           # per-step timeout
    enabled: bool = True


@dataclass
class CronConfig:
    """Top-level [cron] config section."""
    enabled: bool = False
    jobs_dir: str = "cron.d"                       # directory containing per-job .toml files
    jobs: list[CronJobConfig] = field(default_factory=list)   # populated at load time


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
    qmd: QmdConfig = field(default_factory=QmdConfig)
    cron: CronConfig = field(default_factory=CronConfig)


def load_cron_jobs(
    jobs_dir: str | Path,
    base_dir: str | Path | None = None,
) -> list[CronJobConfig]:
    """Load cron job configs from *.toml files in jobs_dir.

    Each file's stem (filename without .toml) becomes the job name.
    Files are processed in alphabetical order for deterministic ordering.
    If jobs_dir does not exist, returns an empty list silently.

    Args:
        jobs_dir: Directory containing per-job TOML files.
        base_dir: If jobs_dir is relative, resolve it against this directory
                  (typically the directory containing config.toml).
    """
    dir_path = Path(jobs_dir)
    if base_dir and not dir_path.is_absolute():
        dir_path = Path(base_dir) / dir_path
    if not dir_path.exists():
        return []
    jobs: list[CronJobConfig] = []
    for toml_file in sorted(dir_path.glob("*.toml")):
        name = toml_file.stem
        with toml_file.open("rb") as f:
            job_data = tomllib.load(f)
        steps = [
            CronPipelineStep(
                tool=s.get("tool"),
                prompt=s.get("prompt"),
            )
            for s in job_data.get("pipeline", [])
        ]
        jobs.append(CronJobConfig(
            name=name,
            schedule=job_data["schedule"],
            pipeline=steps,
            notify_user_id=job_data.get("notify_user_id"),
            timeout_seconds=float(job_data.get("timeout_seconds", 60.0)),
            enabled=bool(job_data.get("enabled", True)),
        ))
    return jobs


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
    elif "concise_mode" in notif_data:
        # Migrate old-style keys
        raw = notif_data["concise_mode"]
        if isinstance(raw, bool):
            notif_mode = "normal" if raw else "verbose"
        elif raw == "partial":
            notif_mode = "normal"
        else:  # "full", "off" or anything unrecognised
            notif_mode = "verbose"
    else:
        # No notifications section or no recognised keys → use defaults
        notif_mode = "normal"

    # Migrate legacy "quiet" mode → "normal" (quiet mode has been removed)
    if notif_mode == "quiet":
        notif_mode = "normal"

    notifications = NotificationsConfig(mode=notif_mode)

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

    qmd_data = data.get("qmd", {})
    qmd = QmdConfig(
        enabled=bool(qmd_data.get("enabled", False)),
        host=str(qmd_data.get("host", "localhost")),
        port=int(qmd_data.get("port", 8181)),
        history_collection=str(qmd_data.get("history_collection", "archon-history")),
    )

    raw_cron = data.get("cron", {})
    jobs_dir = str(raw_cron.get("jobs_dir", "cron.d"))
    cron_jobs = load_cron_jobs(jobs_dir, base_dir=Path(config_file).parent)
    cron = CronConfig(
        enabled=bool(raw_cron.get("enabled", False)),
        jobs_dir=jobs_dir,
        jobs=cron_jobs,
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
        qmd=qmd,
        cron=cron,
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
    for old_key in (
        "show_thinking_result", "brief_tool_output",
        "concise_mode", "concise_interval_minutes", "interval_minutes",
    ):
        if old_key in notif:
            del notif[old_key]  # type: ignore[attr-defined]
    notif["mode"] = notifications.mode  # type: ignore[index]

    with path.open("w", encoding="utf-8") as f:
        tomlkit.dump(doc, f)
