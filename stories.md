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

### S1.1 — Claude session (SDK)
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

### S1.2 — Event mapper
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

### S2.5 — Clear command
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
**so that** no Claude SDK sessions are left open.

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
**I want** an integration test that drives `EventMapper` with a scripted fake SDK message stream,
**so that** I can verify the full AI mapping pipeline without mocking individual methods.

**Acceptance criteria:**
- A fake message sequence (constructed SDK dataclass objects) covers all six event types
- `EventMapper.map_messages(fake_stream)` is awaited and the emitted event sequence matches expected types and content
- Tests cover: `ThinkingStarted`, `ThinkingResult`, `ToolStarted`, `ToolResult`, `ErrorEvent`, `Response` — at least one of each in a single run
- `SplitStrategy` truncation is applied to a content-bearing event to confirm the full AI-layer chain works
- No mocking of internal methods — only the SDK client boundary is substituted

---

### S5.2 — Chat + AI integration test
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

### S5.3 — Full message flow e2e test
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

### S5.4 — Graceful shutdown e2e test
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

### S5.5 — Live Claude Agent SDK test
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

### S5.6 — Live full-stack e2e test
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
                              S2.1 → S2.2 → S2.3 → S2.4 → S2.5 → S5.2 → S3.1 → S5.3 → S3.2 → S5.4 → S4.2 → S5.6
                                                                                                         ↓
                                                                                                   S7.1 → S6.1 → S6.2
```

**Key:**
- S5.7 (live config unit test) immediately follows S0.2 — same component, real files
- S5.1 (AI pipeline integration) + S5.5 (live SDK test) follow S1.3 — need mapper and truncation
- S1.4 (session manager) follows S5.5 — all AI components stable
- S5.2 (chat+AI integration) follows S2.4 — all chat components present
- S5.3 (full e2e mocked) follows S3.1 — gateway wired
- S5.4 (shutdown e2e) follows S3.2 — shutdown implemented
- S5.6 (live full-stack) follows S4.2 — service install complete
- S6.1 (skills integration) follows S5.6 — full stack proven stable before adding skill layer
- S6.2 (live skill loader test) immediately follows S6.1 — same component, real filesystem

> S4.1 (logging) is done early so all subsequent stories use it from the start.
> Tests are woven into the implementation flow — each test story immediately follows the story that satisfies its dependencies.
> S5.7 is a live unit test: single component, real filesystem, no mocks, no credentials needed.
> S5.1 is an integration test with mocks/stubs at the SDK client boundary.
> S5.5 requires real `claude` binary.
> S5.6 requires real `claude` binary and Telegram credentials.
> Live tests (`@pytest.mark.live`) are excluded from `uv run pytest`; run with `uv run pytest -m live`.

---

## Epic 7: Memory & History

### S7.1 — Chat history persistence (QMD-compatible)
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

### S6.1 — Skills integration
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

### S6.2 — Live skill loader test
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
