# 14 — History Event Filtering

**Purpose**: Allow users to suppress specific event types from session history Markdown files via `config.toml`, reducing noise for high-volume sessions.
**Audience**: Archon developers
**Status**: Completed
**Priority**: P2
**Estimated Effort**: 3 tasks, ~0.5 day
**Last reviewed**: 2026-03-21
**Next review**: 2026-04-21

---

## Background

Session history files (`~/.archon/history/sessions/YYYY-MM-DD.md`) log every event: thinking blocks, tool calls, tool results, routing decisions, classification output, etc. For active sessions this produces very verbose files where the actual responses are buried in noise.

The existing `suppressed_tool_results` config already compresses read-like tool result *bodies* — but the tool headers still appear. Users need a coarser filter: exclude entire event types from the log.

---

## Design

Add `suppressed_events: list[str]` to `HistoryConfig`. Default is `[]` (log everything — no behaviour change).

### Event type names

| Config name | Event class(es) suppressed |
|---|---|
| `thinking` | `ThinkingResult` (main + router variants) |
| `tool_started` | `ToolStarted` (main + router variants) |
| `tool_result` | `ToolResult` (main + router variants) |
| `response` | `Response` (main only) |
| `routing_decision` | `Response` where `source == "router"` |
| `error` | `ErrorEvent` (main + router variants) |
| `classification` | `ClassificationEvent` |
| `routing` | `RoutingEvent` (🔀 Pipeline routing choice) |
| `fallback` | `FallbackNoticeEvent` |
| `promotion` | `PromotionEvent` |
| `plan` | `PlanEvent` |
| `subagent` | `SubagentStarted` + `SubagentStopped` |
| `wave` | `WaveStarted` + `WaveCompleted` |
| `recovery` | `RecoveryEvent` |
| `reminder` | `ReminderInjectedEvent` |

Router variants of `thinking`, `tool_started`, `tool_result`, and `error` are suppressed by the same name as their main counterparts — no separate `router_*` keys needed.

> **Warning — suppressing `response`**: If `"response"` is added to `suppressed_events`, `HistoryCompactor` (which reads history files to produce daily summaries) will have no response text to summarize, and `ContextProvider` (which injects history context into new sessions) will inject incomplete context. Use this option only when history files are consumed by external tooling that does not rely on response text.

### Example config

```toml
[history]
suppressed_events = ["thinking", "tool_started", "tool_result", "routing_decision", "classification"]
```

### Data flow

```
HistoryConfig.suppressed_events (list[str])
    ├── HistoryManager.__init__() → passes frozenset[str] to EventRenderer
    │       └── EventRenderer.__init__() → stores as self._suppressed_events
    │               └── EventRenderer.render() → _get_event_filter_name() → early-return "" if suppressed
    └── AgentLogger.__init__() → passes frozenset[str] to each AgentLogWriter
            └── AgentLogWriter.__init__() → passes it to EventRenderer
                    └── EventRenderer.render() → same early-return path
```

---

## Tasks

### Task 1 — Config: add `suppressed_events` to `HistoryConfig`

- **Files**:
  - [x] `archon/ai/event_renderer.py` (prerequisite, see Task 2):
    - `VALID_SUPPRESSED_EVENT_NAMES: frozenset[str]` is the single source of truth for valid names (derived from `_EVENT_TYPE_MAP` values — see Task 2). Import this in `loader.py`.
  - [x] `archon/config/loader.py`:
    - Import `VALID_SUPPRESSED_EVENT_NAMES` from `archon.ai.event_renderer`
    - Add `suppressed_events: list[str] = field(default_factory=list)` to `HistoryConfig`
    - Parse from `history_data.get("suppressed_events", [])` in the `HistoryConfig(...)` constructor call
    - Validate: unknown names raise `ConfigError` referencing `VALID_SUPPRESSED_EVENT_NAMES` — do **not** hardcode the valid set in `loader.py`

- **Tests**:
  - [x] `tests/config/test_config_loader.py`:
    - `test_history_suppressed_events_default_empty` — omitted from TOML → `[]`
    - `test_history_suppressed_events_parses_list` — `["thinking", "tool_result"]` parsed correctly
    - `test_history_suppressed_events_unknown_name_raises` — `["bogus"]` → `ConfigError`

- **Checkpoint**: `uv run pytest tests/config/ -v`

---

### Task 2 — EventRenderer: skip suppressed event types

- **Files**:
  - [x] `archon/ai/event_renderer.py`:
    - Add a module-level `_EVENT_TYPE_MAP: dict[type, str]` mapping each event class to its config name. Where multiple classes share the same name, each gets its own entry pointing to the same string — no tuples. Example:
      ```python
      _EVENT_TYPE_MAP: dict[type, str] = {
          ThinkingResult: "thinking",
          ToolStarted: "tool_started",
          ToolResult: "tool_result",
          Response: "response",   # router variant handled separately in _get_event_filter_name
          ErrorEvent: "error",
          ClassificationEvent: "classification",
          RoutingEvent: "routing",
          FallbackNoticeEvent: "fallback",
          PromotionEvent: "promotion",
          PlanEvent: "plan",
          SubagentStarted: "subagent",
          SubagentStopped: "subagent",
          WaveStarted: "wave",
          WaveCompleted: "wave",
          RecoveryEvent: "recovery",
          ReminderInjectedEvent: "reminder",
      }
      ```
    - Add `VALID_SUPPRESSED_EVENT_NAMES: frozenset[str] = frozenset(_EVENT_TYPE_MAP.values()) | {"routing_decision"}` as a public module-level constant. This is the **single source of truth** imported by `loader.py` for validation — no duplication.
    - Add `suppressed_events: frozenset[str] | None = None` param to `__init__()`. Normalize `None` to `frozenset()` in `__init__` — mirroring the existing `suppressed_tools` pattern — so `self._suppressed_events` is always a `frozenset[str]`, never `None`. This prevents `TypeError` on the `in` check in `render()` at all existing call sites that don't pass this param.
    - Add a private helper `_get_event_filter_name(self, event: Event) -> str | None` that:
      1. Looks up `type(event)` in `_EVENT_TYPE_MAP` to get the base name (or returns `None` if not found)
      2. Special-cases `Response`: if `is_router_event(event)` is `True`, returns `"routing_decision"` instead of `"response"`
    - At the **very top** of `render()` — before any other rendering logic — call `_get_event_filter_name(event)` and, if the result is in `self._suppressed_events`, return `""` immediately. Event-level suppression always wins over all other rendering logic, including `suppressed_tool_results`.

- **Tests**:
  - [x] `tests/ai/test_event_renderer.py`:
    - `test_suppressed_thinking_returns_empty` — `ThinkingResult` suppressed → `""`
    - `test_suppressed_router_thinking_returns_empty` — router `ThinkingResult` suppressed by `"thinking"` → `""`
    - `test_unsuppressed_thinking_renders` — `"thinking"` not in set → normal output
    - `test_suppressed_tool_started_returns_empty` — `ToolStarted` suppressed → `""`
    - `test_suppressed_tool_result_returns_empty` — `ToolResult` suppressed → `""`
    - `test_suppressed_response_does_not_affect_routing_decision` — `"response"` suppressed, router Response → still renders
    - `test_suppressed_routing_decision_does_not_affect_response` — `"routing_decision"` suppressed, main Response → still renders
    - `test_suppressed_subagent_covers_started_and_stopped` — `"subagent"` suppresses both `SubagentStarted` and `SubagentStopped`
    - `test_suppressed_wave_covers_started_and_completed` — `"wave"` suppresses both `WaveStarted` and `WaveCompleted`
    - `test_empty_suppressed_set_renders_all` — `frozenset()` → no events suppressed
    - `test_suppressed_events_wins_over_suppressed_tools` — `ToolResult` event with tool name in `suppressed_tools` AND `"tool_result"` in `suppressed_events` → `""` (event suppression fires first, before any tool-result body logic)

- **Checkpoint**: `uv run pytest tests/ai/test_event_renderer.py -v`

---

### Task 3 — Wire config into HistoryManager + AgentLogger + update docs

- **Files**:
  - [x] `archon/ai/history_manager.py`:
    - Add `suppressed_events: frozenset[str] | None = None` param to `__init__()`
    - Pass it to `EventRenderer(suppressed_tools=..., suppressed_events=...)`
  - [x] `archon/ai/agent_logger.py`:
    - Add `suppressed_events: frozenset[str] | None = None` param to `AgentLogger.__init__()`
    - Store as `self._suppressed_events`
    - Pass it to `AgentLogWriter(...)` alongside `suppressed_tools`
    - Add `suppressed_events: frozenset[str] | None = None` param to `AgentLogWriter.__init__()`
    - Pass it to `EventRenderer(suppressed_tools=suppressed_tools, suppressed_events=suppressed_events)`
  - [x] `archon/gateway/gateway.py` (or wherever `HistoryManager` and `AgentLogger` are instantiated):
    - Pass `frozenset(config.history.suppressed_events)` to both `HistoryManager` and `AgentLogger`
  - [x] `CLAUDE.md`: update `[history]` config section — add `suppressed_events`
  - [x] `examples/config.toml.example`: add commented-out `suppressed_events` example
  - [x] Move this document to `Documentation/Completed/`

- **Tests**:
  - [x] `tests/ai/test_history_manager.py`:
    - `test_suppressed_event_not_written` — `HistoryManager(suppressed_events=frozenset({"thinking"}))`, call `record_event()` with `ThinkingResult` → file not written
    - `test_non_suppressed_event_written` — same setup, call with `Response` → file written
  - [x] `tests/ai/test_agent_logger.py`:
    - `test_agent_log_writer_suppressed_event_not_written` — `AgentLogWriter(..., suppressed_events=frozenset({"thinking"}))`, call `record_event()` with `ThinkingResult` → nothing appended to log file
    - `test_agent_log_writer_non_suppressed_event_written` — same setup, call with `Response` → appended
    - `test_agent_logger_passes_suppressed_events_to_writer` — `AgentLogger(suppressed_events=frozenset({"tool_result"}))`, emit `SubagentStarted` then `ToolResult` → log file does not contain the tool result entry

- **Checkpoint**: `uv run pytest` — full suite green

---

## Dependency graph

```
Task 1 (config + validation) — depends on VALID_SUPPRESSED_EVENT_NAMES from Task 2
    └── Task 2 (EventRenderer filter) — must be implemented first
            └── Task 3 (wire + docs)
```

Note: Task 2 must be implemented before Task 1 because `loader.py` imports `VALID_SUPPRESSED_EVENT_NAMES` from `event_renderer.py`.

## Summary

| Task | Key change | Files |
|---|---|---|
| **1** | `suppressed_events` field in `HistoryConfig` with validation against `VALID_SUPPRESSED_EVENT_NAMES` | `loader.py` |
| **2** | `_get_event_filter_name()` helper + early-return `""` in `EventRenderer.render()`; exports `VALID_SUPPRESSED_EVENT_NAMES` | `event_renderer.py` |
| **3** | Wire config into `HistoryManager` **and** `AgentLogger`/`AgentLogWriter`, update docs and example config | `history_manager.py`, `agent_logger.py`, gateway, docs |
