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

## 3. Core Features

### 3.1 Chat Integration (Telegram)
- Telegram bot powered by **aiogram 3.x**
- Incoming messages from whitelisted users are forwarded to the AI layer
- Non-whitelisted users receive a silent ignore or generic rejection
- Supports basic bot commands: `/start`, `/status`, `/stop` (kill current Claude session)

### 3.2 AI Integration (Claude Code via PTY)
- Claude Code is launched as a **PTY subprocess** with `--dangerously-skip-permissions`
- One persistent Claude session per whitelisted Telegram user
- Session is started on first message, kept alive between messages (maintains context)
- Sessions are recycled on `/stop` command or configurable inactivity timeout

### 3.3 Output Streaming (Logical Boundaries)
Every state transition generates an **immediate** Telegram notification, mirroring the terminal experience. Each event type produces up to two messages: a START (instant) and a RESULT (when available).

| Event | Telegram prefix | When sent |
|---|---|---|
| Thinking started | 💭 **Thinking...** | Immediately when a thinking block begins |
| Thinking result | 💭 **Thought:** | When thinking block ends (transition to tool or response) |
| Tool started | 🔧 **Tool:** `<name>` | Immediately when Claude begins a tool call |
| Tool result | 📤 **Result:** | When tool execution completes |
| Final response | ✅ **Response** | When Claude's text response is complete |
| Error | ❌ **Error** | On crash, timeout, or session failure |

Long outputs (> 4000 chars) are handled by a pluggable **TruncationStrategy**:

| Strategy | Behavior | Status |
|---|---|---|
| `split` | Chunk output into ≤4000-char pages, send all sequentially as `[1/N]`, `[2/N]`… | **MVP** |
| `head_tail` | Keep first N + last M chars, insert `…[X lines omitted]…` in the middle | Future |

Active strategy is set in `config.toml` (`output.truncation_strategy = "split"`). Adding new strategies requires no changes outside the `ai/` module.

### 3.4 Gateway (Orchestrator)
- Starts and connects the chat and AI layers on daemon boot
- Routes messages bidirectionally: Telegram → Claude, Claude output → Telegram
- Handles session lifecycle (create, reuse, destroy)
- Graceful shutdown on SIGTERM/SIGINT

### 3.5 Daemon (Local Service)
- Runs as a **launchd** service on macOS (systemd unit file provided for Linux)
- Auto-starts on login
- Logs to a rotating file

---

## 4. Architecture

```
archon/
├── chat/           # Telegram bot: message routing, whitelist, command handlers
├── ai/             # PTY session manager, output parser, session lifecycle
├── gateway/        # Orchestrator: connects chat ↔ AI, event loop
├── config/         # Config loader (.env + config.toml)
└── main.py         # Entry point
```

**Tech stack:**
- Python 3.12+ managed with **uv**
- Telegram: `aiogram 3.x`
- PTY: `ptyprocess`
- Config: `.env` (secrets) + `config.toml` (structured config)
- Daemon: launchd plist / systemd unit

---

## 5. Configuration

**`.env`** — secrets only:
```
TELEGRAM_BOT_TOKEN=...
```

**`config.toml`** — structured config:
```toml
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "/Users/you/projects/myproject"
inactivity_timeout_seconds = 1800  # 30 min

[output]
max_message_length = 4000          # Telegram limit is 4096
truncation_strategy = "split"      # "split" | "head_tail"
head_chars = 1500                  # used by head_tail strategy
tail_chars = 1500                  # used by head_tail strategy
```

---

## 6. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Latency (first chunk to Telegram) | < 2 seconds after Claude starts outputting |
| Reliability | Auto-reconnect Telegram bot on network drop |
| Security | Whitelist enforced before any message reaches Claude |
| Logging | Rotating file log, INFO level by default, DEBUG configurable |
| Test coverage | ≥ 85% (TDD) |

---

## 7. Out of Scope (Phase 1)

- Multi-AI support (GPT, Gemini, etc.)
- File/image upload to Claude via Telegram
- Web dashboard or API
- Multi-project switching via chat commands
- Cloud deployment
- Telegram inline keyboards / rich UI

---

## 8. Success Criteria

- [ ] Send a message in Telegram → Claude Code receives and processes it
- [ ] All output events (tool calls, thinking, response) arrive in Telegram in real-time with correct labels
- [ ] Session persists across multiple messages (conversational context maintained)
- [ ] Daemon survives machine restart (launchd auto-restart)
- [ ] Only whitelisted Telegram users can interact with the bot
- [ ] `/stop` cleanly kills the active Claude session
