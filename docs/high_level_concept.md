# Archon Assistant

## Short App definition

This app is an AI assistant which can do almost anything on your computer. It connects Claude Code with Telegram chat. You can send messages in Telegram and they will be sent to Claude Code to process. Claude Code output is redirected to the Telegram chat in real-time — thinking blocks, tool calls, and final responses each delivered as they happen, exactly as if you were watching the terminal.

The app consists of 3 main parts: chat integration (Telegram), the AI integration that handles the input and output of the AI (Claude Code via the Claude Agent SDK), and the gateway that handles communication between the parts. The gateway starts and stops everything and establishes the connection between the parts.

## Architecture Decisions

| Concern | Decision | Rationale |
|---|---|---|
| Claude Code control | **Claude Agent SDK** (`claude-agent-sdk`) | Structured typed messages (no ANSI parsing), built-in multi-turn sessions, official Python API |
| Output streaming | Logical boundaries | Tool calls, thinking blocks, and final responses sent as separate Telegram messages — structured and readable |
| Session management | One persistent Claude session per user | Full conversation context maintained per Telegram user via SDK session resume |
| Deployment | Local daemon (launchd on macOS / systemd on Linux) | Private, no cloud cost, always-on while machine is running |
| Access control | Whitelist of Telegram user IDs | Simple, hard to bypass, configured in config file |
| Sub-agent execution | Background agents via MCP `spawn_background_agent` | SDK `Task` tool is always disabled; background agents run as isolated asyncio tasks so the main conversation never blocks |
| Truncation | Pluggable `TruncationStrategy` ABC | `SplitStrategy` MVP; new strategies require no gateway/chat changes |
| Config persistence | `tomlkit` for write-back | Preserves comments and structure when saving notification settings at runtime |
| History format | QMD-compatible daily Markdown | Claude can search its own conversation history via QMD MCP tools |

## Module Structure

```
archon/
├── chat/           # Telegram bot — message routing, whitelist enforcement (aiogram 3.x)
│   ├── bot.py          # Bot factory, BOT_COMMANDS list, create_dispatcher()
│   ├── commands.py     # All command handlers (/status, /stop, /clear, /restart, /notify,
│   │                   # /quiet, /normal, /verbose, /debug, /settings, /skills, /skill,
│   │                   # /model, /context, /agents, /jobs, /running_agents)
│   ├── handler.py      # handle_message() + format_event() — main message loop
│   ├── md_formatter.py # Markdown-to-HTML converter for Telegram
│   └── middleware.py   # WhitelistMiddleware (Message + CallbackQuery)
│
├── ai/             # AI layer — Claude integration, event mapping, background agents
│   ├── claude_session.py          # ClaudeSession — wraps ClaudeSDKClient
│   ├── event_mapper.py            # EventMapper + all event dataclasses
│   ├── truncation.py              # TruncationStrategy ABC + SplitStrategy
│   ├── session_manager.py         # SessionManager — per-user registry, inactivity eviction
│   ├── skill_loader.py            # SkillLoader — reads ~/.claude/skills/*/SKILL.md
│   ├── plugin_loader.py           # PluginLoader — reads ~/.claude/plugins/ + settings.json
│   ├── agent_loader.py            # AgentLoader — reads ~/.claude/agents/*.md
│   ├── history_manager.py         # HistoryManager — daily ~/.archon/history/YYYY-MM-DD.md
│   ├── agent_logger.py            # AgentLogger — per-agent YYYY-MM-DD-HH-MM-{name}.md logs
│   ├── background_agent_manager.py # BackgroundAgentManager — fire-and-forget agent tasks
│   ├── archon_mcp_server.py       # ArchonMCPServer — HTTP MCP JSON-RPC 2.0 server
│   └── cron_scheduler.py          # CronScheduler — asyncio-based cron loop
│
├── gateway/        # Orchestrator — wires everything, handles graceful shutdown
│   └── gateway.py
│
├── config/         # Config loader — .env (token) + config.toml → typed dataclasses
│   └── loader.py
│
└── log_setup.py    # setup_logging() — daily-rotating file handler + stderr capture
```

## Data Flow

### Main conversation flow

```
User (Telegram)
     │ message text
     ▼
WhitelistMiddleware (drops non-whitelisted IDs)
     │
     ▼
handle_message()
     │ get_or_create session
     ▼
SessionManager ──── creates ────▶ ClaudeSession
     │                                 │
     │                                 │ start() — ClaudeSDKClient.connect()
     │                                 │ send(prompt) — async generator
     │                                 ▼
     │                           EventMapper.map_messages()
     │                                 │
     │                       ┌─────────┴──────────┐
     │                 ThinkingStarted         ToolStarted
     │                 ThinkingResult          ToolResult
     │                 Response                ErrorEvent
     │                 SubagentStarted         SubagentStopped
     │                       │
     ▼                       ▼
HistoryManager          format_event()
(records turn)          TruncationStrategy.apply()
                              │
                              ▼
                    message.answer(text)  ──▶ User (Telegram)
```

### Background agent flow

```
ClaudeSession.send()
     │ Claude calls spawn_background_agent MCP tool
     ▼
ArchonMCPServer (HTTP POST /mcp/{user_id})
     │ JSON-RPC tools/call
     ▼
BackgroundAgentManager.spawn(user_id, task, context)
     │ asyncio.create_task(_run_agent(run))
     ▼
_run_agent():
  1. ClaudeSession(isolated).start()
  2. session.send(prompt) — events → AgentLogger (not Telegram)
  3. session.stop()
  4a. On success: bot.send_message(✅) + main_session.inject_context(result)
  4b. On failure: bot.send_message(❌)

[spawn notification message edited in-place every beacon_interval_minutes]
```

### Cron flow

```
CronScheduler._loop() — ticks every 60 s
     │ croniter checks which jobs are due (timezone-aware)
     ▼
_run_job(job):
  for each pipeline step:
    tool   → asyncio subprocess (stdout captured)
    prompt → ClaudeSession(isolated).send("{input}")
  on completion/failure → bot.send_message(notify_user_id)
```

## Tech Stack

- **Language:** Python 3.12+ (asyncio)
- **Telegram:** aiogram 3.x (polling + inline keyboards)
- **Claude Code:** `claude-agent-sdk` (official Python SDK)
- **Background agent MCP:** `aiohttp>=3.9` (HTTP server)
- **Cron expressions:** `croniter>=6.0.0`
- **Config write-back:** `tomlkit>=0.12`
- **Daemon:** launchd plist (macOS) / systemd unit (Linux)
- **Config:** `.env` file (bot token) + `config.toml` (structured config)

## Key Design Invariants

1. **Sub-agents never block the main conversation.** The SDK `Task` tool is unconditionally disabled. All sub-agent execution goes through `BackgroundAgentManager` which runs them as independent asyncio tasks. The main session's `send()` lock is released immediately after the orchestrator responds; the user can send new messages while background agents are still running.

2. **Agent lifecycle events are always delivered.** `SubagentStarted`, `SubagentStopped`, `Response`, and `ErrorEvent` are never suppressed by notification mode — they bypass the quiet-mode filter in `handle_message()`.

3. **Message content is never logged.** `handle_message()` logs only `(N chars)` on receipt. Error handlers log the exception type only. Chat privacy is preserved in log files.

4. **Truncation is decoupled from routing.** `TruncationStrategy.apply(text, max_len) -> list[str]` is the only interface. Adding a new strategy requires no changes outside `archon/ai/`.

5. **Config write-back preserves comments.** `save_notifications_config()` uses `tomlkit` to update only the `[notifications]` section, leaving all other config keys and comments untouched.

6. **Background agent MCP server always starts.** There is no `enabled` flag controlling whether the MCP server runs — it always starts on daemon boot. The `Task` tool is always disabled. The `enabled = false` key in the `[background_agents]` config section is not read by the loader or gateway.
