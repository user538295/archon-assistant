# Archon Assistant — User Stories

Stories are grouped by epic and ordered for implementation. Each story is independently testable.

---

## Epic 0: Project Setup

### S0.1 — Initialize project structure
**As a** developer,
**I want** a properly initialized Python 3.12 project with uv and the correct folder structure,
**so that** all subsequent stories have a consistent foundation to build on.

**Acceptance criteria:**
- `uv init` with Python 3.12+ constraint in `pyproject.toml`
- Directory structure: `archon/chat/`, `archon/ai/`, `archon/gateway/`, `archon/config/`
- Each module has an `__init__.py`
- `.env.example` with `TELEGRAM_BOT_TOKEN=`
- `config.toml.example` with all supported keys and comments
- `.gitignore` excludes `.env`, `*.log`, `__pycache__`
- `README.md` with quickstart (install, configure, run)
- `pytest` configured and a passing smoke test exists

---

### S0.2 — Config loader
**As a** developer,
**I want** a typed config object loaded from `.env` and `config.toml` at startup,
**so that** all modules can access configuration without reading files themselves.

**Acceptance criteria:**
- Loads `TELEGRAM_BOT_TOKEN` from `.env`
- Loads all `config.toml` keys into a typed dataclass/Pydantic model
- Raises a clear `ConfigError` on startup if required fields are missing
- Config is a singleton, importable as `from archon.config import config`
- Tests: missing token raises error, missing config file raises error, valid config loads correctly

---

## Epic 1: AI Module

### S1.1 — PTY session (raw)
**As a** developer,
**I want** to spawn `claude --dangerously-skip-permissions` in a PTY and send/receive raw text,
**so that** I have a low-level foundation for the output parser.

**Acceptance criteria:**
- `PtySession.start()` spawns the claude process in a PTY
- `PtySession.send(text)` writes a line to the PTY input
- `PtySession.read_stream()` is an async generator yielding raw output chunks as they arrive
- `PtySession.stop()` terminates the process cleanly (SIGTERM → SIGKILL fallback)
- `PtySession.is_alive` reflects process state
- Tests: mock PTY process, verify send/receive, verify stop

---

### S1.2 — Output parser
**As a** developer,
**I want** the raw PTY output stream parsed into typed events,
**so that** the gateway can forward structured notifications to Telegram.

**Accepted events (emitted as dataclasses):**
- `ThinkingStarted`
- `ThinkingResult(content: str)`
- `ToolStarted(name: str)`
- `ToolResult(content: str)`
- `Response(content: str)`
- `ErrorEvent(message: str)`

**Acceptance criteria:**
- `OutputParser.parse(stream)` is an async generator of the above events
- Parsing is based on Claude Code PTY output patterns (ANSI sequences, known prefixes)
- Unknown/unrecognized output is buffered and emitted as `Response` on flush
- Tests: given sample PTY output strings → verify correct event sequence emitted

---

### S1.3 — Truncation strategy
**As a** developer,
**I want** a pluggable truncation strategy applied to long event content before sending,
**so that** Telegram's 4096-char limit is never exceeded and the strategy is swappable.

**Acceptance criteria:**
- `TruncationStrategy` ABC with `apply(text: str, max_len: int) -> list[str]` method
- `SplitStrategy`: splits text into chunks ≤ `max_len`, labels as `[1/N] ...`, `[2/N] ...`
- Strategy is selected at startup from `config.toml` (`output.truncation_strategy`)
- Tests: single chunk (no split needed), multi-chunk split, label format correct

---

### S1.4 — Session manager
**As a** developer,
**I want** per-user PTY sessions created, reused, and cleaned up automatically,
**so that** conversation context is maintained per Telegram user without resource leaks.

**Acceptance criteria:**
- `SessionManager.get_or_create(user_id)` returns existing or new `PtySession`
- Sessions are keyed by Telegram `user_id`
- Inactivity timeout (from config) triggers `session.stop()` and removes from registry
- `SessionManager.stop(user_id)` explicitly destroys a session
- `SessionManager.stop_all()` destroys all sessions (used at shutdown)
- Tests: session reuse, timeout eviction, explicit stop, stop_all

---

## Epic 2: Chat Module

### S2.1 — Telegram bot bootstrap
**As a** user,
**I want** the bot to start and respond to a `/start` command,
**so that** I can confirm the bot is running and connected.

**Acceptance criteria:**
- aiogram 3.x `Application` starts with token from config
- `/start` replies with a greeting message
- Bot reconnects automatically on network drop (aiogram default polling handles this)
- Tests: mock bot token, verify `/start` handler fires

---

### S2.2 — Whitelist middleware
**As an** operator,
**I want** messages from non-whitelisted users to be silently dropped,
**so that** only authorized users can interact with Claude.

**Acceptance criteria:**
- Middleware checks `message.from_user.id` against `config.access.allowed_user_ids`
- Non-whitelisted messages are dropped with no response
- Whitelisted messages pass through to handlers
- Tests: whitelisted user ID passes, non-whitelisted user ID is dropped

---

### S2.3 — Message handler + event formatter
**As a** whitelisted user,
**I want** my Telegram messages forwarded to Claude and each output event sent back as a formatted message,
**so that** I can follow along with Claude's work in real-time.

**Acceptance criteria:**
- Incoming text message is sent to `SessionManager.get_or_create(user_id).send(text)`
- Each event from the output parser is formatted and sent as a Telegram message:
  - `ThinkingStarted` → `💭 Thinking...`
  - `ThinkingResult` → `💭 Thought:\n<content>` (truncation applied)
  - `ToolStarted` → `🔧 Tool: <name>`
  - `ToolResult` → `📤 Result:\n<content>` (truncation applied)
  - `Response` → `✅ Response:\n<content>` (truncation applied)
  - `ErrorEvent` → `❌ Error: <message>`
- Tests: mock session, verify each event type produces the correct Telegram message format

---

### S2.4 — Bot commands
**As a** whitelisted user,
**I want** `/status` and `/stop` commands,
**so that** I can check session state and terminate Claude when needed.

**Acceptance criteria:**
- `/status` replies with: session active/inactive, working directory, uptime
- `/stop` calls `SessionManager.stop(user_id)` and replies with confirmation
- `/stop` when no session is active replies with "No active session"
- Tests: each command with active session, `/stop` with no session

---

## Epic 3: Gateway

### S3.1 — Gateway core
**As a** developer,
**I want** a gateway that wires the Telegram bot and session manager together in a single asyncio event loop,
**so that** the app runs as a cohesive whole from `main.py`.

**Acceptance criteria:**
- `Gateway.start()` initializes config, starts the Telegram bot, and starts the session manager
- Telegram message events are routed to the correct user session
- Session output events are routed back to the correct Telegram chat
- `main.py` calls `Gateway.start()` and blocks until shutdown
- Tests: integration test — send mock Telegram message, verify mock session receives it and response is sent back

---

### S3.2 — Graceful shutdown
**As an** operator,
**I want** the daemon to shut down cleanly on SIGTERM or SIGINT,
**so that** no PTY sessions are left as zombie processes.

**Acceptance criteria:**
- SIGTERM/SIGINT triggers `SessionManager.stop_all()` then Telegram bot disconnect
- Shutdown completes within 5 seconds
- Log message emitted on shutdown start and completion
- Tests: send SIGINT to process, verify all sessions stopped

---

## Epic 4: Daemon

### S4.1 — Logging
**As an** operator,
**I want** structured rotating log files,
**so that** I can debug issues without the log growing unbounded.

**Acceptance criteria:**
- Rotating file handler: max 10 MB per file, keep 5 backups
- Log file path configurable in `config.toml` (default: `~/.archon/archon.log`)
- Log level configurable (`INFO` default, `DEBUG` via config)
- All modules use the same logger (`logging.getLogger("archon")`)
- Tests: verify log file created, verify level filtering

---

### S4.2 — launchd service (macOS)
**As an** operator,
**I want** a `make install` command that installs Archon as a launchd service,
**so that** the daemon starts automatically on login without manual intervention.

**Acceptance criteria:**
- `scripts/com.archon.assistant.plist` template with correct paths
- `make install` copies plist to `~/Library/LaunchAgents/` and runs `launchctl load`
- `make uninstall` unloads and removes the plist
- `make logs` tails the log file
- Plist uses `KeepAlive = true` for auto-restart on crash

---

### S4.3 — systemd service (Linux) *(bonus)*
**As an** operator on Linux,
**I want** a systemd unit file,
**so that** the daemon auto-starts on boot.

**Acceptance criteria:**
- `scripts/archon.service` unit file with `Restart=on-failure`
- `make install-linux` copies unit and runs `systemctl enable --user archon`
- `make uninstall-linux` disables and removes

---

## Implementation Order

```
S0.1 → S0.2 → S4.1 → S1.1 → S1.2 → S1.3 → S1.4
                                               ↓
                          S2.1 → S2.2 → S2.3 → S2.4 → S3.1 → S3.2 → S4.2
```

> S4.1 (logging) is done early so all subsequent stories use it from the start.
