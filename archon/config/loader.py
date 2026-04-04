"""Config loader — loads .env and config.toml into typed dataclasses."""
import logging
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

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
    attachments_dir: str = ""  # empty = {working_directory}/attachments
    attachments_cleanup_hours: float = 0  # 0 = disabled


@dataclass
class OutputConfig:
    max_message_length: int = 4000
    truncation_strategy: str = "split"
    head_chars: int = 1500
    tail_chars: int = 1500


@dataclass
class LoggingConfig:
    log_file: str = "~/.archon/logs/archon.log"
    log_level: str = "INFO"


@dataclass
class HistoryConfig:
    enabled: bool = True
    directory: str = "~/.archon/history"
    suppressed_tool_results: list[str] = field(
        default_factory=lambda: ["Read", "Glob", "Grep", "WebFetch"]
    )
    compaction_enabled: bool = True
    context_days: int = 2
    auto_compact_threshold: int = 80  # 0 = disabled; 20-100 = % of context used to trigger compaction
    suppressed_events: list[str] = field(default_factory=list)


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
    context_windows: dict[str, int] = field(default_factory=dict)


@dataclass
class PluginsConfig:
    enabled: bool = True
    plugins_dir: str = ""       # empty = use default (~/.claude/plugins/)
    settings_path: str = ""     # empty = use default (~/.claude/settings.json)


_DEFAULT_SEARCH_COLLECTIONS: list[str] = [
    "~/.archon/history/sessions",
    "~/.archon/workspace",
]


@dataclass
class SearchConfig:
    enabled: bool = False
    host: str = "localhost"
    port: int = 8282
    db_path: str = "~/.archon/search"
    collections: list[str] = field(default_factory=lambda: list(_DEFAULT_SEARCH_COLLECTIONS))
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    providers: list[str] = field(default_factory=list)
    top_k_retrieve: int = 20
    top_k_return: int = 5
    chunk_size: int = 512
    sync_timeout_seconds: int = 0
    deprecated_history_collection: bool = False
    max_parallel_collections: int = 3
    routing_confidence_threshold: float = 0.30
    routing_shortlist_size: int = 8
    pinned_collections: list[str] = field(default_factory=lambda: list(_DEFAULT_SEARCH_COLLECTIONS))
    auto_reindex_on_chunk_size_change: bool = False
    watch: bool = False


# Backward compatibility alias — Task 2.2 will remove all usages
RagConfig = SearchConfig


@dataclass
class VoiceSTTConfig:
    """Speech-to-Text (Whisper) sub-config."""
    model: str = "medium"        # whisper model: tiny, base, small, medium, large
    language: str | None = None  # None = auto-detect; "en", "hu", etc.


@dataclass
class VoiceTTSConfig:
    """Text-to-Speech sub-config."""
    provider: str = "edge"              # "openai" | "edge"
    model: str = "tts-1"                # "tts-1" | "tts-1-hd"
    voice: str = "nova"                 # OpenAI: alloy, echo, fable, onyx, nova, shimmer
    auto: str = "inbound"              # "always" | "inbound" | "off"
    max_text_length: int = 3000
    edge_voice: str = "en-US-MichelleNeural"


@dataclass
class VoiceConfig:
    """Top-level [voice] config section."""
    enabled: bool = False
    stt: VoiceSTTConfig = field(default_factory=VoiceSTTConfig)
    tts: VoiceTTSConfig = field(default_factory=VoiceTTSConfig)


@dataclass
class ReminderConfig:
    """Configuration for periodic context reminder injection.

    When a session accumulates enough messages or tokens, Archon injects a
    compact context-summary prompt into the next Claude turn to counteract
    context drift in long conversations.

    Threshold logic — OR: whichever limit is reached first triggers the injection.
    Set either threshold to a very large value to effectively disable it.

    Fields:
        enabled: Set to true to activate reminder injection (default true).
        interval_messages: Inject after this many user+assistant messages (must be >= 1).
        interval_tokens: Inject after this many cumulative tokens (must be >= 1).
            Counts input_tokens + output_tokens per turn (excludes cache_creation
            because the cold-cache first turn would otherwise blow the threshold).
    """
    enabled: bool = True
    interval_messages: int = 12
    # Tracks input_tokens + output_tokens per turn (~550-3500/turn for typical sessions).
    # 10K fires after ~3-18 turns, complementing the message threshold (12).
    interval_tokens: int = 10_000


@dataclass
class BackgroundAgentsConfig:
    """Configuration for background agent execution (FR.014).

    Archon always hosts a local MCP server exposing ``spawn_background_agent``
    to the main Claude session.  The SDK's native ``Task`` tool is always
    disabled so sub-agents never block the orchestrator's send() turn.

    FR.15 — per-agent working beacon
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    While an agent is running, a new Telegram message is periodically sent
    with live tool/thinking counts.  Set ``beacon_interval_minutes`` to 0
    to disable the beacon entirely.
    """
    spawn_rule: str = "auto"        # "eager" | "auto" | "manual"
    max_parallel: int = 5           # max concurrent background agents per user
    host: str = "localhost"         # MCP server host
    port: int = 18182               # MCP server port
    beacon_interval_minutes: int = 2  # FR.15: interval between beacon messages (0 = off)
    tool_promotion_threshold: int = 10  # promote to background agent after this many tool calls; 0 = disabled
    router_mcp_port: int = 18183    # port for ArchonRouterMCPServer (router session + bg agents)


@dataclass
class SchedulePipelineStep:
    """One step in a scheduled job pipeline.

    - ``name``  — full key name including suffix (e.g. "health_check_tool")
    - ``kind``  — "tool" (shell command) or "prompt" (Claude prompt)
    - ``value`` — command string or prompt template (before {ref} substitution)
    """
    name: str
    kind: Literal["tool", "prompt"]
    value: str


@dataclass
class ScheduledJobConfig:
    """Configuration for a single scheduled job."""
    name: str
    cron: str                               # standard cron expression (5 fields)
    pipeline: list[SchedulePipelineStep]
    timeout_seconds: float = 60.0           # per-step timeout
    enabled: bool = True
    timezone: str | None = None             # IANA timezone name (e.g. "Europe/Budapest"); None = local time
    validation_error: str | None = None     # set if pipeline config is invalid
    source_dir: Path | None = None          # bundle directory, or None for flat files


@dataclass
class ScheduleConfig:
    """Top-level [schedule] config section."""
    enabled: bool = True
    jobs_dir: str = "schedules"                    # directory containing job bundles (name/job.toml) or flat .toml files
    history_enabled: bool = False                  # whether to persist scheduled task execution history
    jobs: list[ScheduledJobConfig] = field(default_factory=list)   # populated at load time


@dataclass
class Config:
    telegram_bot_token: str | None
    access: AccessConfig
    session: SessionConfig
    output: OutputConfig
    logging: LoggingConfig
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    background_agents: BackgroundAgentsConfig = field(default_factory=BackgroundAgentsConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    reminder: ReminderConfig = field(default_factory=ReminderConfig)

    @property
    def rag(self) -> SearchConfig:
        """Backward compatibility alias — Task 2.2 will remove all usages."""
        return self.search

    @rag.setter
    def rag(self, value: SearchConfig) -> None:
        """Backward compatibility alias setter — Task 2.2 will remove all usages."""
        object.__setattr__(self, "search", value)


# Matches {word} placeholders for step-output references.
# Patterns preceded by $ (e.g. ``${word}``) are intentionally excluded so users
# can embed literal braces that should not be treated as step references.
REF_RE = re.compile(r"(?<!\$)\{(\w+)\}")



def _parse_pipeline(
    pipeline_data: dict[str, str],
    job_name: str,
) -> tuple[list[SchedulePipelineStep], str | None]:
    """Parse a flat [pipeline] dict into steps, validating suffixes and refs.

    Step keys must end in ``_tool`` (shell command) or ``_prompt`` (Claude prompt).
    Values may reference earlier steps by name: ``{step_name}``.
    Prefix with ``$`` to suppress substitution: ``${literal}`` is left as-is
    and is not validated as a step reference.

    Returns (steps, None) on success, ([], error_message) on first error.
    """
    if not pipeline_data:
        return [], (
            f"job '{job_name}' has an empty pipeline — "
            f"add at least one step key ending in '_tool' or '_prompt'."
        )

    steps: list[SchedulePipelineStep] = []
    seen: set[str] = set()

    for key, value in pipeline_data.items():
        if key.endswith("_tool"):
            kind: Literal["tool", "prompt"] = "tool"
        elif key.endswith("_prompt"):
            kind = "prompt"
        else:
            return [], (
                f"step '{key}' in job '{job_name}' has no recognized suffix. "
                f"Rename it to end with '_tool' or '_prompt'."
            )

        # Validate references in value
        for ref in REF_RE.findall(value):
            if ref not in seen:
                return [], (
                    f"step '{key}' in job '{job_name}' references '{ref}' which is not a "
                    f"previously defined step. Only backward references are allowed."
                )

        steps.append(SchedulePipelineStep(name=key, kind=kind, value=value))
        seen.add(key)

    return steps, None


def load_scheduled_jobs(
    jobs_dir: str | Path,
    base_dir: str | Path | None = None,
) -> list[ScheduledJobConfig]:
    """Load scheduled job configs from bundle dirs and flat .toml files.

    Two-phase discovery:
      1. Non-recursive subdirectory scan: ``name/job.toml`` → bundle job.
      2. Flat ``*.toml`` glob: ``name.toml`` → flat job.

    If both formats exist for the same name, a collision validation_error is set.
    Jobs are sorted alphabetically by name for deterministic ordering.
    If jobs_dir does not exist, returns an empty list silently.

    Args:
        jobs_dir: Directory containing per-job bundles or TOML files.
        base_dir: If jobs_dir is relative, resolve it against this directory
                  (typically the directory containing config.toml).
    """
    dir_path = Path(jobs_dir)
    if base_dir and not dir_path.is_absolute():
        dir_path = Path(base_dir) / dir_path
    if not dir_path.exists():
        return []

    # Phase 1 — bundle directories (non-recursive, skip symlinks)
    bundles: dict[str, Path] = {}
    for entry in dir_path.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        if (entry / "job.toml").exists():
            bundles[entry.name] = entry

    # Phase 2 — flat *.toml files (skip directories and symlinks)
    flat_files: dict[str, Path] = {}
    for toml_file in dir_path.glob("*.toml"):
        if not toml_file.is_file() or toml_file.is_symlink():
            continue
        flat_files[toml_file.stem] = toml_file

    # Merge and parse
    jobs: list[ScheduledJobConfig] = []
    for name in sorted(set(bundles) | set(flat_files)):
        has_bundle = name in bundles
        has_flat = name in flat_files

        if has_bundle and has_flat:
            jobs.append(ScheduledJobConfig(
                name=name, cron="* * * * *", pipeline=[],
                source_dir=bundles[name],
                validation_error=f"collision: both '{name}.toml' and '{name}/job.toml' exist — remove one",
            ))
            continue

        if has_bundle:
            toml_path = bundles[name] / "job.toml"
            source_dir: Path | None = bundles[name]
        else:
            toml_path = flat_files[name]
            source_dir = None

        try:
            with toml_path.open("rb") as f:
                job_data = tomllib.load(f)
        except (tomllib.TOMLDecodeError, OSError) as exc:
            jobs.append(ScheduledJobConfig(
                name=name, cron="* * * * *", pipeline=[],
                source_dir=source_dir,
                validation_error=f"failed to read '{toml_path.name}': {exc}",
            ))
            continue

        pipeline_data: dict[str, str] = job_data.get("pipeline", {})
        steps, parse_error = _parse_pipeline(pipeline_data, name)
        raw_tz = job_data.get("timezone")
        try:
            cron_expr = job_data["cron"]
        except KeyError:
            raise ConfigError(
                f"scheduled job '{name}' is missing required 'cron' field"
            )
        jobs.append(ScheduledJobConfig(
            name=name,
            cron=cron_expr,
            pipeline=steps,
            timeout_seconds=float(job_data.get("timeout_seconds", 60.0)),
            enabled=bool(job_data.get("enabled", True)),
            timezone=str(raw_tz) if raw_tz else None,
            validation_error=parse_error,
            source_dir=source_dir,
        ))
    return jobs


def load_config(
    env_file: str | Path = "~/.archon/.env",
    config_file: str | Path = "~/.archon/config.toml",
    *,
    require_token: bool = True,
) -> Config:
    """Load config from env_file (.env) and config_file (config.toml).

    Raises ConfigError if required fields are missing.
    Set require_token=False to skip the TELEGRAM_BOT_TOKEN check (e.g. search-only commands).
    """
    load_dotenv(Path(env_file).expanduser())

    token = os.environ.get("TELEGRAM_BOT_TOKEN") or None
    if require_token and not token:
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
        raw_user_ids = data["access"]["allowed_user_ids"]
        for idx, uid in enumerate(raw_user_ids):
            if not isinstance(uid, int):
                raise ConfigError(
                    f"allowed_user_ids[{idx}] must be an integer, got {type(uid).__name__!r}: {uid!r}"
                )
        access = AccessConfig(allowed_user_ids=list(raw_user_ids))
        session_data = data["session"]
        try:
            cleanup_hours = float(session_data.get("attachments_cleanup_hours", 0))
        except (ValueError, TypeError):
            raise ConfigError("attachments_cleanup_hours must be a number")
        session = SessionConfig(
            working_directory=str(Path(session_data["working_directory"]).expanduser()),
            inactivity_timeout_seconds=session_data.get("inactivity_timeout_seconds", SessionConfig.inactivity_timeout_seconds),
            attachments_dir=str(session_data.get("attachments_dir", "")),
            attachments_cleanup_hours=cleanup_hours,
        )
    except KeyError as e:
        raise ConfigError(f"Missing required config key: {e}") from e

    if not access.allowed_user_ids:
        raise ConfigError("allowed_user_ids must not be empty")
    if session.inactivity_timeout_seconds <= 0:
        raise ConfigError("inactivity_timeout_seconds must be > 0")
    if not Path(session.working_directory).expanduser().exists():
        raise ConfigError(f"working_directory does not exist: {session.working_directory}")

    # Resolve attachments_dir: default, expand ~, resolve symlinks, warn on missing parent
    if not session.attachments_dir:
        session.attachments_dir = f"{session.working_directory}/attachments"
    else:
        session.attachments_dir = str(Path(session.attachments_dir).expanduser())
    att_path = Path(session.attachments_dir)
    if att_path.is_symlink():
        resolved = str(att_path.resolve())
        logger.warning("attachments_dir is a symlink, resolved to %s", resolved)
        session.attachments_dir = resolved
        att_path = Path(resolved)
    if not att_path.parent.exists():
        logger.warning("Parent directory of attachments_dir does not exist: %s", att_path.parent)

    if session.attachments_cleanup_hours < 0:
        raise ConfigError("attachments_cleanup_hours must be >= 0 (0 = disabled)")

    output_data = data.get("output", {})
    output = OutputConfig(
        max_message_length=output_data.get("max_message_length", OutputConfig.max_message_length),
        truncation_strategy=output_data.get("truncation_strategy", OutputConfig.truncation_strategy),
        head_chars=output_data.get("head_chars", OutputConfig.head_chars),
        tail_chars=output_data.get("tail_chars", OutputConfig.tail_chars),
    )

    if output.max_message_length <= 0:
        raise ConfigError("max_message_length must be > 0")
    if output.max_message_length > 4096:
        logger.warning("max_message_length %d exceeds Telegram's 4096 limit, clamping", output.max_message_length)
        output = OutputConfig(
            max_message_length=4096,
            truncation_strategy=output.truncation_strategy,
            head_chars=output.head_chars,
            tail_chars=output.tail_chars,
        )

    _valid_truncation_strategies = ("split",)
    if output.truncation_strategy not in _valid_truncation_strategies:
        raise ConfigError(
            f"Invalid truncation_strategy: {output.truncation_strategy!r}. "
            f"Must be one of: {', '.join(_valid_truncation_strategies)}"
        )

    logging_data = data.get("logging", {})
    logging_cfg = LoggingConfig(
        log_file=logging_data.get("log_file", LoggingConfig.log_file),
        log_level=logging_data.get("log_level", LoggingConfig.log_level),
    )

    logging_cfg.log_level = logging_cfg.log_level.upper()
    _valid_log_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    if logging_cfg.log_level not in _valid_log_levels:
        raise ConfigError(
            f"Invalid log_level: {logging_cfg.log_level!r}. "
            f"Must be one of: {', '.join(_valid_log_levels)}"
        )

    notif_data = data.get("notifications", {})
    if "mode" in notif_data:
        # New-style config
        notif_mode = str(notif_data["mode"])
        notif_interval = int(notif_data.get("interval_minutes", NotificationsConfig.interval_minutes))
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
        notif_interval = int(notif_data.get("concise_interval_minutes", NotificationsConfig.interval_minutes))
    else:
        # No notifications section or no recognised keys → use defaults
        notif_mode = NotificationsConfig.mode
        notif_interval = NotificationsConfig.interval_minutes

    _valid_notif_modes = ("quiet", "normal", "verbose", "debug")
    if notif_mode not in _valid_notif_modes:
        raise ConfigError(
            f"Invalid notification mode: {notif_mode!r}. Must be one of: quiet, normal, verbose, debug"
        )

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
    try:
        auto_compact_threshold = int(history_data.get("auto_compact_threshold", HistoryConfig.auto_compact_threshold))
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"[history] auto_compact_threshold must be an integer, got {history_data.get('auto_compact_threshold')!r}"
        ) from exc
    if not (auto_compact_threshold == 0 or 20 <= auto_compact_threshold <= 100):
        raise ConfigError(
            f"[history] auto_compact_threshold must be 0 (disabled) or 20-100, got {auto_compact_threshold}"
        )
    suppressed_events = list(history_data.get("suppressed_events", []))
    from archon.ai.event_renderer import VALID_SUPPRESSED_EVENT_NAMES  # noqa: PLC0415 — lazy: ai/__init__ → ai.truncation → config.loader
    unknown = [name for name in suppressed_events if name not in VALID_SUPPRESSED_EVENT_NAMES]
    if unknown:
        raise ConfigError(
            f"[history] suppressed_events contains unknown event type(s): {unknown!r}. "
            f"Valid names: {sorted(VALID_SUPPRESSED_EVENT_NAMES)}"
        )
    history = HistoryConfig(
        enabled=history_data.get("enabled", HistoryConfig.enabled),
        directory=history_data.get("directory", HistoryConfig.directory),
        suppressed_tool_results=list(
            history_data.get("suppressed_tool_results", ["Read", "Glob", "Grep", "WebFetch"])
        ),
        compaction_enabled=bool(history_data.get("compaction_enabled", HistoryConfig.compaction_enabled)),
        context_days=int(history_data.get("context_days", HistoryConfig.context_days)),
        auto_compact_threshold=auto_compact_threshold,
        suppressed_events=suppressed_events,
    )

    models_data = data.get("models", {})
    models_available = list(models_data.get("available", []))
    models_default = models_data.get("default") or None
    # Bug 22: if available models are listed but no default is set, use the first one.
    # Prevents RoutingEvent.model from being empty in history logs.
    if models_default is None and models_available:
        models_default = models_available[0]
    raw_cw = models_data.get("context_windows")
    if raw_cw is None:
        raw_cw = {}
    if not isinstance(raw_cw, dict):
        raise ConfigError("[models] context_windows must be a TOML table, not a scalar value")
    bad_type = [k for k, v in raw_cw.items() if not isinstance(v, int) or isinstance(v, bool)]
    if bad_type:
        raise ConfigError(f"[models] context_windows values must be integers, got wrong type for: {bad_type!r}")
    invalid_cw = [k for k, v in raw_cw.items() if isinstance(v, int) and not isinstance(v, bool) and v <= 0]
    if invalid_cw:
        raise ConfigError(f"[models] context_windows values must be > 0, got non-positive for: {invalid_cw!r}")
    context_windows: dict[str, int] = dict(raw_cw)
    models = ModelsConfig(
        available=models_available,
        default=models_default,
        context_windows=context_windows,
    )

    plugins_data = data.get("plugins", {})
    plugins = PluginsConfig(
        enabled=plugins_data.get("enabled", True),
        plugins_dir=plugins_data.get("plugins_dir", ""),
        settings_path=plugins_data.get("settings_path", ""),
    )

    search_data = data.get("search", {})
    search_port = int(search_data.get("port", SearchConfig.port))
    if not (1 <= search_port <= 65535):
        raise ConfigError(f"[search] port must be in range 1-65535, got {search_port}")
    search_top_k_retrieve = int(search_data.get("top_k_retrieve", SearchConfig.top_k_retrieve))
    search_top_k_return = int(search_data.get("top_k_return", SearchConfig.top_k_return))
    search_chunk_size = int(search_data.get("chunk_size", SearchConfig.chunk_size))
    if search_top_k_return <= 0:
        raise ConfigError(f"[search] top_k_return must be > 0, got {search_top_k_return}")
    if search_top_k_retrieve <= 0:
        raise ConfigError(f"[search] top_k_retrieve must be > 0, got {search_top_k_retrieve}")
    if search_top_k_return >= search_top_k_retrieve:
        raise ConfigError(
            f"[search] top_k_retrieve must be > top_k_return, "
            f"got top_k_retrieve={search_top_k_retrieve}, top_k_return={search_top_k_return}"
        )
    if search_chunk_size <= 0:
        raise ConfigError(f"[search] chunk_size must be > 0, got {search_chunk_size}")
    search_sync_timeout = int(search_data.get("sync_timeout_seconds", SearchConfig.sync_timeout_seconds))
    if search_sync_timeout < 0:
        raise ConfigError(f"[search] sync_timeout_seconds must be >= 0, got {search_sync_timeout}")
    deprecated_history_collection = "history_collection" in search_data
    if deprecated_history_collection:
        logger.warning(
            "[search] history_collection is no longer supported and is being ignored. "
            "Remove this key from config.toml to silence this warning."
        )
    search = SearchConfig(
        enabled=bool(search_data.get("enabled", SearchConfig.enabled)),
        host=str(search_data.get("host", SearchConfig.host)),
        port=search_port,
        db_path=str(search_data.get("db_path", SearchConfig.db_path)),
        collections=list(search_data.get("collections", _DEFAULT_SEARCH_COLLECTIONS)),
        embedding_model=str(search_data.get("embedding_model", SearchConfig.embedding_model)),
        reranker_model=str(search_data.get("reranker_model", SearchConfig.reranker_model)),
        providers=list(search_data.get("providers", [])),
        top_k_retrieve=search_top_k_retrieve,
        top_k_return=search_top_k_return,
        chunk_size=search_chunk_size,
        sync_timeout_seconds=search_sync_timeout,
        deprecated_history_collection=deprecated_history_collection,
        max_parallel_collections=int(search_data.get("max_parallel_collections", SearchConfig.max_parallel_collections)),
        routing_confidence_threshold=float(search_data.get("routing_confidence_threshold", SearchConfig.routing_confidence_threshold)),
        routing_shortlist_size=int(search_data.get("routing_shortlist_size", SearchConfig.routing_shortlist_size)),
        pinned_collections=list(search_data.get("pinned_collections", _DEFAULT_SEARCH_COLLECTIONS)),
        auto_reindex_on_chunk_size_change=bool(search_data.get("auto_reindex_on_chunk_size_change", SearchConfig.auto_reindex_on_chunk_size_change)),
        watch=bool(search_data.get("watch", SearchConfig.watch)),
    )
    if search.max_parallel_collections < 1:
        raise ConfigError(f"[search] max_parallel_collections must be >= 1, got {search.max_parallel_collections}")
    if not (0.0 <= search.routing_confidence_threshold <= 1.0):
        raise ConfigError(
            f"[search] routing_confidence_threshold must be in [0.0, 1.0], got {search.routing_confidence_threshold}"
        )
    if search.routing_shortlist_size < 1:
        raise ConfigError(f"[search] routing_shortlist_size must be >= 1, got {search.routing_shortlist_size}")

    raw_schedule = data.get("schedule", {})
    jobs_dir = str(raw_schedule.get("jobs_dir", ScheduleConfig.jobs_dir))
    scheduled_jobs = load_scheduled_jobs(jobs_dir, base_dir=config_path.parent)
    schedule = ScheduleConfig(
        enabled=bool(raw_schedule.get("enabled", ScheduleConfig.enabled)),
        jobs_dir=jobs_dir,
        history_enabled=bool(raw_schedule.get("history_enabled", ScheduleConfig.history_enabled)),
        jobs=scheduled_jobs,
    )

    raw_bg = data.get("background_agents", {})
    try:
        bg_max_parallel = int(raw_bg.get("max_parallel", BackgroundAgentsConfig.max_parallel))
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"[background_agents] max_parallel must be an integer, got {raw_bg.get('max_parallel')!r}"
        ) from exc
    try:
        bg_port = int(raw_bg.get("port", BackgroundAgentsConfig.port))
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"[background_agents] port must be an integer, got {raw_bg.get('port')!r}"
        ) from exc
    # Deprecation shim: accept old key "orch_mcp_port" if "router_mcp_port" is absent.
    _raw_router_port_key = "router_mcp_port"
    if _raw_router_port_key in raw_bg and "orch_mcp_port" in raw_bg:
        logger.warning(
            "both 'orch_mcp_port' and 'router_mcp_port' present in config; "
            "using 'router_mcp_port' — remove the deprecated 'orch_mcp_port' key"
        )
    elif _raw_router_port_key not in raw_bg and "orch_mcp_port" in raw_bg:
        logger.warning("config key 'orch_mcp_port' renamed to 'router_mcp_port'; update your config.toml")
        _raw_router_port_key = "orch_mcp_port"
    try:
        bg_router_mcp_port = int(raw_bg.get(_raw_router_port_key, BackgroundAgentsConfig.router_mcp_port))
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            f"[background_agents] router_mcp_port must be an integer, got {raw_bg.get(_raw_router_port_key)!r}"
        ) from exc
    background_agents = BackgroundAgentsConfig(
        spawn_rule=str(raw_bg.get("spawn_rule", BackgroundAgentsConfig.spawn_rule)),
        max_parallel=bg_max_parallel,
        host=str(raw_bg.get("host", BackgroundAgentsConfig.host)),
        port=bg_port,
        beacon_interval_minutes=int(raw_bg.get("beacon_interval_minutes", BackgroundAgentsConfig.beacon_interval_minutes)),
        tool_promotion_threshold=int(raw_bg.get("tool_promotion_threshold", BackgroundAgentsConfig.tool_promotion_threshold)),
        router_mcp_port=bg_router_mcp_port,
    )
    _valid_spawn_rules = ("eager", "auto", "manual")
    if background_agents.spawn_rule not in _valid_spawn_rules:
        raise ConfigError(
            f"Invalid spawn_rule: {background_agents.spawn_rule!r}. "
            f"Must be one of: {', '.join(_valid_spawn_rules)}"
        )

    def _validate_port(value: int, label: str) -> None:
        if not (1 <= value <= 65535):
            raise ConfigError(f"{label} port must be in range 1-65535, got {value}")

    _validate_port(background_agents.port, "[background_agents]")
    _validate_port(background_agents.router_mcp_port, "[background_agents] router_mcp")
    if background_agents.tool_promotion_threshold < 0:
        raise ConfigError("[background_agents] tool_promotion_threshold must be >= 0 (0 = disabled)")
    if background_agents.port == background_agents.router_mcp_port:
        raise ConfigError(
            f"background_agents.port and background_agents.router_mcp_port must be different"
            f" (both are {background_agents.port})"
        )

    raw_voice = data.get("voice", {})
    raw_stt = raw_voice.get("stt", {})
    raw_tts = raw_voice.get("tts", {})
    voice = VoiceConfig(
        enabled=bool(raw_voice.get("enabled", VoiceConfig.enabled)),
        stt=VoiceSTTConfig(
            model=str(raw_stt.get("model", VoiceSTTConfig.model)),
            language=raw_stt.get("language") or None,
        ),
        tts=VoiceTTSConfig(
            provider=str(raw_tts.get("provider", VoiceTTSConfig.provider)),
            model=str(raw_tts.get("model", VoiceTTSConfig.model)),
            voice=str(raw_tts.get("voice", VoiceTTSConfig.voice)),
            auto=str(raw_tts.get("auto", VoiceTTSConfig.auto)),
            max_text_length=int(raw_tts.get("max_text_length", VoiceTTSConfig.max_text_length)),
            edge_voice=str(raw_tts.get("edge_voice", VoiceTTSConfig.edge_voice)),
        ),
    )

    _valid_tts_auto = ("always", "inbound", "off")
    if voice.tts.auto not in _valid_tts_auto:
        raise ConfigError(
            f"Invalid [voice.tts] auto value: {voice.tts.auto!r}. Must be one of: always, inbound, off"
        )

    raw_reminder = data.get("reminder", {})
    reminder = ReminderConfig(
        enabled=bool(raw_reminder.get("enabled", ReminderConfig.enabled)),
        interval_messages=int(raw_reminder.get("interval_messages", ReminderConfig.interval_messages)),
        interval_tokens=int(raw_reminder.get("interval_tokens", ReminderConfig.interval_tokens)),
    )
    if reminder.interval_messages < 1:
        raise ConfigError("[reminder] interval_messages must be >= 1")
    if reminder.interval_tokens < 1:
        raise ConfigError("[reminder] interval_tokens must be >= 1")

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
        search=search,
        schedule=schedule,
        background_agents=background_agents,
        voice=voice,
        reminder=reminder,
    )


def atomic_write(path: Path, content: str) -> None:
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
        os.replace(str(tmp), str(path))
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


def _file_lock(f: Any) -> None:
    """Acquire an exclusive file lock (POSIX: fcntl, Windows: msvcrt)."""
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    except ImportError:
        try:
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        except ImportError:
            pass  # No locking available — best-effort


def _file_unlock(f: Any) -> None:
    """Release a file lock (POSIX: fcntl, Windows: msvcrt)."""
    try:
        import fcntl
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except ImportError:
        try:
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except ImportError:
            pass


def save_notifications_config(
    notifications: NotificationsConfig,
    config_file: str | Path = "~/.archon/config.toml",
) -> None:
    """Persist notification settings to config.toml, preserving all other sections."""
    path = Path(config_file).expanduser()
    lock_file = path.with_suffix(".toml.lock")
    lock_f = lock_file.open("w")
    try:
        _file_lock(lock_f)

        with path.open("r", encoding="utf-8") as f:
            doc = tomlkit.load(f)

        if "notifications" not in doc:
            doc.add("notifications", tomlkit.table())

        # Write only new-style keys; remove legacy keys if present
        notif: Any = doc["notifications"]
        for old_key in ("show_thinking_result", "brief_tool_output", "concise_mode", "concise_interval_minutes"):
            if old_key in notif:
                del notif[old_key]
        notif["mode"] = notifications.mode
        notif["interval_minutes"] = notifications.interval_minutes

        # Persist [notifications.agents] subsection
        if notifications.agents.mode is not None:
            # Ensure the subsection exists and write the mode key
            if "agents" not in notif:
                notif.add("agents", tomlkit.table())
            notif["agents"]["mode"] = notifications.agents.mode
        else:
            # agents.mode=None → remove the mode key if it exists; leave subsection otherwise empty
            if "agents" in notif and "mode" in notif["agents"]:
                del notif["agents"]["mode"]

        atomic_write(path, tomlkit.dumps(doc))
    finally:
        _file_unlock(lock_f)
        lock_f.close()
