# Archon Assistant — Product Requirements Document

## 1. Goal

Deliver a local daemon that bridges Telegram with Claude Code, allowing a whitelisted user to send natural language instructions via Telegram and receive real-time, structured output as Claude works — exactly as if they were watching the terminal.

---

## 2. Users & Access

| Actor | Description |
|---|---|
| Owner / operator | The person running the daemon on their machine |
| Whitelisted user | Telegram user IDs listed in config; the only people who can interact with the bot |

**Phase 1 scope:** single operator, small whitelist (typically just the owner).

---

## 3. Core Features (Phase 1) ✅ ALL DONE

### 3.1 Chat Integration (Telegram) ✅ DONE
- ✅ Telegram bot powered by **aiogram 3.x**
- ✅ Incoming messages from whitelisted users are forwarded to the AI layer
- ✅ Non-whitelisted users receive a silent ignore (whitelist middleware on both `Message` and `CallbackQuery`)
- ✅ Supports bot commands: `/start`, `/status`, `/stop`, `/clear`, `/restart`
- ✅ Native Telegram command menu via `setMyCommands` (`BotCommandScopeDefault` + `BotCommandScopeAllPrivateChats`)

### 3.2 AI Integration (Claude Code via Claude Agent SDK) ✅ DONE
- ✅ Claude Code is controlled via **`claude-agent-sdk`** (`ClaudeSDKClient`), the official Python SDK
- ✅ `ClaudeSession` wraps the SDK client; connects on first message and maintains context across turns
- ✅ One persistent `ClaudeSession` per whitelisted Telegram user
- ✅ Sessions are recycled on `/stop` command or configurable inactivity timeout
- ✅ SDK is configured with `permission_mode="bypassPermissions"` to avoid interactive prompts
- ✅ `EnterPlanMode`, `ExitPlanMode`, and `Task` tools are always disabled (Task prevents blocking sub-agents)

### 3.3 Output Streaming (Logical Boundaries) ✅ DONE
Every state transition generates an **immediate** Telegram notification.

| Event | Telegram prefix | When sent |
|---|---|---|
| Thinking started | 💭 **Thinking...** | Immediately when a thinking block begins |
| Thinking result | 💭 **Thought:** | When thinking block ends |
| Tool started | 🔧 **Tool [N]:** `<name>` | Immediately when Claude begins a tool call |
| Tool result | 📤 **[N]:** brief summary | When tool execution completes |
| Final response | ✅ **Response:** | When Claude's text response is complete |
| Error | ❌ **Error** | On crash, timeout, or session failure |
| Agent started | 🤖 **Agent Name started** | Always, regardless of notification mode |
| Agent done | 🤖 **Agent Name done** | Always, regardless of notification mode |

Long outputs (> 4000 chars) are handled by a pluggable **TruncationStrategy**:

| Strategy | Behavior | Status |
|---|---|---|
| `split` | Chunk output into ≤4000-char pages, send all sequentially as `[1/N]`, `[2/N]`… | ✅ Implemented |
| `head_tail` | Keep first N + last M chars, insert `…[X lines omitted]…` | Future |

### 3.4 Gateway (Orchestrator) ✅ DONE
- ✅ Starts and connects the chat and AI layers on daemon boot
- ✅ Routes messages bidirectionally: Telegram → Claude, Claude output → Telegram
- ✅ Handles session lifecycle (create, reuse, destroy)
- ✅ Graceful shutdown on SIGTERM/SIGINT (5-second timeout)

### 3.5 Daemon (Local Service) ✅ DONE
- ✅ Runs as a **launchd** service on macOS (`make install/uninstall/logs`)
- ✅ systemd unit file for Linux (`make install-linux/uninstall-linux`)
- ✅ Daily-rotating log files (`archon.YYYY-MM-DD.log`)

---

## 4. Architecture ✅ DONE

```
archon/
├── chat/           # Telegram bot: message routing, whitelist, command handlers
├── ai/             # ClaudeSession (SDK), EventMapper, TruncationStrategy, SessionManager,
│                   # SkillLoader, PluginLoader, AgentLoader, HistoryManager, AgentLogger,
│                   # BackgroundAgentManager, ArchonMCPServer, CronScheduler
├── gateway/        # Orchestrator: connects chat ↔ AI, event loop
├── config/         # Config loader (.env + config.toml)
└── main.py         # Entry point
```

**Tech stack:**
- Python 3.12+ managed with **uv**
- Telegram: `aiogram 3.x`
- Claude Code: `claude-agent-sdk`
- HTTP MCP server: `aiohttp>=3.9`
- Cron expressions: `croniter>=6.0.0`
- Config: `.env` (secrets) + `config.toml` (structured config, `tomlkit` for write-back, atomic writes + automatic backup)
- Daemon: launchd plist / systemd unit

---

## 5. Configuration ✅ DONE

**`.env`** — secrets only:
```
TELEGRAM_BOT_TOKEN=...
```

**`config.toml`** — structured config (full reference in `examples/config.toml.example`):
```toml
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "/Users/you/projects/myproject"
inactivity_timeout_seconds = 1800

[output]
max_message_length = 4000
truncation_strategy = "split"

[notifications]
mode = "normal"          # quiet | normal | verbose | debug
interval_minutes = 2     # quiet-mode beacon interval; 0 = no beacon

[notifications.agents]
# mode = "quiet"         # per-agent lifecycle level; omit to inherit

[history]
enabled = true
directory = "~/.archon/history"

[models]
available = ["claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5"]
# default = "claude-sonnet-4-5"

[plugins]
enabled = true

[qmd]
enabled = false
host = "localhost"
port = 8181
history_collection = "archon-history"

[cron]
enabled = false
jobs_dir = "cron.d"

[background_agents]
spawn_rule = "auto"
max_parallel = 5
host = "localhost"
port = 18182
beacon_interval_minutes = 2
```

---

## 6. Non-Functional Requirements

| Requirement | Target | Status |
|---|---|---|
| Latency (first chunk to Telegram) | < 2 seconds after Claude starts outputting | ✅ |
| Reliability | Auto-reconnect Telegram bot on network drop | ✅ (aiogram polling) |
| Security | Whitelist enforced before any message reaches Claude | ✅ |
| Logging | Daily-rotating file log, INFO level by default, DEBUG configurable | ✅ |
| Test coverage | ≥ 85% (TDD) | ✅ 97%+ |
| Type safety | mypy strict | ✅ |

---

## 7. Phase 2 Features ✅ ALL DONE

### 7.1 Notification Mode Redesign ✅ DONE
- ✅ Four named modes: `quiet` / `normal` / `verbose` / `debug`
- ✅ Quiet beacon: periodic `⏳ Working…` while Claude processes (configurable interval)
- ✅ Inline keyboard panel (`/notify`, `/settings`) for tap-to-switch
- ✅ Quick-switch commands: `/quiet [N]`, `/normal`, `/verbose`, `/debug`
- ✅ Config auto-saved on every mode change; persisted across restarts

### 7.2 Chat History Persistence ✅ DONE
- ✅ All conversation turns persisted to daily `~/.archon/history/YYYY-MM-DD.md`
- ✅ QMD-compatible Markdown format with H2/H3 structure and timestamps
- ✅ Contextual Retrieval: user question included as blockquote in Response entries
- ✅ `HistoryConfig` (`enabled`, `directory`) in `config.toml`

### 7.3 Skills Integration ✅ DONE
- ✅ `SkillLoader` reads `~/.claude/skills/*/SKILL.md` with YAML frontmatter
- ✅ Compact skill registry injected into every new `ClaudeSession` via system prompt
- ✅ One-shot skill activation via `/skill <name>`: skill body prepended to next message
- ✅ `/skills` lists personal + plugin-bundled skills

### 7.4 Model Management ✅ DONE
- ✅ `ModelsConfig` (`available` list, `default`) in `config/loader.py`
- ✅ `/model` command with inline keyboard; tap to switch model
- ✅ Model change stops the current session (next message starts fresh with new model)

### 7.5 Plugin Support ✅ DONE
- ✅ `PluginLoader` reads `~/.claude/plugins/installed_plugins.json` + `~/.claude/settings.json`
- ✅ Enabled plugins injected into each `ClaudeSession` via `ClaudeAgentOptions.plugins`
- ✅ Plugin-bundled skills surfaced in `/skills`
- ✅ `PluginsConfig` with `enabled` flag

### 7.6 Context Window Tracking ✅ DONE
- ✅ `ClaudeSession._intercept()` captures `ResultMessage` metadata per turn
- ✅ `usage_stats` property: token counts, accumulated cost, turn count, last duration
- ✅ `/context` command: Unicode progress bar, per-category token counts, cost

### 7.7 Sub-agent Team Configuration ✅ DONE
- ✅ `AgentsConfig` + `AgentDefinitionConfig` dataclasses; parsed from `[agents]` in `config.toml`
- ✅ `SubagentStarted` / `SubagentStopped` event types in `EventMapper`
- ✅ Agent lifecycle events always delivered regardless of notification mode

### 7.8 Per-agent Notification Configuration ✅ DONE
- ✅ `NotificationsAgentsConfig(mode: str | None)` — `None` = inherit from orchestrator
- ✅ `[notifications.agents]` subsection in `config.toml`

### 7.9 Filesystem Agent Loader ✅ DONE
- ✅ `AgentLoader` reads `~/.claude/agents/*.md` with YAML frontmatter
- ✅ `is_archon` property: agents with `-archon` suffix are injected into sessions
- ✅ `/agents` command lists archon agents (🤖) and TUI-only agents (🔍)

### 7.10 Session Observability & Diagnostics ✅ DONE
- ✅ `ClaudeSession` tracks `_processing`, `_last_send_at`, `_last_response_at`, `_send_count`, `_event_log` (deque maxlen=200)
- ✅ `is_processing`, `processing_seconds`, `idle_seconds`, `send_count`, `is_stuck()`, `recent_events()`, `diagnostics` properties
- ✅ Stuck-session monitor: notifies user when session processing > 120 s
- ✅ `/status` enhanced: shows `🔄 Processing for Xs` / `💤 Idle for Xs` / message count

### 7.11 Background Agent Execution ✅ DONE
- ✅ `BackgroundAgentManager`: spawns fire-and-forget asyncio tasks
- ✅ `AgentRun` dataclass; human-readable names from 30-name pool (no two concurrent agents share a name)
- ✅ `ArchonMCPServer`: aiohttp HTTP MCP JSON-RPC 2.0 server exposing `spawn_background_agent`
- ✅ `spawn_background_agent` tool available in every main Claude session
- ✅ `Task` tool always disabled; main conversation never blocks on sub-agent
- ✅ `inject_context()`: completed agent results prepended to next main session message
- ✅ Per-agent working beacon: spawn notification edited in-place with live tool/thinking counts
- ✅ `/running_agents` command with inline `[Cancel Name]` buttons
- ✅ Agent events written to per-agent log files via `AgentLogger`, not sent to Telegram

### 7.12 Cron Scheduler ✅ DONE
- ✅ `CronScheduler` asyncio loop using `croniter`; ticks every 60 s
- ✅ Pipeline: `tool` steps (bash) + `prompt` steps (Claude) chain via `{input}` template
- ✅ Per-step timeout; Telegram notification on completion or failure
- ✅ Per-job timezone support (IANA timezone names via `zoneinfo`)
- ✅ `/jobs` command shows status, last run, last result, next run
- ✅ Job config hot-reload on `/jobs` command

### 7.13 QMD Semantic Search Integration ✅ DONE
- ✅ `QmdConfig` (`enabled`, `host`, `port`, `history_collection`)
- ✅ Gateway auto-starts QMD MCP daemon if not already running (localhost only)
- ✅ QMD MCP endpoint injected into every `ClaudeSession` via `mcp_servers`
- ✅ One-time setup script: `scripts/qmd_installer.sh`

### 7.14 Agent Log Files (FR.003) ✅ DONE
- ✅ `AgentLogger` writes per-agent Markdown logs to `~/.archon/history/YYYY-MM-DD-HH-MM-{name}.md`
- ✅ Events written continuously (not batched); file readable even if process is interrupted
- ✅ Main chat stream not polluted with background agent events

### 7.15 Security & Privacy Fixes ✅ DONE
- ✅ Chat message content never logged (only `(N chars)` logged on receipt)
- ✅ Error handler logs exception type only, not message content
- ✅ Whitelist middleware covers both `Message` and `CallbackQuery`

### 7.16 Config File Resilience ✅ DONE
- ✅ Atomic writes: `save_notifications_config` uses write-to-temp-then-rename (`_atomic_write`) so a SIGTERM/SIGKILL during a config save can never corrupt `config.toml`
- ✅ Automatic backup: `load_config` creates `config.toml.bak` on every successful parse
- ✅ Auto-recovery: if `config.toml` is corrupt on startup, `load_config` automatically restores from `config.toml.bak` and logs a warning
- ✅ If no backup exists and the file is corrupt, a clear `ConfigError` is raised

---

## 8. Success Criteria

- ✅ Send a message in Telegram → Claude Code receives and processes it
- ✅ All output events (tool calls, thinking, response) arrive in Telegram in real-time with correct labels
- ✅ Session persists across multiple messages (conversational context maintained)
- ✅ Daemon survives machine restart (launchd auto-restart)
- ✅ Only whitelisted Telegram users can interact with the bot
- ✅ `/stop` cleanly kills the active Claude session
- ✅ Background agents run concurrently without blocking the main conversation
- ✅ Conversation history persisted to daily Markdown files
- ✅ Skills, plugins, and agents auto-loaded from `~/.claude/`

---

## 9. Out of Scope (Phase 1)

- Multi-AI support (GPT, Gemini, etc.)
- File/image upload to Claude via Telegram
- Web dashboard or API
- Cloud deployment
- Context window auto-compaction (FR.005 — planned, not yet implemented)
