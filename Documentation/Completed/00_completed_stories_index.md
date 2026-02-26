# Completed Stories Index

**Purpose**: Navigation index of all completed user stories in the Archon project
**Audience**: All developers
**Status**: Stable — all listed stories complete
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

---

This index lists every completed story with its one-line description. For full acceptance criteria, technical notes, and test specifications, follow the links to the individual epic files.

---

## Epic 0: Project Setup

→ [13_epic0_project_setup.md](./13_epic0_project_setup.md)

| Story | Description |
|-------|-------------|
| S0.1 ✅ | Initialize project structure — Python 3.12, uv, folder structure, pytest |
| S0.2 ✅ | Config loader — typed config from `.env` + `config.toml`, `ConfigError` on startup |

## Epic 1: AI Module

→ [14_epic1_ai_module.md](./14_epic1_ai_module.md)

| Story | Description |
|-------|-------------|
| S1.1 ✅ | Claude session (SDK) — `ClaudeSession` wrapping `ClaudeSDKClient`, typed event streaming |
| S1.2 ✅ | Event mapper — SDK messages → archon event dataclasses (`ThinkingResult`, `ToolStarted`, `Response`, etc.) |
| S1.3 ✅ | Truncation strategy — `TruncationStrategy` ABC, `SplitStrategy` (chunk labels `[1/N]`) |
| S1.4 ✅ | Session manager — per-user `ClaudeSession`, inactivity timeout, `stop_all()` |

## Epic 2: Chat Module

→ [15_epic2_chat_module.md](./15_epic2_chat_module.md)

| Story | Description |
|-------|-------------|
| S2.1 ✅ | Telegram bot bootstrap — aiogram 3.x bot, `/start` command |
| S2.2 ✅ | Whitelist middleware — non-whitelisted messages silently dropped |
| S2.3 ✅ | Message handler + event formatter — user messages → `ClaudeSession.send()`, event → Telegram |
| S2.4 ✅ | Bot commands — `/status`, `/stop` |
| S2.5 ✅ | Clear command — `/clear` starts a fresh context window |
| S2.6 ✅ | Telegram command menu — `setMyCommands`, all 18 commands in the 📋 menu |

## Epic 3: Gateway

→ [16_epic3_gateway.md](./16_epic3_gateway.md)

| Story | Description |
|-------|-------------|
| S3.1 ✅ | Gateway core — wires Telegram bot + session manager in a single asyncio loop |
| S3.2 ✅ | Graceful shutdown — SIGTERM/SIGINT → `stop_all()` within 5 seconds |

## Epic 4: Daemon

→ [17_epic4_daemon.md](./17_epic4_daemon.md)

| Story | Description |
|-------|-------------|
| S4.1 ✅ | Logging — structured rotating log files, configurable level |
| S4.2 ✅ | launchd service (macOS) — `make install`, plist with `KeepAlive` |
| S4.3 ✅ | systemd service (Linux) — `Restart=on-failure` unit file |
| S4.4 ✅ | Daily log rotation — `TimedRotatingFileHandler`, startup rotation, `_daily_log_namer` |

## Epic 5: Integration & E2E Tests

→ [18_epic5_integration_tests.md](./18_epic5_integration_tests.md)

| Story | Description |
|-------|-------------|
| S5.1 ✅ | AI pipeline integration test — `EventMapper` driven by scripted fake SDK stream |
| S5.2 ✅ | Chat + AI integration test — whitelist middleware + handler + mock `ClaudeSession` |
| S5.3 ✅ | Full message flow e2e test — gateway with mocked Telegram and scripted SDK |
| S5.4 ✅ | Graceful shutdown e2e test — SIGINT → all sessions stopped cleanly |
| S5.5 ✅ | Live Claude Agent SDK test — real `claude` binary, `@pytest.mark.live` |
| S5.6 ✅ | Live full-stack e2e test — real Telegram API + real Claude Agent SDK |
| S5.7 ✅ | Live unit test: config loader — real temp files, no mocks |

## Epic 6: Skills Integration

→ [19_epic6_skills.md](./19_epic6_skills.md)

| Story | Description |
|-------|-------------|
| S6.1 ✅ | Skills integration — `SkillLoader`, `/skill` command, one-shot system-reminder injection |
| S6.2 ✅ | Live skill loader test — real `~/.claude/skills/` directory, `@pytest.mark.live` |

## Epic 7: Memory & History

→ [20_epic7_history.md](./20_epic7_history.md)

| Story | Description |
|-------|-------------|
| S7.1 ✅ | Chat history persistence (QMD-compatible) — `HistoryManager`, daily `.md` files |

## Epic 8: Notification Mode Redesign

→ [21_epic8_notification_modes.md](./21_epic8_notification_modes.md)

| Story | Description |
|-------|-------------|
| S8.1 ✅ | Four named notification modes — quiet, normal, verbose, debug |
| S8.2 ✅ | Quiet beacon mode — periodic `⏳ Working…` heartbeat in quiet mode |
| S8.3 ✅ | Inline keyboard for `/notify` and `/settings` — 2×2 mode-switch panel |
| S8.4 ✅ | Quick-switch mode commands — `/quiet [N]`, `/normal`, `/verbose`, `/debug` |

## Epic 9: Model Management

→ [22_epic9_model_management.md](./22_epic9_model_management.md)

| Story | Description |
|-------|-------------|
| S9.1 ✅ | Model selector — `/model` command, `ModelsConfig`, runtime model switching |

## Epic 10: Plugin Support

→ [12_plugin_support_implementation.md](./12_plugin_support_implementation.md)

| Story | Description |
|-------|-------------|
| S10.1 ✅ | Claude Code plugin loading — `PluginLoader`, `ClaudeAgentOptions.plugins`, `/skills` extended |

## Epic 11: Context Tracking & Sub-agents

→ [23_epic11_context_subagents.md](./23_epic11_context_subagents.md)

| Story | Description |
|-------|-------------|
| S11.1 ✅ | Context window usage — `/context` command, token usage + cost display |
| S11.2 ✅ | Sub-agent team configuration — `AgentsConfig`, SDK hooks, `/agents` command |
| S11.3 ✅ | Per-agent notification configuration — `[notifications.agents]` subsection |

## Epic 12: Filesystem Agent Loader

→ [24_epic12_agent_loader.md](./24_epic12_agent_loader.md)

| Story | Description |
|-------|-------------|
| S12.1 ✅ | Filesystem-based agent loader — `AgentLoader` reads `~/.claude/agents/*.md` |

## Human-readable Agent Names (FR.001)

→ [10_fr001_human_readable_agent_names.md](./10_fr001_human_readable_agent_names.md)

| Feature | Description |
|---------|-------------|
| FR.001 ✅ | Sub-agents get names from a 30-word pool; no two concurrent agents share a name |

## Epic 14: Session Observability & Diagnostics

→ [25_epic14_session_diagnostics.md](./25_epic14_session_diagnostics.md)

| Story | Description |
|-------|-------------|
| S14.1 ✅ | Session state tracking & diagnostics — `is_processing`, bounded event log, enhanced `/status` |

## Epic 15: Background Agent Execution (FR.014)

→ [11_fr014_background_agent_execution.md](./11_fr014_background_agent_execution.md)

| Story | Description |
|-------|-------------|
| S15.1 ✅ | `BackgroundAgentsConfig` + `ClaudeSession` extensions — MCP URL config, context injection, Task disable |
| S15.2 ✅ | `BackgroundAgentManager` — spawn, run, notify, cancel; `AgentRun` lifecycle |
| S15.3 ✅ | `ArchonMCPServer` — aiohttp JSON-RPC 2.0 endpoint exposing `spawn_background_agent` |
| S15.4 ✅ | Gateway + `SessionManager` wiring — MCP server always starts, URL injected into sessions |
| S15.5 ✅ | `/running_agents` command — list active background agents with inline `[Cancel]` buttons |
| S15.6 ✅ | Live E2E test — real `BackgroundAgentManager` + real `ClaudeSession`, no Telegram mock |

---

## Related Documents

- [Documentation/Backlog/](../Backlog/) — pending stories (S16.1 Python installer)
- [Architecture Overview](../Architecture/100_system_architecture_overview.md) — system overview and C4 diagrams
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md) — all runtime components
- [Guiding Principles](../Architecture/000_introduction_and_guiding_principles.md) — project vision behind all completed stories
