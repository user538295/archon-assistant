# Archon Assistant — Development Tasks

## Context

Read these files before working on any task:
- `high_level_concept.md` — concept and architecture decisions
- `prd.md` — full product requirements
- `stories.md` — all user stories with acceptance criteria
- `CLAUDE.md` — dev commands, architecture overview, constraints

Implementation order: `S0.1 → S0.2 → S5.7 → S4.1 → S1.1 → S1.2 → S1.3 → S5.1 → S5.5 → S1.4 → S2.1 → S2.2 → S2.3 → S2.4 → S2.5 → S2.6 → S5.2 → S3.1 → S5.3 → S3.2 → S5.4 → S4.2 → S5.6 → S7.1 → S8.1 → S8.2 → S8.3 → S8.4 → S6.1 → S6.2 → S4.4 → S9.1 → S10.1 → S11.1 → S11.2 → S11.3 → S12.1 → S14.1`

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
- [x] **S11.3** — Per-agent notification configuration: `NotificationsAgentsConfig(mode: str | None)` dataclass; `agents` field on `NotificationsConfig`; `_resolve_agent_mode()` helper in `handler.py`; `format_event` for `SubagentStarted`/`SubagentStopped` uses resolved agent mode; `handle_message` quiet-mode block lets subagent events escape if agents not quiet; `load_config` parses `[notifications.agents]`; `save_notifications_config` persists `agents.mode`; `examples/config.toml.example` documents section with `mode = "quiet"` default (`stories.md` § S11.3)

### Epic 12: Filesystem Agent Loader

- [x] **S12.1** — Filesystem agent loader: `AgentLoader` reads all `~/.claude/agents/*.md`; `is_archon` property on `Agent`; archon agents sorted first; `_build_sdk_agents(list[Agent])` new signature; `_build_sdk_agents_config(AgentsConfig)` renamed; `SessionManager` gains `agent_loader` param; gateway wires loader at startup; `/agents` shows three sections (🤖 archon / 🔍 other / ⚙️ config); `tests/ai/test_agent_loader.py` (`stories.md` § S12.1)

### Epic 14: Session Observability & Diagnostics

- [ ] **S14.1** — Session state tracking & diagnostics: add `_processing`, `_last_send_at`, `_last_response_at`, `_send_count`, `_event_log` (deque maxlen=200) to `ClaudeSession`; `is_processing`, `processing_seconds`, `idle_seconds`, `send_count`, `is_stuck(threshold)`, `recent_events(n)`, `diagnostics` property; `SessionManager.session_diagnostics()`, `processing_sessions()`, `stuck_sessions()`; enhanced `/status` shows `🔄 Processing for X.Xs` / `💤 Idle for Xs` / message count; TDD: 22 unit + 10 integration + 5 E2E + 7 live tests (`stories.md` § S14.1)

### Other tasks (move from here to under the proper epic)

- [x] **Bug.001** — Notify setting can't be changed during the work. Is it broken again or we don't have enough or good enough tests? Write tests for all cases: change from all of the modes to all of the other modes. Verify every cases to work correctly. Write tests first then fix. Write also live e2e tests.
- [x] **Bug.002** — We shouldn't log the chat messages into the log file. It's a security issue.
      Fix: log only `(N chars)` on receipt; error handler logs `ExceptionType` only (not `str(exc)` which could echo the prompt).
      Tests: `test_handle_message_does_not_log_message_content`, `test_handle_message_logs_receipt_without_content`, `test_handle_message_does_not_log_partial_content`, `test_handle_message_error_does_not_log_message_content`, `test_handle_message_error_logs_exception_type`.
- [x] **FR.001** — It would be great if every sub-agent and agent could have a name. When the orchecstrator starts and agent it should also give it a name. Randomize 30 names, save them and use them when spawing agents. Avoid to generate the same name for two running agents. Use TDD.
- [x] **Bug.003** — In Normal mode I didn't get notification about the agent start, but got notification about its run: ⏳ Agent is still working... (2 min elapsed).
      Remove this feature: "quiet"   — hide agent start/stop events (counted in beacon if beacon enabled), only normal mode and later the verbose and debug modes
      The right behaviour is, in every notification mode the user MUST get the notification about the agent start and stop, finish or error, regardless the mode. It can't be overridden. Write tests for every mode to verify the error then fix them; prove the right behaviour with the test.
      Fix: removed `quiet` mode entirely; `handle_message` always sends "⏳ Working..." first; `SubagentStarted`/`SubagentStopped`/`Response`/`ErrorEvent` always sent regardless of mode; `NotificationsAgentsConfig` and per-agent override removed; legacy `quiet` configs silently migrate to `normal` on load.
      Tests: 23 new Bug.003 tests in `test_handler.py`; migration tests in `test_subagent_integration.py` and `test_loader.py`; quiet-mode tests removed from all test files.
	- [ ] Bug.004 - Investigate this error log and fix if needed. By the way, don't interrupt the work because of an Telegram error message. 
	      2026-02-25 00:13:14,402 archon ERROR Error processing message for user 154643621 (TelegramNetworkError)
	Error in hook callback hook_1: **11057 |** - Integrate the improvements naturally into the existing structure
	**11058 |** - Preserve frontmatter (--- block) exactly as-is
	**11059 |** - Preserve the overall format and style
	**11060 |** - Do not remove existing content unless an improvement explicitly replaces it
	**11061 |** - Output the complete updated file inside <updated_file> tags`})],systemPrompt:E0(["You edit skill definition files to incorporate user preferences. Output only the updated file content."]),thinkingConfig:{type:"disabled"},tools:[],signal:tB().signal,options:{getToolPermissionContext:async()=>vC(),model:$O(),toolChoice:void 0,isNonInteractiveSession:!1,hasAppendSystemPrompt:!1,temperatureOverride:0,agents:[],querySource:"skill_improvement_apply",mcpTools:[]}})).message.content.filter((G)=>G.type==="text").map((G)=>G.text).join("").trim(),O=aB(q,"updated_file");if(!O){r(Error("Skill improvement apply: no updated_file tag in response"));return}try{await _.writeFile(B,O,"utf-8")}catch(G){r(G instanceof Error?G:Error(`Failed to write skill file: ${B}`))}}var umA=Q(()=>{vmA();INT();kR();F9();T0();MR();SR();uq();xH();zR();Q_();p_()});function e1T(){let A=((w9()||{}).cleanupPeriodDays??MV8)*24*60*60*1000;return new Date(Date.now()-A)}function SV8(T,R){return{messages:T.messages+R.messages,errors:T.errors+R.errors}}f | ... truncated 
	**11062 |** `)}async sendRequest(T,R,A){let _=imA.randomUUID(),B={type:"control_request",request_id:_,request:T};if(this.inputClosed)throw Error("Stream closed");if(A?.aborted)throw Error("Request aborted");await this.write(B);let D=()=>{this.write({type:"control_cancel_request",request_id:_});let $=this.pendingRequests.get(_);if($)$.reject(new uH)};if(A)A.addEventListener("abort",D,{once:!0});try{return await new Promise(($,H)=>{this.pendingRequests.set(_,{request:{type:"control_request",request_id:_,request:T},resolve:(q)=>{$(q)},reject:H,schema:R})})}finally{if(A)A.removeEventListener("abort",D);this.pendingRequests.delete(_)}}createCanUseTool(T){return async(R,A,_,B,D)=>{let $=await t2(R,A,_,B,D);if($.behavior==="allow"||$.behavior==="deny")return $;let H=new AbortController,q=_.abortController.signal,O=()=>H.abort();q.addEventListener("abort",O,{once:!0});try{let G=Hw8(R.name,D,A,_,$.suggestions).then((W)=>({source:"hook",decision:W}));T?.();let C=this.sendRequest({subtype:"can_use_tool",tool_name:R.name,input:A,per | ... truncated 
	AbortError: 
	      at **_D_** (/$bunfs/root/claude:11062:332)
	      at **___** (/$bunfs/root/claude:6027:3282)
	2026-02-25 00:36:00,781 archon INFO Evicting inactive session for user 154643621

2026-02-25 00:36:00,7
- [x] **FR.002** — QMD support. Add the ability to turn on and use QMD (https://github.com/tobi/qmd) with Archon. In the config you can enable and set it up. After the setup, add the history folder to QMD as a collection. Also this feature should be used by every agent by default.
- [ ] **FR.003** — Log separately the agents' work. Create another md log file in history in YYYY-MM-DD-HH-MM-\[agent-name].md format and it should contain details. This log have to be writen continuously during the work with its final result as well. Use TDD, write unit, integration, e2e and live tests. Start with happy paths, then edge cases and the others.
- [ ] **FR.003B** — Update the text from: ⏳ Agent is still working... (2 min elapsed) to: ⏳ Agent \[agent-name] is still working... (2 min elapsed)
- [ ] **FR.004** — Count the compaction in the session and make it visible in the /context command.  Use TDD, write unit, integration, e2e and live tests. Start with happy paths, then edge cases and the others.
- [ ] **FR.005** — Watch the context window after response(?) and make a summary about the current session before compaction. /clear the session and reload the summary and continue the work.  Use TDD, write unit, integration, e2e and live tests. Start with happy paths, then edge cases and the others.
- [ ] **FR.006** — Installer add option to install: claude-mem and other plugins, agents, skills, ~~QMD~~.
- [ ] **FR.007** — Investigate that the Claude brower plugin is accessible from Archon and how could we use it. Make a deep research and read the official documentation
- [ ] **FR.008** — Know Archon: Missing documentation. Need a world class well structured and documented user guide. From installation to configuration through uninstallation and how to use third party components like QMD as well.
- [ ] **FR.009** — The implementation of the cron job is different than the original specification. In the current implementation the cron toml file pipeline is:
      \[\[pipeline]]
      tool = "scripts/health_check.sh"
      \[\[pipeline]]
      prompt = "Summarize in one line: {input}"
      
      But the pipeline should be something like this in valid json:
      pipeline = [{"tool": "scripts/health_check.sh"}, {"prompt": "Summarize in one line: {input}"}]
      
       Use TDD, write unit, integration, e2e and live tests. Start with happy paths, then edge cases and the others.
    
- [ ] **FR.010** — log uses local time instead of UTC.