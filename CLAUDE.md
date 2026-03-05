# CLAUDE.md

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

**`archon/ai/`** — AI and background execution layer. Core runtime components (`Pipeline`, `ClaudeSession`, `EventMapper`, `SessionManager`, `BackgroundAgentManager`, `ArchonMCPServer`, `CronScheduler`, `TruncationStrategy`) are documented in [README.md — Architecture](README.md#architecture) and [Component Catalog](Documentation/Architecture/110_component_catalog_and_layer_breakdown.md). Additional modules:
- `Pipeline`: multi-agent routing — Classifier (Haiku) classifies intent, Decomposer (user-selected model) handles the request. Duck-types as `ClaudeSession`.
- `Classification` + `parse_classification()`: classification schema and resilient JSON parser (defaults to `task` on any failure)
- `prompts/`: system prompt files (`classifier.md`, `decomposer.md`) loaded via `load_prompt()`
- `agent_plan.py`: `AgentPlan` + `AgentTask` dataclasses; `parse_agent_plan()` detects large-scope plans in Decomposer output; `validate_dependency_graph()` + `topological_sort()` produce execution waves (Phase 2 multi-agent)
- `plan_executor.py`: `PlanExecutor` — resolves dependency graph, spawns workers via `BackgroundAgentManager` wave-by-wave, waits on `AgentRun.done`, delivers plan start/completion Telegram notifications; always runs as a detached asyncio task
- `stt.py`: `STTHandler` — async speech-to-text via Whisper CLI subprocess; auto-detects binary (Homebrew/PATH); supports all Whisper model sizes and optional language hint; `transcribe_with_timeout()` for safety
- `tts.py`: `TTSHandler` + `TTSConfig` — text-to-speech via OpenAI TTS API (Opus, round-bubble in Telegram) or Edge TTS CLI (MP3, free fallback); `should_synthesize()` respects `auto` mode (`always`/`inbound`/`off`)
- `SkillLoader`: reads `~/.claude/skills/*/SKILL.md` (YAML frontmatter: name, description)
- `PluginLoader`: reads `~/.claude/plugins/` + `settings.json`; exposes SDK configs and skills
- `AgentLoader`: reads `~/.claude/agents/*.md`; `-archon` suffix → injected into sessions
- `HistoryManager`: appends conversation turns to `~/.archon/history/sessions/YYYY-MM-DD.md`
- `AgentLogger`: writes per-agent events to `~/.archon/history/sessions/YYYY-MM-DD-HH-MM-{name}.md`

**`archon/chat/`** — aiogram 3.x bot with whitelist middleware (drops non-whitelisted user IDs before any handler runs, for both `Message` and `CallbackQuery`). Message handler calls `async for event in pipeline.send(text):` and sends each formatted event to Telegram, with a live typing indicator while Claude works. Bot commands: `/start`, `/status`, `/context`, `/stop`, `/clear`, `/restart`, `/notify`, `/quiet`, `/normal`, `/verbose`, `/debug`, `/settings`, `/skills`, `/skill`, `/model`, `/agents`, `/jobs`, `/running_agents`. Inline keyboard callbacks: `notify:<mode>`, `model:<name>`, `cancel_agent:<id>`.
- `voice.py`: `VoiceMessageHandler` — downloads Telegram voice/audio files, transcribes via `STTHandler`, routes transcribed text through the existing text message handler, optionally generates a TTS voice-note reply via `TTSHandler`; registered in `gateway.py` when `[voice] enabled = true`

**`archon/gateway/`** — orchestrator: initializes config and logging, starts bot and session manager, routes events bidirectionally, handles SIGTERM/SIGINT graceful shutdown (`stop_all()` → bot disconnect, ≤5s).

**`main.py`** — single entry point: `Gateway.start()`.

## Output event model

Every Claude state change produces a Telegram notification. Thinking is merged into a single message.

| Event dataclass | Telegram format |
|---|---|
| `ClassificationEvent` | `🏷 task (95%)` (verbose/debug only) |
| `ThinkingResult` | `💭 Thinking:\n<content>` |
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
- `[voice] enabled` (default `false`); `[voice.stt] model` (default `"medium"`), `language` (default `null` = auto); `[voice.tts] provider` (`"openai"`/`"edge"`), `model`, `voice`, `auto` (`"always"`/`"inbound"`/`"off"`), `max_text_length`, `edge_voice`

## Key constraints

- TDD is mandatory — write tests before implementation. Maintain ≥85% coverage.
- All modules use `logging.getLogger("archon")` — no `print()`.
- The whitelist check must happen in middleware before any handler runs — never inside handlers.
- New truncation strategies only require adding a class in `ai/` — no changes to gateway or chat.
- `stop_all()` must complete within 5 seconds.
- Always use KISS as the first principle (apply to code implementation, not to required functionality)
- You MUST use SOLID and Clean Code principles, but still keep it simple, do NOT overcomplicate.
- **CRITICAL**: You must NEVER make assumptions. All statements must be based on verified facts.
- **KISS principle** - Simplicity is mandatory
- Increase complexity step-by-step; use best practices when they simplify rather than complicate
- All tests always MUST be passed.
