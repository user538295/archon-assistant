**Purpose**: Describes Archon's runtime architecture using C4-style context, container, and component diagrams.
**Audience**: All developers contributing to or operating Archon
**Status**: Stable
**Last reviewed**: 2026-02-28
**Next review**: 2026-05-28

# System Architecture Overview

## Principles

Five rules govern every design decision in Archon:

1. **Event-driven streaming** — every SDK state change (thinking block, tool call, tool result, final response) emits a typed event that flows immediately to Telegram. There is no batching, buffering, or polling of Claude's output.

2. **One persistent pipeline per user** — each whitelisted Telegram user owns exactly one `Pipeline` (containing a Classifier and a Decomposer `ClaudeSession`) that persists across messages, preserving full conversation context. The SDK manages session continuity; `SessionManager` evicts pipelines after a configurable inactivity period.

3. **Sub-agents never block the main session** — the SDK `Task` tool is unconditionally disabled. All background work runs as independent asyncio tasks via `BackgroundAgentManager`. The main session's send lock releases as soon as Claude's response is complete; new user messages queue and are accepted while sub-agents run concurrently.

4. **Local daemon, no cloud relay** — Archon runs as a launchd (macOS) or systemd (Linux) daemon on the user's own machine. The bot polls Telegram directly over HTTPS. No third-party relay service handles message routing.

5. **Telegram errors never abort AI work** — delivery failures (network flaps, rate limits) are caught, logged as warnings, and swallowed. Claude processing continues to completion regardless of transient Telegram errors.

---

## Context diagram

> C4 Level 1 — who interacts with Archon and via which channels.

```mermaid
graph TB
    User(["👤 User<br/>[Person]"])
    Telegram["📱 Telegram Platform<br/>[External System]"]
    Archon["🤖 Archon<br/>[Software System]<br/>Local Python daemon running on<br/>the user's machine"]
    Claude["🧠 Claude API<br/>[External System]<br/>Anthropic language model service"]
    SearchServer["📚 Search Server<br/>[External System — Optional]<br/>History-search MCP server (archon/search/)"]

    User -->|"sends text commands<br/>via Telegram app"| Telegram
    Telegram -->|"update events<br/>HTTPS long polling"| Archon
    Archon -->|"formatted event messages<br/>Telegram Bot API HTTPS"| Telegram
    Telegram -->|"delivers responses"| User
    Archon -->|"queries and tool calls<br/>claude-agent-sdk over HTTPS"| Claude
    Claude -->|"typed SDK messages<br/>(streaming)"| Archon
    Archon -. "MCP HTTP requests<br/>(optional, history search)" .-> Search
```

---

## Container diagram

> C4 Level 2 — the major runtime containers inside the Archon daemon.
>
> All containers run inside a **single asyncio event loop** started by `Gateway.start()`.

```mermaid
graph TB
    subgraph Daemon["Archon Daemon — single asyncio event loop"]
        GW["Gateway<br/>Bootstraps and wires every container;<br/>manages startup order and graceful shutdown"]

        subgraph ChatLayer["chat/ — Telegram interface layer"]
            BotDP["Bot + Dispatcher<br/>aiogram 3.x · long polling<br/>Routes updates to handlers and commands"]
            MW["WhitelistMiddleware<br/>Drops Message and CallbackQuery events<br/>from non-whitelisted user IDs"]
            Handler["handle_message()<br/>Main message loop<br/>Streams formatted events back to Telegram"]
            Cmds["Commands<br/>/status /context /stop /clear /restart /notify<br/>/skills /skill /models /agents<br/>/tasks /scheduled"]
            VH["VoiceMessageHandler<br/>Downloads voice/audio · STT transcription<br/>Optional TTS voice-note reply"]
        end

        subgraph AILayer["ai/ — AI and background execution layer"]
            SM["SessionManager<br/>Per-user Pipeline registry<br/>Creates on demand; evicts on inactivity"]
            PL_["Pipeline<br/>Classifier (Haiku) → Decomposer (user model)<br/>Duck-types as ClaudeSession"]
            CS["ClaudeSession<br/>Wraps ClaudeSDKClient<br/>send() is an async generator of events"]
            CLS["Classification<br/>parse_classification()<br/>intent: chat|task, confidence: 0.0–1.0"]
            EM["EventMapper<br/>Maps SDK messages to typed<br/>event dataclasses (18 types)"]
            Trunc["TruncationStrategy<br/>Splits content into ≤ 4000-char<br/>Telegram-safe chunks"]
            BAM["BackgroundAgentManager<br/>Fire-and-forget asyncio tasks<br/>One isolated ClaudeSession per agent"]
            MCP["ArchonMCPServer<br/>aiohttp HTTP MCP server<br/>JSON-RPC 2.0 on :18182"]
            Sched["JobScheduler<br/>Asyncio schedule loop<br/>croniter timezone-aware expressions"]
            HM["HistoryManager<br/>Appends conversation turns<br/>to daily ~/.archon/history/sessions/YYYY-MM-DD.md"]
            AL["AgentLogger<br/>Writes per-agent event logs<br/>~/.archon/history/sessions/YYYY-MM-DD-HH-MM-{name}.md"]
            Loaders["SkillLoader · PluginLoader · AgentLoader<br/>Read ~/.claude/skills/, plugins/, agents/<br/>at startup; inject into each new session"]
            AP["AgentPlan · PlanExecutor<br/>Phase 2 multi-agent: plan schema,<br/>dependency graph, wave execution"]
            STT["STTHandler<br/>Whisper CLI subprocess<br/>Audio → text transcription"]
            TTS["TTSHandler · TTSConfig<br/>OpenAI TTS / Edge TTS<br/>Text → voice note audio"]
        end

        subgraph CfgLayer["config/"]
            Cfg["Config loader<br/>~/.archon/.env (bot token)<br/>~/.archon/config.toml (structured config)"]
        end
    end

    TelegramAPI["Telegram API<br/>[External]"]
    ClaudeAPI["Claude API<br/>[External]"]
    FS["File system<br/>~/.archon/  ·  ~/.claude/"]
    SearchServer["Search Server<br/>[External — Optional]"]

    GW -->|"wires and starts"| BotDP
    GW -->|"wires"| SM
    GW -->|"wires"| BAM
    GW -->|"starts"| MCP
    GW -->|"starts"| Sched
    GW -->|"wires (if voice.enabled)"| VH

    BotDP --> MW
    MW --> Handler
    MW --> Cmds
    MW --> VH
    Handler --> SM
    Handler --> Trunc
    Handler --> HM
    Handler --> AL
    Handler -->|"PlanEvent triggers"| AP
    VH --> STT
    VH --> TTS
    SM --> PL_
    PL_ --> CLS
    PL_ --> CS
    PL_ -->|"detects plan in Response"| AP
    CS --> EM
    Loaders -->|"skills + plugins + agents<br/>injected into factory"| SM
    MCP -->|"delegates spawn()"| BAM
    BAM -->|"spawns isolated"| CS
    Sched -->|"spawns isolated"| CS
    AP -->|"PlanExecutor spawns via"| BAM

    BotDP -.->|"HTTPS long polling"| TelegramAPI
    Handler -.->|"answer() messages"| TelegramAPI
    BAM -.->|"send_message()<br/>notifications"| TelegramAPI
    Sched -.->|"send_message()<br/>notifications"| TelegramAPI
    CS -.->|"claude-agent-sdk"| ClaudeAPI
    CS -.->|"MCP HTTP (optional)"| SearchServer
    HM -.->|"writes"| FS
    AL -.->|"writes"| FS
    Loaders -.->|"reads"| FS
    Cfg -.->|"loaded at boot"| GW
```

---

## Component diagrams

### Chat layer internals

> `archon/chat/` — Telegram bot, access control, message formatting.

```mermaid
graph LR
    subgraph chat["archon/chat/"]
        bot["bot.py<br/>Bot factory<br/>Dispatcher factory<br/>setup_bot_commands()"]
        mw["middleware.py<br/>WhitelistMiddleware<br/>BaseMiddleware subclass<br/>Checks from_user.id"]
        handler["handler.py<br/>handle_message()<br/>format_event()<br/>_partial_update_task()"]
        commands["commands.py<br/>All slash-command handlers<br/>/status /context /stop /clear /restart /notify<br/>/skills /skill /models /agents /tasks /scheduled<br/>Inline keyboard callbacks"]
        mdfmt["md_formatter.py<br/>Markdown → Telegram HTML<br/>md_to_html()"]
    end

    bot -->|"registers handlers"| commands
    bot -->|"registers handler"| handler
    bot -->|"registers middleware"| mw
    handler -->|"format_event()<br/>calls"| mdfmt
    commands -->|"format replies<br/>via"| mdfmt
```

### AI layer internals

> `archon/ai/` — Claude integration, session lifecycle, background agents, scheduling, and logging.

```mermaid
graph TB
    subgraph ai["archon/ai/"]
        sm["session_manager.py<br/>SessionManager<br/>get_or_create(user_id)<br/>set_model()<br/>inactivity eviction via asyncio.Task"]
        pl["pipeline.py<br/>Pipeline<br/>Classifier (Haiku) → Decomposer (user model)<br/>Duck-types as ClaudeSession<br/>Graceful degradation on Classifier failure"]
        cs["claude_session.py<br/>ClaudeSession<br/>start() · send() · stop()<br/>_send_lock prevents concurrent use<br/>activate_skill() · inject_context()<br/>usage_stats · is_processing · diagnostics"]
        cls["classification.py<br/>Classification dataclass<br/>parse_classification()<br/>intent: chat|task · confidence: 0.0–1.0"]
        prompts["prompts/<br/>load_prompt(name)<br/>classifier.md · decomposer.md"]
        em["event_mapper.py<br/>EventMapper<br/>map_messages(stream) → events<br/>ThinkingResult · ToolStarted · ToolResult<br/>Response · ErrorEvent · ClassificationEvent<br/>SubagentStarted · SubagentStopped<br/>PlanEvent · PromotionEvent · RoutingEvent<br/>FallbackNoticeEvent · RecoveryEvent<br/>WaveStarted · WaveCompleted · ReminderInjectedEvent<br/>ContextInjectedEvent · SkillInjectedEvent"]
        trunc["truncation.py<br/>TruncationStrategy (ABC)<br/>apply(text, max_len) → list[str]<br/>SplitStrategy — labels chunks [1/N]"]
        sl["skill_loader.py<br/>SkillLoader<br/>Reads ~/.claude/skills/*/SKILL.md<br/>YAML frontmatter: name, description"]
        pl["plugin_loader.py<br/>PluginLoader<br/>Reads ~/.claude/plugins/<br/>Provides SDK plugin configs and skills"]
        ald["agent_loader.py<br/>AgentLoader<br/>Reads ~/.claude/agents/*.md<br/>-archon suffix → injected into sessions"]
        hm["history_manager.py<br/>HistoryManager<br/>record_user_message()<br/>record_event()<br/>Daily Markdown files in ~/.archon/history/sessions/"]
        agl["agent_logger.py<br/>AgentLogger<br/>record_event() for sub-agent events<br/>Per-agent timestamped Markdown files"]
        bam["background_agent_manager.py<br/>BackgroundAgentManager<br/>spawn() → AgentRun (fire-and-forget)<br/>_run_agent() asyncio.Task<br/>Beacon messages every beacon_interval_minutes<br/>Max max_parallel agents per user"]
        mcp["archon_mcp_server.py<br/>ArchonMCPServer<br/>aiohttp HTTP · JSON-RPC 2.0<br/>POST /mcp/{user_id}<br/>Exposes spawn_background_agent tool"]
        sched2["job_scheduler.py<br/>JobScheduler<br/>Ticks every 60 s via asyncio.sleep<br/>croniter for expression evaluation<br/>Pipeline steps: tool (subprocess) · prompt (ClaudeSession)"]
        ap["agent_plan.py<br/>AgentPlan · AgentTask<br/>parse_agent_plan()<br/>topological_sort() → waves<br/>validate_dependency_graph()"]
        pe["plan_executor.py<br/>PlanExecutor<br/>execute(plan) as asyncio.Task<br/>wave-by-wave spawning via BAM<br/>_done.wait() for completion signal"]
        stt["stt.py<br/>STTHandler<br/>transcribe(audio_path)<br/>transcribe_with_timeout()<br/>Whisper CLI subprocess"]
        tts["tts.py<br/>TTSHandler · TTSConfig<br/>synthesize(text, output_path)<br/>OpenAI TTS (Opus) · Edge TTS (MP3)<br/>should_synthesize(message_has_voice)"]
    end

    sm -->|"creates and starts"| pl
    pl -->|"contains two"| cs
    pl -->|"parses via"| cls
    pl -->|"reads prompts from"| prompts
    pl -->|"detects plan via"| ap
    cs -->|"events piped through"| em
    sl -->|"loaded into"| sm
    pl -->|"loaded into"| sm
    ald -->|"loaded into"| sm
    mcp -->|"spawn() call"| bam
    bam -->|"isolated<br/>ClaudeSession"| cs
    bam -->|"logs events"| agl
    sched2 -->|"isolated<br/>ClaudeSession"| cs
    pe -->|"spawns workers via"| bam
    pe -->|"uses"| ap
```

---

## Module layering

Archon has four layers. Each layer depends only on layers below it.

| Layer | Package | Responsibility |
|---|---|---|
| **Chat** | `archon/chat/` | Telegram bot lifecycle, whitelist enforcement, message formatting, command routing. The only layer that touches the Telegram API. |
| **Gateway** | `archon/gateway/` | Single orchestrator that wires all components, manages startup order, routes events bidirectionally, and handles graceful shutdown on SIGTERM/SIGINT. |
| **AI** | `archon/ai/` | Claude session lifecycle, event mapping, truncation, background agents, job scheduling, history and agent logging, skill/plugin/agent loading. No Telegram knowledge. |
| **Config** | `archon/config/` | Loads `~/.archon/.env` (bot token) and `~/.archon/config.toml` (all structured settings) into typed dataclasses. Raises `ConfigError` on missing required fields. |

The dependency rule: `chat/` and `gateway/` import from `ai/` and `config/`; `ai/` imports from `config/` only; `config/` imports nothing internal.

---

## Data flow

### Main conversation flow

A text message travels through seven stages from the user's Telegram app to Claude and back.

**Stage 1 — Telegram delivery**
The user sends a message in the Telegram app. The aiogram `Dispatcher` receives the update object via HTTPS long polling from the Telegram Bot API.

**Stage 2 — Access control**
`WhitelistMiddleware.__call__()` checks `message.from_user.id` against `allowed_user_ids` (from `config.toml`). Non-whitelisted IDs are silently dropped; no handler runs. This check applies to both `Message` and `CallbackQuery` events.

**Stage 3 — Handler dispatch**
If the message is a slash command (e.g. `/status`), the `Dispatcher` routes to the matching command handler in `commands.py`. Plain text messages route to `handle_message()`.

**Stage 4 — Pipeline acquisition**
`handle_message()` calls `session_manager.get_or_create(user_id)`. If no session exists yet, `SessionManager` builds a new `Pipeline` — injecting skills from `SkillLoader`, plugin configs from `PluginLoader`, and agent definitions from `AgentLoader` — then calls `pipeline.start()`, which starts both the Classifier (`ClaudeSession` with Haiku) and the Decomposer (`ClaudeSession` with the user-selected model).

**Stage 5 — Classification and prompt dispatch**
`handle_message()` records the incoming message via `HistoryManager.record_user_message()` (if history is enabled) and calls `pipeline.send(message.text)`, which returns an async generator. Inside `send()`, the Pipeline first routes the user message to the Classifier (Haiku), which outputs a JSON classification (`{"intent": "chat"|"task", "confidence": 0.0–1.0}`). The classification is parsed via `parse_classification()` (defaulting to `intent="task", confidence=0.0` on any failure), a `ClassificationEvent` is yielded, and the classification JSON is prepended to the user prompt before forwarding to the Decomposer. The Decomposer's `ClaudeSession` acquires `_send_lock`, calls `client.query(full_prompt)`, and begins iterating `client.receive_response()`.

**Stage 6 — Event mapping**
`EventMapper.map_messages()` converts raw SDK messages to typed event dataclasses:

| SDK message | SDK content block | Archon event |
|---|---|---|
| `AssistantMessage` | `ThinkingBlock` | `ThinkingResult` |
| `AssistantMessage` | `ToolUseBlock` | `ToolStarted` |
| `UserMessage` | `ToolResultBlock` | `ToolResult` |
| `ResultMessage` (no error) | — | `Response` |
| `ResultMessage` (is_error) | — | `ErrorEvent` |

Additionally, `Pipeline.send()` yields a `ClassificationEvent` (with `intent` and `confidence`) after the Classifier response is parsed — before any Decomposer events.

For `task` intent, `Pipeline.send()` calls `Decomposer.route_task()`, which is an async generator. It first yields intermediate router session events (tool calls, thinking) — each re-tagged with `source="router"` — then yields a `TaskOutput` sentinel consumed internally by the Pipeline. Router events appear in the stream before main-session events. The `is_router_event(event)` helper (`event.source == "router"`) identifies them; they render with a `[Router]` prefix in history and are suppressed in quiet/normal Telegram mode.

Each event is yielded from `pipeline.send()` as it arrives — no buffering.

**Stage 7 — Formatting and delivery**
For each event received in `handle_message()`:

- Sub-agent events (`event.source == "sub-agent"`) go to `AgentLogger.record_event()` only; they are never sent to Telegram.
- `HistoryManager.record_event()` logs orchestrator events (if history is enabled).
- The notification mode (quiet / normal / verbose / debug) filters which events reach the user:
  - **quiet** — `Response`, `ErrorEvent`, `SubagentStarted`, `SubagentStopped` only
  - **normal** — adds tool names and brief tool results; no thinking
  - **verbose** — adds tool arguments, full thinking output, and `ClassificationEvent` (`🏷 task (95%)`)
  - **debug** — adds full tool results and `ClassificationEvent`
- `format_event()` converts the event to Telegram HTML strings.
- `TruncationStrategy.apply()` splits content longer than `max_message_length` into labeled chunks (`[1/N]`, `[2/N]`, …).
- Each chunk is sent via `message.answer(text)`.

### Background agent flow

When Claude decides to offload work, the following sequence runs entirely outside the main session's event loop turn:

```mermaid
sequenceDiagram
    participant CS as ClaudeSession (main)
    participant SDK as claude-agent-sdk
    participant MCP as ArchonMCPServer (:18182)
    participant BAM as BackgroundAgentManager
    participant BCS as ClaudeSession (isolated)
    participant TG as Telegram

    CS->>SDK: query(prompt)
    SDK->>MCP: HTTP POST /mcp/{user_id}<br/>(JSON-RPC tools/call: spawn_background_agent)
    MCP->>BAM: spawn(user_id, task, context)
    BAM->>TG: send_message("🤖 Agent Atlas spawned")
    BAM-->>MCP: AgentRun (returns immediately)
    MCP-->>SDK: tool result (agent started)
    SDK-->>CS: continues streaming events

    Note over BAM,BCS: asyncio.Task runs concurrently

    BAM->>BCS: ClaudeSession(isolated).start()
    BAM->>BCS: send(prompt)
    BCS-->>BAM: events (ThinkingResult, ToolStarted, ToolResult, Response)
    BAM->>BAM: AgentLogger.record_event() for each event
    BAM->>TG: periodic beacon messages (if beacon_interval_minutes > 0)
    BAM->>BCS: stop()
    BAM->>TG: send_message("✅ 🤖 Agent Atlas completed<br/>{result}")
```

Key invariants:
- The main `ClaudeSession` never waits for the background agent; `spawn()` returns immediately.
- Background agent events never reach the user's main Telegram stream; they go to `AgentLogger` only.
- The background agent runs in a fully isolated `ClaudeSession` with no shared state.
- Agent names are drawn from a 30-name pool (`Atlas`, `Sage`, `Orion`, …) shared globally across users.

### Scheduled job flow

`JobScheduler` runs a background asyncio task that ticks every 60 seconds. On each tick, `croniter` evaluates which jobs are due based on their cron expressions (timezone-aware). For each due job, the scheduler executes a pipeline of steps sequentially:

```mermaid
flowchart TD
    L["JobScheduler._loop()<br/>(ticks every 60 s)"] -->|"croniter: timezone-aware due check"| RJ["_run_job(job)"]
    RJ --> ST{pipeline step type}
    ST -->|tool| SUB["asyncio subprocess<br/>(stdout captured)"]
    ST -->|prompt| CLS["ClaudeSession (isolated)<br/>.send(input)"]
    SUB --> NEXT[next step / completion]
    CLS --> NEXT
    NEXT -->|"all steps done or failure"| NOTIFY["bot.send_message<br/>(notify_user_id)"]
```

- **tool step** — runs a bash command via `asyncio.create_subprocess_exec`; stdout is captured and passed as `{input}` to the next step.
- **prompt step** — sends the prompt to a freshly created isolated `ClaudeSession`.

On completion or failure, `JobScheduler` sends a Telegram notification to the job's configured `notify_user_id`.

### Voice message flow

When `config.voice.enabled = true`, the Gateway registers `VoiceMessageHandler` for `F.voice` and `F.audio` Telegram filters. Voice messages follow a six-step path:

```
Telegram voice note (OGG/Opus)
  → WhitelistMiddleware (same access control as text)
  → VoiceMessageHandler.handle_voice_message()
      1. Download file from Telegram to a temp dir
      2. STTHandler.transcribe_with_timeout(audio_path)  ← Whisper CLI subprocess
      3. Show transcription preview to user ("🎤 …")
      4. _process_and_respond(): get_or_create(user_id) → session.send(text)
         → streams events directly (same Pipeline flow, independent of handle_message)
      5. [optional] TTSHandler.synthesize(response_text, output_path)
      6. [optional] message.answer_voice(voice=audio_file)
```

TTS reply is gated by `TTSConfig.auto`:
- `"inbound"` (default) — reply with voice only when the incoming message was voice
- `"always"` — always reply with voice regardless of input type
- `"tagged"` — reserved for tag-based TTS triggering (not yet implemented)
- `"off"` — never reply with voice

### Multi-agent plan execution flow (Phase 2)

When the Decomposer outputs a structured agent plan JSON (scope `"large"`), `Pipeline.send()` detects it and yields a `PlanEvent` instead of the normal `Response`. The handler then starts a `PlanExecutor` asyncio task:

```
Decomposer Response (plan JSON)
  → Pipeline detects via parse_agent_plan() → yields PlanEvent
  → handle_message() formats PlanEvent → sends "📋 Plan: …" to Telegram
  → asyncio.create_task(PlanExecutor.execute(plan))
      1. Validates dependency graph (aborts with error on cycles/unknown IDs)
      2. topological_sort() → execution waves
      3. For each wave:
         a. Spawn agents via BackgroundAgentManager.spawn()
         b. Inject upstream log paths into dependent agents' task prompts
         c. await asyncio.gather(*[run._done.wait() for run in wave])
         d. Mark failed agents; skip dependents transitively
      4. Send "✅ Plan completed: N/M agents succeeded" to Telegram
```

`Pipeline.send()` returns immediately after yielding `PlanEvent`; the main session is free for new user messages while agents execute concurrently.

---

## Cross-references

- **Component catalog and layering** — see `110_component_catalog_and_layer_breakdown.md` for a full inventory of all classes and their public interfaces.
- **Integration architecture** — see `120_services_and_integration_architecture.md` for the Telegram Bot API, Claude API, and MCP integration patterns.
- **Error handling strategy** — see `140_error_handling_strategy.md` for the swallow-on-delivery, fail-fast-on-config patterns applied throughout the codebase.
- **Operational readiness** — see `160_operational_readiness_monitoring_and_reliability.md` for log rotation, startup checks, and graceful shutdown details.

---

## Related Decisions

The following ADRs record the architectural decisions that shaped this system:

- [ADR-01: Use Claude Agent SDK](../ADRs/01_use_claude_agent_sdk.md) — why `ClaudeSDKClient` was chosen over PTY/subprocess control
- [ADR-02: Logical Boundary Output Streaming](../ADRs/02_logical_boundary_output_streaming.md) — why each logical event maps to a separate Telegram message
- [ADR-03: One Session per User](../ADRs/03_one_session_per_user.md) — why one persistent `ClaudeSession` per Telegram user is maintained
