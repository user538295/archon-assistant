# Release Notes

## v26.4.919

**Search: LanceDB write-conflict fix, CPU throttling, sync reliability**

- Fixed LanceDB write conflicts during sync: search service is now stopped before sync and restarted after
- Extended auto-start poll timeout from 10s to 30s to accommodate slower startup environments
- CPU throttle for search server: `taskpolicy -b` on macOS, `nice` + `CPUQuota` on Linux
- Background sync task exceptions now logged at ERROR level (previously silent)
- Skip `set_trigger` on restart when collections are already indexed (prevents redundant re-indexing)
- Uninstall now correctly stops the search service before removing files
- Extended test coverage for stop/restart behavior in `archon sync` CLI

---

## v26.4.907

**Bug fixes: claude-opus-4-6 context window + installer works from any directory**

- Restored `claude-opus-4-6` context window to 1,000,000 tokens (regressed to 200,000 in v26.4.899 release formatting pass); fixed `update_models.py` so the API sync no longer overwrites this known-correct value
- Installer now always downloads from GitHub using the embedded version tag unless `--local` is explicitly passed — previously, running from any git repo (e.g. an unrelated project) would clone that repo instead of archon

---

## v26.4.899

**FIX-028: Router silent failure fix + classifier thinking events + model context windows**

- Fixed `asyncio.timeout()` silent drop bug across pipeline, router, and voice handler — replaced with rolling-deadline `wait_for()` pattern that never swallows timeouts
- Router timeout increased from 60s to 180s; timeout now surfaces as user-visible error instead of silent drop
- Classifier extended thinking disabled to prevent reasoning bleed into classification output
- Classifier `ThinkingResult` events now yielded in debug mode
- `AVAILABLE_MODELS` changed from list to `dict[str, int]` (model → context window); `MODEL_CONTEXT_WINDOWS` removed
- `archon doctor` now checks for context window mismatches between config and constants
- `release.sh` documented in CLAUDE.md for future reference

---

## v26.4.864

**Installer: reinstall search/voice deps on update + throttle launchd restarts**
- `--update` now reinstalls search and voice dependencies after pulling the latest release, preventing stale venv state from breaking these optional subsystems
- launchd restart calls are throttled to avoid rapid-fire service bounces during install

**release.sh dry-run improvements**
- Dry-run now warns instead of failing on a dirty working tree, missing RELEASE.md entry, and missing `GITHUB_TOKEN`, allowing inspection runs without a clean repo or credentials

**Release process: RELEASE.md calculation timing**
- Documentation updated: calculate the target version only after the full test suite passes, not before, to avoid version drift from fix commits

**Release pipeline fix: constants.py and config.toml.example kept in sync**
- `update_models.py` now also updates `examples/config.toml.example` when syncing `AVAILABLE_MODELS` from the Anthropic API, so the two files remain consistent after every release
- `release.sh` stages `config.toml.example` alongside `constants.py` when models are updated
- Fixes recurring regression where `AVAILABLE_MODELS` was overwritten with the full API list while `config.toml.example` retained only the curated 3-model list, causing `test_default_config_contains_models_section` to fail

---

## v26.4.858

**Search service installer fix**
- `pre_activate_cleanup()` now stops and removes the legacy `com.archon.rag` / `archon-rag` service before registering `com.archon.search`, preventing `EADDRINUSE` on port 8282 when upgrading from a pre-rename install

**Model list cleanup (re-sync)**
- `AVAILABLE_MODELS` in `constants.py` re-aligned with `config.toml.example` curated list after API overwrite during previous release (`["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]`)

---

## v26.4.853

**Unified Installer UX (FEAT-028)**
- Extracted shared `Console` class into `archon/cli/console.py` — single source of truth for styled terminal output across all installer paths
- `VoiceInstaller` migrated from raw `print()` to `Console`; added `check_torch()` to detect PyTorch presence before installation and show accurate download size message (~2 GB only when torch is absent)
- `VoiceInstaller` header line removed; enable hint (`archon voice enable`) shown only in interactive mode
- `SearchInstaller` migrated from raw `print()` to `Console`; `console` parameter injected via constructor (assigned before `load_config()` for safe error reporting)
- Root `install.py` `_offer_voice_setup()` and `_offer_search_setup()` cleaned up: removed redundant pre-install info lines now emitted by the installer classes themselves; voice success message corrected to "Voice configured. Start or restart Archon: archon restart"
- Jargon fixed: "STT model" → "Speech-to-text model (Whisper)"; ffmpeg step notes "(needed for audio decoding)"

**Model list cleanup**
- `AVAILABLE_MODELS` in `constants.py` trimmed to current Claude 4 family: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`

---

## v26.4.802

**macOS Documents folder permission prompt during install (FIX)**
- Added `_request_documents_permission()` to `install.py` that runs a harmless `os.listdir(~/Documents)` via `uv run python` after every install/update on macOS
- This forces the TCC consent dialog to appear while the user is at the terminal, instead of silently failing when the background daemon accesses the folder later
- Root cause: `uv sync --upgrade` may download a new Python interpreter, changing its binary path in uv's cache; macOS TCC ties grants to the specific path, so the grant is lost on every install or update

---

## v26.4.801

**RAG → Search Rename (Refactor)**
- All internal `rag` symbols renamed to `search`: modules, classes, config keys, CLI commands, MCP tools, and tests
- `archon/rag/` → `archon/search/`, `rag_cmd.py` → `search_cmd.py`, `[rag]` config section → `[search]`
- MCP tools renamed: `rag_status` → `search_status`, `rag_start` → `search_start`, etc.
- Backward-incompatible: `config.toml` must use `[search]` section going forward
- Documentation updated: `180_rag_architecture.md` → `180_search_architecture.md`, `09_rag_history_format.md` → `09_search_history_format.md`, `rag_guide.md` → `search_guide.md`; all Architecture, Backlog, UserManual, CLAUDE.md, and README.md updated to Search terminology

**Search Background Indexing with Progress Tracking (FEAT-027)**
- `IndexingStateStore` tracks per-collection indexing state atomically across async tasks
- Per-collection `asyncio.Lock` prevents concurrent re-ingestion of the same collection
- `archon search status` and `search_status` MCP tool show live progress (docs indexed, chunk count, percentage)
- Pinned collections are ingested first; partial readiness shown for in-progress collections
- ETA calculation (`compute_eta_seconds`) surfaced in CLI and MCP status for in-progress collections
- `archon doctor` detects `IN_PROGRESS` and `PENDING` collection states with appropriate health signals

**Search Watch Mode (FEAT-027-P8)**
- `CollectionWatcher` monitors collection directories with debounced `watchdog` events
- `WatcherManager` integrates into the search server lifecycle — starts/stops watchers with the server
- `watch = true` field in `[search]` collection config enables automatic re-sync on file changes
- `archon search status` shows a watching indicator for active watchers

---

## v26.4.709

**Voice Setup via Installer**
- `archon voice` CLI command installs voice dependencies (Whisper, ffmpeg, Edge TTS) interactively
- `VoiceInstaller` handles STT/TTS dependency checks, installation, and model configuration
- `_offer_voice_setup()` prompts during `archon install` to optionally enable voice at install time
- New `voice` optional-dependency group in `pyproject.toml` isolates heavy audio packages
- MCP tools: `voice_status`, `voice_enable`, `voice_disable` registered in `ArchonToolkit`

**Installer Fixes**
- `uv sync --upgrade` used during `archon update` to ensure dependencies are upgraded, not just synced
- TOML comments and key order preserved when `install.py --update` rewrites `config.toml` (switched from `tomli_w` to `tomlkit`)
- `config.toml.example` defaults synced with Python dataclass defaults

---

## v26.3.683

**Per-Model Context Window Configuration**
- `context_windows` config section allows per-model context window overrides for accurate usage tracking
- AVAILABLE_MODELS now synced from Anthropic API in release process, automatically restored after updates
- Context percentage calculation fixed for accurate context utilization reporting

**RAG Installer Progress**
- Progress dots and step labels [1/5]–[5/5] in RAG installer for better visibility into setup stages

---

## v26.3.664

**RAG Setup & Installation**
- Interactive RAG setup offer during `archon install` — users choose GPU type and embedding model upfront
- RAG CLI commands now work without `TELEGRAM_BOT_TOKEN` set (e.g., pre-install setup)
- Fixed Python path handling in RAG dependency installation

---

## v26.3.660

**RAG Control Plane (MCP Tools)**
- Full RAG lifecycle tools: `rag_status`, `rag_start`, `rag_stop`, `rag_ingest`, `rag_sync`
- Collection management: `rag_collection_list`, `rag_collection_add`, `rag_collection_remove`, `rag_collection_info`, `rag_collection_reindex`
- New `archon_doctor` MCP tool enables health checks from Claude sessions
- Image OCR support via docling — images now indexed alongside text documents

**Schedule History & Config**
- Optional job run history logging per schedule config (`history_enabled`)
- `archon config get` now supports full-dump with empty path argument
- `get_version` and `get_logs` MCP tools for session-level diagnostics

**Defaults**
- Auto-compaction threshold lowered to 80% (from 85%) for better responsiveness
- Sync timeout default set to 0 to prevent install hangs

---

## v26.3.618

**RAG Auto-Start & Health Monitoring**
- RAG service auto-starts on first user message if configured but offline
- `archon doctor` now reports RAG service health and fails with exit code 1 if RAG is required but down
- Post-install success message includes RAG discovery guidance

---

## v26.3.608

**RAG — Intelligent Multi-Collection Routing**
- Centroid pre-computation during ingestion for efficient collection routing
- Haiku-generated collection descriptions for semantic understanding
- Multi-collection router with cosine-similarity ranking (configurable shortlist & confidence threshold)
- Parallel multi-collection retrieval orchestrator ranks and searches collections simultaneously

**RAG Administration**
- `archon rag collection info` shows collection metadata (doc count, chunk count, centroid, description)
- `archon rag collection reindex` regenerates embeddings after model changes
- `--dry-run` flag for safe preview of `archon rag collection remove`
- RAG health checks in `archon doctor` (staleness, model mismatch, pinned vs routed collections)

**UX**
- RAG injection shown in verbose/debug mode with 🔍 format indicator
- Reinstall now allows Enter to keep existing Telegram user IDs

---

## v26.3.582

**RAG — Foundation & Multi-Collection Setup**
- Complete RAG system built on LanceDB + fastembed with async support
- Multi-format document parsing (PDF, Markdown, Word, HTML, code)
- Recursive document chunking via Chonkie for semantic coherence
- New `archon rag` CLI subcommand with full collection lifecycle management

**RAG Service Architecture**
- Auto-starting RAG background service (launchd on macOS, systemd on Linux)
- GPU type detection (Apple Silicon, NVIDIA, CPU fallback) for optimal model selection
- Validation-first setup flow prevents misconfigured deployments

**RAG Collection Management**
- Collection sync engine with manifest tracking for reproducible embeddings
- `archon rag collection` commands: `list`, `add`, `remove`
- `archon rag sync` auto-syncs collections defined in config at startup
- Deprecation notification for legacy `history_collection` config

---

## v26.3.497

**Context & Skill Injection Visibility**
- New `ContextInjectedEvent` and `SkillInjectedEvent` — visible in verbose/debug mode showing what was injected into each session
- `/context` command improved with three-way no-session logic (never started / evicted / active)

**Custom Commands**
- New `/command` executor for running Claude Code custom slash commands directly from Telegram

**Config & Automation**
- Replaced hardcoded defaults with `config.toml.example` template for easier setup and maintenance
- `suppressed_events` config to exclude specific event types from session history files
- Auto-compaction enabled by default at 85% context threshold

**Fixes & Polish**
- Agent task descriptions show as bulleted summaries in Telegram notifications for multi-agent plans
- Reminder injection notifications are now quiet by default (visible only in verbose/debug mode)
- Fix: task first-line description limit raised from 60 to 100 characters

---

## v26.3.451

### Archon Control Plane — MCP Tools for Daemon Management
Claude (and background agents) can now manage the Archon daemon directly through 17 MCP tools, replacing unsafe patterns like shell commands that previously risked self-termination.

**Status & introspection**
- `archon_status()` — uptime, active sessions, running agents, current model, restart state
- `get_session_status()` — per-user session health
- `get_context_stats()` — token usage, cost, turn count

**Daemon control**
- `archon_restart()` — schedule a restart with a 2–60 s delay; `RestartCoordinator` prevents ghost-restarts on cancel

**Agent management**
- `list_running_agents()` (with optional name filter), `get_agent_status()`, `get_agent_by_name()`, `cancel_agent()`, `read_agent_log()`

**Configuration**
- `get_config()`, `set_config()`, `get_job_config()` — read/modify `config.toml` values by dot-notation path; secrets auto-redacted

**Notifications & scheduling**
- `send_notification()`, `set_notification_mode()`, `list_scheduled_tasks()`, `add_scheduled_task()`, `update_scheduled_task()`, `remove_scheduled_task()`

**Model & skills**
- `get_model()`, `set_model()`, `list_skills()`

### Background Agents — Access to Archon Toolkit
Background agents now connect to a curated subset of Archon MCP tools (`archon_status`, `list_running_agents`, `get_config`, `get_job_config`, `send_notification`) via a dedicated MCP server on port 18184, enabling agents to query daemon state and send notifications without shell commands. The router MCP server is restricted to read-only history tools to prevent prompt-injection escalation.

### File Transfer Tools
Two new MCP tools let agents deliver files to users via Telegram:
- `list_attachments(date, limit, mime_pattern)` — list stored attachments with pagination
- `send_file(path, caption)` — transmit a file with path-containment validation, symlink resolution, 50 MB size cap, and 10 s per-user rate limiting

### Router Event Streaming
All routing-session events are now streamed to Telegram and session history in real time, so users can see Claude's step-by-step routing decisions:
- `[Router] Tool: <name>` and `[Router] Result: <summary>` shown in verbose/debug mode
- Router thinking shown in debug mode only
- `🎯 Routing decision` recorded in history (never sent to Telegram)
- Quiet/normal modes suppress router events entirely

### Notification Mode Refinements
- **Normal**: now shows full thinking + final response (high-value context without tool clutter)
- **Verbose**: now shows tool names only, no args or results
- **Debug**: unchanged — full tool I/O trace
- Reminder injection notifications are shown only in verbose/debug mode

### Auto-Compaction on Context Pressure
When context usage exceeds the configurable `auto_compact_threshold` (20–100 %), Archon automatically compacts today's history via Haiku, then tears down and recreates the session — no manual `/clear` needed. Set to `0` to disable. A notification is sent in verbose/debug mode and always recorded in session history.

---

## v26.3.383

### File Attachment Support
Full multimedia support: send any file type from Telegram and Claude processes it directly.

- **Documents** — any file type; downloads, saves to date-based storage, and builds a structured prompt for Claude. Files over 20 MB are rejected before download.
- **Images** — photos and images-as-documents with automatic resizing (>5 MB or >8000 px compressed to 1568 px long edge), EXIF orientation correction, and animated GIF detection.
- **Media groups (albums)** — multiple images/documents sent together are accumulated and presented to Claude as a combined prompt with captions.
- **Video** — regular videos and video notes (round messages).
- **Stickers** — static (WebP), animated (TGS), and video (WebM) stickers.
- **Archives** — ZIP, TAR, and similar archive formats.
- **Audio** — audio files when voice transcription is disabled.

Attachments are stored with TTL-based cleanup (configurable via `attachments_cleanup_hours`). `/status` now shows the attachments directory path and disk usage.

---

## v26.3.368

### Startup Broadcast Notification
Archon now sends a Telegram notification to all whitelisted users every time the daemon starts — including after crashes, launchd/systemd restarts, or external scripts. Includes crash-loop protection (30 s threshold) and deduplication with the `/restart` acknowledgment.

### CLI Help Flags
`archon -h` and `archon --help` now work alongside the existing `archon help` subcommand.

---

## v26.3.364

### Auto-Reload Scheduled Jobs
The job scheduler polls for file changes on every 60-second tick. Adding, removing, or editing a job file takes effect automatically without restarting Archon.

---

## v26.3.362

### Project-Scoped Skills Installation
Skills bundled in the app's `skills/` directory are installed to `~/.archon/workspace/.claude/skills/` during setup, making them available only to Archon sessions without polluting the user's global `~/.claude/skills/`.

---

## v26.3.356

### Reminder and History Compaction Enabled by Default
Both context-reminder injection and daily history compaction are now on by default for new installations — no manual config changes needed.

---

## v26.3.345

### Directory-Based Job Bundles
Scheduled jobs can now be organized as `schedules/<name>/job.toml` directories alongside companion scripts, instead of flat `.toml` files.

---

## v26.3.339

### Session Timeout Recovery
When a task stalls, Archon now automatically promotes it to a background agent or retries instead of hanging indefinitely.

### Cross-Platform Service Management
A Platform Strategy pattern isolates all OS-specific service code (macOS launchd, Linux systemd, Windows stubs) behind clean ABCs, ensuring consistent behaviour across platforms.

---

## v26.3.330

### Short Model Aliases
`/model sonnet`, `/model opus`, and `/model haiku` now work as aliases for the full model IDs in the `/models` inline keyboard and `archon config set`.

### Model Configuration in config.toml
A new `[models]` section in `config.toml` declares the available model list and default model, replacing hardcoded values.

---

## v26.3.321

### Cron Jobs Installed from App Bundle
Scheduled jobs bundled in `app/cron.d/` are now installed automatically during `uv run install.py`, so jobs like the health summary are active immediately after setup.

---

## v26.3.311

### Archon CLI Management Tool
A new `archon` command-line tool provides full service lifecycle management:
- `archon start | stop | restart | status` — service lifecycle
- `archon logs [--follow] [--lines N] [--date YYYY-MM-DD]` — log viewer
- `archon update [--tag <version>]` — pull latest release and restart
- `archon doctor` — 9-point pre-flight health check
- `archon config show | edit | get <key> | set <key> <value>` — config inspection and editing
- `archon version` — display current version

### Inline Routing with Background-Agent Safety Net
Small and trivial tasks run inline for lower latency. Tasks that exceed a tool-call threshold are automatically promoted to background agents mid-execution, with a notification sent when promotion occurs. Fallback events are emitted when the router times out or returns a parse error.

### Orchestration Session Redesign
The orchestration layer was fully redesigned across five implementation waves, improving agent isolation, history accuracy, and recovery behaviour.

### Context Reminders
A `ContextReminder` injects the `REMINDER.md` file periodically into sessions to prevent context drift. Injection triggers on either a message count threshold or a token count threshold (whichever is reached first), configurable via `[reminder] interval_messages` and `interval_tokens`.

### Workspace Templates
`install.py` now copies `AGENTS.md` and `REMINDER.md` workspace templates to `~/.archon/workspace/` on install and update.

---

## v26.3.198 — Initial Release

### Telegram ↔ Claude Bridge
Local daemon that forwards every Telegram message to Claude Code via the Claude Agent SDK and streams all state transitions back to Telegram in real time.

### Real-Time Streaming Notifications
Six event types sent to Telegram as they occur: thinking, tool start, tool result, response, error, and sub-agent lifecycle (started/stopped).

### Four Notification Modes
`quiet` / `normal` / `verbose` / `debug` — switchable live via `/notify` with an inline keyboard. Beacon messages fire on an interval in quiet mode to confirm Claude is still working.

### Multi-Agent Orchestration
- A Classifier (Haiku) categorizes each request by intent and scope
- A Decomposer breaks large-scope tasks into dependency-graph plans
- Background agents execute plan waves in parallel with per-agent logging
- Human-readable names assigned from a 30-name pool

### Background Agent Execution
Agents run as asyncio tasks with their own SDK sessions, per-agent progress beacons, and dedicated log files at `~/.archon/history/sessions/YYYY-MM-DD-HH-MM-<name>.md`.

### Voice Messages
- Speech-to-text via Whisper (configurable model and language)
- Text-to-speech replies via OpenAI TTS or Edge TTS (free fallback)
- Configurable trigger: `always` / `inbound` / `off`

### Skills Integration
Loads skills from `~/.claude/skills/*/SKILL.md` (YAML frontmatter) and exposes them via `/skills` and `/skill <name>` commands.

### Plugins Integration
Loads Claude Code plugins from `~/.claude/plugins/` and injects their SDK configurations and skills into every session.

### Agents Integration
Loads agent definitions from `~/.claude/agents/*.md`; agents with the `-archon` suffix are injected into Archon sessions automatically.

### Daily History Compaction
Compacts previous days' session logs into summarized `-compacted.md` digests via Haiku, keeping history context usable over time.

### Chat History Persistence
Full conversation history written to `~/.archon/history/sessions/YYYY-MM-DD.md` in structured Markdown format.

### `/context` Usage Tracking
Displays current session token usage, estimated cost, and turn count.

### Telegram Markdown Rendering
Claude's Markdown responses are converted to Telegram HTML (bold, italic, code blocks, links).

### Commands
`/start`, `/status`, `/context`, `/stop`, `/clear`, `/restart`, `/notify`, `/skills`, `/skill`, `/models`, `/model`, `/agents`, `/tasks`, `/scheduled`

### Installation
- One-command install via `uv run install.py`
- macOS launchd and Linux systemd service registration
- Config written to `~/.archon/config.toml`; secrets in `.env`

### Access Control
Telegram user ID whitelist enforced in middleware before any handler runs.
