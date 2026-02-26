"""Config loader — loads .env and config.toml into typed dataclasses."""
import logging
import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from dotenv import load_dotenv

logger = logging.getLogger("archon")


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
    suppressed_tool_results: list[str] = field(
        default_factory=lambda: ["Read", "Glob", "Grep", "WebFetch"]
    )


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
class QmdConfig:
    enabled: bool = False               # disabled until user explicitly opts in
    host: str = "localhost"             # QMD MCP daemon host
    port: int = 8181                    # QMD MCP daemon port
    history_collection: str = "archon-history"  # collection name for ~/.archon/history


@dataclass
class BackgroundAgentsConfig:
    """Configuration for background agent execution (FR.014).

    Archon always hosts a local MCP server exposing ``spawn_background_agent``
    to the main Claude session.  The SDK's native ``Task`` tool is always
    disabled so sub-agents never block the orchestrator's send() turn.

    FR.15 — per-agent working beacon
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    While an agent is running, its spawn-notification message is periodically
    edited in-place to show live tool/thinking counts.  Set
    ``beacon_interval_minutes`` to 0 to disable the beacon entirely.
    """
    spawn_rule: str = "auto"        # "eager" | "auto" | "manual"
    max_parallel: int = 5           # max concurrent background agents per user
    host: str = "localhost"         # MCP server host
    port: int = 18182               # MCP server port
    beacon_interval_minutes: int = 2  # FR.15: how often to edit the spawn msg (0 = off)


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
    timezone: str | None = None             # IANA timezone name (e.g. "Europe/Budapest"); None = local time


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
    background_agents: BackgroundAgentsConfig = field(default_factory=BackgroundAgentsConfig)


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
        raw_tz = job_data.get("timezone")
        jobs.append(CronJobConfig(
            name=name,
            schedule=job_data["schedule"],
            pipeline=steps,
            notify_user_id=job_data.get("notify_user_id"),
            timeout_seconds=float(job_data.get("timeout_seconds", 60.0)),
            enabled=bool(job_data.get("enabled", True)),
            timezone=str(raw_tz) if raw_tz else None,
        ))
    return jobs


def load_config(
    env_file: str | Path = "~/.archon/.env",
    config_file: str | Path = "~/.archon/config.toml",
) -> Config:
    """Load config from env_file (.env) and config_file (config.toml).

    Raises ConfigError if required fields are missing.
    """
    load_dotenv(Path(env_file).expanduser())

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is missing from environment or .env file")

    config_path = Path(config_file).expanduser()
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    backup_path = config_path.with_suffix(".toml.bak")
    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        # Config file is corrupt.  Attempt to restore from the backup
        # created during the last successful load.
        if backup_path.exists():
            logger.warning(
                "config.toml is corrupt (%s); restoring from %s",
                exc,
                backup_path,
            )
            shutil.copy2(backup_path, config_path)
            with config_path.open("rb") as f:
                data = tomllib.load(f)
        else:
            raise ConfigError(
                f"config.toml is corrupt ({exc}) and no backup exists at {backup_path}"
            ) from exc

    # Backup the known-good config so future loads can recover from corruption.
    try:
        shutil.copy2(config_path, backup_path)
    except OSError as exc:
        logger.warning("Failed to back up config.toml: %s", exc)

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
        suppressed_tool_results=list(
            history_data.get("suppressed_tool_results", ["Read", "Glob", "Grep", "WebFetch"])
        ),
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
    cron_jobs = load_cron_jobs(jobs_dir, base_dir=config_path.parent)
    cron = CronConfig(
        enabled=bool(raw_cron.get("enabled", False)),
        jobs_dir=jobs_dir,
        jobs=cron_jobs,
    )

    raw_bg = data.get("background_agents", {})
    background_agents = BackgroundAgentsConfig(
        spawn_rule=str(raw_bg.get("spawn_rule", "auto")),
        max_parallel=int(raw_bg.get("max_parallel", 5)),
        host=str(raw_bg.get("host", "localhost")),
        port=int(raw_bg.get("port", 18182)),
        beacon_interval_minutes=int(raw_bg.get("beacon_interval_minutes", 2)),
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
        background_agents=background_agents,
    )


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* atomically via write-to-temp-then-rename.

    The temporary file is created in the same directory as *path* so that
    ``os.rename`` is guaranteed to be atomic (same filesystem).  If the
    process is killed between ``open()`` and ``rename()``, only the
    temporary file is left behind — the original *path* is never truncated.
    """
    tmp = path.with_suffix(".toml.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)
    except BaseException:
        # Clean up the temp file on any failure (including KeyboardInterrupt).
        with _suppress_os_errors():
            tmp.unlink()
        raise


class _suppress_os_errors:  # noqa: N801 — tiny context manager
    """Suppress OSError inside a ``with`` block (e.g. unlink of missing file)."""

    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type: type | None, *_: object) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def save_notifications_config(
    notifications: NotificationsConfig,
    config_file: str | Path = "~/.archon/config.toml",
) -> None:
    """Persist notification settings to config.toml, preserving all other sections."""
    path = Path(config_file).expanduser()
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

    _atomic_write(path, tomlkit.dumps(doc))
