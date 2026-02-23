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
- **Per-user sessions** — one persistent Claude session per whitelisted Telegram user, with full conversation context
- **Pluggable truncation** — long outputs chunked as `[1/N]` pages (more strategies extensible via ABC)
- **Whitelist access control** — only listed Telegram user IDs can interact; all others are silently ignored
- **Graceful shutdown** — SIGTERM/SIGINT stops all sessions cleanly within 5 seconds
- **Daemon-ready** — ships with a launchd plist (macOS) and systemd unit (Linux) for auto-start on login
- **99%+ test coverage** — full TDD, mypy strict, 181 tests

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
# 1. Clone and install dependencies
git clone https://github.com/user538295/archon-assistant.git
cd archon-assistant
uv sync

# 2. Configure secrets
cp .env.example .env
# Edit .env — set TELEGRAM_BOT_TOKEN

# 3. Configure the daemon
cp config.toml.example config.toml
# Edit config.toml — set allowed_user_ids, working_directory

# 4. Run
uv run python main.py
```

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
working_directory = "/Users/you/projects/myproject"
# Seconds of inactivity before a session is automatically closed.
inactivity_timeout_seconds = 1800

[output]
# Maximum characters per Telegram message (Telegram hard limit: 4096).
max_message_length = 4000
# "split" — send all chunks as [1/N], [2/N], ...
truncation_strategy = "split"

[logging]
log_file = "~/.archon/archon.log"
log_level = "INFO"   # DEBUG for verbose output
```

---

## Output Events

Every Claude state change produces an immediate notification. Content-bearing events are truncated and split if needed.

| Event | Telegram message |
|---|---|
| Thinking started | `💭 Thinking...` |
| Thinking result | `💭 Thought:` + content |
| Tool call started | `🔧 Tool: <name>` |
| Tool result | `📤 Result:` + content |
| Final response | `✅ Response:` + content |
| Error | `❌ Error: <message>` |

---

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Confirm the bot is running |
| `/status` | Show active session info |
| `/stop` | Terminate the current Claude session |

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
├── ai/             # ClaudeSession, EventMapper, SessionManager, TruncationStrategy
├── chat/           # aiogram bot, whitelist middleware, message handler, commands
├── config/         # .env + config.toml loader → typed Config singleton
├── gateway/        # Orchestrator — wires everything, handles graceful shutdown
└── log_setup.py    # Rotating file handler

scripts/
├── com.archon.assistant.plist   # macOS launchd template
└── archon.service               # Linux systemd template

tests/                           # 181 tests, 99%+ coverage
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
