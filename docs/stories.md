# Archon Assistant — User Stories

Stories are grouped by epic and ordered for implementation. Each story is independently testable.

---

## Epic 0: Project Setup

### S0.1 ✅ — Initialize project structure
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

### S0.2 ✅ — Config loader
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

### S1.1 ✅ — Claude session (SDK)
**As a** developer,
**I want** to send prompts to Claude via the Claude Agent SDK and receive typed event dataclasses,
**so that** I have a clean AI layer foundation without any PTY management or ANSI parsing.

**Acceptance criteria:**
- `ClaudeSession` wraps `ClaudeSDKClient` from `claude-agent-sdk`
- `ClaudeSession.start()` connects the SDK client (`ClaudeSDKClient.connect()`)
- `ClaudeSession.send(prompt: str)` is an async generator yielding archon event dataclasses
- `ClaudeSession.stop()` disconnects the SDK client (`ClaudeSDKClient.disconnect()`)
- `ClaudeSession.is_alive` returns `True` after `start()` and `False` after `stop()`
- Sessions are created with `permission_mode="bypassPermissions"` and `cwd` from config
- Tests: mock `ClaudeSDKClient`, verify `start()` calls `connect()`, `send()` calls `query()` and yields mapped events, `stop()` calls `disconnect()`, double `stop()` is a no-op

---

### S1.2 ✅ — Event mapper
**As a** developer,
**I want** a mapper that translates Claude Agent SDK message objects into archon event dataclasses,
**so that** the rest of the system works with a stable, SDK-independent event API.

**SDK message → archon event mapping:**
- `AssistantMessage` with `ThinkingBlock` → `ThinkingStarted` + `ThinkingResult(thinking)`
- `AssistantMessage` with `ToolUseBlock` → `ToolStarted(name)`
- `UserMessage` with `ToolResultBlock` in content list → `ToolResult(content)`
- `ResultMessage(is_error=False, result=…)` → `Response(content=result)`
- `ResultMessage(is_error=True)` → `ErrorEvent(message=result or fallback)`
- `SystemMessage`, `TextBlock` in `AssistantMessage`, empty `ResultMessage.result` → no event

**Acceptance criteria:**
- Event dataclasses (`ThinkingStarted`, `ThinkingResult`, `ToolStarted`, `ToolResult`, `Response`, `ErrorEvent`) defined in `archon/ai/event_mapper.py`
- `EventMapper.map_messages(stream)` is an async generator of the above events
- Tests: given constructed SDK message objects → verify correct archon event sequence

---

### S1.3 ✅ — Truncation strategy
**As a** developer,
**I want** a pluggable truncation strategy applied to long event content before sending,
**so that** Telegram's 4096-char limit is never exceeded and the strategy is swappable.

**Acceptance criteria:**
- `TruncationStrategy` ABC with `apply(text: str, max_len: int) -> list[str]` method
- `SplitStrategy`: splits text into chunks ≤ `max_len`, labels as `[1/N] ...`, `[2/N] ...`
- Strategy is selected at startup from `config.toml` (`output.truncation_strategy`)
- Tests: single chunk (no split needed), multi-chunk split, label format correct

---

### S1.4 ✅ — Session manager
**As a** developer,
**I want** per-user `ClaudeSession` instances created, reused, and cleaned up automatically,
**so that** conversation context is maintained per Telegram user without resource leaks.

**Acceptance criteria:**
- `SessionManager.get_or_create(user_id)` returns existing or new `ClaudeSession` (calling `start()` on new sessions)
- Sessions are keyed by Telegram `user_id`
- Inactivity timeout (from config) triggers `session.stop()` and removes from registry
- `SessionManager.stop(user_id)` explicitly destroys a session
- `SessionManager.stop_all()` destroys all sessions (used at shutdown)
- Tests: session reuse, timeout eviction, explicit stop, stop_all

---

## Epic 2: Chat Module

### S2.1 ✅ — Telegram bot bootstrap
**As a** user,
**I want** the bot to start and respond to a `/start` command,
**so that** I can confirm the bot is running and connected.

**Acceptance criteria:**
- aiogram 3.x `Application` starts with token from config
- `/start` replies with a greeting message
- Bot reconnects automatically on network drop (aiogram default polling handles this)
- Tests: mock bot token, verify `/start` handler fires

---

### S2.2 ✅ — Whitelist middleware
**As an** operator,
**I want** messages from non-whitelisted users to be silently dropped,
**so that** only authorized users can interact with Claude.

**Acceptance criteria:**
- Middleware checks `message.from_user.id` against `config.access.allowed_user_ids`
- Non-whitelisted messages are dropped with no response
- Whitelisted messages pass through to handlers
- Tests: whitelisted user ID passes, non-whitelisted user ID is dropped

---

### S2.3 ✅ — Message handler + event formatter
**As a** whitelisted user,
**I want** my Telegram messages forwarded to Claude and each output event sent back as a formatted message,
**so that** I can follow along with Claude's work in real-time.

**Acceptance criteria:**
- Incoming text message triggers `async for event in session.send(text):` and each event is sent to Telegram
- Each event type is formatted correctly:
  - `ThinkingStarted` → `💭 Thinking...`
  - `ThinkingResult` → `💭 Thought:\n<content>` (truncation applied)
  - `ToolStarted` → `🔧 Tool: <name>`
  - `ToolResult` → `📤 Result:\n<content>` (truncation applied)
  - `Response` → `✅ Response:\n<content>` (truncation applied)
  - `ErrorEvent` → `❌ Error: <message>`
- Tests: mock session, verify each event type produces the correct Telegram message format

---

### S2.4 ✅ — Bot commands
**As a** whitelisted user,
**I want** `/status` and `/stop` commands,
**so that** I can check session state and terminate Claude when needed.

**Acceptance criteria:**
- `/status` replies with: session active/inactive, working directory, uptime
- `/stop` calls `SessionManager.stop(user_id)` and replies with confirmation
- `/stop` when no session is active replies with "No active session"
- Tests: each command with active session, `/stop` with no session

---

### S2.5 ✅ — Clear command
**As a** whitelisted user,
**I want** a `/clear` command that starts a fresh context window,
**so that** I can begin a new conversation with Claude without accumulated history, just like `/clear` in the Claude Code TUI.

**Acceptance criteria:**
- `/clear` calls `SessionManager.stop(user_id)` then `SessionManager.get_or_create(user_id)` to immediately start a fresh session
- Replies with `🧹 Context cleared. New session started.`
- Works whether or not a session was previously active (`stop` is a no-op when no session exists)
- New session is started eagerly so the next message has no cold-start delay
- Tests: `stop()` called with correct `user_id`, `get_or_create()` called, confirmation reply sent, works with no prior session; `clear_command` registered in dispatcher

---

### S2.6 ✅ — Telegram command menu
**As a** whitelisted user,
**I want** to see all available bot commands when I type `/` or tap the 📋 menu button in Telegram,
**so that** I can discover and invoke commands without memorizing them.

**Background:**
Telegram's native command menu is populated via the `setMyCommands` Bot API method. Commands are shown as an auto-suggestion overlay when the user types `/`, and via a persistent 📋 menu button next to the message input. Using `BotCommandScopeAllPrivateChats` restricts the menu to private chats, keeping it off group chat UIs if the bot is ever added to one.

**Acceptance criteria:**
- `BOT_COMMANDS: list[BotCommand]` defined in `archon/chat/bot.py` as single source of truth for all 8 command names and descriptions
- `setup_bot_commands(bot: Bot)` async function calls `bot.set_my_commands(commands=BOT_COMMANDS, scope=BotCommandScopeAllPrivateChats())`
- A startup hook `dp.startup.register(setup_bot_commands)` is registered in `Gateway._run()` so the menu is updated every time the daemon starts
- All 7 commands (`start`, `status`, `stop`, `clear`, `restart`, `notify`, `settings`) appear in the Telegram command menu with human-readable descriptions

---

## Epic 3: Gateway

### S3.1 ✅ — Gateway core
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

### S3.2 ✅ — Graceful shutdown
**As an** operator,
**I want** the daemon to shut down cleanly on SIGTERM or SIGINT,
**so that** no Claude SDK sessions are left open.

**Acceptance criteria:**
- SIGTERM/SIGINT triggers `SessionManager.stop_all()` then Telegram bot disconnect
- Shutdown completes within 5 seconds
- Log message emitted on shutdown start and completion
- Tests: send SIGINT to process, verify all sessions stopped

---

## Epic 4: Daemon

### S4.1 ✅ — Logging
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

### S4.4 ✅ — Daily log rotation
**As an** operator,
**I want** the log file to rotate every day,
**so that** each day's log is in its own file and old logs are easy to find by date.

**Behaviour:**
- At midnight the current `archon.log` is renamed `archon.YYYY-MM-DD.log` (yesterday's date) and a fresh `archon.log` starts
- On daemon startup, if an existing `archon.log` has an mtime from a previous day it is renamed immediately (handles crash/stop-before-midnight)
- All daily log files are kept (no automatic deletion)

**Implementation notes:**
- Replaces `RotatingFileHandler` with `TimedRotatingFileHandler(when="midnight", backupCount=0)`
- Custom `namer` callable transforms the stdlib default `archon.log.YYYY-MM-DD` → `archon.YYYY-MM-DD.log`
- `_rotate_on_startup(log_path)` handles the startup edge case
- Both helpers are exposed as module-level functions for unit testing

**Acceptance criteria:**
- `_daily_log_namer("…/archon.log.2026-02-22")` → `"…/archon.2026-02-22.log"`
- `_rotate_on_startup` is a no-op when the file does not exist or its mtime is today
- `_rotate_on_startup` renames the file to `archon.<mtime_date>.log` when mtime < today
- `setup_logging` calls `_rotate_on_startup` before opening the handler
- Handler is `TimedRotatingFileHandler` with `when="MIDNIGHT"` and `backupCount=0`
- Handler's `namer` attribute is `_daily_log_namer`
- Tests: namer unit tests (correct rename, parent dir preserved, date in stem), `_rotate_on_startup` (no file, today, yesterday, 5 days old), handler wiring, full `setup_logging` integration

---

### S4.2 ✅ — launchd service (macOS)
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

### S4.3 ✅ — systemd service (Linux) *(bonus)*
**As an** operator on Linux,
**I want** a systemd unit file,
**so that** the daemon auto-starts on boot.

**Acceptance criteria:**
- `scripts/archon.service` unit file with `Restart=on-failure`
- `make install-linux` copies unit and runs `systemctl enable --user archon`
- `make uninstall-linux` disables and removes

---

## Epic 5: Integration & E2E Tests

### S5.1 ✅ — AI pipeline integration test
**As a** developer,
**I want** an integration test that drives `EventMapper` with a scripted fake SDK message stream,
**so that** I can verify the full AI mapping pipeline without mocking individual methods.

**Acceptance criteria:**
- A fake message sequence (constructed SDK dataclass objects) covers all six event types
- `EventMapper.map_messages(fake_stream)` is awaited and the emitted event sequence matches expected types and content
- Tests cover: `ThinkingStarted`, `ThinkingResult`, `ToolStarted`, `ToolResult`, `ErrorEvent`, `Response` — at least one of each in a single run
- `SplitStrategy` truncation is applied to a content-bearing event to confirm the full AI-layer chain works
- No mocking of internal methods — only the SDK client boundary is substituted

---

### S5.2 ✅ — Chat + AI integration test
**As a** developer,
**I want** an integration test that wires whitelist middleware, message handler, `SessionManager`, and a mock `ClaudeSession`,
**so that** I can verify the full Telegram→AI pathway without a live bot connection.

**Acceptance criteria:**
- Build an aiogram `Dispatcher` with `WhitelistMiddleware` and the message handler registered
- Use aiogram's test utilities to inject a fake `Message` from a whitelisted user ID
- The handler calls `session.send(text)` on a mock `ClaudeSession`
- A non-whitelisted user ID is dropped — no session is created or called
- Tests: whitelisted message reaches session, non-whitelisted message is silently dropped

---

### S5.3 ✅ — Full message flow e2e test
**As a** developer,
**I want** an end-to-end test that runs the full gateway with only external boundaries mocked,
**so that** I can verify that a Telegram message produces the correct sequence of formatted Telegram replies.

**Boundaries mocked:**
- Telegram API: replaced with an in-process aiogram `Bot` stub that records `send_message` calls
- Claude SDK client: replaced with a scripted fake that emits a known event sequence

**Acceptance criteria:**
- `Gateway.start()` is called in a test loop with mocked bot and scripted SDK client
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

### S5.4 ✅ — Graceful shutdown e2e test
**As a** developer,
**I want** an end-to-end test that sends `SIGINT` to a running gateway and verifies a clean shutdown,
**so that** I can confirm no SDK sessions are left open after the daemon stops.

**Acceptance criteria:**
- Gateway starts with at least one active mock `ClaudeSession`
- `SIGINT` is sent to the running event loop
- `SessionManager.stop_all()` is called and all sessions reach `is_alive == False`
- Telegram bot polling is disconnected
- Shutdown completes within 5 seconds
- Log messages "shutdown initiated" and "shutdown complete" are both present

---

### S5.5 ✅ — Live Claude Agent SDK test
**As a** developer,
**I want** a live test that uses the real Claude Agent SDK to process a trivial prompt,
**so that** I can verify that `ClaudeSession` works against the actual Claude binary.

**Prerequisites:**
- `claude` binary present in `PATH`
- Test is marked `@pytest.mark.live` and skipped automatically if `which claude` fails

**Acceptance criteria:**
- `ClaudeSession.start()` connects using the real SDK
- A trivial prompt (e.g. `"Say: OK"`) is sent via `ClaudeSession.send()`
- At least one `Response` event with non-empty content is received within a 30-second timeout
- `ClaudeSession.stop()` disconnects; `is_alive` returns `False` afterwards
- No internal mocks of any kind

---

### S5.7 ✅ — Live unit test: config loader
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

### S5.6 ✅ — Live full-stack e2e test
**As a** developer,
**I want** a live test that runs the full gateway against the real Telegram API and real Claude Agent SDK,
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
S0.1 → S0.2 → S5.7 → S4.1 → S1.1 → S1.2 → S1.3 → S5.1 → S5.5 → S1.4
                                                                       ↓
                              S2.1 → S2.2 → S2.3 → S2.4 → S2.5 → S2.6 → S5.2 → S3.1 → S5.3 → S3.2 → S5.4 → S4.2 → S5.6
                                                                                                               ↓
                                                                           S7.1 → S8.1 → S8.2 → S8.3 → S8.4 → S6.1 → S6.2
                                                                                                                       ↓
                                                                                           S4.4 → S9.1 → S10.1 → S11.1 → S11.2
```

**Key:**
- S5.7 (live config unit test) immediately follows S0.2 — same component, real files
- S5.1 (AI pipeline integration) + S5.5 (live SDK test) follow S1.3 — need mapper and truncation
- S1.4 (session manager) follows S5.5 — all AI components stable
- S5.2 (chat+AI integration) follows S2.4 — all chat components present
- S5.3 (full e2e mocked) follows S3.1 — gateway wired
- S5.4 (shutdown e2e) follows S3.2 — shutdown implemented
- S5.6 (live full-stack) follows S4.2 — service install complete
- S8.1–S8.4 (notification redesign) follows S7.1 — history and chat infrastructure stable
- S6.1 (skills integration) follows S8.4 — notification system complete
- S6.2 (live skill loader test) immediately follows S6.1 — same component, real filesystem
- S4.4 (daily log rotation) follows S6.2 — refines S4.1 logging with rotation
- S9.1 (model selector) follows S4.4 — session manager mature enough to support runtime model switching
- S10.1 (plugin support) follows S9.1 — extends session factory with plugin layer
- S11.1 (context tracking) follows S10.1 — ClaudeSession stable, adds usage interception
- S11.2 (sub-agent team) follows S11.1 — adds agent hooks and config on top of stable session layer

> S4.1 (logging) is done early so all subsequent stories use it from the start.
> Tests are woven into the implementation flow — each test story immediately follows the story that satisfies its dependencies.
> S5.7 is a live unit test: single component, real filesystem, no mocks, no credentials needed.
> S5.1 is an integration test with mocks/stubs at the SDK client boundary.
> S5.5 requires real `claude` binary.
> S5.6 requires real `claude` binary and Telegram credentials.
> Live tests (`@pytest.mark.live`) are excluded from `uv run pytest`; run with `uv run pytest -m live`.

---

## Epic 7: Memory & History

### S7.1 ✅ — Chat history persistence (QMD-compatible)
**As a** developer,
**I want** all conversation turns persisted to daily Markdown files in `~/.archon/history/`,
**so that** Claude Code can later search its own past conversations as semantic memory via QMD's MCP server.

**Architecture note:** QMD exposes `qmd mcp` tools (`qmd_deep_search`, `qmd_vector_search`). Once history files exist, a future setup step (`qmd collection add ~/.archon/history --name archon`) + `qmd mcp --daemon` lets Claude Code call those tools directly to retrieve past context — no retrieval code needed inside Archon itself.

**Format — daily `.md` file (`~/.archon/history/YYYY-MM-DD.md`):**
- `# YYYY-MM-DD — Archon Conversations` — one-time file header (QMD uses title for chunk prefix)
- `## HH:MM:SS UTC · User {id} · {cwd}` — H2 = one chunk boundary per conversation turn
- `### {emoji} {type} · HH:MM:SS` — H3 per event within a turn; timestamps enable BM25 temporal queries
- `### ✅ Response` repeats the user question as a blockquote (Contextual Retrieval — reduces retrieval failure 49% per Anthropic research)
- `### ✅ Response` and `### ❌ Error` end with `\n\n---\n` (turn separator)
- `ThinkingStarted` emits nothing; tool I/O in fenced code blocks (prevents code-token noise in prose embeddings)

**New files:**
- `archon/ai/history_manager.py` — `HistoryManager(directory)` with `record_user_message(user_id, text, cwd)` and `record_event(user_id, event)`
- `tests/ai/test_history_manager.py` — 20 TDD tests

**Modified files:**
- `archon/config/loader.py` — `HistoryConfig(enabled, directory)` + `Config.history` field + `[history]` parsing
- `archon/chat/handler.py` — `cwd` and `history_manager` params; calls `record_user_message` + `record_event`
- `archon/gateway/gateway.py` — wires `HistoryManager` into dispatcher when enabled
- `config.toml.example` — `[history]` section documented

**Acceptance criteria:**
- `HistoryManager` creates `~/.archon/history/YYYY-MM-DD.md` with correct header on first write per day
- Header is not duplicated on subsequent writes to the same file
- File rotates to a new `.md` when the date changes
- Directory is created if missing
- `record_user_message(user_id, text, cwd)` writes `## HH:MM:SS UTC · User {id} · {cwd}` section + body
- Each event type renders the correct H3 subsection; `ThinkingStarted` emits nothing
- `Response` includes contextual retrieval blockquote (user's last question, truncated at 120 chars)
- `Response` and `ErrorEvent` end with `\n\n---\n`
- `HistoryConfig` defaults: `enabled=True`, `directory="~/.archon/history"`; overridable via `[history]` in `config.toml`
- `history_manager=None` → no crash (history is optional)
- All tests pass; ≥85% total coverage; `mypy` clean

---

## Epic 6: Skills Integration

### S6.1 ✅ — Skills integration
**As a** Telegram user,
**I want** to list and activate Claude Code skills from the Telegram chat,
**so that** I can leverage specialized skill prompts without leaving Telegram or copy-pasting them manually.

**Background:**
Claude Code skills are Markdown files at `~/.claude/skills/<name>/SKILL.md` with YAML frontmatter (`name`, `description`) and a body containing specialized instructions. The Claude Code TUI injects skill content into Claude's context via system-reminder blocks. This story brings that capability to Archon.

`ClaudeAgentOptions.system_prompt: str | None` is confirmed available in `claude-agent-sdk` 0.1.39.

**Technical approach:**
- A compact skill registry (name + description of each installed skill) is injected into every new `ClaudeSession` via `ClaudeAgentOptions.system_prompt`, so Claude is always aware of what skills exist.
- When the user activates a skill via `/skill <name>`, the full `SKILL.md` body is queued and prepended as a context block to the **next outgoing message only** (one-shot injection), mirroring the TUI's system-reminder behaviour.
- Skills are loaded from disk at `SessionManager` startup and cached in memory; skill changes require an Archon restart.
- Skill bodies are not injected into the system prompt at startup — they are too large (168–386 lines each, 2453 lines total across 8 skills) and most will never be used in a given session.

**New module: `archon/ai/skill_loader.py`**
- `Skill` dataclass: `name: str`, `description: str`, `content: str` (SKILL.md body with frontmatter stripped)
- `SkillLoader` class:
  - `__init__(skills_dir: Path = Path("~/.claude/skills"))`
  - `load_all() -> list[Skill]` — reads all `*/SKILL.md` files, parses YAML frontmatter, returns list; malformed frontmatter is logged as a warning and skipped
  - `get(name: str) -> Skill | None`

**Changes to `archon/ai/claude_session.py`:**
- `__init__` accepts `skills: list[Skill] = []`
- Builds a compact system prompt block listing all skill names and descriptions; passes it as `system_prompt` to `ClaudeAgentOptions`
- `activate_skill(skill: Skill)` — appends the skill to an internal `_pending_skills: list[Skill]` queue
- `send(prompt)` — if `_pending_skills` is non-empty, prepends each skill's full body as a labelled context block before the user prompt, then clears the queue

**Changes to `archon/ai/session_manager.py`:**
- `__init__` receives `skill_loader: SkillLoader`
- `get_or_create(user_id)` passes `skill_loader.load_all()` to each new `ClaudeSession`

**New Telegram commands (`archon/chat/`):**
- `/skills` — replies with a formatted list of available skills (name + one-line description each)
- `/skill <name>` — activates the named skill for the current session; calls `session.activate_skill(skill)` and confirms to the user

**Acceptance criteria:**
- `SkillLoader.load_all()` reads all `~/.claude/skills/*/SKILL.md` files, parses frontmatter, returns a `Skill` list; malformed frontmatter is logged as a warning and skipped
- `SkillLoader.get(name)` returns the matching `Skill` or `None`
- Every new `ClaudeSession` receives a `system_prompt` that lists all installed skill names and descriptions
- `/skills` replies with a formatted list of skill names and descriptions
- `/skill <name>` with a valid name: queues skill, replies `✅ Skill \`<name>\` activated — it will be applied to your next message`
- `/skill <name>` with an unknown name: replies `❌ Unknown skill \`<name>\`. Use /skills to see available skills`
- `/skill <name>` when no session exists: replies `No active session. Send a message first to start one`
- The first `send()` after activation prepends the full skill body as a context block; subsequent sends do not re-inject it (one-shot)
- Tests: `SkillLoader` with `tmp_path` skills (happy path, malformed frontmatter, empty skills dir), `ClaudeSession` system prompt contains the compact registry, skill activation and one-shot injection verified, `/skills` and `/skill` handler unit tests with mock session

---

### S6.2 ✅ — Live skill loader test
**As a** developer,
**I want** a live test that exercises `SkillLoader` against the real `~/.claude/skills/` directory,
**so that** I can verify frontmatter parsing and file I/O work against actual installed skills without any mocking.

**Prerequisites:**
- Test is marked `@pytest.mark.live`; no external services required
- `~/.claude/skills/` must exist and contain at least one skill; test is skipped otherwise

**Acceptance criteria:**
- `SkillLoader().load_all()` returns at least one `Skill` with non-empty `name`, `description`, and `content`
- `SkillLoader().get(first_skill.name)` returns the same skill object
- `SkillLoader().get("nonexistent-skill")` returns `None`
- No mocks, no patching — pure real filesystem reads

**Placed after:** S6.1

---

## Epic 8: Notification Mode Redesign

### S8.1 ✅ — Four named notification modes
**As a** whitelisted user,
**I want** a single notification verbosity axis with four named modes (quiet / normal / verbose / debug),
**so that** I can control how much output I see without memorising two overlapping command dimensions.

**Background:**
The previous design exposed two independent dimensions (`concise_mode` × `show_thinking_result` / `brief_tool_output`) with opaque names (`off/full/partial`). Users think in terms of *"how much do I want to see?"*, not in terms of orthogonal toggles. A single mode axis is easier to grasp and switches in one tap.

**Acceptance criteria:**
- `NotificationsConfig` has exactly two fields: `mode: str = "normal"` and `interval_minutes: int = 2`
- Valid modes: `"quiet"`, `"normal"`, `"verbose"`, `"debug"`
- `format_event` filters by mode per the visibility matrix:
  - **always** (all modes): `✅ Response`, `❌ Error`
  - **normal+**: `🔧 ToolStarted` (name only), `📤 ToolResult` (brief one-line)
  - **verbose+**: `💭 ThinkingStarted`, `💭 ThinkingResult` (truncated), `🔧 ToolStarted` (name + args)
  - **debug only**: `📤 ToolResult` (full), `💭 ThinkingResult` (full, same as verbose but unabbreviated label)
- `handle_message` uses the mode to decide event routing; quiet mode suppresses intermediate events
- `load_config` reads new `[notifications]` keys; gracefully migrates old keys (`concise_mode="full"` → `"quiet"`, `"partial"` → `"normal"`, `"off"` → `"verbose"`)
- `save_notifications_config` writes only `mode` and `interval_minutes`; old keys are dropped
- Tests: every cell of the visibility matrix, config load/save round-trip, migration from old config

---

### S8.2 ✅ — Quiet beacon mode
**As a** whitelisted user,
**I want** to optionally receive periodic heartbeat messages while Claude works in quiet mode,
**so that** I know the bot is alive during long-running tasks without being flooded with events.

**Background:**
In quiet mode all intermediate events are suppressed. Without feedback, a long task feels broken. The optional *beacon* sends `⏳ Working… (N tools, M thinking)` every `interval_minutes` minutes — a heartbeat signal that proves something is happening. When `interval_minutes = 0` the beacon is disabled.

**Acceptance criteria:**
- `interval_minutes = 0` → no beacon (plain quiet mode)
- `interval_minutes > 0` → periodic `⏳ Working… (N tools, M thinking)` every `interval_minutes` minutes while in quiet mode
- Beacon task is cancelled cleanly when the response arrives or an error occurs
- Interval only applies in quiet mode; other modes stream events in real-time
- Tests: beacon fires at correct interval, beacon not started when `interval_minutes = 0`, beacon cancelled on completion, other modes unaffected

---

### S8.3 ✅ — Inline keyboard for /notify and /settings
**As a** whitelisted user,
**I want** `/notify` and `/settings` to display an inline keyboard panel,
**so that** I can switch modes with a single tap without typing subcommands.

**Background:**
Telegram inline keyboards allow the bot to edit a single message in-place when a button is tapped — no extra messages pollute the chat. The panel shows all four modes with a ✓ on the current one; tapping another mode updates it immediately.

**Acceptance criteria:**
- `/notify` (no arg) sends a message: `⚙️ Notification mode` with a 2×2 inline keyboard
- Button labels: `🔇 Quiet`, `🔔 Normal`, `📢 Verbose`, `🔬 Debug`; current mode marked with ` ✓`
- When in quiet beacon mode the Quiet button shows `🔇 Quiet 🔦Nm ✓` (N = interval minutes)
- Tapping a button: updates `notifications.mode`, saves config, edits the keyboard message in-place, answers the callback query
- `/settings` shows the same inline keyboard (replaces the old text-only view)
- Callback queries from non-whitelisted users are dropped (whitelist middleware extended to `dp.callback_query`)
- Tests: `/notify` sends reply with `InlineKeyboardMarkup`, callback updates mode + saves + edits message, Quiet button label reflects beacon state, whitelist drops unauthorised callbacks

---

### S8.4 ✅ — Quick-switch mode commands
**As a** whitelisted user,
**I want** `/quiet [N]`, `/normal`, `/verbose`, and `/debug` shortcut commands,
**so that** I can switch modes instantly without navigating the keyboard panel.

**Background:**
The inline keyboard is the primary UX for touch users. Power users who know what they want prefer a single command. The quick commands set the mode and echo the inline keyboard panel so the user always sees the current state.

**Acceptance criteria:**
- `/quiet` → sets `mode="quiet"`, `interval_minutes=0`; replies `🔇 Quiet mode` + inline keyboard
- `/quiet N` (N > 0 integer) → sets `mode="quiet"`, `interval_minutes=N`; replies `🔇 Quiet — beacon every N min` + inline keyboard
- `/quiet 0` treated as `/quiet` (no beacon)
- `/normal` → sets `mode="normal"`; replies `🔔 Normal mode` + inline keyboard
- `/verbose` → sets `mode="verbose"`; replies `📢 Verbose mode` + inline keyboard
- `/debug` → sets `mode="debug"`; replies `🔬 Debug mode` + inline keyboard
- All four commands registered in dispatcher and appear in `BOT_COMMANDS`
- `/notify quiet [N]` text subcommand works identically to `/quiet [N]`
- Config saved after every change
- Tests: each command sets correct mode, interval parsing, `/quiet 0` clears beacon, config saved, reply text correct

---

## Epic 9: Model Management

### S9.1 ✅ — Model selector (/model command)
**As a** whitelisted user,
**I want** to switch the Claude model from Telegram via `/model`,
**so that** I can change between models without editing config files or restarting the daemon.

**Acceptance criteria:**
- `ModelsConfig` dataclass in `config/loader.py` with `available: list[str]` and `default: str | None`; parsed from `[models]` in `config.toml`
- `/model` (no args) shows current active model with an inline keyboard (one button per `available` model; active model marked with ` ✓`)
- Tapping a model button calls `session_manager.set_model(model)`, edits the keyboard message in-place, answers the callback query
- Sessions started after the switch use the new model (`SessionManager.set_model` stores the value; new `ClaudeSession` instances pick it up via the factory)
- `/model <name>` text subcommand also accepted
- `BOT_COMMANDS` entry for `/model` with description `"Show or switch the Claude model"`
- Tests: `/model` handler sends reply with `InlineKeyboardMarkup`, `model_callback` with valid model switches and edits, `model_callback` with unknown model is ignored gracefully, `session_manager.get_model()` returns updated value

---

## Epic 10: Plugin Support

### S10.1 ✅ — Claude Code plugin loading
**As a** developer,
**I want** Archon to automatically load enabled Claude Code plugins into every session,
**so that** MCP servers and tools from installed plugins (e.g. `claude-mem`) are available to Claude without any extra configuration.

**Background:**
Claude Code plugins live at `~/.claude/plugins/` and register MCP servers via `.mcp.json`. The Claude Agent SDK accepts a `plugins` list in `ClaudeAgentOptions`; passing an enabled plugin's cache path is sufficient for the SDK to start its MCP servers and inject its `CLAUDE.md` system prompt. Archon mirrors the enabled-plugin state from `~/.claude/settings.json`.

**New module: `archon/ai/plugin_loader.py`**
- `PluginInfo` dataclass: `key`, `name`, `marketplace`, `version`, `install_path`, `description`, `skills`
- `PluginLoader(plugins_dir, settings_path)`:
  - `load_all() -> list[PluginInfo]` — reads `installed_plugins.json` + `settings.json`, loads only enabled plugins; result is cached
  - `get_sdk_configs() -> list[dict]` — returns `[{"type": "local", "path": install_path}, …]` for `ClaudeAgentOptions.plugins`
  - `get_skills() -> list[Skill]` — returns plugin-bundled skills namespaced as `"plugin-name:skill-dir-name"`

**Acceptance criteria:**
- `PluginsConfig` dataclass in `config/loader.py` (`enabled: bool`, `plugins_dir: str`, `settings_path: str`); parsed from `[plugins]` in `config.toml`
- `PluginLoader.load_all()` returns only plugins enabled in `settings.json`; disabled, missing, or malformed plugins are skipped with a warning log
- `get_sdk_configs()` returns the correct `{"type": "local", "path": …}` format for each enabled plugin
- `get_skills()` returns skills namespaced as `plugin-name:skill-name`
- `load_all()` is idempotent (cached after first call)
- `SessionManager` accepts `plugin_loader`; factory merges personal skills + plugin skills and passes SDK plugin configs to `ClaudeSession`
- `/skills` command shows plugin-bundled skills alongside personal skills, grouped by source
- `plugins.enabled = false` in `config.toml` disables plugin loading without code changes
- Tests: enabled/disabled plugins, missing `installed_plugins.json`, missing `settings.json`, invalid install path skipped, SDK config format, skill namespacing, caching (`load_all` returns same object on second call)

---

## Epic 11: Context Tracking & Sub-agents

### S11.1 ✅ — Context window usage (/context command)
**As a** whitelisted user,
**I want** to see a real-time snapshot of my context window usage via `/context`,
**so that** I know how much of the 200k-token window is used, my accumulated cost, and turn count.

**Acceptance criteria:**
- `ClaudeSession._intercept()` wraps `receive_response()` and captures `ResultMessage` fields: `usage` (token dict), `total_cost_usd` (accumulated across turns), `num_turns`, `duration_ms`
- `ClaudeSession.usage_stats` property returns a dict with keys `usage`, `total_cost_usd`, `num_turns`, `last_duration_ms`; returns `None` before the first response
- `SessionManager.context_stats(user_id)` delegates to `session.usage_stats`; returns `None` when no session exists
- `/context` with no active session replies `"ℹ️ No active session"`
- `/context` with a session but no data yet replies `"📊 No context data yet — send a message first"`
- `/context` with data replies with an HTML-formatted message containing:
  - Unicode block progress bar showing `input_tokens / 200,000`
  - Per-category token counts: input, output, cache-read, cache-creation
  - Accumulated cost (formatted as `$0.0000`), turn count, last response duration
- Tests: `usage_stats` before first response returns `None`, after one response returns correct values, accumulated cost adds across turns; `/context` handler for each state (no session, no data, has data); `_progress_bar` edge cases (0 tokens, at capacity)

---

### S11.2 ✅ — Sub-agent team configuration (/agents command)
**As a** developer,
**I want** to define a team of named sub-agents in `config.toml` and have them available in every Claude session,
**so that** Claude can delegate specialised tasks to sub-agents (e.g. a `bash` agent or `explore` agent) via the Task tool.

**Background:**
The Claude Agent SDK accepts an `agents` dict in `ClaudeAgentOptions` mapping agent names to `AgentDefinition` objects. Sub-agent lifecycle events arrive via SDK hooks (`SubagentStart`, `SubagentStop`), which must be surfaced as archon events for the Telegram UI.

**New event types in `archon/ai/event_mapper.py`:**
- `SubagentStarted(agent_id: str, agent_type: str)` — fired when the main agent spawns a sub-agent
- `SubagentStopped(agent_id: str, agent_type: str)` — fired when a sub-agent completes

**Acceptance criteria:**
- `AgentDefinitionConfig` dataclass: `name`, `description`, `prompt`, `tools: list[str]`, `model: str | None`
- `AgentsConfig` dataclass: `enabled: bool`, `definitions: list[AgentDefinitionConfig]`; parsed from `[agents]` / `[[agents.definitions]]` in `config.toml`
- `_build_sdk_agents(agents_cfg)` in `session_manager.py` converts `AgentsConfig` → `dict[str, AgentDefinition]` (or `None` if disabled/empty)
- `ClaudeSession.__init__` accepts `agents: dict[str, AgentDefinition] | None`; passed to `ClaudeAgentOptions`
- `ClaudeSession._build_hooks()` creates `SubagentStart`/`SubagentStop` SDK hook matchers that push `SubagentStarted`/`SubagentStopped` events into a side-channel `asyncio.Queue`
- `ClaudeSession.send()` drains the queue between each SDK-derived event and in a final drain after the stream ends
- `format_event` formats `SubagentStarted` as `🤖 Agent: <b>{agent_type}</b> started`; suppressed in quiet mode; `SubagentStopped` similarly formatted
- `/agents` command lists all configured agent definitions with name, model, description, and tools; replies with info message when no agents configured
- `BOT_COMMANDS` entry for `/agents`
- Gateway wires `agents_config` into `SessionManager` and `/agents` command dependency injection
- Tests: `AgentsConfig` loading from TOML, `_build_sdk_agents` with enabled/disabled/empty config, hook queue draining, `SubagentStarted`/`SubagentStopped` event formatting, `/agents` with no config and with definitions

---

### S11.3 ✅ — Per-agent notification configuration
**As an** operator,
**I want** to set a separate notification level for sub-agents in `config.toml` independently of the orchestrator's notification mode,
**so that** I can keep the orchestrator events fully visible while silencing sub-agent lifecycle chatter (or vice versa) without changing how the main agent reports.

**Background:**
S11.2 added `SubagentStarted`/`SubagentStopped` events that follow the single `notifications.mode` setting. When running an agent team, a single long task can produce many `🤖 Agent: X started/done` messages. Operators want to suppress these by default without also suppressing orchestrator tool and thinking events.

**Config shape:**
```toml
[notifications]
mode = "normal"          # orchestrator: thinking, tool calls, tool results, response

[notifications.agents]
mode = "quiet"           # sub-agent lifecycle events; omit section to inherit from notifications.mode
```

**Inheritance rule:** If `[notifications.agents]` is absent or `mode` is not set, sub-agent events follow `notifications.mode`. If explicitly set, that value pins agent events regardless of the orchestrator mode. Runtime commands (`/quiet`, `/normal`, `/verbose`, `/debug`) change only `notifications.mode`; agents with an explicit override stay pinned.

**What "quiet" means per layer:**

| Layer | `quiet` hides | `quiet` still surfaces |
|---|---|---|
| Orchestrator | thinking, tool calls, tool results | ✅ final response |
| Agents | start/stop lifecycle events | sub-agent result (arrives as parent's `ToolResult`) |

**New dataclass in `archon/config/loader.py`:**
- `NotificationsAgentsConfig(mode: str | None = None)` — `None` means inherit from orchestrator

**Updated `NotificationsConfig`:**
- Adds `agents: NotificationsAgentsConfig` field (default: `NotificationsAgentsConfig()`, i.e. inherit)

**New helper in `archon/chat/handler.py`:**
- `_resolve_agent_mode(notifications: NotificationsConfig | None) -> str` — returns `notifications.agents.mode` if set, else `notifications.mode`, else `"debug"` (backward-compat fallback when `notifications` is `None`)

**Changes to `archon/chat/handler.py`:**
- `format_event` for `SubagentStarted`/`SubagentStopped`: uses `_resolve_agent_mode(notifications)` instead of `mode` (the orchestrator mode)
- `handle_message` quiet-mode block: `SubagentStarted`/`SubagentStopped` only counted in beacon and skipped when resolved agent mode is `"quiet"`; if agent mode is anything else, they fall through to `format_event` even when the orchestrator is in quiet mode

**Changes to `archon/config/loader.py`:**
- `load_config`: parses `[notifications.agents]` subsection; missing section or missing key → `mode=None`
- `save_notifications_config`: writes `[notifications.agents] mode = …` when `agents.mode` is not `None`; removes `mode` key from the subsection (or omits the subsection entirely) when `None`

**Changes to `examples/config.toml.example`:**
- Documents `[notifications.agents]` with `mode = "quiet"` as the recommended default

**Acceptance criteria:**
- `NotificationsAgentsConfig(mode=None)` → resolved mode equals the orchestrator's current mode (inheritance)
- `NotificationsAgentsConfig(mode="quiet")` → agent events suppressed regardless of orchestrator mode
- `format_event(SubagentStarted, …, notifications)` returns `[]` when resolved agent mode is `"quiet"`; returns formatted string otherwise
- Orchestrator `"quiet"` + agents `"normal"` → `SubagentStarted` notification is still sent; event is **not** counted in beacon
- Orchestrator `"normal"` + agents `"quiet"` → `SubagentStarted` returns `[]` from `format_event`
- Orchestrator `"quiet"` + agents `"quiet"` (or inherit) → `SubagentStarted` counted in beacon, no message sent
- Orchestrator `"quiet"` + agents `"verbose"` → `SubagentStarted` notification sent, not counted in beacon
- `load_config` parses `[notifications.agents] mode = "quiet"` → `NotificationsAgentsConfig(mode="quiet")`
- `load_config` with no `[notifications.agents]` section → `NotificationsAgentsConfig(mode=None)`
- `save_notifications_config` with `agents.mode = "quiet"` writes `notifications.agents.mode = "quiet"` in TOML
- `save_notifications_config` with `agents.mode = None` omits/removes the `mode` key from `[notifications.agents]`
- All existing subagent formatting tests remain green (no regressions)
- Tests: `_resolve_agent_mode` (None → inherit, explicit → override), `format_event` matrix (all orchestrator/agent mode combos for subagent events), `handle_message` integration (quiet orch + normal agents shows event), config load/save round-trip for `agents.mode`

---

## Epic 12: Filesystem Agent Loader

### S12.1 ✅ — Filesystem-based agent loader (AgentLoader)
**As an** operator,
**I want** Archon to automatically discover and load agent definitions from ``~/.claude/agents/*.md``,
**so that** I can manage my agent team by editing markdown files (the same files used by the Claude TUI) without having to maintain a parallel ``[agents]`` section in ``config.toml``.

**Background:**
S11.2 introduced config.toml-based agent definitions. This creates duplication with the Claude TUI, which already manages agents as markdown files in ``~/.claude/agents/``. The AgentLoader reads these files directly, using an opt-in ``-archon`` suffix convention to distinguish agents designed for the Archon API environment (which can be injected into SDK sessions) from TUI-only agents (shown for reference but not injected).

**Opt-in convention:**
An agent is an *Archon agent* when its ``name`` frontmatter field ends with ``-archon`` (e.g. ``researcher-archon``).  The corresponding file is typically named ``researcher-archon.md``.  TUI-only agents (e.g. ``devils-advocate.md`` with ``name: devils-advocate``) are loaded and shown in ``/agents`` but are **not** passed to the Claude SDK.

**Agent file format (``~/.claude/agents/<stem>.md``):**
```markdown
---
name: researcher-archon
description: Web research and data-gathering specialist
model: haiku
tools: WebSearch, Read
---

You are a research specialist. Your job is to gather accurate,
up-to-date information from the web and synthesise it clearly.
```

**New file: ``archon/ai/agent_loader.py``:**
- ``_strip_quotes(value: str) -> str`` — strips surrounding double-quotes from YAML string values
- ``Agent`` dataclass: ``name``, ``description``, ``prompt`` (body), ``model: str | None``, ``tools: list[str]``; ``is_archon`` property returns ``name.endswith("-archon")``
- ``AgentLoader(agents_dir: Path = Path("~/.claude/agents"))``
  - ``load_all() -> list[Agent]`` — loads all ``.md`` files; returns archon agents (sorted alphabetically) followed by non-archon agents (sorted alphabetically); result is cached after first call
  - ``get(name: str) -> Agent | None`` — returns agent by name, or None
  - ``_load_agent(path: Path) -> Agent | None`` — parses a single file; logs a warning and returns None on any error (unreadable file, missing frontmatter, missing ``name`` or ``description`` field)

**Changes to ``archon/ai/session_manager.py``:**
- Renamed ``_build_sdk_agents(AgentsConfig | None)`` → ``_build_sdk_agents_config`` (kept for backward compat with config.toml agents)
- New ``_build_sdk_agents(agents: list[Agent] | None) -> dict[str, AgentDefinition] | None`` — converts an Agent list to SDK dict; empty tools become ``None`` for the SDK
- ``SessionManager.__init__`` gains ``agent_loader: AgentLoader | None = None`` parameter, stored as ``self._agent_loader``
- Default factory: filters loader agents to ``is_archon=True``, converts with new ``_build_sdk_agents``, merges with config agents (loader agents take priority on name collision); result passed to ``ClaudeSession(agents=...)``

**Changes to ``archon/gateway/gateway.py``:**
- Imports ``AgentLoader``
- Instantiates ``AgentLoader()`` at startup; calls ``load_all()`` eagerly so warnings appear in logs at boot
- Passes ``agent_loader=agent_loader`` to ``SessionManager``
- ``_setup_dp()`` accepts ``agent_loader`` parameter; wires ``dp["agent_loader"] = agent_loader``

**Changes to ``archon/chat/commands.py``:**
- Imports ``AgentLoader``
- ``agents_command`` gains ``agent_loader: AgentLoader | None = None`` parameter
- Output split into three sections: 🤖 Archon agents (filesystem, is_archon=True), 🔍 Other agents (filesystem, is_archon=False), ⚙️ Config agents (config.toml); empty sections are omitted

**Changes to ``archon/chat/bot.py``:**
- ``/agents`` description updated from ``"List configured custom agent types"`` to ``"List all available agent types"``

**Tests: ``tests/ai/test_agent_loader.py``:**
- ``Agent.is_archon`` property for names with/without ``-archon`` suffix
- ``AgentLoader.load_all()`` — happy paths: empty dir, single archon agent, single non-archon agent, all fields (name/description/prompt/model/tools), quoted descriptions, tools parsing (comma-separated, whitespace trimming, empty list when absent/empty)
- Ordering: archon agents before non-archon; each group alphabetically sorted within group
- Caching: second call returns same object
- Edge cases: nonexistent dir (logs warning), no frontmatter, missing name/description (each logs warning), unclosed fence, non-.md files ignored, subdirectories ignored, unreadable files (OS error → skip + log), mixed valid+invalid (valid returned)
- ``AgentLoader.get()`` — by name, unknown name, empty dir
- ``_build_sdk_agents()`` — None and empty list → None; list conversion (description, prompt, model, tools→None for empty)
- ``SessionManager`` — accepts ``agent_loader`` parameter; stores as ``_agent_loader``; default factory calls ``load_all()``

**Acceptance criteria:**
- ``AgentLoader`` loads all ``.md`` files from ``~/.claude/agents/``; archon agents sorted before non-archon agents
- ``Agent.is_archon`` is ``True`` iff ``name.endswith("-archon")``
- Malformed files (no frontmatter, missing required fields) are skipped with a warning log; valid siblings are still returned
- ``_build_sdk_agents(agents)`` converts ``list[Agent]`` to ``dict[str, AgentDefinition]``; empty tools → ``None``; returns ``None`` for empty/None input
- ``SessionManager`` with ``agent_loader`` uses only archon agents for the SDK; merges with config.toml agents (loader wins on name collision)
- Gateway instantiates ``AgentLoader`` at startup and wires it into the dispatcher
- ``/agents`` lists archon agents (🤖), non-archon agents (🔍), and config agents (⚙️) in separate sections; sections absent when empty
- All existing tests remain green; full suite ≥ 97 % coverage
- Tests: full suite in ``tests/ai/test_agent_loader.py``; updated ``test_subagent_integration.py`` imports ``_build_sdk_agents_config`` for old config tests; updated ``test_commands.py`` for new ``/agents`` output format

---

## Epic 14: Session Observability & Diagnostics

### S14.1 ✅ — Session state tracking & diagnostics
**As an** operator,
**I want** to inspect whether a `ClaudeSession` is actively processing or stuck, how long it has been running, and what events it has recently emitted,
**so that** I can detect hangs, surface processing state in `/status`, and use this information programmatically (e.g. from the cron scheduler or future health-check tooling).

**Background:**
Currently `ClaudeSession.is_alive` only indicates whether the SDK subprocess is connected — not whether a request is in flight. There is no way to tell if Claude has been "thinking" for 30 seconds vs 5 minutes, nor what events it produced during that time. This story adds lightweight timing state and a bounded in-memory event log directly to `ClaudeSession`, exposes aggregation methods on `SessionManager`, and enhances `/status` to surface the new data.

**Changes to `archon/ai/claude_session.py`:**

New `__init__` attributes:
- `_processing: bool = False` — True while the `send()` generator is being iterated
- `_last_send_at: float | None = None` — `time.monotonic()` set when `send()` body first executes
- `_last_response_at: float | None = None` — `time.monotonic()` set when a `Response` or `ErrorEvent` is yielded
- `_send_count: int = 0` — incremented once per `send()` call
- `_event_log: deque[tuple[float, Event]] = deque(maxlen=200)` — bounded ring-buffer; each `(time.monotonic(), event)` pair appended as events are yielded

`send()` modified:
- Sets `_processing = True` and `_last_send_at` at the top of the generator body; increments `_send_count`
- Appends every yielded event to `_event_log`
- Sets `_last_response_at` when a `Response` or `ErrorEvent` is yielded
- `finally` block resets `_processing = False` — covers normal exit, `break`, and exceptions

New properties/methods:
- `is_processing: bool` — property returning `_processing`
- `processing_seconds: float | None` — seconds since `send()` was called; `None` when not processing
- `idle_seconds: float | None` — seconds since last `Response`/`ErrorEvent`; `None` if never responded
- `send_count: int` — total prompts sent in this session
- `is_stuck(threshold_seconds: float = 120.0) -> bool` — `True` if processing and duration exceeds threshold
- `recent_events(n: int = 20) -> list[tuple[float, Event]]` — last `n` `(timestamp, event)` pairs from the log
- `diagnostics: dict` — complete state snapshot: `is_alive`, `is_processing`, `processing_seconds`, `idle_seconds`, `send_count`, `recent_events(10)`, `usage_stats`

**Changes to `archon/ai/session_manager.py`:**
- `session_diagnostics(user_id: int) -> dict | None` — delegates to `session.diagnostics`; `None` if no session
- `processing_sessions() -> dict[int, float]` — maps `user_id → processing_seconds` for all currently-processing sessions
- `stuck_sessions(threshold_seconds: float = 120.0) -> list[int]` — user IDs whose sessions exceed threshold

**Changes to `archon/chat/commands.py`:**
- `status_command` enhanced: when a session is active, additionally shows:
  - `🔄 Processing for X.Xs` (when `is_processing`)
  - `💤 Idle for Xs` (when not processing and `idle_seconds` is not None)
  - `📨 X messages sent` (always, when session exists)

**Test files:**
- `tests/ai/test_claude_session.py` — new class `TestClaudeSessionDiagnostics` (22 cases)
- `tests/ai/test_session_manager.py` — new class `TestSessionManagerDiagnostics` (10 cases)
- `tests/ai/test_session_diagnostics_e2e.py` — new file, mocked slow SDK (5 cases)
- `tests/ai/test_session_diagnostics_live.py` — new file, `@pytest.mark.live`, real SDK (7 cases)
- `tests/chat/test_commands.py` — extended for enhanced `/status` output (3 new cases)

**Acceptance criteria:**
- `ClaudeSession` tracks `_processing`, `_last_send_at`, `_last_response_at`, `_send_count`, `_event_log` (deque maxlen=200)
- `is_processing` is `True` while `send()` generator is being iterated; `False` before, after, and after early `break` or exception
- `processing_seconds` is `None` when not processing; positive float while processing
- `idle_seconds` is `None` before first response; non-negative float after
- `is_stuck(threshold)` returns `False` when not processing; `True` when `processing_seconds > threshold`
- `_event_log` auto-drops oldest beyond 200; `recent_events(n)` returns the last `n` entries
- `diagnostics` dict contains all defined keys with correct types
- `SessionManager.session_diagnostics(unknown)` returns `None`; known user returns dict
- `SessionManager.processing_sessions()` returns correct `{user_id: seconds}` map
- `SessionManager.stuck_sessions(threshold)` returns correct list
- `/status` shows `🔄 Processing for X.Xs` or `💤 Idle for Xs` plus message count when session active
- All 44 test cases pass; full suite coverage remains ≥ 85%
- Live tests: `is_processing` transitions correctly around a real SDK query; `event_log` populated; `diagnostics` fully populated

---

## Epic 15: Background Agent Execution (FR.014)

### S15.1 ✅ — BackgroundAgentsConfig + ClaudeSession extensions
**As a** developer,
**I want** configuration for background agent execution and a `ClaudeSession` that can receive injected context and disable the native `Task` tool,
**so that** the Archon background-agent feature can be enabled and configured, and the main session is prepared to work with background agents via MCP.

**Background:**
The Claude Agent SDK does not support parallel sessions or fire-and-forget subtasks.  The solution is for Archon to host a local MCP tool (`spawn_background_agent`) that Claude can call.  Before wiring the MCP server, the `ClaudeSession` needs (1) a way to receive results from completed background agents as context for its next prompt, and (2) the `Task` tool disabled so Claude uses the MCP route rather than the native SDK sub-agent mechanism.

**New dataclass in `archon/config/loader.py`:**
```python
@dataclass
class BackgroundAgentsConfig:
    enabled: bool = False
    spawn_rule: str = "auto"     # "eager" | "auto" | "manual"
    max_parallel: int = 5        # max concurrent background agents per user
    host: str = "localhost"
    port: int = 18182
```

**`Config` dataclass** — add:
```python
background_agents: BackgroundAgentsConfig = field(default_factory=BackgroundAgentsConfig)
```

**New `ClaudeSession.__init__` parameters:**
- `background_agent_mcp_url: str | None = None` — MCP endpoint URL; when set, the session connects to the Archon background-agent server
- `spawn_rule: str | None = None` — "eager" | "auto" | "manual"; controls system-prompt hint

**`ClaudeSession.start()` changes:**
- When `background_agent_mcp_url` is set: add `"archon": {"type": "http", "url": background_agent_mcp_url}` to `mcp_servers`
- When `background_agent_mcp_url` is set: append `"Task"` to `disallowed_tools`

**`_build_system_prompt()` changes:**
- Accept optional `spawn_rule` parameter
- Append the spawn-rule-specific hint paragraph when `spawn_rule` is not None

Spawn-rule hints:
- `"eager"`: "When a task involves multiple independent steps or parallel workstreams, proactively use the `spawn_background_agent` MCP tool to run subtasks in the background. The main conversation remains interactive while agents work."
- `"auto"`: "You have access to a `spawn_background_agent` MCP tool. Use it when running a long task in the background would keep the main conversation more responsive."
- `"manual"`: "You have access to a `spawn_background_agent` MCP tool. Only use it when the user explicitly asks you to run something in the background."

**New `ClaudeSession.inject_context(text: str) -> None`:**
- Appends `text` to `self._pending_context: list[str]` (new attribute, initialized to `[]`)
- Thread-safe (asyncio single-threaded model)

**`ClaudeSession.send()` changes:**
- If `_pending_context` is non-empty, prepend all entries as a joined block before skill blocks and before the user prompt
- Clear `_pending_context` after prepending (one-shot)
- Context blocks format: entries joined with `\n\n`; final format: `{context_block}\n\n{skill_blocks_if_any}\n\n{prompt}`

**Acceptance criteria:**
- `BackgroundAgentsConfig` defaults: `enabled=False`, `spawn_rule="auto"`, `max_parallel=5`, `host="localhost"`, `port=18182`
- `load_config()` parses `[background_agents]` TOML section; missing section → defaults; no `ConfigError`
- `inject_context("foo")` appends to `_pending_context`; multiple calls accumulate
- `send()` with pending context: context prepended; `_pending_context` cleared after first `send()`
- `send()` without pending context: no prefix added
- Context is prepended before skill blocks (order: context → skills → prompt)
- `"Task"` appears in `disallowed_tools` when `background_agent_mcp_url` is set
- `"Task"` NOT in `disallowed_tools` when `background_agent_mcp_url` is `None`
- System prompt contains spawn-rule hint when `spawn_rule` is set; no hint when `None`
- Each of the 3 spawn-rule values produces a distinct hint paragraph
- Tests: config defaults, TOML parsing, `inject_context` accumulation and clearing, context-prefix ordering, disallowed_tools both cases, system-prompt hints for all 3 rules + None

---

### S15.2 ✅ — BackgroundAgentManager
**As a** developer,
**I want** a manager that spawns isolated `ClaudeSession` tasks in the background, tracks their state, delivers results to the user via Telegram, and injects results into the main session's context,
**so that** background agents run independently without blocking the main conversation.

**New file: `archon/ai/background_agent_manager.py`**

**`AgentRun` dataclass:**
```python
@dataclass
class AgentRun:
    run_id: str            # uuid4 hex string
    name: str              # human-readable name from _AGENT_NAMES pool
    task: str              # task description as given
    context: str           # context passed at spawn time
    user_id: int
    started_at: float      # time.monotonic()
    status: str = "running"       # "running" | "completed" | "failed" | "cancelled"
    result: str | None = None
    error: str | None = None
    _task_ref: asyncio.Task | None = field(default=None, repr=False)
```

**`BackgroundAgentManager` class:**
- `__init__(bot, session_manager, max_parallel=5, model=None, cwd=None, qmd_url=None)`
- `async spawn(user_id, task, context="", name=None) -> AgentRun` — creates an `AgentRun`, starts `asyncio.create_task(_run_agent(run))`, returns the `AgentRun` immediately; raises `RuntimeError` if `len(list_running(user_id)) >= max_parallel`
- `list_running(user_id) -> list[AgentRun]` — all `AgentRun` objects for `user_id` with `status="running"`
- `list_all(user_id) -> list[AgentRun]` — all `AgentRun` objects for `user_id`
- `cancel(run_id) -> bool` — cancels the asyncio task; sets `status="cancelled"`; returns `True` if found, `False` otherwise
- `get_run(run_id) -> AgentRun | None`
- `async stop_all() -> None` — cancels all running tasks; waits for completion

**`_run_agent(run)` internal:**
1. Create an isolated `ClaudeSession(model=model, cwd=cwd, qmd_url=qmd_url)`
2. `await session.start()`
3. Compose prompt: `f"Context:\n{run.context}\n\nTask:\n{run.task}"` (when context non-empty)
4. `async for event in session.send(prompt):`; collect the last `Response` content as result
5. `await session.stop()`
6. On success: set `run.status="completed"`, `run.result=result`; send Telegram `✅` notification; call `inject_context()` on main session (if still alive)
7. On `asyncio.CancelledError`: set `run.status="cancelled"`; re-raise
8. On other exception: set `run.status="failed"`, `run.error=str(exc)`; send Telegram `❌` notification

**Result injection format** (passed to `main_session.inject_context()`):
```
[Background agent {name} completed]
Task: {task}
Response:
{result}
[End agent {name}]
```

**Telegram notification format:**
- Success: `✅ Background agent **{name}** completed\n{result[:800]}`
- Failure: `❌ Background agent **{name}** failed\n{error[:400]}`
- Cancelled: no notification (user initiated)

**Name management:**
- Imports `_AGENT_NAMES` from `archon.ai.claude_session`
- Tracks names in use **globally across all users** in `_active_names: set[str]`
- `_assign_name(preferred=None) -> str` — picks requested name if available, else random from pool; falls back to `f"Agent-{run_id[:6]}"` if pool exhausted
- `_release_name(name) -> None` — removes from `_active_names` on completion/cancel

**Acceptance criteria:**
- `spawn()` returns an `AgentRun` with `status="running"` before the agent finishes
- `spawn()` creates an asyncio task (verifiable via `_task_ref is not None`)
- `list_running()` filters by `user_id` and `status=="running"`
- `list_all()` returns all for `user_id` regardless of status
- `cancel(run_id)` — cancels the task; `run.status` becomes `"cancelled"`; returns `True`
- `cancel(unknown)` returns `False`
- Successful run: `run.status="completed"`, `run.result` set, Telegram `✅` sent, `inject_context()` called
- Failed run: `run.status="failed"`, `run.error` set, Telegram `❌` sent
- `max_parallel` limit: 6th `spawn()` for same user raises `RuntimeError`
- `stop_all()` cancels all running tasks
- Name pool: each agent gets a unique name; no two concurrent agents share a name
- Name released on completion/failure/cancellation so it can be reused
- Tests: all above scenarios with mock `ClaudeSession` and mock bot; live test in S15.6

---

### S15.3 ✅ — ArchonMCPServer
**As a** developer,
**I want** a local HTTP MCP server that exposes the `spawn_background_agent` tool to Claude sessions,
**so that** Claude can call this tool via the MCP protocol to spawn background agents without using the native SDK Task mechanism.

**New file: `archon/ai/archon_mcp_server.py`**

**Protocol**: MCP over HTTP (`{"type": "http", "url": "..."}`) — JSON-RPC 2.0 requests
via POST, single endpoint per user.

**Class `ArchonMCPServer`:**
- `__init__(manager: BackgroundAgentManager, host: str = "localhost", port: int = 18182)`
- `async start() -> None` — starts `aiohttp.web.Application` on `host:port`; route: `POST /mcp/{user_id}`
- `async stop() -> None` — graceful shutdown of the aiohttp runner
- `mcp_url_for(user_id: int) -> str` — returns `f"http://{host}:{port}/mcp/{user_id}"`
- `async _handle_post(request: web.Request) -> web.Response` — dispatches JSON-RPC method

**JSON-RPC methods:**
- `initialize` → `{"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "archon-background-agents", "version": "1.0"}}`
- `tools/list` → `{"tools": [<spawn_background_agent descriptor>]}`
- `tools/call` with `name="spawn_background_agent"` → calls `manager.spawn(user_id, task, context, name?)` → `{"content": [{"type": "text", "text": "Agent {name} started (run_id: {run_id})"}], "isError": false}`
- Unknown method → JSON-RPC error `-32601 Method not found`
- Unknown tool name → JSON-RPC error `-32602 Invalid params` with descriptive message
- Missing required param `task` → JSON-RPC error `-32602`
- Max parallel exceeded → `isError: true` content with descriptive message (not a JSON-RPC error — it's a tool-level failure)
- Invalid JSON body → HTTP 400

**Tool descriptor (tools/list response):**
```json
{
  "name": "spawn_background_agent",
  "description": "Spawn a background agent to run a task asynchronously while the main conversation remains interactive. The agent runs in an isolated Claude session. When done, you receive its output as context in your next message.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task":    {"type": "string", "description": "The task for the agent to perform"},
      "context": {"type": "string", "description": "Relevant context or data the agent needs", "default": ""},
      "name":    {"type": "string", "description": "Optional human-readable name for the agent (random if omitted)"}
    },
    "required": ["task"]
  }
}
```

**Acceptance criteria:**
- `start()` / `stop()` — server starts and stops without error
- `mcp_url_for(42)` returns `"http://localhost:18182/mcp/42"` (or configured values)
- `initialize` → correct `protocolVersion` and `serverInfo`
- `tools/list` → returns exactly one tool with name `"spawn_background_agent"` and the above schema
- `tools/call spawn_background_agent` happy path → `manager.spawn()` called with correct args, returns started confirmation
- `tools/call` unknown tool → JSON-RPC `-32602` error
- `tools/call` missing `task` → JSON-RPC `-32602` error
- Unknown method → JSON-RPC `-32601` error
- Invalid JSON body → HTTP 400
- `user_id` correctly extracted from URL path and passed to `manager.spawn()`
- Tests: all JSON-RPC methods, error cases, user_id extraction; integration test with real `aiohttp` test client

---

### S15.4 ✅ — Gateway + SessionManager wiring
**As a** developer,
**I want** the `BackgroundAgentManager` and `ArchonMCPServer` wired into the gateway and `SessionManager`,
**so that** every user's main `ClaudeSession` can reach the background-agent MCP server and results are delivered correctly.

**Changes to `archon/ai/session_manager.py`:**
- `SessionManager.__init__` gains `background_agent_mcp_server: ArchonMCPServer | None = None` (stored as `self._bg_mcp_server`)
- `get_or_create(user_id)` — when `_bg_mcp_server` is set, pass `_bg_mcp_server.mcp_url_for(user_id)` to the session factory before creating the `ClaudeSession`
- Default factory gains `background_agent_mcp_url` and `spawn_rule` parameters (passed through to `ClaudeSession`)

**Changes to `archon/gateway/gateway.py`:**

In `_run()`:
1. Read `cfg.background_agents`
2. If `cfg.background_agents.enabled`:
   - Instantiate `BackgroundAgentManager(bot, session_manager, max_parallel, model, cwd, qmd_url)`
   - Instantiate `ArchonMCPServer(manager, cfg.background_agents.host, cfg.background_agents.port)`
   - `await mcp_server.start()`
   - Pass `background_agent_mcp_server=mcp_server` to `SessionManager`
   - Pass `spawn_rule=cfg.background_agents.spawn_rule` to `SessionManager` (stored, forwarded to factory)
3. In `finally` block: if `mcp_server` is not None, `await mcp_server.stop()`, `await manager.stop_all()`

`_setup_dp()` gains `background_agent_manager: BackgroundAgentManager | None = None` parameter:
- `dp["background_agent_manager"] = background_agent_manager`

**`pyproject.toml`:**
- Add `aiohttp>=3.9` to `[project].dependencies`

**Acceptance criteria:**
- When `background_agents.enabled=False`: `BackgroundAgentManager` and `ArchonMCPServer` not instantiated; no port opened
- When `background_agents.enabled=True`: MCP server started before bot polling; stopped in `finally`
- `SessionManager.get_or_create(user_id)` with `_bg_mcp_server` set passes correct URL to session factory
- Main `ClaudeSession` has `background_agent_mcp_url` set when feature enabled; `None` when disabled
- `background_agent_manager` accessible via `dp["background_agent_manager"]`
- `stop_all()` called for both manager and MCP server at shutdown
- Tests: gateway integration tests; `SessionManager` unit tests for URL passing

---

### S15.5 ✅ — `/running_agents` command
**As a** whitelisted user,
**I want** a `/running_agents` command that shows all my background agents and lets me cancel them,
**so that** I can monitor and control parallel background work without leaving Telegram.

**New command handler `running_agents_command`** in `archon/chat/commands.py`:
- Injected dependency: `background_agent_manager: BackgroundAgentManager | None`
- Fetches `manager.list_running(user_id)` for the requesting user
- Reply when no agents running: `"🤖 No background agents currently running."`
- Reply when feature disabled (manager is None): `"ℹ️ Background agent execution is not enabled."`
- Reply when agents are running: formatted list with inline keyboard

**Reply format (agents present):**
```
🤖 Running background agents (N):

• **{name}** — {task[:60]}... ({elapsed}s)
  ...
```
Inline keyboard: one `[Cancel {name}]` button per agent (callback data: `cancel_agent:{run_id}`).

**New callback handler `cancel_agent_callback`:**
- Parses `cancel_agent:{run_id}` from callback data
- `await manager.cancel(run_id)` → if `True`: edits message to `"✅ Agent {name} cancellation requested."`
- If `False` (not found): answers callback with `"⚠️ Agent not found (may have already completed)."`

**`archon/chat/bot.py`:**
- Add `BotCommand("/running_agents", "List and cancel running background agents")` to `BOT_COMMANDS`

**Dispatcher registration** in `create_dispatcher()`:
- Register `running_agents_command` for `/running_agents`
- Register `cancel_agent_callback` for callback queries matching `cancel_agent:*`

**Acceptance criteria:**
- `/running_agents` with no running agents → `"🤖 No background agents currently running."`
- `/running_agents` with manager disabled → `"ℹ️ Background agent execution is not enabled."`
- `/running_agents` with 2 running agents → list with 2 `[Cancel X]` buttons
- Tapping `[Cancel Atlas]` → `manager.cancel(run_id)` called; message edited to `"✅ Agent Atlas cancellation requested."`
- Tapping cancel for unknown run_id → callback answered with warning
- `/running_agents` appears in `BOT_COMMANDS` with correct description
- Tests: each state; cancel callback happy path and not-found; `BOT_COMMANDS` entry present

---

### S15.6 ✅ — Live E2E test
**As a** developer,
**I want** a live end-to-end test for the background agent system using a real `ClaudeSession` and `BackgroundAgentManager`,
**so that** I can verify the full async lifecycle works against the real Claude Agent SDK without any mocks.

**Prerequisites:**
- Test marked `@pytest.mark.live`; requires real `claude` binary
- No Telegram bot token required (bot notifications use a mock/no-op)
- `~/.archon/history/` and the working directory must be accessible

**Test scenarios (`tests/ai/test_background_agent_live.py`):**
1. **Happy path**: spawn agent with prompt `"Say 'done' and nothing else."` → within 30s `run.status == "completed"` and `run.result` is non-empty
2. **Context injection**: after completion, the mock main session's `inject_context()` was called with a string containing the agent name and result
3. **Cancel mid-flight**: spawn agent with a longer prompt → cancel before completion → `run.status == "cancelled"` within 2s; no inject_context called
4. **Multiple agents**: spawn 2 agents concurrently; both complete within 60s; names are distinct
5. **Max parallel**: with `max_parallel=1`, second spawn raises `RuntimeError`

**Acceptance criteria:**
- All 5 live test scenarios pass against real SDK
- Completed agents have `status="completed"` and non-empty `result`
- Cancelled agents have `status="cancelled"` promptly
- Names are unique across concurrent agents
- `inject_context()` called exactly once per successful completion (verifiable via spy)
