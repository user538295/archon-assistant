# Archon


> **Claude Code in your pocket.** Send a message from Telegram. Watch your AI agent think, work, and deliver results — live, as it happens.

![Archon logo](https://github.com/user538295/archon-assistant/blob/main/archon_banner.jpg?raw=true)

Archon is a local daemon that bridges **Telegram** with **Claude Code** via the Claude Agent SDK. Every state transition — thinking, tool calls, responses — streams to your phone in real time.

```
You (Telegram) → Archon → Claude Agent SDK → back to you, step by step
```

No web UI. No polling. No waiting for a final answer. Just a persistent agent running on your machine, reachable from anywhere.

---

## Why it's different

Most Claude integrations give you a chat box. Archon gives you a **running agent**.

- Watch Claude **think** before it acts — every `<thinking>` block shows up in Telegram
- See every **tool call** fire as it happens: `🔧 Tool: bash` → `📤 Result: ...`
- Spawn **background agents** that work in parallel while you keep chatting
- Schedule **jobs** that chain shell scripts with Claude prompts and notify you on completion
- Switch models, manage sessions, cancel agents — all from Telegram commands

---

## Features

**Real-time streaming**
Every Claude state change is a Telegram message. Thinking, tool calls, results — nothing is batched or hidden.

**Multi-agent pipeline**
A fast Classifier (Haiku) routes each message as `chat` or `task`. The Decomposer (your chosen model) handles it with context. Intent-aware from the first token.

**Background agents**
Claude calls `spawn_background_agent` via MCP. Your main session stays interactive. Background agents run in isolated asyncio tasks and notify you on completion. `/tasks` shows live status with cancel buttons.

**Job scheduler**
Job bundles in `schedules/` — each job is a directory (`name/job.toml`) that can include supporting scripts and data. Chain shell scripts and Claude prompts. Timezone-aware. Results delivered via Telegram.

**Skills & agents**
Drop Markdown files into `~/.claude/skills/` or `~/.claude/agents/`. Skills activate per-message via `/skill <name>`. Agents with `-archon` suffix inject automatically into every session.

**File attachments**
Send documents, photos, videos, stickers, audio, or archives directly in Telegram. Files are saved to the workspace and Claude receives a structured prompt with metadata. Albums (media groups) are batched automatically.

**Notification modes**
`quiet` / `normal` / `verbose` / `debug` — switch live from Telegram. Normal shows thinking. Verbose adds tool names. Debug shows full tool I/O.

**Context window tracking**
`/context` shows tokens, cost, turn count, and a progress bar. `/status` shows session health.

**Daemon-ready**
Ships with launchd (macOS) and systemd (Linux) service files. Auto-starts on login, restarts on crash.

---

## Quick Start

```bash
uv run https://raw.githubusercontent.com/user538295/archon-assistant/v26.3.383/install.py
```

No clone needed. The installer checks prerequisites, prompts for your bot token + Telegram user ID, writes config, and registers the daemon. Done.

**Prerequisites:**
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — authenticated, in `PATH`
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

---

## Configuration

Two files: `.env` for secrets, `config.toml` for everything else. Start from `examples/config.toml.example`.

**`.env`**
```env
TELEGRAM_BOT_TOKEN=your_token_here
```

**`config.toml` essentials**
```toml
[access]
allowed_user_ids = [123456789]   # your Telegram user ID (@userinfobot)

[session]
working_directory = "~/.archon/workspace"
inactivity_timeout_seconds = 1800

[notifications]
mode = "normal"   # quiet | normal | verbose | debug

[models]
available = ["claude-sonnet-4-6", "claude-haiku-4-5"]  # opus accessible via /models alias
default = "claude-sonnet-4-6"

[background_agents]
spawn_rule = "auto"   # eager | auto | manual
max_parallel = 5

[history]
auto_compact_threshold = 80   # auto-compact + clear session at 80% context (0 = disabled, min 20)
```

### Scheduled jobs

Each job is a directory bundle in `schedules/` containing a `job.toml` and optional supporting files:

```toml
# schedules/daily-summary/job.toml
cron = "0 8 * * *"
notify_user_id = 123456789
timeout_seconds = 60

[pipeline]
health_check_tool = "scripts/health_check.sh"
summarize_prompt = "Summarise these results in 2-3 bullet points: {health_check_tool}"
```

Each key in `[pipeline]` ends with `_tool` (shell command) or `_prompt` (Claude prompt). Steps run top-to-bottom; use `{step_name}` to substitute an earlier step's output. Flat files (`name.toml`) are deprecated; see the [User Manual](Documentation/UserManual/user_manual.md#migrating-from-flat-files) for migration steps.

---

## Notification visibility

| Event | quiet | normal | verbose | debug |
|---|:---:|:---:|:---:|:---:|
| ✅ Response | ✅ | ✅ | ✅ | ✅ |
| ❌ Error | ✅ | ✅ | ✅ | ✅ |
| 🤖 Agent started/stopped | ✅ | ✅ | ✅ | ✅ |
| 📋 Plan | ✅ | ✅ | ✅ | ✅ |
| ⚠️ Promotion | ✅ | ✅ | ✅ | ✅ |
| ⚠️ Fallback notice | ✅ | ✅ | ✅ | ✅ |
| 🔄 Recovery | ✅ | ✅ | ✅ | ✅ |
| 💭 Thinking | ❌ | ✅ | ✅ | ✅ |
| 🔧 Tool name | ❌ | ❌ | ✅ | ✅ |
| 📤 Tool result (brief) | ❌ | ❌ | ✅ | ❌ |
| 🔧 Tool name + args | ❌ | ❌ | ❌ | ✅ |
| 📤 Tool result (full) | ❌ | ❌ | ❌ | ✅ |
| 🏷 Classification | ❌ | ❌ | ✅ | ✅ |
| 🔀 Routing | ❌ | ❌ | ✅ | ✅ |
| 🔔 Reminder injected | ✅ | ✅ | ✅ | ✅ |

---

## Bot commands

```
/status     — session info, uptime, processing state
/context    — token usage, cost, turn count, progress bar
/models     — switch model (inline keyboard)
/clear      — fresh session
/restart    — hot-reload daemon without losing config
/skills     — list loaded skills
/skill <n>  — activate skill for next message
/tasks      — live background agent status + cancel buttons
/scheduled  — scheduled job list + last-run status
/notify     — tap-to-switch notification mode panel
/agents     — list available agents
/quiet [N]  — quiet mode, optional beacon every N minutes
/verbose    — verbose mode
/debug      — debug mode
```

---

## Architecture

```mermaid
flowchart TD
    TG[Telegram] --> GW[Gateway]
    GW --> SM[SessionManager]
    SM --> PL["Pipeline (per user)"]
    PL --> CLF["Classifier (Haiku)"]
    PL --> DEC["Decomposer (user model)"]
    CLF --> SDK[Claude Agent SDK]
    DEC --> SDK
    SM --> EM[EventMapper]
    EM --> TS[TruncationStrategy]
    DEC -.->|spawn_background_agent MCP| AMCP[ArchonMCPServer]
    AMCP --> BAM[BackgroundAgentManager]
    BAM --> CSI["ClaudeSession (isolated)"]
    CSI --> SDK
    GW --> JSCHED[JobScheduler]
    JSCHED --> CSC["ClaudeSession (per step)"]
    CSC --> SDK
    GW --> HM[HistoryManager]
    GW --> AL[AgentLogger]
```

Three layers wired by a single asyncio event loop:

| Component | Role |
|---|---|
| `Pipeline` | Classifier (Haiku) → Decomposer routing; duck-types as `ClaudeSession` |
| `ClaudeSession` | Wraps `ClaudeSDKClient`; async generator of typed event dataclasses |
| `EventMapper` | SDK messages → typed event dataclasses (thinking, tools, responses, classification, agent lifecycle, plan events) |
| `SessionManager` | Per-user pipeline registry; inactivity eviction, model switching, diagnostics |
| `BackgroundAgentManager` | Fire-and-forget agent tasks; enforces `max_parallel`; delivers results |
| `ArchonMCPServer` | aiohttp MCP JSON-RPC 2.0 server for `spawn_background_agent` |
| `JobScheduler` | asyncio scheduled job loop with `croniter`; timezone-aware |
| `FileHandler` | Document/photo/video/sticker/audio handlers; delegates to `handle_message` with prompt override |
| `MediaGroupCollector` | Batches Telegram albums by `media_group_id` with 1s timeout |
| `AttachmentStore` | Date-based file storage with sanitization, collision handling, TTL cleanup |
| `Gateway` | Single event loop; `stop_all()` in ≤5 seconds |

---

## Service management

```bash
# Install / uninstall (macOS launchd + Linux systemd)
uv run install.py             # install + start
uv run install.py --uninstall # stop + remove

# Tail logs
tail -f ~/.archon/logs/archon.log
```

---

## Development

```bash
uv run pytest                                    # all tests
uv run pytest tests/ai/test_event_mapper.py      # single file
uv run pytest -k "test_split_strategy_labels"    # single test
uv run mypy archon/                              # type check
uv run pytest -m live --no-cov -v               # live tests (needs real credentials)
```

Tests are TDD with ≥85% coverage. No external dependencies required for the default test suite.

```
archon/
├── ai/          # Pipeline, sessions, event mapping, agents, scheduling, skills
├── chat/        # aiogram bot, command handlers, whitelist middleware
├── cli/         # CLI entry point, service management, doctor, logs, update
├── config/      # Typed config loader (toml + .env)
├── gateway/     # Orchestrator, event routing, graceful shutdown
└── platform/    # Cross-platform service + runtime (launchd, systemd, Windows stubs)
```

---

## License

MIT
