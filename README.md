# Archon Assistant

A local daemon that bridges **Telegram** with **Claude Code** via the Claude Agent SDK — streaming every state transition (thinking, tool calls, responses) as real-time Telegram notifications.

Send a message from your phone. Watch Claude work. Get every step delivered as it happens.

```
You (Telegram) ──▶ Archon ──▶ Claude Agent SDK ──▶ claude CLI
      ▲                │
      └────────────────┘  (💭 Thinking... / 🔧 Tool: / ✅ Response:)
```

---

## Features

- **Real-time streaming** — every Claude state change arrives as a Telegram message the moment it happens
- **Typing indicator** — live "typing…" indicator in Telegram while Claude is working
- **Per-user sessions** — one persistent Claude session per whitelisted Telegram user, with full conversation context
- **Native command menu** — all commands registered with Telegram via `setMyCommands`; type `/` or tap the 📋 menu button to browse and select any command
- **Notification modes** — quiet / normal / verbose / debug with optional beacon in quiet mode
- **Cron scheduler** — run automated jobs on a schedule; chain bash scripts and Claude prompts; get results via Telegram notification
- **Per-job TOML files** — each cron job lives in `cron.d/<name>.toml`; filename becomes the job name
- **Pluggable truncation** — long outputs chunked as `[1/N]` pages (more strategies extensible via ABC)
- **Skills & plugins** — inject skill prompts or load plugin bundles from `~/.claude/`
- **Whitelist access control** — only listed Telegram user IDs can interact; all others are silently ignored
- **Graceful shutdown** — SIGTERM/SIGINT stops all sessions cleanly within 5 seconds
- **Hot-reload** — `/restart` replaces the daemon process without losing config
- **Daemon-ready** — ships with a launchd plist (macOS) and systemd unit (Linux) for auto-start on login
- **800+ tests, 97%+ coverage** — full TDD, mypy strict

---

## Requirements

| Dependency | Version | Notes |
|---|---|---|
| Python | 3.12+ | |
| [uv](https://docs.astral.sh/uv/) | any | package manager & runner |
| [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) | latest | `claude` must be in `PATH` and authenticated |
| Telegram bot token | — | create via [@BotFather](https://t.me/BotFather) |

---

## Quick Start

```bash
git clone https://github.com/user538295/archon-assistant.git
cd archon-assistant
bash install.sh
```

The installer will:
1. Verify prerequisites (`uv`, Python 3.12+, `claude` CLI)
2. Prompt for your bot token, Telegram user ID, and working directory
3. Write `.env` and `config.toml`
4. Install dependencies via `uv sync`
5. Register and start the daemon (launchd on macOS, systemd on Linux)

**Prerequisites:** [uv](https://docs.astral.sh/uv/), Python 3.12+, [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) authenticated and in `PATH`, a Telegram bot token from [@BotFather](https://t.me/BotFather).

---

## Configuration

### `.env`

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Bot token from @BotFather |

### `config.toml`

```toml
[access]
# Telegram user IDs allowed to send messages to the bot.
# Find yours by messaging @userinfobot on Telegram.
allowed_user_ids = [123456789]

[session]
# Directory Claude Code will use as its working directory.
working_directory = "~/.archon/workspace"
# Seconds of inactivity before a session is automatically closed.
inactivity_timeout_seconds = 1800

[output]
# Maximum characters per Telegram message (Telegram hard limit: 4096).
max_message_length = 4000
# "split" — send all chunks as [1/N], [2/N], ...
truncation_strategy = "split"

[notifications]
# quiet | normal | verbose | debug
mode = "normal"
# Minutes between beacon messages in quiet mode (0 = no beacon)
interval_minutes = 2

[logging]
log_file = "~/.archon/archon.log"
log_level = "INFO"   # DEBUG for verbose output

[cron]
# Set to true to enable the cron scheduler.
enabled = false
# Directory containing one TOML file per job (relative to this config file).
jobs_dir = "cron.d"
```

### Cron jobs

Place one `.toml` file per job inside `cron.d/` (relative to `config.toml`). The filename stem becomes the job name.

```toml
# cron.d/daily-summary.toml
schedule = "0 8 * * *"          # daily at 08:00
notify_user_id = 123456789      # Telegram user ID to notify
timeout_seconds = 60

[[pipeline]]
tool = "git log --oneline --since='24 hours ago'"

[[pipeline]]
prompt = "Summarise these commits in 2-3 bullet points: {input}"
```

Steps chain automatically — the stdout of each `tool` step feeds `{input}` in the next `prompt` step. See `cron.d/echo-test.toml` for a minimal example.

---

## Output Events

Every Claude state change produces an immediate notification. Content-bearing events are truncated and split if needed.

| Event | Telegram message |
|---|---|
| Thinking started | `💭 Thinking...` |
| Thinking result | `💭 Thought:` + content |
| Tool call started | `🔧 Tool: <name>` + input summary |
| Tool result | `📤 Result:` + content |
| Final response | `✅ Response:` + content |
| Error | `❌ Error: <message>` |

---

## Bot Commands

All commands are registered with Telegram's native command menu — type `/` or tap the 📋 button to browse them interactively.

| Command | Description |
|---|---|
| `/start` | Confirm the bot is running |
| `/status` | Show active session info and uptime |
| `/stop` | Terminate the current Claude session |
| `/clear` | Stop current session and immediately start a fresh one |
| `/restart` | Gracefully stop all sessions and hot-reload the daemon |
| `/context` | Show context window usage (tokens, cost, turns) |
| `/model` | Show or switch the Claude model |
| `/skills` | List available Claude Code skills |
| `/skill <name>` | Activate a skill for the next message |
| `/agents` | List configured agent types |
| `/jobs` | List cron jobs and their last-run status |
| `/quiet [N]` | Switch to quiet mode; optional beacon every N minutes |
| `/normal` | Switch to normal mode |
| `/verbose` | Switch to verbose mode |
| `/debug` | Switch to debug mode |
| `/notify` | Tap-to-switch notification panel |
| `/settings` | Same as `/notify` |

---

## Service Installation

### macOS (launchd — auto-starts on login)

```bash
make install      # install and load the launchd service
make uninstall    # unload and remove
make logs         # tail ~/.archon/archon.log
```

The plist is installed to `~/Library/LaunchAgents/com.archon.assistant.plist`. The service will restart automatically if it crashes (`KeepAlive = true`).

### Linux (systemd user service)

```bash
make install-linux    # copy unit file + systemctl enable --user archon
make uninstall-linux  # systemctl disable --user + remove unit file
```

The unit file is installed to `~/.config/systemd/user/archon.service`. Restarts on failure (`Restart=on-failure`).

---

## Development

```bash
# Run all tests (live tests excluded)
uv run pytest

# Run a specific test file
uv run pytest tests/ai/test_event_mapper.py

# Run a specific test
uv run pytest -k "test_split_strategy_labels"

# Type check (mypy strict)
uv run mypy archon/

# Live tests — require real credentials (TELEGRAM_BOT_TOKEN, claude binary)
uv run pytest -m live --no-cov -v
```

### Test markers

| Marker | Meaning |
|---|---|
| *(no marker)* | Pure unit / integration tests, no external dependencies, run by default |
| `@pytest.mark.live` | Requires real external resources (filesystem, claude binary, Telegram API) |
| `@pytest.mark.requires_telegram` | Requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_LIVE_CHAT_ID` in env |

### Project structure

```
archon/
├── ai/             # ClaudeSession, EventMapper, SessionManager, CronScheduler, TruncationStrategy
├── chat/           # aiogram bot, whitelist middleware, message handler, commands
├── config/         # .env + config.toml loader → typed Config singleton
├── gateway/        # Orchestrator — wires everything, handles graceful shutdown
└── log_setup.py    # Rotating file handler

cron.d/                          # Per-job cron TOML files (filename = job name)
├── echo-test.toml               # Minimal example (disabled by default)
└── health-summary.toml          # Script + Claude prompt pipeline example

docs/
├── high_level_concept.md        # Architecture & design decisions
├── prd.md                       # Product requirements document
├── stories.md                   # User stories with acceptance criteria
├── tasks.md                     # Implementation task checklist
└── USER_MANUAL.md               # End-user guide

scripts/
├── com.archon.assistant.plist   # macOS launchd template
└── archon.service               # Linux systemd template

tests/                           # 800+ tests, 97%+ coverage
```

### Architecture

```
Telegram ──▶ Gateway ──▶ SessionManager ──▶ ClaudeSession (per user)
   ▲               │             │
   └───────────────┘             └──▶ EventMapper ──▶ TruncationStrategy
```

- **`ClaudeSession`** — wraps `ClaudeSDKClient`; `send(prompt)` is an async generator yielding typed event dataclasses
- **`EventMapper`** — translates raw SDK messages (`AssistantMessage`, `ResultMessage`, …) into `ThinkingStarted`, `ToolStarted`, `Response`, etc.
- **`SessionManager`** — per-user session registry with inactivity eviction and a per-user asyncio lock to prevent double-start races
- **`TruncationStrategy`** — ABC; add a new class in `archon/ai/` to get a new strategy — no gateway or chat changes needed
- **`Gateway`** — single asyncio event loop; `stop_all()` completes within 5 seconds

---

## License

MIT
