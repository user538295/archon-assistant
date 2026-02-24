# Archon Assistant — Development Tasks

## Context

Read these files before working on any task:
- `high_level_concept.md` — concept and architecture decisions
- `prd.md` — full product requirements
- `stories.md` — all user stories with acceptance criteria
- `CLAUDE.md` — dev commands, architecture overview, constraints

Implementation order: `S0.1 → S0.2 → S5.7 → S4.1 → S1.1 → S1.2 → S1.3 → S5.1 → S5.5 → S1.4 → S2.1 → S2.2 → S2.3 → S2.4 → S2.5 → S2.6 → S5.2 → S3.1 → S5.3 → S3.2 → S5.4 → S4.2 → S5.6 → S7.1 → S8.1 → S8.2 → S8.3 → S8.4 → S6.1 → S6.2 → S4.4 → S9.1 → S10.1 → S11.1 → S11.2`

---

## Tasks

### Epic 0: Project Setup

- [x] **S0.1** — Initialize project structure (`stories.md` § S0.1)
- [x] **S0.2** — Config loader (`stories.md` § S0.2, `prd.md` § 5)
- [x] **S5.7** — Live unit test: config loader — real tmp files, no mocks, `@pytest.mark.live` (`stories.md` § S5.7)

### Epic 4 (partial): Daemon — Logging

- [x] **S4.1** — Logging: rotating file handler, configurable path/level, `logging.getLogger("archon")` in all modules (`stories.md` § S4.1, `prd.md` § 6)
- [x] **S4.4** — Daily log rotation: `TimedRotatingFileHandler(when="midnight")`, custom `_daily_log_namer` (`archon.log.YYYY-MM-DD` → `archon.YYYY-MM-DD.log`), `_rotate_on_startup` for crash/stop-before-midnight edge case (`stories.md` § S4.4)

### Epic 1: AI Module

- [x] **S1.1** — Claude session (SDK): `ClaudeSession` wrapping `ClaudeSDKClient`, `start()` / `send()` / `stop()` / `is_alive` (`stories.md` § S1.1, `prd.md` § 3.2)
- [x] **S1.2** — Event mapper: translate SDK messages to archon event dataclasses (`stories.md` § S1.2, `prd.md` § 3.3)
- [x] **S1.3** — Truncation strategy: `TruncationStrategy` ABC + `SplitStrategy` MVP (`stories.md` § S1.3, `prd.md` § 3.3)
- [x] **S5.1** — AI pipeline integration: `FakeClaudeClient` (SDK message stream) → `EventMapper` → all 6 event types + truncation, no internal mocks (`stories.md` § S5.1)
- [x] **S5.5** — Live Claude Agent SDK test (`@pytest.mark.live`): real `claude` binary + SDK, trivial prompt, verify `Response` event within 30s (`stories.md` § S5.5)
- [x] **S1.4** — Session manager: per-user `ClaudeSession` registry, inactivity timeout, `stop_all()` (`stories.md` § S1.4, `prd.md` § 3.2)

### Epic 2: Chat Module

- [x] **S2.1** — Telegram bot bootstrap: aiogram 3.x, `/start` command (`stories.md` § S2.1, `prd.md` § 3.1)
- [x] **S2.2** — Whitelist middleware: drop non-whitelisted users before handlers (`stories.md` § S2.2, `prd.md` § 3.1)
- [x] **S2.3** — Message handler + event formatter: `async for event in session.send(text):` → formatted Telegram messages (`stories.md` § S2.3, `prd.md` § 3.3)
- [x] **S2.4** — Bot commands: `/status` and `/stop` (`stories.md` § S2.4, `prd.md` § 3.1)
- [x] **S2.5** — Clear command: `/clear` stops current session and immediately starts a fresh one (`stories.md` § S2.5)
- [x] **S2.6** — Telegram command menu: `BOT_COMMANDS` list + `setup_bot_commands(bot)` in `bot.py`, startup hook in `Gateway._run()` via `dp.startup.register`, `BotCommandScopeAllPrivateChats` scope (`stories.md` § S2.6)
- [x] **S5.2** — Chat + AI integration: aiogram `Dispatcher` + `WhitelistMiddleware` + message handler + `SessionManager` + mock `ClaudeSession` (`stories.md` § S5.2)

### Hardening

- [x] **H1** — Config validation: fail-fast on invalid values — `inactivity_timeout_seconds > 0`, `max_message_length > 0`, non-empty `allowed_user_ids`, `working_directory` must exist; raise `ConfigError` with clear message; add tests in `tests/config/test_loader.py`
- [x] **H2** — Non-happy path tests: invalid config values (`tests/config/test_loader.py`) + concurrent `SessionManager.get_or_create()` for same user must not double-start (`tests/ai/test_session_manager.py`)
- [x] **H3** — Gateway must register `WhitelistMiddleware`: when implementing S3.1, wire `dp.message.middleware(WhitelistMiddleware(allowed_user_ids=config.access.allowed_user_ids))` — `create_dispatcher()` intentionally does not do this
- [x] **H4** — Test coverage gap closure: audit all modules for untested branches; add tests for `PluginsConfig` fields, `SessionManager` model management and factory behaviour, `ClaudeSession` plugins/model property/error handling, bot dispatcher registration, command handler edge cases, gateway init/config, `PluginLoader` JSON corruption, `HistoryManager` responses lacking prior questions, `SplitStrategy` empty string, smoke tests for `main.py` entry point — target ≥ 98% overall coverage (`docs/test_gap_report.md`)

### Epic 3: Gateway

- [x] **S3.1** — Gateway core: wire bot + session manager in single asyncio loop, `main.py` entry point (`stories.md` § S3.1, `prd.md` § 3.4)
- [x] **S5.3** — Full message flow e2e: gateway with stubbed bot + scripted SDK client, verify exact Telegram reply sequence and log output (`stories.md` § S5.3)
- [x] **S3.2** — Graceful shutdown: SIGTERM/SIGINT → `stop_all()` → bot disconnect within 5s (`stories.md` § S3.2, `prd.md` § 3.4)
- [x] **S5.4** — Graceful shutdown e2e: SIGINT → `stop_all()` → bot disconnect within 5s, verify log messages (`stories.md` § S5.4)

### Epic 4: Daemon — Service Install

- [x] **S4.2** — launchd service (macOS): `make install/uninstall/logs`, plist with `KeepAlive` (`stories.md` § S4.2, `prd.md` § 3.5)
- [x] **S4.3** *(bonus)* — systemd service (Linux): unit file, `make install-linux/uninstall-linux` (`stories.md` § S4.3)
- [x] **S5.6** — Live full-stack e2e (`@pytest.mark.live @pytest.mark.requires_telegram`): real Gateway + real Telegram API + real Claude Agent SDK, verify `✅ Response:` delivered to `TELEGRAM_LIVE_CHAT_ID` within 60s (`stories.md` § S5.6)

### Epic 7: Memory & History

- [x] **S7.1** — Chat history persistence: daily `~/.archon/history/YYYY-MM-DD.md`, `HistoryManager`, `HistoryConfig`, Contextual Retrieval (user question blockquote in Response), QMD-compatible Markdown format (`stories.md` § S7.1)

### Epic 8: Notification Mode Redesign

- [x] **S8.1** — Four named modes: replace `NotificationsConfig` 4-field design with `mode`/`interval_minutes`, update `format_event` visibility matrix, update `load_config` + `save_notifications_config` with migration from old keys (`stories.md` § S8.1)
- [x] **S8.2** — Quiet beacon mode: `interval_minutes > 0` fires periodic `⏳ Working…` in quiet mode, `0` = no beacon, cancel on completion (`stories.md` § S8.2)
- [x] **S8.3** — Inline keyboard: `/notify` + `/settings` show 2×2 mode panel, callback handler edits in-place, whitelist extended to `dp.callback_query` (`stories.md` § S8.3)
- [x] **S8.4** — Quick-switch commands: `/quiet [N]`, `/normal`, `/verbose`, `/debug` registered in dispatcher + `BOT_COMMANDS`, `/notify <mode> [N]` text subcommands work identically (`stories.md` § S8.4)

### Epic 6: Skills Integration

- [x] **S6.1** — Skills integration: `SkillLoader` (`archon/ai/skill_loader.py`), compact registry in `ClaudeSession` system prompt via `ClaudeAgentOptions.system_prompt`, one-shot skill activation via `ClaudeSession.activate_skill()`, `/skills` and `/skill <name>` Telegram commands (`stories.md` § S6.1)
- [x] **S6.2** — Live skill loader test (`@pytest.mark.live`): real `~/.claude/skills/` dir, verify `load_all()` / `get()` / `get("nonexistent")` — no mocks (`stories.md` § S6.2)

### Epic 9: Model Management

- [x] **S9.1** — Model selector: `ModelsConfig` (`available` list + `default`) in `config/loader.py`, `/model` command with inline keyboard, `model_callback` switches `SessionManager` model in-place, `BOT_COMMANDS` entry (`stories.md` § S9.1)

### Epic 10: Plugin Support

- [x] **S10.1** — Claude Code plugin loading: `PluginLoader` (`archon/ai/plugin_loader.py`) reads `~/.claude/plugins/installed_plugins.json` + `~/.claude/settings.json`; `get_sdk_configs()` for `ClaudeAgentOptions.plugins`; `get_skills()` for namespaced plugin skills; `PluginsConfig` in `config/loader.py`; `SessionManager` wired with `plugin_loader`; `/skills` shows plugin-bundled skills; `plugins.enabled` flag respected (`stories.md` § S10.1)

### Epic 11: Context Tracking & Sub-agents

- [x] **S11.1** — Context window usage: `ClaudeSession._intercept()` captures `ResultMessage` metadata; `usage_stats` property; `SessionManager.context_stats(user_id)`; `/context` command with Unicode progress bar, per-category token counts, accumulated cost, turn count, last duration (`stories.md` § S11.1)
- [x] **S11.2** — Sub-agent team configuration: `AgentDefinitionConfig` + `AgentsConfig` dataclasses + TOML parsing; `_build_sdk_agents()` → `dict[str, AgentDefinition]`; `ClaudeSession` agents param + `_build_hooks()` with side-channel `asyncio.Queue`; `SubagentStarted` / `SubagentStopped` event types; `format_event` for subagent events (suppressed in quiet mode); `/agents` command; `BOT_COMMANDS` entry; gateway wiring; `tests/ai/test_subagent_integration.py` (`stories.md` § S11.2)
