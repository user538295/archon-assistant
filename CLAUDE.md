# CLAUDE.md

**Purpose**: AI assistant operating instructions and architecture reference for Claude Code
**Audience**: Claude Code AI
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

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

Three modules wired together by a gateway, all running in a single asyncio event loop.

> **Architecture diagram**: See [System Architecture Overview](Documentation/Architecture/100_system_architecture_overview.md) for the full diagram, C4 views, and data-flow diagrams. A compact reference is also in [README.md — Architecture](README.md#architecture).

**Architecture docs**: See [Documentation/Architecture/](Documentation/Architecture/) for full system design, or start with [000_introduction_and_guiding_principles.md](Documentation/Architecture/000_introduction_and_guiding_principles.md). Coding standards are in [500_development_workflows_and_conventions.md](Documentation/Architecture/500_development_workflows_and_conventions.md); the C4 system design is in [100_system_architecture_overview.md](Documentation/Architecture/100_system_architecture_overview.md).

**`archon/config/`** — loads `.env` (bot token) + `config.toml` (everything else) into a typed singleton at startup. All modules import `from archon.config import config`. Raises `ConfigError` on missing required fields.

**`archon/ai/`** — AI and background execution layer. Core runtime components (`ClaudeSession`, `EventMapper`, `SessionManager`, `BackgroundAgentManager`, `ArchonMCPServer`, `CronScheduler`, `TruncationStrategy`) are documented in [README.md — Architecture](README.md#architecture) and [Component Catalog](Documentation/Architecture/110_component_catalog_and_layer_breakdown.md). Additional modules:
- `SkillLoader`: reads `~/.claude/skills/*/SKILL.md` (YAML frontmatter: name, description)
- `PluginLoader`: reads `~/.claude/plugins/` + `settings.json`; exposes SDK configs and skills
- `AgentLoader`: reads `~/.claude/agents/*.md`; `-archon` suffix → injected into sessions
- `HistoryManager`: appends conversation turns to `~/.archon/history/YYYY-MM-DD.md`
- `AgentLogger`: writes per-agent events to `YYYY-MM-DD-HH-MM-{name}.md`

**`archon/chat/`** — aiogram 3.x bot with whitelist middleware (drops non-whitelisted user IDs before any handler runs, for both `Message` and `CallbackQuery`). Message handler calls `async for event in session.send(text):` and sends each formatted event to Telegram, with a live typing indicator while Claude works. Bot commands: `/start`, `/status`, `/context`, `/stop`, `/clear`, `/restart`, `/notify`, `/quiet`, `/normal`, `/verbose`, `/debug`, `/settings`, `/skills`, `/skill`, `/model`, `/agents`, `/jobs`, `/running_agents`. Inline keyboard callbacks: `notify:<mode>`, `model:<name>`, `cancel_agent:<id>`.

**`archon/gateway/`** — orchestrator: initializes config and logging, starts bot and session manager, routes events bidirectionally, handles SIGTERM/SIGINT graceful shutdown (`stop_all()` → bot disconnect, ≤5s).

**`main.py`** — single entry point: `Gateway.start()`.

## Output event model

Every Claude state change produces a Telegram notification. Thinking is merged into a single message.

| Event dataclass | Telegram format |
|---|---|
| `ThinkingResult` | `💭 Thinking complete:\n<content>` |
| `ToolStarted(name, input)` | `🔧 Tool: <name>` + input summary |
| `ToolResult` | `📤 Result:\n<content>` |
| `Response` | `✅ Response:\n<content>` |
| `ErrorEvent` | `❌ Error: <message>` |
| `SubagentStarted` | `🤖 Agent <b>Name</b> started` |
| `SubagentStopped` | `🤖 Agent <b>Name</b> done` |

Content-bearing events pass through `TruncationStrategy` before sending.

## Configuration

`.env` — `TELEGRAM_BOT_TOKEN` only.

`config.toml` sections and key fields:
- `[access] allowed_user_ids`
- `[session] working_directory`, `inactivity_timeout_seconds`
- `[output] max_message_length`, `truncation_strategy`
- `[notifications] mode` (`quiet`/`normal`/`verbose`/`debug`), `interval_minutes`; `[notifications.agents] mode`
- `[logging] log_file`, `log_level`
- `[history] enabled`, `directory`
- `[models] available`, `default`
- `[plugins] enabled`, `plugins_dir`, `settings_path`
- `[qmd] enabled`, `host`, `port`, `history_collection`
- `[cron] enabled`, `jobs_dir` — per-job TOML files in `jobs_dir/`
- `[background_agents] spawn_rule`, `max_parallel`, `host`, `port`, `beacon_interval_minutes`

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
- Use Clean Code principle without violating the KISS
- All tests always MUST be green
