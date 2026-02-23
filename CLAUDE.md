# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Archon Assistant — a local daemon that bridges Telegram with Claude Code via the Claude Agent SDK, forwarding every state transition as a real-time Telegram notification.

## Commands

```bash
# Install dependencies
uv sync

# Run the daemon
uv run python main.py

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/ai/test_event_mapper.py

# Run a single test by name
uv run pytest -k "test_split_strategy_labels"

# Type check
uv run mypy archon/

# Install as launchd service (macOS)
make install

# Uninstall service
make uninstall

# Tail logs
make logs
```

## Architecture

Three modules wired together by a gateway, all running in a single asyncio event loop:

```
Telegram ──▶ Gateway ──▶ SessionManager ──▶ ClaudeSession (per user)
   ▲               │             │
   └───────────────┘             └──▶ EventMapper ──▶ TruncationStrategy
```

**`archon/config/`** — loads `.env` (bot token) + `config.toml` (everything else) into a typed singleton at startup. All modules import `from archon.config import config`. Raises `ConfigError` on missing required fields.

**`archon/ai/`** — three layered components:
- `ClaudeSession`: wraps `ClaudeSDKClient` from `claude-agent-sdk`; `start()` connects, `send(prompt)` is an async generator yielding archon event dataclasses, `stop()` disconnects
- `EventMapper`: maps SDK messages (`AssistantMessage`, `UserMessage`, `ResultMessage`) to typed event dataclasses (`ThinkingStarted`, `ThinkingResult`, `ToolStarted`, `ToolResult`, `Response`, `ErrorEvent`)
- `SessionManager`: maintains a `user_id → ClaudeSession` registry; creates sessions on demand, evicts on inactivity timeout or explicit `/stop`
- `TruncationStrategy`: ABC with `apply(text, max_len) -> list[str]`; active strategy selected from config. `SplitStrategy` (MVP) chunks into ≤4000-char pages labeled `[1/N]`.

**`archon/chat/`** — aiogram 3.x bot with whitelist middleware (drops non-whitelisted user IDs before any handler runs, for both `Message` and `CallbackQuery`). Message handler calls `async for event in session.send(text):` and sends each formatted event to Telegram, with a live typing indicator while Claude works. Bot commands: `/start`, `/status`, `/stop`, `/clear`, `/restart`, `/notify`, `/quiet`, `/normal`, `/verbose`, `/debug`, `/settings`. Inline keyboard callbacks (`notify:<mode>`) are handled by `notify_callback`.

**`archon/gateway/`** — orchestrator: initializes config and logging, starts bot and session manager, routes events bidirectionally, handles SIGTERM/SIGINT graceful shutdown (`stop_all()` → bot disconnect, ≤5s).

**`main.py`** — single entry point: `Gateway.start()`.

## Output event model

Every Claude state change produces two Telegram messages: an immediate START and a RESULT when done.

| Event dataclass | Telegram format |
|---|---|
| `ThinkingStarted` | `💭 Thinking...` |
| `ThinkingResult` | `💭 Thought:\n<content>` |
| `ToolStarted(name, input)` | `🔧 Tool: <name>` + input summary |
| `ToolResult` | `📤 Result:\n<content>` |
| `Response` | `✅ Response:\n<content>` |
| `ErrorEvent` | `❌ Error: <message>` |

Content-bearing events pass through `TruncationStrategy` before sending.

## Configuration

`.env` — `TELEGRAM_BOT_TOKEN` only.

`config.toml` keys:
- `[access] allowed_user_ids` — whitelist of Telegram user IDs
- `[session] working_directory`, `inactivity_timeout_seconds`
- `[output] max_message_length`, `truncation_strategy`, `head_chars`, `tail_chars`
- `[notifications] mode` (`quiet`/`normal`/`verbose`/`debug`), `interval_minutes` (beacon interval in quiet mode; `0` = no beacon)
- `[logging] log_file`, `log_level`

## Key constraints

- TDD is mandatory — write tests before implementation. Maintain ≥85% coverage.
- All modules use `logging.getLogger("archon")` — no `print()`.
- The whitelist check must happen in middleware before any handler runs — never inside handlers.
- New truncation strategies only require adding a class in `ai/` — no changes to gateway or chat.
- `stop_all()` must complete within 5 seconds.
- Always use KISS as the first principle (apply to code implementation, not to required functionality)
- **CRITICAL**: You must NEVER make assumptions. All statements must be based on verified facts.
- **KISS principle** - Simplicity is mandatory
- Increase complexity step-by-step; use best practices when they simplify rather than complicate
- Use Clean Code principle without to violate the KISS
- All tests always MUST be green
