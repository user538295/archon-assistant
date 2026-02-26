**Purpose**: Completed stories for Epic 11 — context window tracking, sub-agent team configuration, and per-agent notification control
**Audience**: All developers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

# Epic 11: Context Tracking & Sub-agents

## Stories

### S11.1: Context window usage (/context command)

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As a whitelisted user, I want to see a real-time snapshot of my context window usage via `/context`, so that I know how much of the 200k-token window is used, my accumulated cost, and turn count.

#### Acceptance Criteria

- `ClaudeSession._intercept()` wraps `receive_response()` and captures `ResultMessage` fields: `usage` (token dict), `total_cost_usd` (accumulated across turns), `num_turns`, `duration_ms`
- `ClaudeSession.usage_stats` property returns a dict with keys `usage`, `total_cost_usd`, `num_turns`, `last_duration_ms`; returns `None` before the first response
- `SessionManager.context_stats(user_id)` delegates to `session.usage_stats`; returns `None` when no session exists
- `/context` with no active session replies `"ℹ️ No active session"`
- `/context` with a session but no data yet replies `"📊 No context data yet — send a message first"`
- `/context` with data replies with an HTML-formatted message containing:
  - Unicode block progress bar showing `input_tokens / 200,000`
  - Per-category token counts: input, output, cache-read, cache-creation
  - Accumulated cost (formatted as `$0.0000`), turn count, last response duration
- Tests: `usage_stats` before first response returns `None`, after one response returns correct values, accumulated cost adds across turns; `/context` handler for each state (no session, no data, has data); `_progress_bar` edge cases (0 tokens, at capacity)

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Operational Readiness](../Architecture/160_operational_readiness_monitoring_and_reliability.md)

---

### S11.2: Sub-agent team configuration (/agents command)

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: L

**User Story**: As a developer, I want to define a team of named sub-agents in `config.toml` and have them available in every Claude session, so that Claude can delegate specialised tasks to sub-agents (e.g. a `bash` agent or `explore` agent) via the Task tool.

#### Acceptance Criteria

- `AgentDefinitionConfig` dataclass: `name`, `description`, `prompt`, `tools: list[str]`, `model: str | None`
- `AgentsConfig` dataclass: `enabled: bool`, `definitions: list[AgentDefinitionConfig]`; parsed from `[agents]` / `[[agents.definitions]]` in `config.toml`
- `_build_sdk_agents(agents_cfg)` in `session_manager.py` converts `AgentsConfig` → `dict[str, AgentDefinition]` (or `None` if disabled/empty)
- `ClaudeSession.__init__` accepts `agents: dict[str, AgentDefinition] | None`; passed to `ClaudeAgentOptions`
- `ClaudeSession._build_hooks()` creates `SubagentStart`/`SubagentStop` SDK hook matchers that push `SubagentStarted`/`SubagentStopped` events into a side-channel `asyncio.Queue`
- `ClaudeSession.send()` drains the queue between each SDK-derived event and in a final drain after the stream ends
- `format_event` formats `SubagentStarted` as `🤖 Agent <b>{display}</b> started` (no colon; uses `agent_name` if non-empty, else `agent_type`); suppressed in quiet mode; `SubagentStopped` formats as `🤖 Agent <b>{display}</b> done`
- `/agents` command lists all configured agent definitions with name, model, description, and tools; replies with info message when no agents configured
- `BOT_COMMANDS` entry for `/agents`
- Gateway wires `agents_config` into `SessionManager` and `/agents` command dependency injection
- Tests: `AgentsConfig` loading from TOML, `_build_sdk_agents` with enabled/disabled/empty config, hook queue draining, `SubagentStarted`/`SubagentStopped` event formatting, `/agents` with no config and with definitions

#### Technical Notes

The Claude Agent SDK accepts an `agents` dict in `ClaudeAgentOptions` mapping agent names to `AgentDefinition` objects. Sub-agent lifecycle events arrive via SDK hooks (`SubagentStart`, `SubagentStop`), which must be surfaced as archon events for the Telegram UI.

New event types in `archon/ai/event_mapper.py`:
- `SubagentStarted(agent_id: str, agent_type: str)` — fired when the main agent spawns a sub-agent
- `SubagentStopped(agent_id: str, agent_type: str)` — fired when a sub-agent completes

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
- [Component Catalog](../Architecture/110_component_catalog_and_layer_breakdown.md)
- [10 FR.001 Human-readable Agent Names](./10_fr001_human_readable_agent_names.md)

---

### S11.3: Per-agent notification configuration

**Status**: Completed ✅
**Priority**: Medium
**Estimated effort**: M

**User Story**: As an operator, I want to set a separate notification level for sub-agents in `config.toml` independently of the orchestrator's notification mode, so that I can keep the orchestrator events fully visible while silencing sub-agent lifecycle chatter (or vice versa) without changing how the main agent reports.

#### Acceptance Criteria

- `NotificationsAgentsConfig(mode=None)` → resolved mode equals the orchestrator's current mode (inheritance)
- `NotificationsAgentsConfig(mode="quiet")` → agent events suppressed regardless of orchestrator mode
- `format_event(SubagentStarted, …, notifications)` returns `[]` when resolved agent mode is `"quiet"`; returns formatted string otherwise
- Orchestrator `"quiet"` + agents `"normal"` → `SubagentStarted` notification is still sent; event is **not** counted in beacon
- Orchestrator `"normal"` + agents `"quiet"` → `SubagentStarted` returns `[]` from `format_event`
- Orchestrator `"quiet"` + agents `"quiet"` (or inherit) → `SubagentStarted` counted in beacon, no message sent
- Orchestrator `"quiet"` + agents `"verbose"` → `SubagentStarted` notification sent, not counted in beacon
- `load_config` parses `[notifications.agents] mode = "quiet"` → `NotificationsAgentsConfig(mode="quiet")`
- `load_config` with no `[notifications.agents]` section → `NotificationsAgentsConfig(mode=None)`
- `save_notifications_config` with `agents.mode = "quiet"` writes `notifications.agents.mode = "quiet"` in TOML
- `save_notifications_config` with `agents.mode = None` omits/removes the `mode` key from `[notifications.agents]`
- All existing subagent formatting tests remain green (no regressions)
- Tests: `_resolve_agent_mode` (None → inherit, explicit → override), `format_event` matrix (all orchestrator/agent mode combos for subagent events), `handle_message` integration (quiet orch + normal agents shows event), config load/save round-trip for `agents.mode`

#### Technical Notes

Config shape:
```toml
[notifications]
mode = "normal"          # orchestrator: thinking, tool calls, tool results, response

[notifications.agents]
mode = "quiet"           # sub-agent lifecycle events; omit section to inherit from notifications.mode
```

**Inheritance rule:** If `[notifications.agents]` is absent or `mode` is not set, sub-agent events follow `notifications.mode`. If explicitly set, that value pins agent events regardless of the orchestrator mode. Runtime commands (`/quiet`, `/normal`, `/verbose`, `/debug`) change only `notifications.mode`; agents with an explicit override stay pinned.

New dataclass: `NotificationsAgentsConfig(mode: str | None = None)` — `None` means inherit from orchestrator.

New helper `_resolve_agent_mode(notifications: NotificationsConfig | None) -> str` in `archon/chat/handler.py`.

#### Related Documents

- [Architecture Overview](../Architecture/100_system_architecture_overview.md)
