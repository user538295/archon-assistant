# CLAUDE.md

Archon Assistant — a local daemon that bridges Telegram with Claude Code via the Claude Agent SDK, forwarding every state transition as a real-time Telegram notification.

## Documentation

Comprehensive project documentation lives in [Documentation/](Documentation/):
- **Architecture**: [Documentation/Architecture/](Documentation/Architecture/) — system design, component catalog, data architecture, error handling, security, testing strategy, coding conventions. Start with [000_introduction_and_guiding_principles.md](Documentation/Architecture/000_introduction_and_guiding_principles.md).
- **ADRs**: [Documentation/ADRs/](Documentation/ADRs/) — architectural decision records (SDK choice, streaming, session model, deployment, access control, MCP, truncation, config write-back, history format).
- **User manual**: [Documentation/UserManual/](Documentation/UserManual/) — end-user docs, CLI reference, scheduled jobs guide.
- **Backlog**: [Documentation/Backlog/](Documentation/Backlog/) — open feature requests and bug reports.
- **Completed**: [Documentation/Completed/](Documentation/Completed/) — implemented epics and feature records.
- **Index**: [Documentation/990_documentation_index_and_contribution_guide.md](Documentation/990_documentation_index_and_contribution_guide.md)

Always consult the relevant Architecture doc before making design decisions.

## Release process

See [Documentation/release-process.md](Documentation/release-process.md) for the full step-by-step procedure. Summary:
1. Run tests (`uv run pytest --no-cov -q --tb=no`)
2. Calculate version: `COUNT=$(git rev-list --count HEAD); echo "v$(date +%y).$(date +%-m).$((COUNT+2))"` ← `+2` because RELEASE.md commit adds 1, `release.sh` adds 1 more
3. Add RELEASE.md entry with that exact version
4. Commit: `git add RELEASE.md && git commit -m "chore(release): update RELEASE.md for vYY.M.NNN"`
5. Verify version still matches (extra commits shift the count)
6. Run `bash release.sh` (requires `GITHUB_TOKEN`)

## Commands

```bash
# Install dependencies
uv sync

# Run the daemon
uv run python main.py

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/ai/test_event_mapper.py

# Run a single test by name
uv run pytest -k "test_split_strategy_labels"

# Type check
uv run mypy archon/

# Install as launchd service (macOS) / systemd (Linux)
uv run install.py

# Install flags
uv run install.py --uninstall          # remove service and ~/.archon
uv run install.py --update             # pull latest + restart, preserve config
uv run install.py --dry-run            # print actions without executing
uv run install.py --non-interactive    # read ARCHON_BOT_TOKEN / ARCHON_USER_IDS from env
uv run install.py --tag <version>      # install a specific release tag

# CLI (after install)
archon start | stop | restart | status
archon logs [--follow] [--lines N] [--date YYYY-MM-DD]
archon update [--tag <version>]
archon doctor                          # pre-flight health checks
archon config show | edit | get <key> | set <key> <value>
archon version

# Tail logs (without CLI)
tail -f ~/.archon/logs/archon.log
```

## Architecture

Four modules + CLI wired together by a gateway, all running in a single asyncio event loop.

> **Architecture diagram**: See [System Architecture Overview](Documentation/Architecture/100_system_architecture_overview.md) for the full diagram, C4 views, and data-flow diagrams. A compact reference is also in [README.md — Architecture](README.md#architecture).

**`archon/config/`** — loads `.env` (bot token) + `config.toml` (everything else) into a typed singleton at startup. All modules import `from archon.config import config`. Raises `ConfigError` on missing required fields.

**`archon/ai/`** — AI and background execution layer. Core runtime components (`Pipeline`, `ClaudeSession`, `EventMapper`, `SessionManager`, `BackgroundAgentManager`, `ArchonMCPServer`, `JobScheduler`, `TruncationStrategy`) are documented in [README.md — Architecture](README.md#architecture) and [Component Catalog](Documentation/Architecture/110_component_catalog_and_layer_breakdown.md). Additional modules:
- `Pipeline`: multi-agent routing — Classifier (Haiku) classifies intent, Decomposer (user-selected model) handles the request. Duck-types as `ClaudeSession`. Router events from `route_task()` are re-tagged with `source="router"` and streamed inline before main-session events.
- `classifier.py`: `Classifier` — intent classification via Haiku; `classify(prompt, recent_context=None)` accepts last N user messages as context to disambiguate follow-up messages; `Classification` + `parse_classification()` with resilient JSON parser (defaults to `task` on failure)
- `decomposer.py`: `Decomposer` + `TaskOutput` — task execution with timeout thresholds and orchestrator session management. `route_task()` is an async generator (`AsyncGenerator[Event | TaskOutput, None]`) that yields router session events first, then a `TaskOutput` sentinel; `is_router_event(event)` checks `source=="router"`. Router events render with `[Router]` prefix in history; ToolResult content is truncated to 160 chars without summarization; Response content is suppressed (replaced with a "Routing decision" heading). Chat handler shows router events only in verbose/debug mode (except ErrorEvent which is always shown); quiet mode suppresses router events without counting them toward beacon totals. TTS capture skips router Response.
- `prompts/`: system prompt files (`classifier.md`, `decomposer.md`) loaded via `load_prompt()`
- `agent_plan.py`: `AgentPlan` + `AgentTask` dataclasses; `parse_agent_plan()` detects large-scope plans; `validate_dependency_graph()` + `topological_sort()` produce execution waves
- `plan_executor.py`: `PlanExecutor` — resolves dependency graph, spawns workers via `BackgroundAgentManager` wave-by-wave
- `constants.py`: shared constants — `DEFAULT_MODEL`, `DEFAULT_FAST_MODEL`, `AVAILABLE_MODELS: dict[str, int]` (model name → context window), `MODEL_ALIASES`
- `history_compactor.py`: `HistoryCompactor` — daily history summarization via Haiku; creates `-compacted.md` digests
- `context_provider.py`: `ContextProvider` protocol — read-only history context interface for session startup; `search_enabled` parameter controls whether search context is included
- `event_renderer.py`: `EventRenderer` — renders SDK events to Markdown for history logging
- `tool_result_policy.py`: `should_suppress_tool_result()` + `summarize_tool_result()` — suppression policy for verbose tool output in history
- `reminder.py`: `ContextReminder` — periodic injection of `REMINDER.md` to prevent context drift; merges versioned `archon/ai/prompts/system_reminder.md` (Archon Control Plane) with `workspace/REMINDER.md` (user rules)
- `stt.py`: `STTHandler` — async speech-to-text via Whisper CLI subprocess; auto-detects binary via `get_runtime().find_binary("whisper")`
- `tts.py`: `TTSHandler` + `TTSConfig` — text-to-speech via OpenAI TTS API or Edge TTS CLI (free fallback)
- `archon_toolkit.py`: `ArchonToolkit` — central registry for Archon control-plane MCP tools (`archon_status`, `send_notification`, `send_file`, `list_attachments`, `list_running_agents`, `get_agent_status`, `cancel_agent`, `archon_restart`, `read_agent_log`, `get_agent_by_name`, `get_session_status`, `get_context_stats`, `set_notification_mode`, `get_model`, `set_model`, `list_skills`, `list_scheduled_tasks`, `add_scheduled_task`, `update_scheduled_task`, `remove_scheduled_task`, `get_job_config`, `get_config`, `set_config`, `get_version`, `get_logs`, `archon_doctor`; Search tools registered via `archon_toolkit_search.py`); dispatched by both `ArchonMCPServer` and `ArchonRouterMCPServer`
- `archon_toolkit_search.py`: standalone Search helper module (not a mixin) — registers Search tools into `ArchonToolkit` via `_register_search_tools()`; tools: `search_status`, `search_start`, `search_stop`, `search_ingest`, `search_collection_list`, `search_collection_add`, `search_collection_remove`, `search_collection_info`, `search_collection_reindex`
- `search_client.py`: `SearchClient` — async HTTP client adapter for the `archon-search` REST API (sole import boundary); `get_search_client()` / `reset_search_client()` singletons; all methods return `None`/`[]`/status code on failure, never raise; HTTP read-back methods: `telemetry_stats()` (`GET /telemetry/stats`), `telemetry_entries()` (`GET /telemetry/entries`); `search(collection, query, top_k)` — `POST /search`, returns `SearchQueryResult(results: list[dict], acl_filtered: bool)` or empty `SearchQueryResult` on failure; bare-list fallback for old servers (logs WARNING). `SearchApiKeyAuth(httpx.Auth)` — lazy key load (env var `ARCHON_SEARCH_API_KEY` first, then `~/.archon/.search.env`), caches on success, clears cache and retries on 401, logs ERROR only when retry with valid key also fails; WARNING when no key found; key value never appears in logs; wired into `SearchClient` by default
- `search_context_provider.py`: `SearchContextProvider` — orchestrates multi-collection routing (via `SearchClient.route()`) and parallel search; one instance per `Pipeline`
- `notification_monitor.py` (`archon/gateway/notification_monitor.py`): `IndexingNotificationMonitor` — asyncio background task that polls `GET /indexing-state` via `SearchClient` (HTTP, no direct file access) and sends a Telegram summary notification when all collections reach a terminal state (DONE/FAILED) after an `"install"` or `"update"` trigger; suppressed in `quiet` mode and for `"manual"` triggers
- `diagnostics.py`: `CheckResult` dataclass + all `_check_*` functions + `run_checks() -> list[CheckResult]` — synchronous health checks shared by CLI and MCP toolkit
- `archon_router_mcp_server.py`: `ArchonRouterMCPServer` — MCP server for router-level tools (separate from background agent MCP)
- `SkillLoader`: reads `~/.claude/skills/*/SKILL.md` (YAML frontmatter: name, description)
- `PluginLoader`: reads `~/.claude/plugins/` + `settings.json`; exposes SDK configs and skills
- `AgentLoader`: reads `~/.claude/agents/*.md`; `-archon` suffix → injected into sessions
- `HistoryManager`: appends conversation turns to `~/.archon/history/sessions/YYYY-MM-DD.md`
- `AgentLogger`: writes per-agent events to `~/.archon/history/sessions/YYYY-MM-DD-HH-MM-{name}.md`
- `attachment_types.py`: `AttachmentInfo` dataclass, `detect_mime_type()`, `format_file_size()`, `check_file_size()`
- `attachment_store.py`: `AttachmentStore` — date-based storage with filename sanitization, collision handling, TTL cleanup
- `attachment_prompt.py`: `build_attachment_prompt()` — structured text prompts for all file types
- `image_resizer.py`: `ImageResizer` — Pillow-based auto-resize for images exceeding thresholds

**Async generator timeout pattern**: never use `asyncio.timeout()` spanning a `yield` — it is cancelled by the generator's own `throw()` path, causing silent drops. Use the rolling-deadline `asyncio.wait_for(gen.__anext__(), timeout=remaining)` pattern instead. See FIX-028.

**`archon/chat/`** — aiogram 3.x bot with whitelist middleware (drops non-whitelisted user IDs before any handler runs, for both `Message` and `CallbackQuery`). Key modules:
- `bot.py`: bot and dispatcher creation, bot command menu setup
- `commands.py`: all Telegram command handlers (`/start`, `/status`, `/context`, `/stop`, `/clear`, `/restart`, `/notify`, `/skills`, `/skill`, `/models`, `/agents`, `/tasks`, `/scheduled`, `/command`). Hidden aliases: `/quiet`, `/normal`, `/verbose`, `/debug`, `/model`, `/jobs`, `/running_agents`, `/commands`. Inline keyboard callbacks: `notify:<mode>`, `model:<name>`, `cancel_agent:<id>`.
- `command_loader.py`: `CommandLoader` — discovers `.md` command files from global and project dirs, exposes `load_all()` and `exists()`
- `handler.py`: main message handler — calls `async for event in pipeline.send(text):` and sends each formatted event to Telegram
- `middleware.py`: `WhitelistMiddleware` — access control filter
- `md_formatter.py`: Markdown → Telegram HTML conversion via mistune 3.x (`_TelegramRenderer`)
- `telegram_delivery.py`: `render_split_messages()` — binary-search message splitting within Telegram's size limit
- `voice.py`: `VoiceMessageHandler` — voice/audio transcription via `STTHandler`, optional TTS reply via `TTSHandler`
- `file_handler.py`: `FileHandler` — document, photo, video, sticker, audio attachment handlers; delegates to `handle_message` with `prompt_override`
- `media_group_collector.py`: `MediaGroupCollector` — collects Telegram albums by `media_group_id` with 1s timeout

Handler registration order: commands -> callbacks -> sticker -> photo -> video -> voice/audio (mutually exclusive) -> document -> generic text. File handlers delegate to `handle_message(..., prompt_override=prompt)` — no event streaming duplication.

**`archon/cli/`** — command-line interface for service management (installed as `archon` entry point):
- `main.py`: CLI entry point with subcommand routing
- `service.py`: `start`, `stop`, `restart` — delegates to `archon/platform/`
- `status.py`: service status with health checks, PID, uptime
- `logs.py`: log viewer with `--follow`, `--lines`, `--date` filtering
- `update.py`: pull latest release + restart; `--tag` for specific versions
- `doctor.py`: pre-flight checks (Python version, dependencies, config validity, service state); Search collection health (staleness, model mismatch) via SearchClient HTTP; suppresses false alarms on `IN_PROGRESS`/`PENDING` collections — shows `⏳ partial (N/M files)` informational output instead of warnings; `FAILED` collections still show `❌`; `_check_search_key_file()` (in `diagnostics.py`) — checks `~/.archon/.search.env` exists with permissions 600 (POSIX only; skipped on Windows; skipped when `ARCHON_SEARCH_API_KEY` env var is set); authenticated status check piggybacks on existing `health()` call — 401 reported as actionable failure
- `config_cmd.py`: `show`, `edit`, `get <key>`, `set <key> <value>` — config inspection and modification

**`archon/platform/`** — Strategy pattern for cross-platform service management and runtime operations. Two ABCs: `PlatformService` (service lifecycle: start/stop/restart/status/register/unregister) and `PlatformRuntime` (signal handling, binary discovery, process restart). Lazy singletons via `get_service()` / `get_runtime()` with `override()` / `reset()` for DI in tests. Implementations: `macos/` (launchd), `linux/` (systemd), `windows/` (stubs — service management not yet supported, manual run only). Supporting modules: `types.py` (`ServiceInfo` dataclass), `_run_mixin.py` (shared subprocess helper). All platform-specific code is isolated here — no `platform.system()` / `sys.platform` checks elsewhere in `archon/`.

**`archon/gateway/`** — orchestrator: initializes config and logging, starts bot and session manager, routes events bidirectionally, handles SIGTERM/SIGINT graceful shutdown (`stop_all()` → bot disconnect, ≤5s).

**`archon/version.py`** — `get_version()` — cached version computation from git tags/commit count (`YY.M.<count>` format).

**`main.py`** — single entry point: `Gateway.start()`.

## Output event model

Every Claude state change produces a Telegram notification. Thinking is merged into a single message.

| Event dataclass | Telegram format |
|---|---|
| `ClassificationEvent` | `🏷 task (95%)` (verbose/debug only) |
| `ThinkingResult` | `💭 Thinking:\n<content>` |
| `ToolStarted(name, input)` | `🔧 Tool: <name>` + input summary |
| `ToolResult` | `📤 Result:\n<content>` |
| `Response` | `✅ Response:\n<content>` |
| `ErrorEvent` | `❌ Error: <message>` |
| `SubagentStarted` | `🤖 Agent <b>Name</b> started` |
| `SubagentStopped` | `🤖 Agent <b>Name</b> done` |
| `ReminderInjectedEvent` | `🔔 Reminder injected (message N)` (verbose/debug only) |
| `ContextInjectedEvent` | `📌 Context injected [<type>] (N <unit>)[: detail]` (verbose/debug only) — unit set by `[output] size_unit` |
| `SkillInjectedEvent` | `🎯 Skill injected: <name> (N <unit>)` (verbose/debug only) — unit set by `[output] size_unit` |

Router variants (source=router) — suppressed in quiet/normal, visible in verbose/debug:

| Event | Telegram format |
|---|---|
| `ToolStarted` source=router | `🔧 [Router] {name}` (verbose/debug) |
| `ToolResult` source=router | `📤 [Router] {summary ≤160}` (verbose/debug) |
| `ThinkingResult` source=router | `💭 [Router] Thinking:` (debug only) |
| `Response` source=router | history-only (🎯 Routing decision — never sent to Telegram) |
| `ErrorEvent` source=router | `❌ [Router] Error: <message>` (all modes) |

Content-bearing events pass through `TruncationStrategy` before sending.

## Configuration

`.env` — `TELEGRAM_BOT_TOKEN` only.

`config.toml` sections and key fields:
- `[access] allowed_user_ids`
- `[session] working_directory`, `inactivity_timeout_seconds`, `attachments_dir`, `attachments_cleanup_hours` — file attachment storage and TTL cleanup
- `[output] max_message_length`, `truncation_strategy`, `size_unit` (`"chars"` default | `"codepoints"` | `"words"` | `"tokens"` | `"lines"` | `"sentences"`) — unit for injection event size display; `"tokens"` requires `tiktoken`
- `[notifications] mode` (`quiet`/`normal`/`verbose`/`debug`), `interval_minutes`; `[notifications.agents] mode`
- `[logging] log_file`, `log_level`
- `[history] enabled`, `directory`, `suppressed_tool_results`, `suppressed_events` — list of event type names to exclude from history files entirely (default: `[]`), `compaction_enabled`, `context_days`, `auto_compact_threshold`
- `[models] available`, `default`
- `[plugins] enabled`, `plugins_dir`, `settings_path`
- `[search] enabled`, `url` (default `"http://127.0.0.1:8765"`), `max_parallel_collections` (max concurrent search operations; default `3`), `top_k_return` (results returned after reranking; default `5`) — auth key lives in `~/.archon/.search.env` (not config.toml); override with `ARCHON_SEARCH_API_KEY` env var; server-side fields (`db_path`, `embedding_model`, `chunk_size`, `pinned_collections`, `routing_*`, `watch`, etc.) are configured in `~/.archon/archon-search.toml` and emit a deprecation warning if present in `config.toml`; `[telemetry]` in `archon-search.toml`: `enabled` (default `false`), `retention_days` (default `30`), `log_dir` (default `"~/.archon/search-logs"`) — opt-in local JSONL query logging; `export_enabled = true` logs a WARNING and is ignored (reserved for FEAT-039d); HTTP read-back endpoints: `GET /telemetry/stats` (aggregated query statistics), `GET /telemetry/entries` (raw JSONL log entries with optional filters)
- `[schedule] enabled` (default `true`), `jobs_dir`, `history_enabled` (default `false`) — job bundles (`name/job.toml` directories) or flat files (`name.toml`, deprecated) in `jobs_dir/`; when `history_enabled = true` each job run is logged to `~/.archon/history/schedule/`
- `[background_agents] spawn_rule`, `max_parallel`, `host`, `port`, `beacon_interval_minutes`, `tool_promotion_threshold`, `router_mcp_port`
- `[voice] enabled` (default `false`); `[voice.stt] model` (default `"medium"`), `language` (default `null` = auto); `[voice.tts] provider` (`"openai"`/`"edge"`), `model`, `voice`, `auto` (`"always"`/`"inbound"`/`"off"`), `max_text_length`, `edge_voice`
- `[reminder] enabled` (default `true`); `interval_messages` (default `20`), `interval_tokens` (default `10000`) — OR thresholds, whichever is reached first triggers injection. Telegram notification shown only in verbose/debug mode

See [examples/config.toml.example](examples/config.toml.example) for the full annotated reference.

## Test structure

Tests are organized by module under `tests/`:
- `tests/ai/` — event mapper, classifier, session, pipeline, background agents, history, truncation, etc.
- `tests/chat/` — bot, commands, handler, voice, middleware, delivery
- `tests/cli/` — config, doctor, logs, main, service, status, update
- `tests/config/` — config loader, Search config
- `tests/gateway/` — full flow, shutdown, Search integration
- `tests/platform/` — organized by OS (`macos/`, `linux/`, `windows/`) with runtime/service tests
- `tests/schedule/` — job scheduler, schedule config, integration

## Key constraints

- TDD is mandatory — write tests before implementation. Maintain ≥85% coverage.
- **Cross-platform**: all new features must work on macOS, Linux, and Windows (where applicable). All platform-specific code goes in `archon/platform/` — no `platform.system()` / `sys.platform` checks elsewhere. When adding OS-dependent behaviour, implement it behind the `PlatformService` / `PlatformRuntime` ABCs.
- All modules use `logging.getLogger("archon")` — no `print()`.
- The whitelist check must happen in middleware before any handler runs — never inside handlers.
- New truncation strategies only require adding a class in `ai/` — no changes to gateway or chat.
- `stop_all()` must complete within 5 seconds.
- Always use KISS as the first principle (apply to code implementation, not to required functionality)
- You MUST use SOLID and Clean Code principles, but still keep it simple, do NOT overcomplicate.
- **CRITICAL**: You must NEVER make assumptions. All statements must be based on verified facts.
- **KISS principle** - Simplicity is mandatory
- Increase complexity step-by-step; use best practices when they simplify rather than complicate
- All tests always MUST be passed.
- **SDK rule**: Always use `claude-agent-sdk` (`ClaudeSDKClient`) for all LLM calls — including background tasks like history compaction. Never use `anthropic.AsyncAnthropic()` or the Anthropic Messages API directly. Tests must mock the SDK (`connect/query/receive_response/disconnect`), not `client.messages.create`.
- **Commit message fixes**: When asked to fix a commit message, always inspect the diff (`git show <hash>`) first and derive the correct message from the actual changes — never ask the user what the message should be.
