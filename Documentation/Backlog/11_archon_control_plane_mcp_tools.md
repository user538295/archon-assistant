# 11 — Archon Control Plane MCP Tools

**Purpose**: Expose safe MCP tools for service management, agent control, session inspection, communication, and configuration — preventing background agents from resorting to dangerous shell commands.
**Audience**: Archon developers, background agents, orchestrator sessions
**Status**: Pending
**Priority**: P1
**Estimated Effort**: 5 phases, ~23 tasks
**Last reviewed**: 2026-03-17
**Next review**: 2026-04-17

---

## Background

On 2026-03-17, background agent Nova executed `launchctl unload com.archon.assistant.plist` to reload the Archon daemon — killing its own host process. The `launchctl load` that would have restarted Archon never executed because Nova was cancelled during shutdown. Root cause: no safe MCP tool existed for service restart, so the Decomposer generated shell commands.

**Three gaps exposed:**
1. No safe tool — agents had no MCP alternative to `launchctl`/`systemctl`/`kill`
2. Bad plan — the Decomposer's route_task prompt generated dangerous instructions
3. No guardrail — nothing prevented the self-destructive command

This epic addresses all three layers.

## Architecture

### Shared ArchonToolkit

A single `ArchonToolkit` class implements all tool logic as a **facade/mediator** (not a god object — it delegates to existing components, adding no business logic of its own). Both MCP servers instantiate it with shared dependencies.

```
ArchonToolkit (shared implementation)
  ├── Dependencies: SessionManager, BackgroundAgentManager,
  │                 JobScheduler, SkillLoader, Bot, Config, RestartCoordinator
  ├── Service:      archon_status(), archon_restart()
  ├── Agents:       list_running_agents(), get_agent_status(), cancel_agent(), read_agent_log()
  ├── Sessions:     get_session_status(), get_context_stats()
  ├── Comms:        send_notification(), set_notification_mode()
  ├── Model:        get_model(), set_model()
  ├── Config:       list_skills(), list_scheduled_tasks()
  └── Schedule:     add_scheduled_task(), update_scheduled_task(), remove_scheduled_task()

ArchonMCPServer (port 18182, background agents)
  └── Registers ALL toolkit tools + existing spawn_background_agent

ArchonOrchestratorMCPServer (port 18183, orchestrator sessions)
  └── Registers ALL toolkit tools + existing history_list/read/grep
```

### Explicit tool registration (not "delegate unknown")

Each MCP server explicitly registers the toolkit tool names it handles. Unknown tools return an error (existing safe-by-default behavior preserved). This prevents namespace collisions with future SDK tool names.

### Trust model — all tools available to all callers

All 17 tools are available on both MCP servers. Background agents are spawned by the orchestrator (which runs on behalf of the authenticated user), so they inherit the same trust level. The hard guardrails are per-tool safeguards, not per-caller restrictions:

- `archon_restart`: cross-process rate limiting (60s), Telegram notification, delay clamp [2–60s]
- `add_scheduled_task`: creates as `enabled=false`, Telegram notification, min 5m cron, max 20 tasks, `tomli_w` serialization
- `send_notification`: rate limited 1/10s per user_id
- `cancel_agent`, `set_model`, `set_notification_mode`: audit logged at WARNING level

### User-scoped authorization

Tools that accept `user_id` enforce ownership when called via the background agent MCP server (which has `user_id` in the path `/mcp/{user_id}`):
- `cancel_agent(run_id)`: verify `run.user_id == caller_user_id` before cancelling
- `get_agent_status(run_id)`: verify ownership before returning data
- `read_agent_log(run_id)`: verify ownership before reading

**Orchestrator bypass**: The orchestrator MCP server (port 18183) has no per-user path — it passes `user_id=None` to `call_tool()`. When `user_id` is None, ownership checks are skipped. This is acceptable because:
1. Orchestrator sessions are created by the Decomposer, which runs on behalf of the authenticated user
2. The orchestrator server is localhost-only with bearer token auth
3. Archon is a single-user daemon — the orchestrator always acts for the one whitelisted user

**user_id=None behavior differs by tool** — this is intentional, driven by the BAM API:
- `list_running_agents`: **requires user_id** — returns `"No user context available."` when None. Reason: `bg_manager.list_running(user_id)` and `list_all(user_id)` both filter by user_id. There is no "list all users' agents" API.
- `get_agent_status`, `cancel_agent`, `read_agent_log`: **allows user_id=None** — skips ownership check. Reason: `bg_manager.get_run(run_id)` looks up by run_id directly, no user_id needed.
- `get_agent_by_name`: **requires user_id** — same reason as `list_running_agents` (uses `list_all(user_id)`).

For write tools that require a target user (e.g., `send_notification`), the orchestrator path uses the first (or only) whitelisted user ID from `config.access.allowed_user_ids`. This matches the single-user model.

### Instance-level tool registry

`TOOL_DEFINITIONS` is an **instance** attribute (not class attribute) to avoid shared mutable state between test instances. Populated in `__init__()` from a static schema.

### Audit logging

Every `call_tool()` invocation logs to `archon` logger at INFO level: `"MCP tool call: {tool_name}({truncated_args}) by user={user_id} -> {status}"`. Mutating operations (`archon_restart`, `set_model`, `add/update/remove_scheduled_task`) additionally log at WARNING level with full arguments for post-incident forensics.

### Session history integration

Toolkit tool calls must appear in session history the same way SDK tool calls do. `call_tool()` emits **synthetic `ToolStarted` and `ToolResult` events** (from `archon/ai/event_mapper.py`) via an optional `event_callback` parameter. This means:
- `call_tool()` accepts an optional `event_callback: Callable[[Event], None] | None` parameter
- Before executing, it emits `ToolStarted(name=tool_name, input=truncated_args)`
- After executing, it emits `ToolResult(content=truncated_result)` (or `ToolResult(is_error=True)` on handler failure)

**MCP servers do NOT pass `event_callback`** — by design. When a background agent or orchestrator session calls a toolkit tool via MCP, the Claude SDK on the caller's side already logs the MCP tool call + result in that session's history. Passing `event_callback` at the MCP server level would double-log or log to the wrong session context. The `event_callback` exists for a future scenario where toolkit tools might be called directly from within a Pipeline/session context (not via MCP) — e.g., inline tool use during decomposition.

### RestartCoordinator

Async-safe restart mechanism with multi-layer protection:
1. Background agents call `archon_restart()` → sets an `asyncio.Event` with a configurable delay
2. Gateway watcher task picks it up → **sends Telegram notification to user** → initiates graceful `stop_all()` → `os.execv()`
3. **Restart rate limiting across process restarts**: writes timestamp to `~/.archon/.last_restart` before `os.execv()`. On startup, refuses restart requests within 60 seconds of last restart.
4. **Idempotent shutdown**: `stop_all()` protected by a `_shutting_down` flag to prevent concurrent shutdown sequences (SIGTERM + restart watcher race).

### Safety Prompt Layer

Agent injection (`agents.md` / `REMINDER.md`) updated with explicit rules:
- NEVER use `launchctl`, `systemctl`, `kill`, `pkill` to manage Archon or its services
- Use the available MCP tools instead (with tool names listed)

> **Accepted risk**: The prompt layer is a soft guardrail — LLMs can ignore instructions. This is defense-in-depth, not a guarantee. The per-tool safeguards (rate limiting, `enabled=false`, Telegram notifications, audit logging) provide the hard guardrails.

---

## Scope

### In Scope
- `ArchonToolkit` class with 17 tools across 5 categories
- `RestartCoordinator` for async-safe restart with cross-process rate limiting
- Explicit tool registration in both MCP servers (all tools on both)
- Audit logging for all tool invocations
- User-scoped authorization for agent management tools
- Safety prompt additions
- TDD: unit + integration + E2E tests bundled with every tool task

### Out of Scope
- Merging the two MCP servers into one
- Code-level command blocking / sandboxing (accepted risk — agents still have shell access via bypassPermissions)
- Changes to Claude Agent SDK integration

---

## Cross-cutting: Test organization

Tests are split by category to avoid a monolithic test file:
- `tests/ai/test_archon_toolkit_service.py` — archon_status, archon_restart
- `tests/ai/test_archon_toolkit_agents.py` — list_running_agents, get_agent_status, cancel_agent, read_agent_log
- `tests/ai/test_archon_toolkit_sessions.py` — get_session_status, get_context_stats
- `tests/ai/test_archon_toolkit_comms.py` — send_notification, set_notification_mode
- `tests/ai/test_archon_toolkit_config.py` — get_model, set_model, list_skills, list_scheduled_tasks
- `tests/ai/test_archon_toolkit_schedule.py` — add_scheduled_task, update_scheduled_task, remove_scheduled_task
- `tests/ai/test_archon_toolkit_core.py` — scaffold, call_tool dispatch, audit logging, authorization

Integration tests (HTTP MCP calls) extend existing `tests/ai/test_archon_mcp_server.py` and `tests/ai/test_archon_orch_mcp_server.py`.

### Shared E2E test fixture

E2E tests require a `BackgroundAgentManager` → `SessionManager` → `Pipeline` dependency chain. Since BAM has no `session_factory` param (it uses `SessionManager`), E2E fixtures must:
1. Create a `SessionManager` with a mock `session_factory` that returns a slow mock `Pipeline`
2. Create a `BackgroundAgentManager` with that `SessionManager` + mock `Bot`
3. Create `ArchonToolkit` with both

Provide a shared `conftest.py` fixture `toolkit_with_real_bam` that builds this chain. Use `_make_slow_claude_session()` (existing pattern) for tests that need to observe `running` state, and `asyncio.Event` barriers for timing control — never wall-clock sleeps.

### Rate limiter time source

Rate limiting in `send_notification` uses an injectable `_clock` callable (defaulting to `time.monotonic`). Tests inject a mock clock. Rate limiting is per `user_id` only (not per `caller_context` — simpler, harder to bypass).

---

## Phase 1 — Foundation & Service Management

> **Releasable after each tool task.** After Task 1.5 agents can check status; after Task 1.6 orchestrator can trigger safe restarts.

### Task 1.1 — Create `RestartCoordinator` class

- [x] **File**: `archon/ai/restart_coordinator.py`
- **Depends on**: nothing
- **Description**: Create `RestartCoordinator` with:
  - `schedule(reason: str, delay_seconds: float = 5.0) -> str` — stores reason, starts a background `asyncio.Task` that sleeps for `delay_seconds` then sets `_event`. Returns confirmation string. Raises `RuntimeError` if restart already scheduled.
  - `wait() -> tuple[str, float]` — awaits `_event`, returns `(reason, delay_seconds)`.
  - `is_scheduled` property — `bool`.
  - `cancel()` — cancels pending restart if not yet fired.
  - **Cross-process restart rate limiting**: `check_restart_allowed(restart_file: Path) -> bool` — reads `~/.archon/.last_restart`, returns False if last restart was < 60s ago. `write_restart_timestamp(restart_file: Path)` — writes current timestamp before `os.execv()`.
- **Tests (TDD)** — `tests/ai/test_restart_coordinator.py`:
  - Unit: `test_schedule_sets_event_after_delay` — schedule with 0.1s delay, await `wait()`, assert returns reason. Use `asyncio.Event` barrier, not wall-clock sleep.
  - Unit: `test_schedule_raises_if_already_scheduled` — schedule twice, assert `RuntimeError`.
  - Unit: `test_is_scheduled_property` — False before schedule, True after.
  - Unit: `test_cancel_prevents_restart` — schedule, cancel, assert event not set after reasonable wait using `asyncio.wait_for`.
  - Unit: `test_check_restart_allowed_no_file` — no file exists, returns True.
  - Unit: `test_check_restart_allowed_recent` — file with recent timestamp, returns False.
  - Unit: `test_check_restart_allowed_old` — file with old timestamp, returns True.
  - Unit: `test_write_restart_timestamp` — writes file, verify parseable timestamp.
  - Checkpoint: `uv run pytest tests/ai/test_restart_coordinator.py -v`

### Task 1.2 — Create `ArchonToolkit` class scaffold with MCP server wiring

- [x] **File**: `archon/ai/archon_toolkit.py` + modify `archon/ai/archon_mcp_server.py` + modify `archon/ai/archon_orch_mcp_server.py`
- **Depends on**: Task 1.1
- **Description**: Create `ArchonToolkit` class with:
  - Constructor accepting all dependencies as keyword args: `session_manager: SessionManager | None`, `bg_manager: BackgroundAgentManager | None`, `restart_coordinator: RestartCoordinator | None`, `bot: Bot | None`, `config: ArchonConfig | None`, `skill_loader: SkillLoader | None`, `job_scheduler: JobScheduler | None`, `gateway_started_at: float | None`. All default to `None` — tools that require missing deps raise `RuntimeError("dependency X not available")`.
  - `tool_definitions: list[dict]` **instance** attribute — JSON-serializable MCP tool schemas (empty list initially, grows as tools are added in subsequent tasks). Populated in `__init__()`.
  - `async def call_tool(name: str, arguments: dict, user_id: int | None = None, event_callback: Callable[[Event], None] | None = None) -> str` — dispatcher method. Raises `ValueError` for unknown tool. **Audit logging**: logs every call at INFO level, mutating operations at WARNING level. **Session history**: emits synthetic `ToolStarted(name, input)` before execution and `ToolResult(content)` after execution via `event_callback` (if provided). On handler failure, emits `ToolResult(is_error=True)` and re-raises.
  - `register_tool(name, schema, handler)` — public API for tool registration. Raises `ValueError` on duplicate name.
  - **MCP server wiring — explicit registration** (not "delegate unknown"):
    - `ArchonMCPServer`: add `toolkit: ArchonToolkit | None = None` to `__init__()`. In `_handle_tools_list()`: append all `toolkit.tool_definitions`. In `_handle_tools_call()`: if tool name in `toolkit.tool_names`, delegate to `toolkit.call_tool(name, arguments, user_id)`. MCP servers do NOT pass `event_callback` (see architecture §Session history integration). Unknown tools still return error.
    - `ArchonOrchestratorMCPServer`: same pattern — add `toolkit`, append all `toolkit.tool_definitions`, delegate to `toolkit.call_tool(name, arguments, user_id=None)`. Orchestrator has no per-user path — `user_id=None` by design (see architecture §User-scoped authorization).
    - Preserve all existing tool behavior unchanged.
- **Tests (TDD)**:
  - `tests/ai/test_archon_toolkit_core.py`:
    - Unit: `test_construction_with_no_deps` — instantiate with all None, assert no crash.
    - Unit: `test_call_tool_unknown_raises` — call unknown tool, assert `ValueError`.
    - Unit: `test_tool_definitions_is_instance_attr` — two instances have independent `tool_definitions`.
    - Unit: `test_audit_logging` — call a tool, assert log message contains tool name + user_id.
    - Unit: `test_event_callback_emits_tool_started_and_result` — call a tool with a mock `event_callback`, assert it receives `ToolStarted` then `ToolResult` events with correct name/content.
    - Unit: `test_event_callback_none_no_error` — call a tool without `event_callback`, assert no error (callback is optional).
  - `tests/ai/test_archon_mcp_server.py` (extend):
    - Integration: `test_all_toolkit_tools_exposed` — create server with toolkit, call tools/list, assert all toolkit tools present alongside `spawn_background_agent`.
    - Integration: `test_spawn_still_works_with_toolkit` — call `spawn_background_agent`, assert existing behavior unchanged.
    - Integration: `test_unknown_tool_still_rejected` — call a completely unknown tool, assert error (not delegated).
  - `tests/ai/test_archon_orch_mcp_server.py` (extend):
    - Integration: `test_all_toolkit_tools_exposed` — create server with toolkit, assert all toolkit tools present alongside history tools.
    - Integration: `test_history_tools_still_work_with_toolkit` — call `history_list`, assert existing behavior unchanged.
    - Integration: `test_unknown_tool_still_rejected` — call unknown tool, assert error.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_core.py tests/ai/test_archon_mcp_server.py tests/ai/test_archon_orch_mcp_server.py -v`

### Task 1.3 — Wire ArchonToolkit and RestartCoordinator in Gateway

- [x] **File**: `archon/gateway/gateway.py` (modify)
- **Depends on**: Task 1.2
- **Description**:
  - In `Gateway.start()`:
    1. Create `RestartCoordinator()` instance.
    2. Create `ArchonToolkit(session_manager=..., bg_manager=..., restart_coordinator=..., bot=..., config=..., skill_loader=..., job_scheduler=..., gateway_started_at=time.monotonic())`.
    3. Pass `toolkit=toolkit` to both `ArchonMCPServer(...)` and `ArchonOrchestratorMCPServer(...)`.
    4. Spawn background task `_restart_watcher(restart_coordinator)`.
  - Implement `_restart_watcher(coordinator)`:
    1. `reason, delay = await coordinator.wait()`
    2. **Send Telegram notification**: `bot.send_message(chat_id, f"🔄 Restart scheduled by agent: {reason}")` to all whitelisted users. Also append the restart message to the active session's history log via `HistoryManager` so it's preserved in the session record.
    3. `coordinator.write_restart_timestamp(restart_file)` — persist timestamp for cross-process rate limiting.
    4. Log: `"Restart requested: {reason}"`
    5. Call `stop_all()` (existing shutdown sequence).
    6. Call `get_runtime().restart_process()` (existing restart mechanism).
  - **Idempotent shutdown**: Add `_shutting_down` flag to `stop_all()` — early return if already in progress (prevents SIGTERM + restart watcher race).
  - Add `RestartCoordinator` to the shutdown sequence in `stop_all()` — cancel if pending.
- **Tests (TDD)** — `tests/gateway/test_gateway_restart_watcher.py`:
  - Integration: `test_restart_watcher_triggers_shutdown` — create coordinator + mock gateway, schedule restart with 0.1s delay, assert `stop_all()` called.
  - Integration: `test_restart_watcher_sends_notification` — assert bot.send_message called before stop_all.
  - Integration: `test_restart_watcher_writes_timestamp` — assert `.last_restart` file created.
  - Integration: `test_restart_watcher_cancelled_on_normal_shutdown` — start watcher, call `stop_all()` normally, assert watcher task cancelled cleanly.
  - Integration: `test_stop_all_idempotent` — call stop_all twice concurrently, assert no errors.
  - E2E: `test_gateway_exposes_toolkit_on_both_ports` — start gateway with test config, HTTP GET tools/list on both MCP ports, assert all toolkit tools present on both.
  - Checkpoint: `uv run pytest tests/gateway/test_gateway_restart_watcher.py -v`

### Task 1.4 — Add safety rules to agent prompt injection

- [x] **Files**: `workspace/REMINDER.md` (modify), verify `agents.md` injection path
- **Depends on**: Task 1.2
- **Description**:
  - Add a `## Archon Control Plane` section to `REMINDER.md` with:
    ```
    ## Archon Control Plane
    You have MCP tools for managing Archon. NEVER use shell commands
    (launchctl, systemctl, kill, pkill, killall) to manage Archon,
    its services, or background agents. Use these MCP tools instead:
    - archon_status — check daemon health and state
    - archon_restart — schedule a safe graceful restart
    - list_running_agents — see running background agents
    - cancel_agent — cancel a background agent
    - send_notification — send a message to the user
    ```
  - Update the tool list as new tools are added in later phases.
- **Tests (TDD)** — `tests/ai/test_reminder.py` (extend or create):
  - Unit: `test_reminder_contains_control_plane_section` — read `REMINDER.md`, assert `"Archon Control Plane"` section present.
  - Unit: `test_reminder_lists_mcp_tools` — assert `archon_restart` and `archon_status` mentioned.
  - Unit: `test_reminder_forbids_shell_commands` — assert `launchctl`, `systemctl`, `kill` mentioned as forbidden.
  - Checkpoint: `uv run pytest tests/ai/test_reminder.py -v`

### Task 1.5 — Implement `archon_status()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.3
- **Description**: Implement `archon_status()` returning JSON string with:
  - `uptime_seconds`: `time.monotonic() - gateway_started_at`
  - `active_sessions`: `len(session_manager.processing_sessions())`
  - `running_agents`: count from `bg_manager.list_running(user_id)` if user_id provided, else total across all users
  - `notification_mode`: from `config.notifications.mode`
  - `model`: from `session_manager.get_model()`
  - `restart_scheduled`: from `restart_coordinator.is_scheduled`
  - Add tool schema to `tool_definitions`. Add tool schema to `tool_definitions`.
  - Add to `call_tool` dispatcher.
  - **Releasable**: after this task, `archon_status` is callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_service.py`:
  - Unit: `test_archon_status_returns_json` — mock all deps, call tool, assert valid JSON with all expected keys.
  - Unit: `test_archon_status_missing_deps_partial` — only `config` provided, assert returns partial result (not crash).
  - Integration: `test_archon_status_via_bg_mcp` — create `ArchonMCPServer` with toolkit, HTTP call, assert JSON response (read tool = allowed).
  - Integration: `test_archon_status_via_orch_mcp` — same via orchestrator MCP server.
  - E2E: `test_archon_status_full_stack` — create toolkit with real `RestartCoordinator` + mocked session_manager/bg_manager/config, call `archon_status`, assert `uptime_seconds > 0`, `restart_scheduled: false`, all fields present.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_service.py -k "status" -v`

### Task 1.6 — Implement `archon_restart()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.3
- **Description**: Implement `archon_restart(reason: str, delay_seconds: float = 5.0)`:
  - Validates `delay_seconds` in range `[2.0, 60.0]`, clamps if outside.
  - **Cross-process rate check**: calls `restart_coordinator.check_restart_allowed()`. If too recent, returns: `"Restart denied: last restart was less than 60s ago."`.
  - Calls `restart_coordinator.schedule(reason, delay_seconds)`.
  - Returns confirmation: `"Restart scheduled in {delay_seconds}s. Reason: {reason}"`.
  - If already scheduled, returns: `"Restart already scheduled."`.
  - Add tool schema to `tool_definitions`. Add tool schema to `tool_definitions`.
  - Add to `call_tool` dispatcher.
  - **Releasable**: after this task, `archon_restart` is callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_service.py` (extend):
  - Unit: `test_archon_restart_schedules` — mock coordinator, call tool, assert `schedule()` called with correct args.
  - Unit: `test_archon_restart_clamps_delay` — pass `delay_seconds=0.5`, assert clamped to 2.0.
  - Unit: `test_archon_restart_already_scheduled` — coordinator raises, assert error message returned (no exception).
  - Unit: `test_archon_restart_missing_coordinator_raises` — no coordinator injected, assert `RuntimeError`.
  - Unit: `test_archon_restart_rate_limited_cross_process` — mock `check_restart_allowed()` returning False, assert denied message.
  - Integration: `test_archon_restart_via_bg_mcp` — call via background agent MCP server, assert accepted.
  - Integration: `test_archon_restart_via_orch_mcp` — call via orchestrator MCP server, assert accepted.
  - E2E: `test_archon_restart_then_status_shows_scheduled` — create toolkit with real `RestartCoordinator`, call `archon_restart("test", 30)`, then call `archon_status`, assert `restart_scheduled: true`.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_service.py -k "restart" -v`

---

## Phase 2 — Agent Management

> **Releasable after each tool task.** Each tool is independently usable via MCP the moment its task completes.

### Task 2.1 — Implement `list_running_agents()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `list_running_agents(user_id: int, name: str | None = None)`:
  - **Without `name` filter**: calls `bg_manager.list_running(user_id)` — returns only running agents.
  - **With `name` filter**: calls `bg_manager.list_all(user_id)` and filters by name (case-insensitive). This searches all statuses (running, completed, failed, cancelled) — enabling queries like "what did Atlas do?" even after the agent finished.
  - Returns JSON array of objects: `{run_id, name, task_summary (first 100 chars), age_seconds, status}`.
  - If no agents match, returns `"No running agents."` (no filter) or `"No agent named '{name}' found."` (with filter).
  - Add tool schema: `name: "list_running_agents"`, optional param: `name` (string, "Filter by agent name, e.g. 'Atlas'. Searches all agents including completed ones."). Add to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_agents.py`:
  - Unit: `test_list_running_agents_returns_json_array` — mock bg_manager with 2 running agents, assert correct JSON.
  - Unit: `test_list_running_agents_empty` — mock empty list, assert `"No running agents."`.
  - Unit: `test_list_running_agents_truncates_task` — mock agent with 200-char task, assert truncated to 100.
  - Unit: `test_list_running_agents_filter_by_name` — mock bg_manager.list_all with 3 agents (Atlas running, Nova completed, Iris cancelled), filter by "Atlas", assert only Atlas returned.
  - Unit: `test_list_running_agents_filter_by_name_case_insensitive` — filter by "atlas" (lowercase), assert matches "Atlas".
  - Unit: `test_list_running_agents_filter_by_name_not_found` — filter by "Unknown", assert `"No agent named 'Unknown' found."`.
  - Unit: `test_list_running_agents_filter_by_name_includes_completed` — mock completed agent "Nova", filter by "Nova", assert it appears with status "completed".
  - Integration: `test_list_running_agents_via_mcp` — HTTP call through `ArchonMCPServer`, assert user_id extracted from path and passed correctly.
  - E2E: `test_list_running_agents_with_real_bam` — use `toolkit_with_real_bam` fixture, spawn agent (slow mock session), call `list_running_agents`, assert agent appears with correct name and status.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_agents.py -k "list_running" -v`

### Task 2.2 — Implement `get_agent_status()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `get_agent_status(run_id: str)`:
  - Calls `bg_manager.get_run(run_id)`.
  - **User-scoped authorization**: if `user_id` provided (background agent path), verify `run.user_id == user_id`. Return `"Agent not found."` if mismatch.
  - Returns JSON: `{run_id, name, status, task_summary, age_seconds, result (if completed), error (if failed), log_path}`.
  - If not found, returns `"Agent {run_id} not found."`.
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_agents.py` (extend):
  - Unit: `test_get_agent_status_running` — mock running agent, assert all fields present, result/error null.
  - Unit: `test_get_agent_status_completed` — mock completed agent, assert result field populated.
  - Unit: `test_get_agent_status_not_found` — mock None return, assert error message.
  - Unit: `test_get_agent_status_wrong_user_rejected` — mock agent owned by user A, call with user_id B, assert "not found".
  - Integration: `test_get_agent_status_via_mcp` — HTTP call through MCP server, assert correct JSON response.
  - E2E: `test_get_agent_status_with_real_bam` — use `toolkit_with_real_bam` fixture, spawn agent (slow mock), call `get_agent_status`, assert `status: "running"`. Release event barrier, call again, assert `status: "completed"`.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_agents.py -k "get_agent_status" -v`

### Task 2.3 — Implement `cancel_agent()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `cancel_agent(run_id: str)`:
  - **User-scoped authorization**: if `user_id` provided, call `bg_manager.get_run(run_id)` first, verify `run.user_id == user_id`. Return `"Agent not found."` if mismatch.
  - Calls `bg_manager.cancel(run_id)`.
  - If True: returns `"Agent {run_id} cancelled."`.
  - If False: returns `"Agent {run_id} not found or already finished."`.
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_agents.py` (extend):
  - Unit: `test_cancel_agent_success` — mock cancel returns True, assert success message.
  - Unit: `test_cancel_agent_not_found` — mock cancel returns False, assert failure message.
  - Unit: `test_cancel_agent_missing_manager` — no bg_manager, assert `RuntimeError`.
  - Unit: `test_cancel_agent_wrong_user_rejected` — mock agent owned by user A, call with user_id B, assert "not found".
  - Integration: `test_cancel_agent_via_mcp` — call via MCP server, assert delegation works.
  - E2E: `test_cancel_agent_with_real_bam` — use `toolkit_with_real_bam` fixture, spawn agent (slow mock), cancel, await `run.done.wait()`, assert status "cancelled". Call `list_running_agents`, assert empty.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_agents.py -k "cancel_agent" -v`

### Task 2.4 — Implement `read_agent_log()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `read_agent_log(run_id: str, tail_lines: int = 100)`:
  - **User-scoped authorization**: if `user_id` provided, verify `run.user_id == user_id`.
  - Calls `bg_manager.get_run(run_id)` to get `log_path`.
  - If agent not found or `log_path` is None: returns error message.
  - Reads last `tail_lines` lines from `log_path` (clamp to `[1, 500]`).
  - **Path validation**: resolved log_path must be under the history sessions directory (prevent path traversal). Also check `log_path.is_symlink()` and reject symlinks.
  - Returns the log content as string.
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_agents.py` (extend):
  - Unit: `test_read_agent_log_success` — mock agent with log file (use tmp_path), assert content returned.
  - Unit: `test_read_agent_log_tail_lines` — write 200 lines, request 50, assert only last 50 returned.
  - Unit: `test_read_agent_log_not_found` — mock None run, assert error message.
  - Unit: `test_read_agent_log_path_traversal_blocked` — mock agent with `../../etc/passwd` log_path, assert error.
  - Unit: `test_read_agent_log_symlink_blocked` — create symlink in tmp_path, assert rejected.
  - Unit: `test_read_agent_log_wrong_user_rejected` — mock agent owned by user A, call with user_id B, assert "not found".
  - Integration: `test_read_agent_log_via_mcp` — HTTP call through MCP server, assert log content returned.
  - E2E: `test_read_agent_log_with_real_bam` — use `toolkit_with_real_bam` fixture, spawn agent, wait for log output, call `read_agent_log`, assert content contains agent task text.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_agents.py -k "read_agent_log" -v`

### Task 2.5 — Implement `get_agent_by_name()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `get_agent_by_name(name: str, user_id: int)`:
  - Calls `bg_manager.list_all(user_id)`, finds the agent matching `name` (case-insensitive). If multiple agents share the same name (e.g., reused pool name across sessions), return the most recent one (highest `started_at`).
  - **User-scoped authorization**: if `user_id` provided, only searches that user's agents.
  - Returns JSON with **full details** (not truncated):
    - `run_id`, `name`, `status`, `age_seconds`
    - `task` — the full prompt/task given to the agent
    - `context` — the injected conversation context
    - `user_request` — the original user message that triggered the spawn
    - `result` — agent output (if completed)
    - `error` — error message (if failed)
    - `log_path` — absolute path to the agent's session log file
  - If not found, returns `"No agent named '{name}' found."`.
  - Add tool schema: `name: "get_agent_by_name"`, required: `name` (string, "Agent name like 'Atlas', 'Nova', etc."). Add to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers. This is the human-friendly entry point — one tool call gives Archon everything needed to answer questions about a specific agent.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_agents.py` (extend):
  - Unit: `test_get_agent_by_name_found` — mock list_all with agent "Atlas", call with "Atlas", assert full details returned (task, context, user_request, log_path all present).
  - Unit: `test_get_agent_by_name_case_insensitive` — call with "atlas", assert matches "Atlas".
  - Unit: `test_get_agent_by_name_not_found` — call with "Unknown", assert error message.
  - Unit: `test_get_agent_by_name_returns_most_recent` — mock 2 agents both named "Atlas" (different started_at), assert most recent returned.
  - Unit: `test_get_agent_by_name_wrong_user_rejected` — mock agent owned by user A, call with user_id B, assert "not found".
  - Unit: `test_get_agent_by_name_includes_completed` — mock completed agent with result, assert result field populated.
  - Integration: `test_get_agent_by_name_via_mcp` — HTTP call through MCP server, assert correct response.
  - E2E: `test_get_agent_by_name_with_real_bam` — use `toolkit_with_real_bam` fixture, spawn agent "TestAgent", call `get_agent_by_name("TestAgent")`, assert full details returned with log_path pointing to real file.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_agents.py -k "get_agent_by_name" -v`

---

## Phase 3 — Session Management

> **Releasable after each tool task.**

### Task 3.1 — Implement `get_session_status()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `get_session_status(user_id: int)`:
  - Calls `session_manager.session_diagnostics(user_id)`.
  - If no session: returns `"No active session for user {user_id}."`.
  - Returns JSON with: `{is_processing, processing_seconds, idle_seconds, send_count, is_alive, model}`.
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_sessions.py`:
  - Unit: `test_get_session_status_active` — mock diagnostics return, assert all fields present.
  - Unit: `test_get_session_status_no_session` — mock None return, assert message.
  - Integration: `test_get_session_status_via_mcp` — HTTP call through MCP server, assert delegation.
  - E2E: `test_get_session_status_with_real_session_manager` — create toolkit with real `SessionManager` (mocked Pipeline factory), create session, call tool, assert `is_alive: true`. Stop session, call again, assert "No active session".
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_sessions.py -k "session_status" -v`

### Task 3.2 — Implement `get_context_stats()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `get_context_stats(user_id: int)`:
  - Calls `session_manager.context_stats(user_id)`.
  - If no session: returns `"No active session for user {user_id}."`.
  - Returns JSON with token usage, cost, turns — same structure as `/context` command output.
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_sessions.py` (extend):
  - Unit: `test_get_context_stats_active` — mock stats return, assert JSON structure.
  - Unit: `test_get_context_stats_no_session` — mock None return, assert message.
  - Integration: `test_get_context_stats_via_mcp` — HTTP call through MCP server.
  - E2E: `test_get_context_stats_with_real_session_manager` — create session, call tool, assert token/cost fields present.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_sessions.py -k "context_stats" -v`

---

## Phase 4 — Communication

> **Releasable after each tool task.**

### Task 4.1 — Implement `send_notification()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `send_notification(user_id: int, message: str)`:
  - **Rate-limiting**: track last send time per `user_id` using injectable `_clock` callable (default `time.monotonic`). Reject if called within 10 seconds of last send. Return `"Rate limited. Wait {remaining}s."`.
  - Validate `message` length: max 4000 chars, truncate with `"… [truncated]"` suffix if exceeded.
  - Send via `bot.send_message(chat_id=user_id, text=message)`.
  - Returns `"Notification sent."` on success.
  - On Telegram error: returns `"Failed to send: {error}"` (no exception propagation).
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_comms.py`:
  - Unit: `test_send_notification_success` — mock bot, assert `send_message` called with correct args.
  - Unit: `test_send_notification_rate_limited` — inject mock clock, advance < 10s, assert rejected.
  - Unit: `test_send_notification_rate_limit_expires` — inject mock clock, advance > 10s, assert allowed.
  - Unit: `test_send_notification_truncates_long_message` — 5000-char message, assert truncated.
  - Unit: `test_send_notification_telegram_error` — mock bot raises, assert error message returned (no exception).
  - Integration: `test_send_notification_via_mcp` — call via MCP server, assert bot invoked.
  - E2E: `test_send_notification_rate_limit_lifecycle` — inject controllable clock, send (assert "sent"), send immediately (assert "Rate limited"), advance clock 10s, send (assert "sent").
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_comms.py -k "send_notification" -v`

### Task 4.2 — Implement `set_notification_mode()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `set_notification_mode(user_id: int, mode: str)`:
  - Validate `mode` is one of `{"quiet", "normal", "verbose", "debug"}`. Return error if invalid.
  - Update `config.notifications.mode` (same as `/notify` command does).
  - Returns `"Notification mode set to {mode}."`.
  - Add tool schema. Add tool schema to `tool_definitions`.
  - **Releasable**: after this task, callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_comms.py` (extend):
  - Unit: `test_set_notification_mode_valid` — set to "verbose", assert config updated.
  - Unit: `test_set_notification_mode_invalid` — set to "turbo", assert error message.
  - Integration: `test_set_notification_mode_via_mcp` — call via MCP server, assert config changed.
  - E2E: `test_set_notification_mode_reflects_in_status` — set mode to "debug", call `archon_status`, assert `notification_mode: "debug"`.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_comms.py -k "notification_mode" -v`

---

## Phase 5 — Model, Config & Schedule Management

> **Releasable after each tool task.**

### Task 5.1 — Implement `get_model()` and `set_model()` tools

- [x] **File**: `archon/ai/archon_toolkit.py` (add methods + tool definitions + dispatcher entries)
- **Depends on**: Task 1.2
- **Description**:
  - `get_model()`: returns `session_manager.get_model()` or `config.models.default`. Add tool schema to `tool_definitions`.
  - `set_model(model: str)`: validates against `config.models.available` list. Calls `session_manager.set_model(model)`. Returns confirmation. Returns error if model not in available list. Add tool schema to `tool_definitions`.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_config.py`:
  - Unit: `test_get_model_returns_current` — mock session_manager, assert correct model.
  - Unit: `test_set_model_valid` — set known model, assert success.
  - Unit: `test_set_model_invalid` — set unknown model, assert error with available models listed.
  - Integration: `test_get_model_via_both_mcp` — GET allowed on both servers.
  - Integration: `test_set_model_via_mcp` — call via MCP server, assert model changed.
  - E2E: `test_set_model_then_get_model_roundtrip` — set model, get model, assert match. Call `archon_status`, assert `model` field matches.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_config.py -k "model" -v`

### Task 5.2 — Implement `list_skills()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `list_skills()`:
  - Calls `skill_loader.skills` (returns list of `Skill` dataclasses).
  - Returns JSON array: `[{name, description}]`.
  - If no skills: returns `"No skills available."`.
  - Add tool schema to `tool_definitions`.
  - **Releasable**: callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_config.py` (extend):
  - Unit: `test_list_skills_with_skills` — mock 2 skills, assert JSON array.
  - Unit: `test_list_skills_empty` — mock empty, assert message.
  - Integration: `test_list_skills_via_mcp` — HTTP call, assert correct response.
  - E2E: `test_list_skills_with_real_skill_loader` — tmp_path skills dir with 2 SKILL.md files, assert both returned.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_config.py -k "list_skills" -v`

### Task 5.3 — Implement `list_scheduled_tasks()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `list_scheduled_tasks()`:
  - Calls `job_scheduler.job_statuses` and `job_scheduler.next_run_times()`.
  - Returns JSON array: `[{name, enabled, cron, last_run, last_result, last_error, next_run, run_count}]`.
  - If no jobs: returns `"No scheduled jobs."`.
  - Add tool schema to `tool_definitions`.
  - **Releasable**: callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_config.py` (extend):
  - Unit: `test_list_scheduled_tasks_with_jobs` — mock 2 jobs, assert JSON array with all fields.
  - Unit: `test_list_scheduled_tasks_empty` — mock empty, assert message.
  - Integration: `test_list_scheduled_tasks_via_mcp` — HTTP call, assert correct response.
  - E2E: `test_list_scheduled_tasks_with_real_scheduler` — tmp_path jobs_dir with 1 job TOML, assert job appears.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_config.py -k "list_scheduled" -v`

### Task 5.4 — Implement `add_scheduled_task()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `add_scheduled_task(name: str, cron: str, prompt: str, timeout_seconds: float = 60.0)`:
  - Validate `name`: regex `^[a-zA-Z0-9_-]{1,50}$`. Reject otherwise.
  - Validate `cron`: parse with `croniter`. Reject invalid. **Minimum interval**: reject schedules more frequent than every 5 minutes (configurable).
  - **Max jobs limit**: reject if `len(job_scheduler.job_configs) >= 20` (configurable).
  - **Use `tomli_w`** (or `tomllib` round-trip) for TOML serialization — never string interpolation (prevents TOML injection).
  - Write job bundle directory: `{jobs_dir}/{name}/job.toml` with fields: `name`, `cron`, **`enabled = false`**, `timeout_seconds`, `[[pipeline]] kind = "prompt"`, `value = prompt`.
  - **Jobs created as `enabled = false`** — require human activation via `/scheduled` Telegram command. Send Telegram notification: `"📋 New scheduled job '{name}' created (disabled). Use /scheduled to review and enable."`.
  - Call `job_scheduler.reload_jobs()` to pick up the new (disabled) job.
  - Returns `"Job '{name}' created (disabled). Use /scheduled in Telegram to review and enable."`.
  - If job name already exists: return error (no overwrite).
  - Add tool schema to `tool_definitions`.
  - **Releasable**: callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_schedule.py`:
  - Unit: `test_add_scheduled_task_creates_toml` — tmp_path jobs_dir, call tool, assert file created. Parse TOML with `tomllib`, assert `enabled = false`, prompt matches.
  - Unit: `test_add_scheduled_task_invalid_cron` — pass `"not a cron"`, assert error.
  - Unit: `test_add_scheduled_task_too_frequent_cron` — pass `"* * * * *"` (every minute), assert rejected with "minimum 5 minutes" message.
  - Unit: `test_add_scheduled_task_invalid_name` — pass `"../etc"`, assert error.
  - Unit: `test_add_scheduled_task_duplicate_name` — create twice, assert error on second.
  - Unit: `test_add_scheduled_task_max_jobs_exceeded` — mock 20 existing jobs, assert rejected.
  - Unit: `test_add_scheduled_task_triggers_reload` — mock scheduler, assert `reload_jobs()` called.
  - Unit: `test_add_scheduled_task_sends_notification` — mock bot, assert `send_message` called with job details.
  - Unit: `test_add_scheduled_task_toml_injection_safe` — prompt containing `"""` and `[section]`, assert TOML parses correctly.
  - Integration: `test_add_scheduled_task_via_mcp` — call via MCP server, assert job TOML created.
  - E2E: `test_add_then_list_scheduled_task` — real `JobScheduler` (tmp_path), add job, list jobs, assert appears with `enabled: false`.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_schedule.py -k "add_scheduled" -v`

### Task 5.4b — Extend `/scheduled` Telegram command with enable/disable toggle

- [x] **File**: `archon/chat/commands.py` (modify `/scheduled` handler)
- **Depends on**: Task 5.4
- **Description**: The `add_scheduled_task` MCP tool creates jobs as `enabled=false`. Users need a way to review and enable them via Telegram:
  - Extend the existing `/scheduled` command to show an inline keyboard with ▶️ Enable / ⏸ Disable buttons per job.
  - Callback handler `toggle_job:<name>`: reads current `enabled` state, toggles it, writes updated TOML (using `tomli_w`), calls `job_scheduler.reload_jobs()`.
  - Confirms: `"Job '{name}' enabled."` or `"Job '{name}' disabled."`.
  - This is the human activation path for agent-created jobs — without it, `enabled=false` is a dead end.
- **Tests (TDD)** — `tests/chat/test_commands.py` (extend):
  - Unit: `test_scheduled_command_shows_toggle_buttons` — mock 2 jobs (1 enabled, 1 disabled), assert inline keyboard has correct buttons.
  - Unit: `test_toggle_job_callback_enables` — mock disabled job, trigger callback, assert TOML updated with `enabled=true`, `reload_jobs()` called.
  - Unit: `test_toggle_job_callback_disables` — mock enabled job, trigger callback, assert TOML updated with `enabled=false`.
  - Unit: `test_toggle_job_not_found` — trigger callback for nonexistent job, assert error answer.
  - Checkpoint: `uv run pytest tests/chat/test_commands.py -k "scheduled" -v`

### Task 5.5 — Implement `update_scheduled_task()` tool

- [x] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 5.4
- **Description**: Implement `update_scheduled_task(name: str, cron: str | None = None, prompt: str | None = None, enabled: bool | None = None, timeout_seconds: float | None = None)`:
  - Locate existing job bundle at `{jobs_dir}/{name}/job.toml`. Return error if not found.
  - **Use `tomllib` to read + `tomli_w` to write** — proper TOML round-trip, no string manipulation.
  - Update only provided fields (merge, not replace).
  - Validate `cron` if provided (same rules as Task 5.4 including minimum interval).
  - Write updated TOML back.
  - Call `job_scheduler.reload_jobs()`.
  - Returns `"Job '{name}' updated."` with list of changed fields.
  - Add tool schema to `tool_definitions`.
  - **Releasable**: callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_schedule.py` (extend):
  - Unit: `test_update_scheduled_task_cron` — create job, update cron, parse TOML, assert changed.
  - Unit: `test_update_scheduled_task_enabled_false` — disable job, assert `enabled = false` in TOML.
  - Unit: `test_update_scheduled_task_not_found` — update nonexistent, assert error.
  - Unit: `test_update_scheduled_task_partial` — update only prompt, parse TOML, assert cron unchanged.
  - Unit: `test_update_scheduled_task_too_frequent_cron` — update cron to `* * * * *`, assert rejected.
  - Integration: `test_update_scheduled_task_via_mcp` — call via MCP server, assert TOML updated.
  - E2E: `test_add_update_list_scheduled_task` — real `JobScheduler` (tmp_path), add job, update cron, list jobs, assert updated cron.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_schedule.py -k "update_scheduled" -v`

### Task 5.6 — Implement `remove_scheduled_task()` tool

- [ ] **File**: `archon/ai/archon_toolkit.py` (add method + tool definition + dispatcher entry)
- **Depends on**: Task 1.2
- **Description**: Implement `remove_scheduled_task(name: str)`:
  - Validate name: same regex as Task 5.4.
  - Locate job bundle directory at `{jobs_dir}/{name}/`. Return error if not found.
  - **Safety**: refuse if job is currently running (`job_statuses[name].is_running`).
  - **Symlink check**: verify directory and contents are not symlinks before removal.
  - Remove directory and contents (`shutil.rmtree`).
  - Call `job_scheduler.reload_jobs()`.
  - Returns `"Job '{name}' removed."`.
  - Add tool schema to `tool_definitions`.
  - **Releasable**: callable via both MCP servers.
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_schedule.py` (extend):
  - Unit: `test_remove_scheduled_task_success` — create job dir, remove, assert dir gone.
  - Unit: `test_remove_scheduled_task_not_found` — remove nonexistent, assert error.
  - Unit: `test_remove_scheduled_task_currently_running` — mock is_running=True, assert refused.
  - Unit: `test_remove_scheduled_task_path_traversal` — pass `"../../etc"`, assert error.
  - Unit: `test_remove_scheduled_task_symlink_blocked` — create symlink in jobs_dir, assert rejected.
  - Integration: `test_remove_scheduled_task_via_mcp` — call via MCP server, assert dir removed.
  - E2E: `test_add_remove_list_scheduled_task` — real `JobScheduler` (tmp_path), add job, remove, list, assert empty.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_schedule.py -k "remove_scheduled" -v`

---

## Final — Update REMINDER.md with complete tool inventory

### Task 6.1 — Update safety prompt with complete tool inventory

- [ ] **File**: `workspace/REMINDER.md`
- **Depends on**: Task 5.6
- **Description**: Update the `## Archon Control Plane` section added in Task 1.4 with the final complete list of all 17 tools grouped by category. All tools available on both MCP servers.
- **Tests (TDD)** — `tests/ai/test_reminder.py` (extend):
  - Unit: `test_reminder_lists_all_tools` — read REMINDER.md, assert all 17 tool names mentioned.
  - Checkpoint: `uv run pytest tests/ai/test_reminder.py -v`

---

## Summary

| Phase | Tasks | Tools Added | Releasable |
|-------|-------|-------------|------------|
| 1 — Foundation & Service | 6 | archon_status, archon_restart | After each tool |
| 2 — Agent Management | 5 | list_running_agents, get_agent_status, cancel_agent, read_agent_log, get_agent_by_name | After each tool |
| 3 — Session Management | 2 | get_session_status, get_context_stats | After each tool |
| 4 — Communication | 2 | send_notification, set_notification_mode | After each tool |
| 5 — Model, Config & Schedule | 7 | get_model, set_model, list_skills, list_scheduled_tasks, add_scheduled_task, update_scheduled_task, remove_scheduled_task + `/scheduled` enable/disable toggle | After each tool |
| Final — Prompt update | 1 | — | Yes |

**Totals: 23 tasks, 18 MCP tools + 1 Telegram UI task. All tools available on both MCP servers.**

## Security review findings addressed

| Finding | Resolution |
|---------|------------|
| Cross-user agent access | User-scoped authorization on get_agent_status, cancel_agent, read_agent_log |
| add_scheduled_task persistent code execution | Jobs created as `enabled=false`, Telegram notification, min 5m cron interval, max 20 jobs, `tomli_w` serialization |
| archon_restart DoS | Cross-process rate limiting (60s), Telegram notification before restart, delay clamp [2–60s] |
| Namespace collision ("delegate unknown") | Explicit tool registration per server, unknown tools still error |
| No tool authorization | All tools on both servers — trust model: user → orchestrator → background agent. Per-tool safeguards (rate limits, `enabled=false`, notifications, audit) provide guardrails instead of per-caller restrictions. |
| TOML injection | `tomli_w` serialization library, not string interpolation |
| No audit logging | Every call_tool logged (INFO), write ops at WARNING level |
| Shared mutable state | `tool_definitions` is instance attribute, not class attribute |
| Rate limiter undefined | Per user_id only, injectable clock for testability |
| Single test file | Split into 7 test files by category |
| E2E wiring unspecified | Shared `conftest.py` fixture `toolkit_with_real_bam`, slow mock sessions, asyncio.Event barriers |
| Path traversal incomplete | Symlink checks added to read_agent_log and remove_scheduled_task |
| Restart during shutdown race | Idempotent stop_all() with _shutting_down flag |
| Orchestrator has no user_id | Orchestrator bypass documented — passes user_id=None, ownership checks skipped (single-user daemon, localhost+token auth). Write tools use first whitelisted user_id. |
| No human activation path for disabled jobs | Task 5.4b: `/scheduled` Telegram command extended with enable/disable inline keyboard toggle |
