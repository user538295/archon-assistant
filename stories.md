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
- Run tests and fix them.

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

## Epic 5: Integration & E2E Tests

### S5.1 — AI pipeline integration test
**As a** developer,
**I want** an integration test that drives `OutputParser` with a scripted fake PTY process,
**so that** I can verify the full AI parsing pipeline without mocking individual methods.

**Acceptance criteria:**
- A `FakePtySession` helper emits a pre-recorded sequence of Claude-like raw output (ANSI/prefix patterns covering all six event types)
- `OutputParser.parse(fake_stream)` is awaited and the emitted event sequence matches expected types and content
- Tests cover: `ThinkingStarted`, `ThinkingResult`, `ToolStarted`, `ToolResult`, `Response`, `ErrorEvent` — at least one of each in a single run
- `SplitStrategy` truncation is applied to a content-bearing event to confirm the full AI-layer chain works
- No mocking of internal methods — only the PTY process boundary is substituted

---

### S5.2 — Chat + AI integration test
**As a** developer,
**I want** an integration test that wires whitelist middleware, message handler, `SessionManager`, and a mock `PtySession`,
**so that** I can verify the full Telegram→AI pathway without a live bot connection.

**Acceptance criteria:**
- Build an aiogram `Dispatcher` with `WhitelistMiddleware` and the message handler registered
- Use aiogram's test utilities to inject a fake `Message` from a whitelisted user ID
- The handler calls `SessionManager.get_or_create(user_id).send(text)` on a mock `PtySession`
- A non-whitelisted user ID is dropped — no session is created or called
- Tests: whitelisted message reaches session, non-whitelisted message is silently dropped

---

### S5.3 — Full message flow e2e test
**As a** developer,
**I want** an end-to-end test that runs the full gateway with only external boundaries mocked,
**so that** I can verify that a Telegram message produces the correct sequence of formatted Telegram replies.

**Boundaries mocked:**
- Telegram API: replaced with an in-process aiogram `Bot` stub that records `send_message` calls
- Claude process: replaced with a scripted fake PTY that emits a known event sequence

**Acceptance criteria:**
- `Gateway.start()` is called in a test loop with mocked bot and scripted PTY
- One simulated Telegram message is injected
- The bot stub records exactly the expected Telegram messages in order:
  1. `💭 Thinking...`
  2. `💭 Thought:\n<content>`
  3. `🔧 Tool: <name>`
  4. `📤 Result:\n<content>`
  5. `✅ Response:\n<content>`
- Long content is split by `SplitStrategy` and multiple messages are recorded
- Log entries for the run are present in the log file

---

### S5.4 — Graceful shutdown e2e test
**As a** developer,
**I want** an end-to-end test that sends `SIGINT` to a running gateway and verifies a clean shutdown,
**so that** I can confirm no PTY zombie processes are left after the daemon stops.

**Acceptance criteria:**
- Gateway starts with at least one active mock `PtySession`
- `SIGINT` is sent to the running event loop
- `SessionManager.stop_all()` is called and all sessions reach `is_alive == False`
- Telegram bot polling is disconnected
- Shutdown completes within 5 seconds
- Log messages "shutdown initiated" and "shutdown complete" are both present

---

### S5.7 — Live unit test: config loader
**As a** developer,
**I want** a live unit test that exercises the config loader with real temporary files,
**so that** I can verify file I/O paths work on the actual filesystem without any mocking.

**Prerequisites:**
- Test is marked `@pytest.mark.live`; no external services required

**Acceptance criteria:**
- Test creates real temporary `.env` and `config.toml` files using `tmp_path`
- `load_config()` reads them successfully and returns correctly typed values
- Deleting the `.env` file and calling `load_config()` raises `ConfigError` with a real file-not-found path in the message
- Deleting `config.toml` raises `ConfigError` likewise
- No mocks, no patching of `open` or `os` — pure real file system calls

**Placed after:** S0.2

---

### S5.8 — Live unit test: PtySession
**As a** developer,
**I want** a live unit test that spawns a real subprocess via `PtySession` (not `claude`),
**so that** I can verify PTY I/O mechanics against a real process without requiring the claude binary.

**Prerequisites:**
- `/bin/bash` available (standard on macOS/Linux)
- Test is marked `@pytest.mark.live`

**Acceptance criteria:**
- `PtySession.start()` is called with `["/bin/bash", "-c", "cat"]` (a long-running echo process)
- `send("hello\n")` writes to the PTY; `read_stream()` yields a chunk containing `"hello"` within 5 seconds
- `stop()` terminates the process; `is_alive` returns `False` immediately after
- Calling `stop()` a second time is a no-op (no exception raised)
- No mocks — only the process command is substituted

**Placed after:** S1.1

---

### S5.9 — Live unit test: SessionManager
**As a** developer,
**I want** a live unit test for `SessionManager` that uses real `PtySession` instances backed by a bash process,
**so that** I can verify lifecycle management (creation, reuse, timeout, teardown) against real processes.

**Prerequisites:**
- `/bin/bash` available
- Test is marked `@pytest.mark.live`
- `SessionManager` is configured to use the bash command instead of `claude` (via constructor param or test config)

**Acceptance criteria:**
- `get_or_create(user_id)` starts a real `PtySession`; calling again returns the same instance
- `stop(user_id)` terminates the session; `is_alive` returns `False`; subsequent `get_or_create` creates a new session
- `stop_all()` with two active sessions terminates both; registry is empty afterwards
- Inactivity timeout: set to 1 second in test config; verify session is evicted after 2 seconds of inactivity
- No mocks — real processes throughout

**Placed after:** S1.4

---

### S5.5 — Live PTY pipeline test
**As a** developer,
**I want** a live test that spawns the real `claude` binary and runs a trivial prompt through the full AI pipeline,
**so that** I can verify that `PtySession` + `OutputParser` work against the actual process.

**Prerequisites:**
- `claude` binary present in `PATH`
- Test is marked `@pytest.mark.live` and skipped automatically if `which claude` fails

**Acceptance criteria:**
- `PtySession.start()` spawns the real `claude --dangerously-skip-permissions` process
- A trivial prompt (e.g. `"Say: OK"`) is sent via `PtySession.send()`
- At least one `Response` event with non-empty content is received within a 30-second timeout
- `PtySession.stop()` terminates the process cleanly; `is_alive` returns `False` afterwards
- The test is idempotent — repeated runs produce the same pass/fail result
- No internal mocks of any kind

---

### S5.6 — Live full-stack e2e test
**As a** developer,
**I want** a live test that runs the full gateway against the real Telegram API and real Claude process,
**so that** I can confirm the entire pipeline works in a production-identical environment.

**Prerequisites:**
- `TELEGRAM_BOT_TOKEN` set in `.env`
- `TELEGRAM_LIVE_CHAT_ID` set in `.env` (ID of a pre-configured test chat the bot can write to)
- `claude` binary present in `PATH`
- Test is marked `@pytest.mark.live` and `@pytest.mark.requires_telegram`; skipped if any prerequisite is missing

**Acceptance criteria:**
- `Gateway.start()` is called; the real bot connects to Telegram polling
- The test directly calls `SessionManager.get_or_create(TELEGRAM_LIVE_CHAT_ID).send(prompt)` to inject a prompt
- The real Telegram bot sends formatted event messages to `TELEGRAM_LIVE_CHAT_ID` via the live API
- Test asserts at least one `✅ Response:` message is delivered (verified via `Bot.get_updates()` or a short polling loop)
- Shutdown is triggered after the response arrives; all sessions stop cleanly
- Total test timeout: 60 seconds

---

## Implementation Order

```
S0.1 → S0.2 → S5.7 → S4.1 → S1.1 → S5.8 → S1.2 → S1.3 → S5.1 → S5.5 → S1.4 → S5.9
                                                                                      ↓
                                              S2.1 → S2.2 → S2.3 → S2.4 → S5.2 → S3.1 → S5.3 → S3.2 → S5.4 → S4.2 → S5.6
```

**Key:**
- S5.7 (live config unit test) immediately follows S0.2 — same component, real files
- S5.8 (live PtySession unit test) immediately follows S1.1 — real bash process, no claude
- S5.1 (AI pipeline integration) + S5.5 (live PTY+claude) follow S1.3 — need parser and truncation
- S5.9 (live SessionManager unit test) follows S1.4 — real PtySession lifecycle
- S5.2 (chat+AI integration) follows S2.4 — all chat components present
- S5.3 (full e2e mocked) follows S3.1 — gateway wired
- S5.4 (shutdown e2e) follows S3.2 — shutdown implemented
- S5.6 (live full-stack) follows S4.2 — service install complete

> S4.1 (logging) is done early so all subsequent stories use it from the start.
> Tests are woven into the implementation flow — each test story immediately follows the story that satisfies its dependencies.
> S5.7–S5.9 are live unit tests: single component, real filesystem/processes, no mocks, no credentials needed.
> S5.1–S5.4 are integration/e2e tests with mocks/stubs at external boundaries.
> S5.5–S5.6 are live tests requiring real `claude` binary and/or Telegram credentials.
> Live tests (`@pytest.mark.live`) are excluded from `uv run pytest`; run with `uv run pytest -m live`.
