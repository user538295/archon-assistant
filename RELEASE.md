# Release Notes

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
Full conversation history written to `~/.archon/history/sessions/YYYY-MM-DD.md` in QMD-compatible Markdown format.

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
