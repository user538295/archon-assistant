# 12 — Tool Visibility in Session History and Chat

**Purpose**: Make Archon toolkit tool calls and routing session activity visible in session history and Telegram chat — the same way regular SDK tools (Read, Grep, Bash) are already visible — using the existing event pipeline instead of introducing new notification mechanisms.
**Audience**: Archon developers, background agents, orchestrator sessions
**Status**: Pending
**Priority**: P1
**Estimated Effort**: 10 tasks + 1 doc update, ~3–4 days
**Last reviewed**: 2026-03-19
**Next review**: 2026-04-19

---

## Background

When Claude uses a regular SDK tool (Read, Grep, Bash), the event appears in session history and in Telegram (gated by notification mode). When Claude uses an Archon toolkit tool (`archon_status`, `archon_restart`, `list_running_agents`, etc.) the call is completely invisible — no history entry, no Telegram message. The same is true for the routing session's internal activity (routing decisions, history reads, thinking).

**Root cause A — Background agents have no MCP connection.**
`BackgroundAgentManager` spawns `ClaudeSession` without `background_agent_mcp_url`, so agents cannot call any Archon toolkit tools. The toolkit MCP server (port 18183) was never connected to them.

**Root cause B — Routing session events are silently discarded.**
`Decomposer.route_task()` iterates over the routing session's event stream but only extracts the final `Response` (routing decision JSON). Every other event — tool calls, thinking — is consumed and thrown away. Fix: convert to `AsyncIterator[Event | TaskOutput]` that yields each event immediately, delivering them to Telegram one-by-one as they arrive.

---

## Architecture

### Event pipeline (unchanged)

```
ClaudeSession.send() → EventMapper → typed events
    → source="sub-agent"    → AgentLogger (agent log file only, no Telegram)
    → source="orchestrator" → HistoryManager (main session history)
                            → handler.py → Telegram (gated by notification mode)
    → source="router"       → HistoryManager (main session history, distinct rendering)
                            → handler.py → Telegram (verbose/debug only)
```

### Session naming

| Session | Variable (after Task 0.1) | Source value | Role |
|---|---|---|---|
| Main session | `Pipeline` / `ClaudeSession` | `"orchestrator"` | User-facing — processes messages, spawns agents |
| Routing session | `Decomposer._router_session` | `"router"` | Internal — reads history, decides scope |
| Background agent | `ClaudeSession` inside BAM | `"sub-agent"` | Isolated — runs a specific task |

### What reaches where

| Event source | Session history | Telegram quiet | Telegram normal | Telegram verbose | Telegram debug |
|---|---|---|---|---|---|
| `"orchestrator"` (main) | ✅ | ❌ | ✅ tools + response | ✅ + thinking | ✅ everything |
| `"sub-agent"` (agent) | ✅ agent log only | ❌ | ❌ | ❌ | ❌ |
| `"router"` (routing) | ✅ main history | ❌ | ❌ | ✅ tool names + tool results (no Response — `RoutingEvent` covers it) | ✅ tool names + tool results + thinking (no Response) |

**Debug mode shows everything — no suppression by source, no suppression by tool type.**

### Streaming router events in real time

> **Lock hold time**: `Pipeline.send()` holds `self._lock` for its full duration. Telegram delivery for router events happens under the lock, adding latency before the main session starts. Current worst case: `_SUMMARY_WAIT_TIMEOUT(3s) + _ORCH_RESET_TIMEOUT_S(30s) × 2 = 63s` theoretical. Intentional tradeoff — do not treat as a regression. Worst-case reduction tracked separately (see M2 options: outer `asyncio.timeout` on `route_task()` call, or move setup before lock acquisition).

`route_task()` becomes an `AsyncIterator[Event | TaskOutput]` — it yields every router event immediately as it arrives, then yields exactly one `TaskOutput` as a sentinel final item. `Pipeline.send()` distinguishes the two by type: events are re-tagged and yielded to Telegram immediately; the sentinel is captured as `task_output` and consumed internally.

```python
# route_task() — conceptual shape (all existing logic preserved)
async def route_task(self, ...) -> AsyncIterator[Event | TaskOutput]:
    fallback = TaskOutput(content="trivial", ...)
    try:
        async with asyncio.timeout(self._timeout):
            gen = self._router_session.send(instruction)
            try:
                async for event in gen:
                    if isinstance(event, Response):
                        yield event                        # stream Response immediately
                        task_output = self._parse(event)
                        await self._await_pending_summary()
                        self._reset_router_if_needed()
                        yield task_output                  # sentinel — always last
                        return
                    yield event                            # stream all intermediate events
            except Exception:
                pass
            finally:
                await gen.aclose()
    except (asyncio.TimeoutError, Exception):
        pass
    yield fallback                                         # sentinel fallback
```

```python
# In Pipeline.send() — replaces the single await call
# NOTE: requires `import dataclasses` added to pipeline.py (not currently present).
# NOTE: `dataclasses.replace(item, source="router")` requires every event type in
# the router generator to have a `source` field. Verify this for all emittable types.
task_output: TaskOutput | None = None
async for item in self._decomposer.route_task(prompt, instruction):
    if isinstance(item, TaskOutput):
        task_output = item          # consumed, not yielded
    else:
        yield dataclasses.replace(item, source="router")  # real-time delivery
```

> **Pre-implementation checks for Task 2.1**:
> - `pipeline.py` has no `import dataclasses` — add it.
> - `dataclasses.replace(item, source="router")` raises `TypeError` if any event type lacks a `source` field. Verify `ToolStarted`, `ToolResult`, `ThinkingResult`, `Response`, `ErrorEvent` all have `source` before starting. Fix any missing field first. (`WaveStarted`/`WaveCompleted` verified safe: `source="plan_executor"` ≠ `"router"`.)
> - Wrap `async for item in self._decomposer.route_task(...)` in `try/finally` with explicit `aclose()` in `Pipeline.send()`.
> - `_await_pending_summary()` and `_reset_orch_if_needed()` run before the first `yield` — they block on the first `__anext__()` call. This is correct; do not treat as a deadlock when profiling.

### Routing tool result suppression

Routing session tool results (history reads) must not be written as full content to main session history — that causes recursive embedding (routing reads history → result written → next call reads larger file → quadratic growth). `source="router"` `ToolResult` events are rendered as a 160-char summary.

> **`tools=[]` disables ALL SDK built-in tools** (verified: `tests/ai/test_claude_session.py:804`). The routing session has only what port 18183 exposes. After Task 0.2, that is history tools only.

---

## Scope

### In scope
- [ ] SDK spike: verify MCP tool calls surface as SDK events
- [ ] Rename routing session variable
- [ ] Router MCP server restricted to read-only tools only
- [ ] Background agents connected to the MCP server
- [ ] Routing session events streamed in real time to main session
- [ ] Routing tool result and thinking content suppressed in history
- [ ] `EventRenderer` renders `source="router"` events distinctly
- [ ] `handler.py` delivers routing events to Telegram (verbose/debug only)
- [ ] `voice.py` updated for routing event support
- [ ] Documentation updated

### Out of scope
- Background agent tool allowlist (agents have shell access regardless; MCP is the audited path)
- New tools added to `ArchonRouterMCPServer` (Task 0.2 restricts existing tools; no new tools added)
- Changes to MCP server notification logic

---

## Test levels

| Level | What it covers | Mock depth |
|---|---|---|
| **Unit** | Single function / class in isolation | Deep mocks |
| **Integration** | Multiple real components wired together | Minimal mocks (no HTTP, no Telegram) |
| **E2E** | Full stack in-process, real file I/O | Only Telegram bot mocked |
| **Live E2E** | Manual verification checklist against running daemon | None |

---

## Phase 0 — Prepare

### Task 0.0 — Verify the SDK surfaces MCP tool calls as events

- [ ] **Status**: Pending
- **Why first**: The entire benefit of Task 1.1 rests on `claude-agent-sdk 0.1.39` emitting `ToolUseBlock` / `ToolResultBlock` for MCP tool calls the same way it does for built-in tools. If the SDK silently handles MCP calls without surfacing them through the event stream, Task 1.1 delivers nothing visible and the architecture needs redesigning. This must be confirmed before any implementation begins.
- **Dependencies**: None.

**What to verify**: When a `ClaudeSession` is configured with `background_agent_mcp_url` and the model invokes a tool hosted on that MCP server, do `ToolStarted` and `ToolResult` events appear in the session's event stream (via `EventMapper`)?

**Files**:
- [ ] `tests/ai/test_sdk_mcp_event_emission.py` (new spike/verification test)

**Tests**:

- [ ] *Integration spike* — `test_sdk_emits_tool_events_for_mcp_tool_call`:
  - Start a minimal in-process MCP server using the `mcp` Python package (FastMCP or `@server.tool()`) — bare `aiohttp` does not implement MCP JSON-RPC; the real protocol is required
  - Create a `ClaudeSession` with `background_agent_mcp_url` pointing to it
  - Send a prompt that causes the model to call the tool
  - Collect all events from `session.send()`
  - Assert `ToolStarted(name="echo")` and `ToolResult` appear in the event stream
  - Mark test `@pytest.mark.live` (requires real SDK / API key)

- [ ] *Fallback verification*: If live test is not runnable in CI, add a comment documenting the manual verification result and the SDK version it was confirmed on.

**Checkpoint**: `uv run pytest tests/ai/test_sdk_mcp_event_emission.py -v -m live`

**If the test fails**: Stop. The assumption underlying Task 1.1 is wrong. Return to the design phase before proceeding with any other task in this epic.

---

### Task 0.1 — Rename the routing session and its server to match what they do

- [ ] **Status**: Pending
- **Why**: `_orch_session` is the internal routing sub-session — its job is to *route* the request (trivial / small / large). "Orchestrator" implies the top-level coordinator, which is the main user-facing session. Half-renames are technical debt: `router_mcp_url` pointing to `ArchonOrchestratorMCPServer` would force every developer to pause and second-guess. Full rename removes all ambiguity.
- **Dependencies**: None. Pure rename, zero behavior change.

**Full rename map**:

| Old | New |
|-----|-----|
| `archon/ai/archon_orch_mcp_server.py` | `archon/ai/archon_router_mcp_server.py` |
| `ArchonOrchestratorMCPServer` | `ArchonRouterMCPServer` |
| `_orch_session` | `_router_session` |
| `_ensure_orch_session()` | `_ensure_router_session()` |
| `_orch_call_count` | `_router_call_count` |
| `_orch_cost_carryover` | `_router_cost_carryover` |
| `orch_mcp_url` / `orch_mcp_headers` (all occurrences) | `router_mcp_url` / `router_mcp_headers` |
| `orch_mcp_server` (Gateway local var) | `router_mcp_server` |
| `orch_mcp_port` (config key in `BackgroundAgentsConfig`) | `router_mcp_port` — rename in **both** Python attribute AND `config.toml` key. This is a breaking change for existing `config.toml` files; add a migration note to the release changelog and to `examples/config.toml.example`. |

**Files**:

*Renames (use `git mv` to preserve history):*
- [ ] `archon/ai/archon_orch_mcp_server.py` → `archon/ai/archon_router_mcp_server.py` (`git mv`)
- [ ] `tests/ai/test_archon_orch_mcp_server.py` → `tests/ai/test_archon_router_mcp_server.py` (`git mv`)

*Code updates (variable/class/param names):*
- [ ] `archon/ai/archon_router_mcp_server.py`: class `ArchonOrchestratorMCPServer` → `ArchonRouterMCPServer`; all internal references updated
- [ ] `archon/ai/decomposer.py`: `_orch_session` → `_router_session`, `_ensure_orch_session()` → `_ensure_router_session()`, constructor params updated
- [ ] `archon/ai/pipeline.py`: params `orch_mcp_url` / `orch_mcp_headers` → `router_mcp_url` / `router_mcp_headers`
- [ ] `archon/ai/session_manager.py`: stored fields and `Pipeline(...)` call updated
- [ ] `archon/ai/claude_session.py`: any `orch_*` references updated
- [ ] `archon/ai/archon_toolkit.py`: docstring/comment references updated
- [ ] `archon/gateway/gateway.py`: import updated, local variable `orch_mcp_server` → `router_mcp_server`, `SessionManager(...)` call updated
- [ ] `archon/config/loader.py`: rename `orch_mcp_port` key → `router_mcp_port`, comment references updated
- [ ] `examples/config.toml.example`: rename `orch_mcp_port` → `router_mcp_port`, add migration comment
- [ ] `CLAUDE.md`: update `[background_agents]` config section — `orch_mcp_port` → `router_mcp_port`

*Test files (name/variable/import updates):*
- [ ] `tests/ai/test_archon_router_mcp_server.py`: class import updated to `ArchonRouterMCPServer`
- [ ] `tests/ai/test_decomposer.py`: variable names updated
- [ ] `tests/ai/test_orch_redesign_e2e.py`: variable names and any class imports updated
- [ ] `tests/ai/test_orch_redesign_integration.py`: variable names and any class imports updated
- [ ] `tests/ai/test_session_manager.py`: variable names updated
- [ ] `tests/ai/test_background_agent_manager.py`: variable names updated
- [ ] `tests/gateway/test_gateway.py`: variable names updated
- [ ] `tests/gateway/test_shutdown.py`: variable names updated
- [ ] `tests/gateway/test_shutdown_e2e.py`: variable names updated
- [ ] `tests/gateway/test_background_agent_gateway_integration.py`: variable names updated

**Tests**: All existing tests pass with new names. No new test logic needed.

**Checkpoint**: `uv run pytest -v` — full suite green, zero behavior change.

---

### Task 0.2 — Restrict the router MCP server to read-only tools

- [ ] **Status**: Pending
- **Why**: `ArchonRouterMCPServer` currently registers the full toolkit — including `cancel_agent`, `archon_restart`, `add_scheduled_task`, `set_notification_mode`, `set_model` — alongside history tools. It calls `call_tool(tool_name, arguments, user_id=None)`. The `user_id=None` path bypasses the authorization check in `_handle_cancel_agent` — the check (`if user_id is not None: [auth check]`) is skipped entirely when `user_id` is `None`, so `cancel()` is called unconditionally on any `run_id`. This is confirmed in `archon/ai/archon_toolkit.py` lines 748–753. A prompt-injected routing session could call any destructive operation without authorization. The routing session's role is to read history and decide scope — it needs no write or service-management access.
- **Dependencies**: Task 0.1 (file renamed).

**Fix**: Set `ROUTER_ALLOWED_TOOLS = frozenset()` (empty). The routing session (`tools=[]` → no SDK built-ins) has only what port 18183 exposes. History tools (`history_list`, `history_read`, `history_grep`) are hardcoded in `_handle_tools_list()` and NOT part of `self._toolkit.tool_definitions` — they are always present regardless of `ROUTER_ALLOWED_TOOLS`. The router needs only history access to decide scope.

**Files**:
- [ ] `archon/ai/archon_router_mcp_server.py`: add `ROUTER_ALLOWED_TOOLS: frozenset[str] = frozenset()` constant. In `_handle_tools_list()`, filter `self._toolkit.tool_definitions` to only include tools in `ROUTER_ALLOWED_TOOLS` (resulting in an empty toolkit list). In `_handle_call_tool()`, reject (return error) if `tool_name not in ROUTER_ALLOWED_TOOLS` for toolkit-delegated calls.

**Tests**:
- [ ] *Unit*: `test_router_mcp_server_tools_list_empty_toolkit` — assert `list_tools()` response contains zero toolkit tools (no `archon_status`, `cancel_agent`, `get_config`, etc.); history tools (`history_read`, `history_list`, `history_grep`) are still present
- [ ] *Unit*: `test_router_mcp_server_rejects_toolkit_call` — call any toolkit tool (e.g. `archon_status`) via router MCP server, assert error response returned (not executed)
- [ ] *Unit*: `test_router_mcp_server_history_read_hardcoded_not_allowlist_gated` — call `history_read` via router MCP server, assert it executes normally (hardcoded handler, not gated by `ROUTER_ALLOWED_TOOLS`)

**Checkpoint**: `uv run pytest tests/ai/test_archon_router_mcp_server.py -v`

---

## Phase 1 — Background agents can call Archon tools

### Task 1.1 — Connect background agents to the Archon MCP server

- [ ] **Status**: Pending
- **Why**: Background agents are spawned without `background_agent_mcp_url`, so they cannot call any Archon toolkit tools. Connecting them gives agents the audited MCP path for service management — the same operations they could already perform via shell commands, but now with rate limiting, audit logging, and Telegram notifications.
- **Dependencies**: Task 0.0 (SDK spike confirmed), Task 0.1 (clean rename), Task 0.2 (tool restriction in place).

> **Which MCP server**: Connect to `ArchonRouterMCPServer` (port 18183, after rename) with `BG_AGENT_ALLOWED_TOOLS`, NOT `ArchonMCPServer` (port 18182). Port 18182 only has `spawn_background_agent` — connecting agents to it enables recursive spawning. `spawn_background_agent` is not on port 18183, so recursion is architecturally prevented.
>
> **`BG_AGENT_ALLOWED_TOOLS`**: Broader than `ROUTER_ALLOWED_TOOLS` (which is empty). Include read-only + safe write tools: `archon_status`, `list_running_agents`, `get_config`, `get_job_config`, `send_notification`. Exclude: `archon_restart`, `set_config`, `cancel_agent`, `add_scheduled_task`, `remove_scheduled_task`. Final list decided during implementation.

**Files**:
- [ ] `archon/ai/archon_router_mcp_server.py`: add `BG_AGENT_ALLOWED_TOOLS: frozenset[str]` constant. Update `_handle_tools_list()` and `_handle_call_tool()` to accept an `allowed_tools` parameter (or subclass) so the same server class can serve both router (ROUTER_ALLOWED_TOOLS) and background agents (BG_AGENT_ALLOWED_TOOLS).
- [ ] `archon/ai/background_agent_manager.py`: `__init__()` gains `bg_mcp_server: "ArchonRouterMCPServer | None" = None` stored as `self._bg_mcp_server`. In `_run_agent()`, when `self._bg_mcp_server` is set:
  ```python
  background_agent_mcp_url=self._bg_mcp_server.mcp_url_for(user_id),
  mcp_headers=self._bg_mcp_server.mcp_headers_for(user_id),
  ```
- [ ] `archon/gateway/gateway.py`: create a second `ArchonRouterMCPServer` instance (`bg_toolkit_mcp_server`) on a distinct port (e.g. `cfg.background_agents.bg_toolkit_mcp_port`, new config key) with `allowed_tools=BG_AGENT_ALLOWED_TOOLS`. Pass it to `BackgroundAgentManager(bg_mcp_server=bg_toolkit_mcp_server, ...)`. The existing `router_mcp_server` (after rename) remains unchanged for the routing session.

**What works for free**: SDK emits `ToolStarted`/`ToolResult` → BAM loop sets `source="sub-agent"` → `AgentLogger` writes to agent log → `handler.py` suppresses from Telegram.

> **Assumption (A1)**: This entire task assumes the SDK emits identical event structure for MCP tool calls as it does for built-in tool calls. This is unverified and is exactly what Task 0.0 must confirm before this task is implemented. If Task 0.0 fails, the "works for free" claim does not hold and the architecture must be revisited.

**Tests**:
- [ ] *Unit*: `test_spawn_agent_passes_mcp_url_to_session` — mock `bg_mcp_server`, assert `ClaudeSession` created with correct URL and headers
- [ ] *Unit*: `test_spawn_agent_without_mcp_server_omits_url` — `bg_mcp_server=None` → no `background_agent_mcp_url` in ClaudeSession (backward compat)
- [ ] *Unit*: `test_spawn_agent_mcp_url_uses_correct_user_id` — two spawns for different user IDs, assert per-user URLs
- [ ] *Integration*: `test_toolkit_call_appears_in_agent_log` — `toolkit_with_real_bam` fixture, mock session emits `ToolStarted(name="archon_status")` with default `source="orchestrator"` (do NOT pre-set `"sub-agent"`), assert BAM loop's `event.source = "sub-agent"` tagging fires, and `AgentLogger` records the event with `source="sub-agent"`
- [ ] *Integration*: `test_toolkit_call_does_not_appear_in_main_history` — assert `HistoryManager` does NOT record sub-agent `ToolStarted` to main history
- [ ] *E2E*: `test_toolkit_call_not_sent_to_telegram` — mock `bot`, emit sub-agent toolkit events, assert `bot.send_message` never called for toolkit events
- [ ] *Live E2E*:
  1. Start daemon; send task that spawns a background agent
  2. Inspect agent log: `~/.archon/history/sessions/YYYY-MM-DD-HH-MM-{name}.md` — verify `🔧 Tool: archon_status` entries present if agent called toolkit
  3. Inspect main history: `~/.archon/history/sessions/YYYY-MM-DD.md` — verify toolkit calls from agent absent
  4. Verify no `🔧` Telegram messages during agent execution

**Checkpoint**: `uv run pytest tests/ai/test_background_agent_manager.py -k "mcp_url or toolkit_call" -v`

---

## Phase 2 — Routing session activity visible in the main session

### Task 2.1 — Collect routing session events alongside the routing decision

- [ ] **Status**: Pending
- **Why**: `route_task()` currently discards all intermediate events from the routing session. This task converts it to an `AsyncIterator[Event | TaskOutput]` that yields each event immediately as it arrives (real-time delivery), followed by one `TaskOutput` sentinel as the final item. `Pipeline.send()` forwards events to Telegram one-by-one and captures the sentinel as `task_output`. No existing timeout/fallback/cleanup logic changes.

> **Behavior change (finding #8)**: The current `route_task()` implementation iterates ALL events from the orch session and captures the last `Response` as `raw_response` — it does NOT early-return on `Response`. The new design yields `Response` immediately and then returns (early exit). This means any events the SDK emits after `Response` (if any) will be silently skipped. This is intentional — the routing decision is complete at the first `Response` — but it is a deliberate behavior change that should be tested explicitly (assert that nothing after `Response` is consumed).
- **Dependencies**: Task 0.1.

**Deployment note**: Tasks 2.1, 2.2, 2.3, and 2.4 must land in a single merged commit — releasing 2.1 alone would emit unstyled `source="router"` events with no rendering treatment.

> **Existing test migration**: All `await decomposer.route_task(...)` calls in tests break after generator conversion. Audit `tests/ai/test_decomposer.py` before implementation; migrate each to `async for item in decomposer.route_task(...)`. Estimate: 5–10 tests.

**Files**:
- [ ] `archon/ai/event_mapper.py`:
  - Add module-level helper: `def is_router_event(event: object) -> bool: return getattr(event, "source", "") == "router"`
  - Single canonical check — imported by `event_renderer.py` and `handler.py`, no local duplicates
- [ ] `archon/ai/decomposer.py`:
  - Convert `route_task()` from `async def → TaskOutput` to `AsyncIterator[Event | TaskOutput]` using `yield`
  - Yield each router event immediately as it arrives (before any local processing)
  - Yield `Response` event, then parse it for routing decision, then yield `TaskOutput` sentinel as the final item
  - All early-return fallback paths yield a fallback `TaskOutput` sentinel and return
  - All existing `asyncio.timeout`, `gen.aclose()` in `finally`, `_await_pending_summary()`, `_reset_router_if_needed()`, `_pending_turns` — **all untouched**
- [ ] `archon/ai/pipeline.py` (`Pipeline.send()` at line ~204):
  - Replace `task_output = await self._decomposer.route_task(...)` with:
    ```python
    task_output: TaskOutput | None = None
    async for item in self._decomposer.route_task(prompt, instruction):
        if isinstance(item, TaskOutput):
            task_output = item          # captured, not yielded
        else:
            yield dataclasses.replace(item, source="router")
    ```

**Tests**:
- [ ] *Unit*: `test_is_router_event_returns_true_for_router_source` — `is_router_event(ToolStarted(..., source="router"))` → `True`
- [ ] *Unit*: `test_is_router_event_returns_false_for_orchestrator` — `is_router_event(ToolStarted(..., source="orchestrator"))` → `False`
- [ ] *Unit*: `test_route_task_yields_events_then_task_output` — mock routing session emitting `ToolStarted` + `ThinkingResult` + `Response`, assert all events yielded in order, `TaskOutput` yielded last
- [ ] *Unit*: `test_route_task_events_tagged_before_yield` — events yielded by `route_task()` still have `source="orchestrator"` (tagging to `"router"` happens in `Pipeline.send()`)
- [ ] *Unit*: `test_route_task_fallback_mid_stream` — mock session raising mid-stream, assert `TaskOutput` fallback sentinel still yielded as last item (path 5: `gen.send()` exception)
- [ ] *Unit*: `test_route_task_timeout_yields_fallback` — mock session that never yields `Response`, assert `TaskOutput` fallback sentinel yielded (timeout caught inside generator, not propagated) (path 4: `asyncio.timeout` fires)
- [ ] *Unit*: `test_route_task_reset_timeout_yields_fallback` — mock `_reset_router_if_needed()` raising `TimeoutError`, assert `TaskOutput` fallback sentinel yielded (path 1: reset timeout)
- [ ] *Unit*: `test_route_task_ensure_session_timeout_yields_fallback` — mock `_ensure_router_session()` raising `TimeoutError`, assert `TaskOutput` fallback sentinel yielded (path 3: ensure-session timeout)
- [ ] *Unit*: `test_route_task_reset_exception_yields_fallback` — mock `_reset_router_if_needed()` raising `Exception`, assert `TaskOutput` fallback sentinel yielded (path 2: reset exception)
- [ ] *Unit*: `test_route_task_real_time_ordering` — assert first event is yielded BEFORE `TaskOutput` sentinel, verifying no buffering
- [ ] *Unit*: `test_router_events_precede_main_session_events` — in `Pipeline.send()` output for a **task-scope** message (not `intent="chat"`, which never calls `route_task()`), all `source="router"` events must appear before any `source="orchestrator"` event (verifies the pipeline ordering contract: routing phase always completes before execution phase begins)
- [ ] *Integration*: `test_pipeline_yields_router_events_tagged` — `Pipeline` with mock `Decomposer`, assert `source="router"` events appear one-by-one in `Pipeline.send()` output, before main session events
- [ ] *Integration*: `test_pipeline_task_output_consumed_not_yielded` — assert `TaskOutput` never appears in `Pipeline.send()` output (consumed internally)
- [ ] *E2E*: `test_router_events_reach_history_manager` — full `Pipeline` with mock routing session, process message, assert `HistoryManager.record_event` called with `source="router"` events
- [ ] *Live E2E*:
  1. Set mode `verbose`, send a non-trivial task
  2. Watch Telegram — verify `🔧 [Router] history_read` arrives IMMEDIATELY as routing reads history (not after a delay), followed by `🎯 Routing:` then the main session starts

**Checkpoint**: `uv run pytest tests/ai/test_decomposer.py tests/ai/test_pipeline.py -v`

---

### Task 2.2 — Suppress routing tool result content in session history

- [ ] **Status**: Pending
- **Why**: `ToolResult` from `history_read` writes the full history file content into the main session history. On the next routing call, that content is read again — recursive embedding with quadratic growth. Routing tool results should show a summary only, like the existing treatment of Read/Glob/Grep. `ThinkingResult` is written with full content (same as main session) — history is the complete record, Telegram is the filtered view.
- **Dependencies**: Task 2.1 (router events must be in the stream first).

**What changes**:
- `ToolResult` with `source="router"` and `not event.is_error`: render 160-char summary (never full content). Error results shown in full.
- `ThinkingResult` with `source="router"`: full content in history (consistent with main session pattern — history always has more than Telegram).

**Files**:
- [ ] `archon/ai/event_renderer.py`:
  - In the `ToolResult` branch (uses `is_router_event` from `event_mapper.py`):
    ```python
    if is_router_event(event) and not event.is_error:
        summary = (event.content or "")[:160]   # direct slice — do NOT call summarize_tool_result()
        return f"\n### 📤 [Router] Result [{event.id}] · {ts}\n\n{summary}\n"
    ```
    > **Do NOT call `summarize_tool_result(event)`** — raises `ValueError` for tools not in `DEFAULT_SUPPRESSED_TOOLS`. Use `(event.content or "")[:160]` directly with a comment explaining why.

**Tests**:
- [ ] *Unit*: `test_render_router_tool_result_suppressed` — router `ToolResult` with 5000-char content, assert rendered output ≤ 160 chars (summary only)
- [ ] *Unit*: `test_render_non_router_tool_result_unchanged` — `source="orchestrator"` `ToolResult`, assert full content rendered (no regression)
- [ ] *Unit*: `test_render_router_tool_result_error_not_suppressed` — `is_error=True` router `ToolResult`, assert full content rendered (errors always shown)
- [ ] *Unit*: `test_render_router_tool_result_boundary_at_160` — router `ToolResult` with summary producing exactly 160 chars, assert rendered ≤ 160; summary producing 161 chars, assert truncated to 160
- [ ] *Unit*: `test_render_router_tool_result_in_suppressed_config_list` — router `ToolResult` with `tool_name="Read"` (in `DEFAULT_SUPPRESSED_TOOLS`), assert router suppression path takes precedence and produces `[Router]`-prefixed output (not the normal suppression path)
- [ ] *Integration*: `test_recursive_embedding_prevented` — routing session emits `ToolResult` with large history content, assert history file entry is short
- [ ] *Live E2E*: Open main history after a routing call. Verify `📤 [Router] Result` entries are one-line summaries and `💭 [Router] Thinking` entries contain full thinking content.

**Checkpoint**: `uv run pytest tests/ai/test_event_renderer.py -k "router_tool_result" -v`

---

### Task 2.3 — Render routing events distinctly in session history

- [ ] **Status**: Pending
- **Why**: Without distinct rendering, a routing `Response` appears as `✅ Response:` with raw JSON — indistinguishable from Claude's actual answer. All routing events need `[Router]` labels so history is unambiguous.
- **Dependencies**: Task 2.1.

**Files**:
- [ ] `archon/ai/event_renderer.py`: import `is_router_event` from `archon.ai.event_mapper`. For each event type, add `source="router"` branch using `is_router_event(event)`:

  | Event type | `source="router"` render |
  |---|---|
  | `ToolStarted` | `### 🔧 [Router] Tool: {name} [{id}] · {ts}` |
  | `ToolResult` | handled by Task 2.2 |
  | `ThinkingResult` | `### 💭 [Router] Thinking · {ts}\n\n{content}` (full content — history is always the complete record) |
  | `Response` | `### 🎯 Routing decision: · {ts}` |
  | `ErrorEvent` | `### ❌ [Router] Error: · {ts}` |

  `source="orchestrator"` and `source="sub-agent"` render exactly as before.

**History separator**: After all `[Router]` entries, emit `\n---\n` to mark the boundary between routing phase and execution phase. Implementation: `Pipeline.send()` emits the separator after the `route_task()` loop ends (option b — handles both normal-Response and fallback paths). Do NOT suffix the `Response` event (option a) — fallback paths emit no `Response`, so the separator would be missing.

**Tests**:
- [ ] *Unit*: `test_render_router_tool_started` — assert `[Router]` prefix, correct heading
- [ ] *Unit*: `test_render_router_thinking` — assert `[Router] Thinking` heading AND full content present (not suppressed)
- [ ] *Unit*: `test_render_router_response` — assert `🎯 Routing decision:` heading, NOT `✅ Response:`
- [ ] *Unit*: `test_render_router_error` — assert `[Router]` prefix
- [ ] *Unit*: `test_render_main_session_unchanged` — `source="orchestrator"` events render identically (regression guard)
- [ ] *Unit*: `test_render_sub_agent_unchanged` — `source="sub-agent"` events render identically (regression guard)
- [ ] *Live E2E*: Open main history after verbose/debug session. Verify `[Router]` labels on routing events, `🎯 Routing decision:` for routing JSON, `✅ Response:` only on the real user-facing answer.

**Checkpoint**: `uv run pytest tests/ai/test_event_renderer.py -v`

---

### Task 2.4 — Deliver routing events to Telegram correctly

- [ ] **Status**: Pending
- **Why**: Routing events need distinct Telegram formatting and must only appear in `verbose`/`debug` mode — they are internal implementation details, not user-facing communication. In `quiet`/`normal` mode they are still recorded in history but produce no Telegram message.
- **Dependencies**: Task 2.1, Task 2.3 (concepts align).

**Routing events in Telegram**:

| Event type                     | verbose                      | debug                                     |
| ------------------------------ | ---------------------------- | ----------------------------------------- |
| `ToolStarted` source=router    | `🔧 [Router] {name}`         | `🔧 [Router] {name}\n{input}`             |
| `ToolResult` source=router     | `📤 [Router] {summary ≤160}` | `📤 [Router] {summary ≤160}`              |
| `ThinkingResult` source=router | suppressed (history-only)    | `💭 [Router] Thinking:\n{content}` (full) |
| `Response` source=router       | suppressed (history-only)    | suppressed (history-only)                 |

**Why router `Response` is suppressed in Telegram**: `Pipeline` already emits a `RoutingEvent` (`🔀 task_direct` / `🔀 agent_plan: N agents`) after routing completes. This is the user-facing routing outcome and already appears in verbose/debug mode. Showing the raw routing JSON on top would duplicate the information and force `handler.py` to parse JSON for formatting — an unnecessary coupling. The raw decision is recorded in history under `### 🎯 Routing decision:` for auditing.

In `quiet`/`normal`: no Telegram for any `source="router"` event (history-only).

**Quiet-mode beacon exclusion**: Router `ToolStarted` events must NOT increment `counts["tools"]` / `counts["thinking"]` in `handle_message()`. `voice.py` has no beacon counter — no changes needed there.

> **`ErrorEvent` source=router**: Must display `[Router]` prefix in ALL notification modes (including quiet) — routing errors must be distinguishable from user-request errors.

**Files**:
- [ ] `archon/chat/handler.py` — `format_event()`:
  - Import `is_router_event` from `archon.ai.event_mapper` (shared canonical check — no local duplicate)
  - Add `source="router"` branches per notification mode using `is_router_event(event)`
  - For `quiet`/`normal`: return `[]` for all router events (suppress from Telegram, already recorded in history)
  - For `verbose`/`debug`: return formatted message with `[Router]` prefix per table above
  - `source="orchestrator"` events render identically to before — no regression

**Tests**:
- [ ] *Unit*: `test_format_router_events_quiet_returns_empty` — all router event types in quiet mode return `[]`
- [ ] *Unit*: `test_format_router_events_normal_returns_empty` — all router event types in normal mode return `[]`
- [ ] *Unit*: `test_format_router_tool_started_verbose` — assert `[Router]` in output
- [ ] *Unit*: `test_format_router_response_verbose_suppressed` — router `Response` in verbose mode returns `[]` (suppressed — the spec table shows `Response source=router` is history-only in BOTH verbose and debug; the `RoutingEvent` covers the user-facing routing outcome)
- [ ] *Unit*: `test_format_router_response_debug_suppressed` — router `Response` in debug mode also returns `[]` (history-only; raw JSON is in history under `### 🎯 Routing decision:`, not in Telegram)
- [ ] *Unit*: `test_format_main_session_events_unchanged` — regression guard for `source="orchestrator"`
- [ ] *Unit*: `test_router_tool_started_does_not_increment_beacon_count` — router `ToolStarted` in quiet mode, assert `counts["tools"]` not incremented
- [ ] *Integration*: `test_router_events_telegram_gated_by_mode` — quiet: no `bot.send_message`; verbose: `[Router]` message sent
- [ ] *Integration*: `tests/ai/test_event_pipeline_router.py` (new) — real Decomposer + mock routing session, no real file I/O; labeled Integration not E2E per test level definitions:
  - `test_full_stack_router_event_flow` — process through `Pipeline`, assert `source="router"` events tagged, Telegram receives `[Router]` in verbose, `✅ Response:` is distinct
  - `test_debug_mode_all_router_events_visible` — all event types appear in Telegram in debug mode
- [ ] *Live E2E*:
  1. Set mode `debug` (`/debug`)
  2. Send non-trivial task
  3. Verify in Telegram: `🔧 [Router] history_read`, `💭 [Router] Thinking:`, `🎯 Routing: {...}`, then `✅ Response:` (distinct)
  4. Set mode `normal` — verify zero routing messages in Telegram
  5. Set mode `quiet` — verify zero routing messages in Telegram
  6. In all modes: open history file, verify all `[Router]` entries present

**Checkpoint**: `uv run pytest tests/chat/test_handler.py tests/ai/test_event_pipeline_router.py -v`

---

### Task 2.5 — voice.py routing event support

- [ ] **Status**: Pending
- **Why**: `voice.py` has its own event processing loop that mirrors `handler.py`. Without this task, routing events flow through `voice.py` and are formatted without the `[Router]` treatment from Task 2.4, producing incorrect output.
- **Dependencies**: Task 2.4 (handler.py changes are the reference implementation).

**Implementation note**: `voice.py` imports `format_event` from `handler.py` (line 19). If `format_event()` changes in Task 2.4 are sufficient, `voice.py` may need zero code changes — verify first. The task exists to confirm the import path is correct and that no inline event formatting bypasses `format_event()`.

> **Finding #7 (refuted concern)**: `voice.py` has NO beacon counter. There are no `counts["tools"]` / `counts["thinking"]` variables anywhere in `voice.py`. The beacon counter exclusion note in the task description is a no-op. Only `handler.py`'s `handle_message()` has a beacon counter; the Task 2.4 fix there is sufficient.

**Files**:
- [ ] `archon/chat/voice.py`: verify `format_event()` import covers router event gating. Add `source="router"` exclusion to any inline event loop logic that bypasses `format_event()`. No beacon counter changes needed — `voice.py` has none.

**Tests**:
- [ ] *Unit*: `test_voice_format_router_events_normal_suppressed` — router events in normal mode produce no Telegram output
- [ ] *Unit*: `test_voice_format_router_events_verbose` — `[Router]` prefix present in verbose output
- [ ] *Unit*: `test_voice_main_session_events_unchanged` — regression guard
- [ ] *Live E2E*: In voice mode + debug, send a task that triggers routing. Verify `[Router]` prefixed messages arrive in Telegram.

**Checkpoint**: `uv run pytest tests/chat/test_voice.py -k "router" -v`

---

## Phase 3 — Documentation

### Task 3.1 — Update documentation

- [ ] **Status**: Pending
- **Dependencies**: All previous tasks merged.

**Files**:
- [ ] `CLAUDE.md`:
  - `archon/ai/` section: rename `_orch_session` references to `_router_session`
  - `Decomposer` entry: note `route_task()` is now an `AsyncIterator[Event | TaskOutput]` yielding router events in real time followed by a `TaskOutput` sentinel; routing events forwarded via `Pipeline.send()`
  - `BackgroundAgentManager` entry: agents now receive `background_agent_mcp_url` pointing to `ArchonRouterMCPServer` with `BG_AGENT_ALLOWED_TOOLS`; `spawn_background_agent` is NOT accessible (not on port 18183)
  - Configuration section: `[background_agents]` — new `bg_toolkit_mcp_port` key
  - Output event model table: add `[Router]` variants (`ToolStarted`, `ToolResult`, `ThinkingResult`, `Response`) with emoji prefixes
  - Configuration section: note `debug` shows all routing events without suppression
- [ ] `Documentation/Architecture/100_system_architecture_overview.md`: update `_orch_session` references, add `source="router"` to the three-source event model
- [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`: update `Decomposer` and `BackgroundAgentManager` descriptions
- [ ] `Documentation/Backlog/11_archon_control_plane_mcp_tools.md`: note that routing session events reach main session via `TaskOutput` sentinel in `Pipeline.send()` (not via `event_callback`); `RouteResult` does not exist — the actual return type is `TaskOutput` defined in `decomposer.py`
- [ ] `Documentation/UserManual/` or `CLAUDE.md`: add privacy note — history files now contain routing metadata derived from user messages (`[Router]` entries). Users sharing history files for debugging expose routing decisions and tool call summaries from their messages.
- [ ] Move this document to `Documentation/Completed/` once all tasks are checked off

**Verification**:
- [ ] `grep -r "_orch_session\|orch_mcp_url\|ArchonOrchestratorMCPServer\|archon_orch_mcp_server" archon/` → zero results
- [ ] `grep -r "router_mcp_url" archon/` → results in Decomposer, Pipeline, SessionManager, Gateway
- [ ] `grep -r "ArchonRouterMCPServer" archon/` → results in `archon_router_mcp_server.py`, Gateway, and any other wiring files

---

## Final checkpoint — Full suite

- [ ] `uv run pytest tests/ai/test_archon_router_mcp_server.py tests/ai/test_background_agent_manager.py tests/ai/test_decomposer.py tests/ai/test_event_renderer.py tests/chat/test_handler.py tests/chat/test_voice.py tests/ai/test_event_pipeline_router.py tests/ai/test_pipeline.py -v`
- [ ] `uv run pytest` — full suite clean

---

## Task dependency graph

```
0.0 (SDK spike) ──────────────────────────────────────┐
0.1 (rename) ──────────────────────────────────────────┤
  └── 0.2 (restrict router MCP — ROUTER_ALLOWED_TOOLS=∅) │
        └── 1.1 (bg agent → router MCP with BG_AGENT_ALLOWED_TOOLS) ◄── (0.0 + 0.1 + 0.2)
0.1 ────────────────────────────────────────────────── 2.1 (AsyncIterator route_task + is_router_event)
                                                            ├── 2.2 (suppress content)  ─┐
                                                            ├── 2.3 (EventRenderer)      ─┤── merge commit (atomicity)
                                                            └── 2.4 (handler.py)         ─┘
                                                                  └── 2.5 (voice.py)
                                                                        └── 3.1 (docs)
```

Tasks 0.0 and 0.1 are fully independent and can run in parallel. Task 0.2 depends only on 0.1. Task 1.1 depends on 0.0, 0.1, and 0.2 (needs `BG_AGENT_ALLOWED_TOOLS` defined before wiring). Tasks 1.1 and 2.x are independent of each other. Tasks 2.2, 2.3, 2.4 are independent of each other (all depend only on 2.1). **However, 2.1 + 2.2 + 2.3 + 2.4 must land as a single merged commit** — releasing 2.1 alone would emit unstyled `source="router"` events with no rendering treatment.

---

## Summary

| Task | Title | Key files | Depends on |
|---|---|---|---|
| **0.0** | Verify SDK surfaces MCP tool calls as events | `test_sdk_mcp_event_emission.py` (new) | — |
| **0.1** | Rename routing session and its server | `archon_orch_mcp_server.py` → `archon_router_mcp_server.py`, `decomposer.py`, `pipeline.py`, `session_manager.py`, `gateway.py` | — |
| **0.2** | Restrict router MCP server — `ROUTER_ALLOWED_TOOLS=frozenset()` (history-only) | `archon_router_mcp_server.py` | 0.1 |
| **1.1** | Connect background agents to router MCP server with `BG_AGENT_ALLOWED_TOOLS`; `spawn_background_agent` blocked by architecture | `archon_router_mcp_server.py`, `background_agent_manager.py`, `gateway.py` | 0.0, 0.1, 0.2 |
| **2.1** | Stream routing session events in real time | `decomposer.py`, `pipeline.py`, `event_mapper.py` (add `is_router_event`) | 0.1 |
| **2.2** | Suppress routing tool result and thinking content in history | `event_renderer.py` | 2.1 |
| **2.3** | Render routing events distinctly in session history | `event_renderer.py` | 2.1 |
| **2.4** | Deliver routing events to Telegram correctly | `handler.py` | 2.1 |
| **2.5** | voice.py routing event support | `voice.py` | 2.4 |
| **3.1** | Update documentation | `CLAUDE.md`, Architecture docs | all |
