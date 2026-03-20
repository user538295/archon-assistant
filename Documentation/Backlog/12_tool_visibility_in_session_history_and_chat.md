# 12 — Tool Visibility in Session History and Chat

**Purpose**: Make Archon toolkit tool calls and routing session activity visible in session history and Telegram chat — the same way regular SDK tools (Read, Grep, Bash) are already visible — using the existing event pipeline instead of introducing new notification mechanisms.
**Audience**: Archon developers, background agents, orchestrator sessions
**Status**: Pending
**Priority**: P1
**Estimated Effort**: 11 tasks + 1 doc update, ~3–4 days
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

> **Lock hold time**: `Pipeline.send()` holds `self._lock` for its full duration. Telegram delivery for router events happens under the lock, adding latency before the main session starts. Worst case breakdown: `_SUMMARY_WAIT_TIMEOUT(3s) + _ORCH_RESET_TIMEOUT_S(30s, reset) + _ORCH_RESET_TIMEOUT_S(30s, ensure-session) + _ORCH_TIMEOUT_S(60s) + gen.aclose()(5s) = 128s` theoretical (not 63s — the routing session's own timeout and cleanup are additive). Intentional tradeoff — do not treat as a regression. Worst-case reduction tracked separately (see M2 options: outer `asyncio.timeout` on `route_task()` call, or move setup before lock acquisition).

`route_task()` becomes an `AsyncIterator[Event | TaskOutput]` — it yields every router event immediately as it arrives, then yields exactly one `TaskOutput` as a sentinel final item. `Pipeline.send()` distinguishes the two by type: events are re-tagged and yielded to Telegram immediately; the sentinel is captured as `task_output` and consumed internally.

```python
# route_task() — BEHAVIORAL SPECIFICATION (not copy-paste code)
# IMPORTANT: This is a high-level shape only. The actual implementation MUST:
#   1. Preserve ALL existing timeout wrappers from current code:
#      - asyncio.timeout around _reset_router_if_needed() with its own except TimeoutError + except Exception
#      - asyncio.timeout around _ensure_router_session() with its own except TimeoutError + except Exception
#      - asyncio.timeout(_ORCH_TIMEOUT_S) around the send/event loop
#   2. Preserve ALL existing error logging and fallback_reason strings
#   3. NOT use bare `except Exception: pass` — log and record fallback_reason
#   4. Keep _await_pending_summary() at the TOP of the function, BEFORE _ensure_router_session()
#      (current position, unchanged — the conceptual shape below shows it AFTER Response
#      only for readability; the actual code must keep it before the session send)
#   5. The signature is route_task(self, prompt: str) — only prompt, no instruction argument
#   6. Preserve _pending_turns tracking: `_pending_turns.append()` + `_schedule_summary()`
#      must run AFTER parsing the Response but BEFORE yielding the TaskOutput sentinel.
#      Without this, large-scope context tracking silently breaks — Haiku summaries
#      stop accumulating routing decisions. (Currently at decomposer.py lines 353-354.)
#   7. Do NOT early-return on first Response. The current code iterates ALL events and
#      captures the LAST Response (line 324-327: `async for event in gen: if isinstance(event, Response):
#      raw_response = event.content` — no break/return). With max_turns=5 and MCP tools,
#      the orch session CAN do multiple tool-call/response cycles. Yielding on the first
#      Response would capture an intermediate response instead of the final routing JSON.
#      The generator must iterate all events, yield each one, and capture the last Response.
#
# Conceptual data flow (ignoring internal timeouts and error paths):
async def route_task(self, prompt: str) -> AsyncIterator[Event | TaskOutput]:
    # [existing logic: _await_pending_summary() and _reset_router_if_needed() run here FIRST]
    # [existing logic: _ensure_router_session() runs here]
    fallback = TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="routing fallback")
    gen = self._router_session.send(instruction)  # BEFORE try — matches existing decomposer.py:322
    try:
        last_response: Response | None = None
        async for event in gen:
            yield event                                # stream ALL events immediately
            if isinstance(event, Response):
                last_response = event                  # capture last Response (not first!)
        if last_response is not None:
            task_output = self._parse(last_response)
            # Preserve _pending_turns tracking (currently at lines 353-354):
            if task_output.scope == "large" and task_output.summary:
                self._pending_turns.append((prompt, task_output.summary))
                self._schedule_summary()
            yield task_output                          # sentinel — always last
            return
    except Exception as exc:
        logger.warning("router session error: %s", exc)  # preserve existing logging
    finally:
        await gen.aclose()
    yield fallback                                 # sentinel fallback
```

```python
# In Pipeline.send() — replaces the single await call
# NOTE: requires `import dataclasses` added to pipeline.py (not currently present).
# NOTE: `dataclasses.replace(item, source="router")` requires every event type in
# the router generator to have a `source` field. Verified: ALL 16 event dataclasses
# in event_mapper.py have a `source` field (defaults: "orchestrator", "pipeline", or
# "plan_executor"). Only ThinkingResult, ToolStarted, ToolResult, Response, ErrorEvent
# can come from ClaudeSession.send() — all have `source`. A runtime guard is added
# below to catch future event types that may lack `source`.
# NOTE: route_task() takes only `prompt` — instruction is built internally as today.
task_output: TaskOutput | None = None
router_gen = self._decomposer.route_task(prompt)
try:
    async for item in router_gen:
        if isinstance(item, TaskOutput):
            task_output = item          # consumed, not yielded
        else:
            # Runtime guard: dataclasses.replace() raises TypeError if `source` is missing.
            # This assert catches new event types added without `source` during development.
            assert hasattr(item, "source"), f"Event {type(item).__name__} missing 'source' field"
            yield dataclasses.replace(item, source="router")  # real-time delivery
finally:
    # If Pipeline.send() exits early (e.g., its own exception, caller abandons the
    # generator), ensure the route_task generator is properly closed to avoid leaks.
    await router_gen.aclose()

# Guard: if the generator exited without yielding a TaskOutput sentinel (e.g., raised),
# use a fallback to prevent NoneType crash on task_output.scope below.
if task_output is None:
    task_output = TaskOutput(scope="small", prompt=prompt, is_fallback=True, fallback_reason="pipeline routing sentinel missing")
```

> **Pre-implementation checks for Task 2.1**:
> - `pipeline.py` has no `import dataclasses` — add it.
> - `dataclasses.replace(item, source="router")` raises `TypeError` if any event type lacks a `source` field. **Verified**: all 16 event dataclasses in `event_mapper.py` have a `source` field. Only `ThinkingResult`, `ToolStarted`, `ToolResult`, `Response`, `ErrorEvent` can come from `ClaudeSession.send()` — all have `source: str = "orchestrator"`. A runtime `assert hasattr(item, "source")` guard is included in the Pipeline code to catch future regressions.
> - The `async for` loop in `Pipeline.send()` is wrapped in `try/finally` with `await router_gen.aclose()` — if Pipeline.send() exits early (caller exception, generator abandonment), the route_task generator is properly closed to avoid leaks.
> - After the loop, guard `if task_output is None:` with a fallback `TaskOutput` to prevent `AttributeError` if the generator exits without yielding the sentinel.
> - `_await_pending_summary()` and `_reset_router_if_needed()` run at the TOP of `route_task()`, BEFORE `_ensure_router_session()` and BEFORE any yields — their position is unchanged from the current code. Do not move them to after the `Response` yield. This is expected behavior; do not treat as a deadlock when profiling.
> - **`_pending_turns` tracking**: The existing `_pending_turns.append()` + `_schedule_summary()` at lines 353-354 must happen AFTER parsing the last Response but BEFORE yielding the `TaskOutput` sentinel. Without this, large-scope context tracking silently breaks.
> - **Last Response, not first**: The current code captures the LAST `Response` via `async for event in gen: if isinstance(event, Response): raw_response = event.content` (no early return). With `max_turns=5`, the orch session can do multiple tool-call/response cycles. The generator must iterate ALL events, yield each one, and use the last Response for routing.

### Routing tool result suppression

Routing session tool results (history reads) must not be written as full content to main session history — that causes recursive embedding (routing reads history → result written → next call reads larger file → quadratic growth). `source="router"` `ToolResult` events are rendered as a 160-char summary.

> **`tools=[]` disables ALL SDK built-in tools** (test at `tests/ai/test_claude_session.py:804` verifies Archon passes `tools=[]` through to SDK options; the actual SDK behavior of disabling built-in tools when receiving `[]` is an SDK contract assumption, not independently verified in our codebase). The routing session has only what port 18183 exposes. After Task 0.2, that is history tools only.

---

## Scope

### In scope
- [x] SDK spike: verify MCP tool calls surface as SDK events
- [x] Rename routing session variable
- [x] Router MCP server restricted to read-only tools only
- [x] Background agents connected to the MCP server
- [x] Eliminate third MCP server (per-route filtering)
- [ ] Migrate test call sites for route_task generator conversion
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
- ~~**Port proliferation tech debt**~~: Resolved by Task 1.2 — single `ArchonRouterMCPServer` with per-route filtering eliminates the third port
- **Rate limiting on bg_toolkit MCP server**: `send_notification` in `BG_AGENT_ALLOWED_TOOLS` could be abused by prompt injection in background agents. No rate limiting is added in this epic

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

- [x] **Status**: Complete (2026-03-20, SDK 0.1.46)
- **Why first**: The entire benefit of Task 1.1 rests on `claude-agent-sdk 0.1.39` emitting `ToolUseBlock` / `ToolResultBlock` for MCP tool calls the same way it does for built-in tools. If the SDK silently handles MCP calls without surfacing them through the event stream, Task 1.1 delivers nothing visible and the architecture needs redesigning. This must be confirmed before any implementation begins.
- **Dependencies**: None.

**What to verify**: When a `ClaudeSession` is configured with `background_agent_mcp_url` and the model invokes a tool hosted on that MCP server, do `ToolStarted` and `ToolResult` events appear in the session's event stream (via `EventMapper`)?

**Result**: Confirmed. SDK 0.1.46 emits `ToolStarted(name="echo")` and `ToolResult(tool_name="echo")` for MCP tool calls. The model may invoke built-in tools before the MCP tool, so ordering assertions must be scoped to the specific MCP tool name.

**Files**:
- [x] `tests/ai/test_sdk_mcp_event_emission.py` (new spike/verification test)

**Tests**:

- [x] *Integration spike* — `test_sdk_emits_tool_events_for_mcp_tool_call`:
  - Start a minimal in-process MCP server using the `mcp` Python package (FastMCP or `@server.tool()`) — bare `aiohttp` does not implement MCP JSON-RPC; the real protocol is required
  - Create a `ClaudeSession` with `background_agent_mcp_url` pointing to it
  - Send a prompt that causes the model to call the tool
  - Collect all events from `session.send()`
  - Assert `ToolStarted(name="echo")` and `ToolResult` appear in the event stream
  - Mark test `@pytest.mark.live` (requires real SDK / API key)

- [x] *Fallback verification*: Live test confirmed on SDK 0.1.46. Verification comment updated in test file.

**Checkpoint**: `uv run pytest tests/ai/test_sdk_mcp_event_emission.py -v -m live`

**If the test fails**: Stop. The assumption underlying Task 1.1 is wrong. Return to the design phase before proceeding with any other task in this epic.

---

### Task 0.1 — Rename the routing session and its server to match what they do

- [x] **Status**: Complete (2026-03-20)
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
| `orch_mcp_port` (config key in `BackgroundAgentsConfig`) | `router_mcp_port` — rename in **both** Python attribute AND `config.toml` key. This is a breaking change for existing `config.toml` files; add a migration note to the release changelog and to `examples/config.toml.example`. **Also add a deprecation shim in `loader.py`**: if `orch_mcp_port` is present in the raw TOML and `router_mcp_port` is absent, read the old key with a deprecation warning (`logger.warning("config key 'orch_mcp_port' renamed to 'router_mcp_port'; update your config.toml")`). This prevents silent port reversion for users with custom port values. |

**Files**:

*Renames (use `git mv` to preserve history):*
- [x] `archon/ai/archon_orch_mcp_server.py` → `archon/ai/archon_router_mcp_server.py` (`git mv`)
- [x] `tests/ai/test_archon_orch_mcp_server.py` → `tests/ai/test_archon_router_mcp_server.py` (`git mv`)

*Code updates (variable/class/param names):*
- [x] `archon/ai/archon_router_mcp_server.py`: class `ArchonOrchestratorMCPServer` → `ArchonRouterMCPServer`; all internal references updated
- [x] `archon/ai/decomposer.py`: `_orch_session` → `_router_session`, `_ensure_orch_session()` → `_ensure_router_session()`, constructor params updated
- [x] `archon/ai/pipeline.py`: params `orch_mcp_url` / `orch_mcp_headers` → `router_mcp_url` / `router_mcp_headers`
- [x] `archon/ai/session_manager.py`: stored fields and `Pipeline(...)` call updated
- [x] `archon/ai/claude_session.py`: any `orch_*` references updated
- [x] `archon/ai/archon_toolkit.py`: docstring/comment references updated
- [x] `archon/gateway/gateway.py`: import updated, local variable `orch_mcp_server` → `router_mcp_server`, `SessionManager(...)` call updated
- [x] `archon/config/loader.py`: rename `orch_mcp_port` key → `router_mcp_port`, add deprecation shim (read old key if new key absent, emit `logger.warning`), comment references updated
- [x] `examples/config.toml.example`: rename `orch_mcp_port` → `router_mcp_port`, add migration comment
- [x] `CLAUDE.md`: update `[background_agents]` config section — `orch_mcp_port` → `router_mcp_port`

*Test files (name/variable/import updates):*
- [x] `tests/ai/test_archon_router_mcp_server.py`: class import updated to `ArchonRouterMCPServer`
- [x] `tests/ai/test_decomposer.py`: variable names updated
- [x] `tests/ai/test_orch_redesign_e2e.py`: variable names and any class imports updated
- [x] `tests/ai/test_orch_redesign_integration.py`: variable names and any class imports updated
- [x] `tests/ai/test_session_manager.py`: variable names updated
- [x] `tests/ai/test_background_agent_manager.py`: variable names updated
- [x] `tests/gateway/test_gateway.py`: variable names updated
- [x] `tests/gateway/test_shutdown.py`: variable names updated
- [x] `tests/gateway/test_shutdown_e2e.py`: variable names updated
- [x] `tests/gateway/test_background_agent_gateway_integration.py`: variable names updated

**Tests**:
- [x] *Unit*: `test_config_loader_orch_mcp_port_migration` — load TOML with old key `orch_mcp_port = 18200`, assert `BackgroundAgentsConfig.router_mcp_port == 18200` and a deprecation warning is emitted
- [x] All existing tests pass with new names. No other new test logic needed.

**Checkpoint**: `uv run pytest -v` — full suite green, zero behavior change.

---

### Task 0.2 — Restrict the router MCP server to read-only tools

- [x] **Status**: Complete (2026-03-20)
- **Why**: `ArchonRouterMCPServer` currently registers the full toolkit — including `cancel_agent`, `archon_restart`, `add_scheduled_task`, `set_notification_mode`, `set_model` — alongside history tools. It calls `call_tool(tool_name, arguments, user_id=None)`. The `user_id=None` path bypasses the authorization check in `_handle_cancel_agent` — the check (`if user_id is not None: [auth check]`) is skipped entirely when `user_id` is `None`, so `cancel()` is called unconditionally on any `run_id`. This is confirmed in `archon/ai/archon_toolkit.py` lines 748–753. A prompt-injected routing session could call any destructive operation without authorization. The routing session's role is to read history and decide scope — it needs no write or service-management access.
- **Dependencies**: Task 0.1 (file renamed).

**Fix**: Introduce `allowed_tools: frozenset[str]` as a **constructor parameter** on `ArchonRouterMCPServer` (not a module-level constant — Task 1.1 reuses the same class with a different allowlist). For the routing session, pass `frozenset()`. History tools (`history_list`, `history_read`, `history_grep`) are hardcoded in `_handle_tools_list()` and NOT part of `self._toolkit.tool_definitions` — they are always present regardless of `allowed_tools`. The router needs only history access to decide scope.

> **Why constructor parameter, not module constant**: Task 1.1 creates a second `ArchonRouterMCPServer` instance for background agents with a broader `BG_AGENT_ALLOWED_TOOLS`. A module-level constant would be immediately overwritten by Task 1.1's parameterization — implement it correctly the first time.

**Files**:
- [x] `archon/ai/archon_router_mcp_server.py`: add `allowed_tools: frozenset[str] = frozenset()` constructor parameter stored as `self._allowed_tools`. In `_handle_tools_list()`, filter `self._toolkit.tool_definitions` to only include tools in `self._allowed_tools`. In `_handle_tools_call()`, reject (return error) if `tool_name not in self._allowed_tools` for toolkit-delegated calls.

**Tests**:
- [x] *Unit*: `test_router_mcp_server_tools_list_empty_toolkit` — assert `list_tools()` response contains zero toolkit tools (no `archon_status`, `cancel_agent`, `get_config`, etc.); history tools (`history_read`, `history_list`, `history_grep`) are still present
- [x] *Unit*: `test_router_mcp_server_rejects_toolkit_call` — call any toolkit tool (e.g. `archon_status`) via router MCP server, assert error response returned (not executed)
- [x] *Unit*: `test_router_mcp_server_history_read_hardcoded_not_allowlist_gated` — call `history_read` via router MCP server, assert it executes normally (hardcoded handler, not gated by `ROUTER_ALLOWED_TOOLS`)

**Checkpoint**: `uv run pytest tests/ai/test_archon_router_mcp_server.py -v`

---

## Phase 1 — Background agents can call Archon tools

### Task 1.1 — Connect background agents to the Archon MCP server

- [x] **Status**: Complete (2026-03-20)
- **Why**: Background agents are spawned without `background_agent_mcp_url`, so they cannot call any Archon toolkit tools. Connecting them gives agents the audited MCP path for service management — the same operations they could already perform via shell commands, but now with rate limiting, audit logging, and Telegram notifications.
- **Dependencies**: Task 0.0 (SDK spike confirmed), Task 0.1 (clean rename), Task 0.2 (tool restriction in place).

> **Which MCP server**: Connect to a second `ArchonRouterMCPServer` instance on `bg_toolkit_mcp_port` (new config key), with `BG_AGENT_ALLOWED_TOOLS`. NOT `ArchonMCPServer` (port 18182) — port 18182 exposes `spawn_background_agent`, enabling recursive spawning. Port 18183 (router) is history-only. The second instance on `bg_toolkit_mcp_port` has the toolkit tools background agents are allowed to use.
>
> **`BG_AGENT_ALLOWED_TOOLS`** (explicit, not deferred): `frozenset({"archon_status", "list_running_agents", "get_config", "get_job_config", "send_notification"})`. Excluded (too destructive or creates state conflicts): `archon_restart`, `set_config`, `cancel_agent`, `add_scheduled_task`, `remove_scheduled_task`, `spawn_background_agent`. This list is part of the design, not an implementation decision.
>
> **Per-user URL routing**: `ArchonRouterMCPServer` currently exposes a single `/mcp` endpoint with no `user_id` path parameter (unlike `ArchonMCPServer` which has `/mcp/{user_id}`). To pass user context to toolkit calls (needed for `list_running_agents`, `send_notification`), add `mcp_url_for(user_id: int) -> str` and `mcp_headers_for(user_id: int) -> dict` methods to `ArchonRouterMCPServer`, matching `ArchonMCPServer`'s pattern. Add a `/mcp/{user_id}` route that passes `user_id` through to `call_tool()`. Without this, all toolkit calls arrive with `user_id=None`, making user-scoped operations (e.g. `list_running_agents`) return empty results.

**Files**:
- [x] `archon/ai/archon_router_mcp_server.py`: (a) Add `mcp_url_for(user_id: int) -> str` and `mcp_headers_for(user_id: int) -> dict` methods matching `ArchonMCPServer`'s pattern. (b) Add `/mcp/{user_id}` route that extracts `user_id` and passes it to `call_tool()`. (c) The `allowed_tools` constructor parameter from Task 0.2 is used here with `BG_AGENT_ALLOWED_TOOLS = frozenset({"archon_status", "list_running_agents", "get_config", "get_job_config", "send_notification"})` for the bg agent instance.
- [x] `archon/ai/background_agent_manager.py`: `__init__()` gains `bg_mcp_server: "ArchonRouterMCPServer | None" = None` stored as `self._bg_mcp_server`. In `_run_agent()`, when `self._bg_mcp_server` is set:
  ```python
  background_agent_mcp_url=self._bg_mcp_server.mcp_url_for(user_id),
  mcp_headers=self._bg_mcp_server.mcp_headers_for(user_id),
  ```
- [x] `archon/config/loader.py`: add `bg_toolkit_mcp_port` field to `BackgroundAgentsConfig` with a distinct default (e.g. `18184`). Add port uniqueness validation as three **pairwise** checks: `bg_toolkit_mcp_port != router_mcp_port`, `bg_toolkit_mcp_port != background_agents.port`, `router_mcp_port != background_agents.port` (third check already exists — verify). Note: `A != B != C` in Python is NOT a three-way check — it only checks adjacent pairs and may produce wrong results. Use explicit pairwise comparisons.
- [x] `archon/gateway/gateway.py`: create a second `ArchonRouterMCPServer` instance (`bg_toolkit_mcp_server`) on `cfg.background_agents.bg_toolkit_mcp_port` with `allowed_tools=BG_AGENT_ALLOWED_TOOLS`. Pass it to `BackgroundAgentManager(bg_mcp_server=bg_toolkit_mcp_server, ...)`. The existing `router_mcp_server` (after rename) remains unchanged for the routing session. **Extend `stop_all()` to also stop `bg_toolkit_mcp_server`** — shutdown must complete within the existing 5s budget.
- [x] `examples/config.toml.example`: add `bg_toolkit_mcp_port = 18184` with comment.

**What works for free**: SDK emits `ToolStarted`/`ToolResult` → BAM loop sets `source="sub-agent"` → `AgentLogger` writes to agent log → `handler.py` suppresses from Telegram.

> **Assumption (A1)**: This entire task assumes the SDK emits identical event structure for MCP tool calls as it does for built-in tool calls. This is unverified and is exactly what Task 0.0 must confirm before this task is implemented. If Task 0.0 fails, the "works for free" claim does not hold and the architecture must be revisited.

**Tests**:
- [x] *Unit*: `test_spawn_agent_passes_mcp_url_to_session` — mock `bg_mcp_server`, mock `ClaudeSession` constructor, assert `ClaudeSession(background_agent_mcp_url=..., mcp_headers=...)` called with the URL and headers from `mcp_url_for(user_id)` / `mcp_headers_for(user_id)` (verifies `_run_agent()` wiring, not just `spawn()`)
- [x] *Unit*: `test_spawn_agent_without_mcp_server_omits_url` — `bg_mcp_server=None` → no `background_agent_mcp_url` in ClaudeSession (backward compat)
- [x] *Unit*: `test_spawn_agent_mcp_url_uses_correct_user_id` — two spawns for different user IDs, assert per-user URLs
- [x] *Unit*: `test_gateway_stop_all_stops_bg_toolkit_server` — assert `bg_toolkit_mcp_server.stop()` is called during `Gateway.stop_all()`
- [x] *Integration*: `test_gateway_stop_all_within_5s_budget` — mock all services (including `bg_toolkit_mcp_server`) with `stop()` that sleeps 2s each, assert total `stop_all()` completes within 5s (verifies parallel shutdown, not sequential). If `ArchonRouterMCPServer.stop()` calls `self._runner.cleanup()` which waits for open connections, two slow MCP servers could eat the budget
- [x] *Unit*: `test_config_bg_toolkit_mcp_port_equals_router_port_raises` — config with `bg_toolkit_mcp_port == router_mcp_port` raises `ConfigError`
- [x] *Unit*: `test_config_bg_toolkit_mcp_port_equals_bg_agents_port_raises` — config with `bg_toolkit_mcp_port == background_agents.port` raises `ConfigError`
- [x] *Integration*: `test_toolkit_call_appears_in_agent_log` — `toolkit_with_real_bam` fixture, mock session emits `ToolStarted(name="archon_status")` with default `source="orchestrator"` (do NOT pre-set `"sub-agent"`), assert BAM loop's `event.source = "sub-agent"` tagging fires, and `AgentLogger` records the event with `source="sub-agent"`
- [x] *Integration*: `test_toolkit_call_does_not_appear_in_main_history` — assert `HistoryManager` does NOT record sub-agent `ToolStarted` to main history
- [x] *E2E*: `test_toolkit_call_not_sent_to_telegram` — mock `bot`, emit sub-agent toolkit events, assert `bot.send_message` never called for toolkit events
- [x] *Live E2E* (`tests/ai/test_epic12_task1_1_live.py`, `@pytest.mark.live`):
  1. `test_agent_log_contains_toolkit_tool_entries` — real ArchonRouterMCPServer + real ClaudeSession; agent calls `archon_status` via MCP; agent log contains `🔧` + `archon_status` entries
  2. `test_main_history_does_not_contain_toolkit_calls` — main history (YYYY-MM-DD.md) does NOT contain `🔧 archon_status` lines; only agent Response recorded
  3. `test_no_toolkit_telegram_messages_during_agent_execution` — bot.send_message never called with `🔧 archon_status`; only spawn + completion notifications present

**Checkpoint**: `uv run pytest tests/ai/test_background_agent_manager.py -k "mcp_url or toolkit_call" -v`

---

### Task 1.2 — Eliminate third MCP server: per-route tool filtering on single ArchonRouterMCPServer

- [x] **Status**: Complete (2026-03-20)
- **Why**: Task 1.1 introduced a third MCP server instance (`bg_toolkit_mcp_server` on port 18184) alongside existing ports 18182 and 18183. Three hardcoded ports is unnecessary complexity and tech debt. The routing session connects via `/mcp` (no user_id), background agents connect via `/mcp/{user_id}` — the URL path already distinguishes the two callers. Per-route tool filtering on a single `ArchonRouterMCPServer` (port 18183) eliminates the third port entirely.
- **Dependencies**: Task 1.1 (bg agent MCP wiring in place).

**Design**: Make `allowed_tools` apply only to the `/mcp/{user_id}` route. The `/mcp` route (routing session) always gets `frozenset()` — history-only, no toolkit tools. This way one server instance on port 18183 serves both callers with different tool sets:

| Route | Caller | Toolkit tools |
|---|---|---|
| `/mcp` | Routing session | None (history-only) |
| `/mcp/{user_id}` | Background agents | `BG_AGENT_ALLOWED_TOOLS` (5 tools) |

**Implementation**: Pass an `effective_allowed_tools` parameter through the dispatch chain (`_dispatch` → `_handle_tools_list` / `_handle_tools_call`). `/mcp` route passes `frozenset()`, `/mcp/{user_id}` route passes `self._allowed_tools`.

**Files**:
- [x] `archon/ai/archon_router_mcp_server.py`: Refactor `_dispatch()`, `_handle_tools_list()`, `_handle_tools_call()` to accept an `effective_allowed_tools: frozenset[str]` parameter. `/mcp` handler passes `frozenset()`. `/mcp/{user_id}` handler passes `self._allowed_tools`.
- [x] `archon/gateway/gateway.py`: Remove second `ArchonRouterMCPServer` instance (`bg_toolkit_mcp_server`). Pass `allowed_tools=BG_AGENT_ALLOWED_TOOLS` to the single `router_mcp_server`. Point `BackgroundAgentManager.bg_mcp_server` to `router_mcp_server` instead. Remove `bg_toolkit_mcp_server` from `stop_all()`.
- [x] `archon/ai/background_agent_manager.py`: No changes needed — already uses `bg_mcp_server.mcp_url_for(user_id)` which routes to `/mcp/{user_id}`.
- [x] `archon/config/loader.py`: Remove `bg_toolkit_mcp_port` field from `BackgroundAgentsConfig`. Remove pairwise collision checks involving `bg_toolkit_mcp_port` (keep `router_mcp_port != port` check).
- [x] `examples/config.toml.example`: Remove `bg_toolkit_mcp_port` entry.

**Tests**:
- [x] *Unit*: `test_anonymous_route_gets_no_toolkit_tools` — `/mcp` route returns only history tools in `tools/list`, even when `allowed_tools` is non-empty
- [x] *Unit*: `test_anonymous_route_rejects_toolkit_call` — `/mcp` route rejects toolkit tool calls, even when `allowed_tools` includes them
- [x] *Unit*: `test_user_route_gets_allowed_toolkit_tools` — `/mcp/{user_id}` route returns `allowed_tools` toolkit tools in `tools/list`; disallowed tools (`archon_restart`, `cancel_agent`) confirmed absent
- [x] *Unit*: `test_user_route_executes_allowed_toolkit_call` — `/mcp/{user_id}` route executes allowed toolkit tool calls
- [x] *Unit*: `test_user_route_rejects_disallowed_toolkit_call` — `/mcp/{user_id}` route rejects toolkit tool calls not in `allowed_tools`
- [x] Existing BAM tests still pass (bg agents use `/mcp/{user_id}` path — no change)
- [x] Config tests updated: remove `bg_toolkit_mcp_port` collision tests (`test_config_bg_toolkit_mcp_port_equals_router_port_raises`, `test_config_bg_toolkit_mcp_port_equals_bg_agents_port_raises` removed; replaced by `test_config_bg_toolkit_mcp_port_field_removed`)
- [x] Gateway tests updated: single `ArchonRouterMCPServer` instance (`test_run_starts_router_mcp_server` and `test_run_stops_router_mcp_server_on_shutdown` assert `len == 1`; `test_gateway_single_router_mcp_server` independently verifies)

**Checkpoint**: `uv run pytest tests/ai/test_archon_router_mcp_server.py tests/ai/test_background_agent_manager.py tests/gateway/ tests/config/ -v`

---

## Phase 2 — Routing session activity visible in the main session

### Task 2.0 — Migrate test call sites for route_task generator conversion

- [ ] **Status**: Pending
- **Why**: Task 2.1 converts `route_task()` from `async def → TaskOutput` to `AsyncIterator[Event | TaskOutput]`. All `await decomposer.route_task(...)` calls in tests break after this conversion — `AsyncMock(return_value=TaskOutput(...))` patterns no longer work. Migrating ~281 occurrences across 12 test files in the same task as the behavioral change makes the PR unreviewable. This task prepares the test infrastructure first.
- **Dependencies**: Task 0.1 (renamed variables).

**Implementation note**: Task 2.0 and 2.1 land atomically (same commit). The helpers target the NEW generator signature only. The migration order within the commit is: (1) add helpers to conftest, (2) convert `route_task()` to generator in decomposer.py, (3) migrate all test call sites. Tests pass only after all three steps — there is no intermediate green state between 2.0 and 2.1.

**Files**:
- [ ] `tests/conftest.py` (or `tests/ai/conftest.py` — whichever is the common ancestor):
  - Add `collect_route_task()` helper:
    ```python
    async def collect_route_task(decomposer, prompt) -> tuple[list[Event], TaskOutput]:
        events: list[Event] = []
        sentinel: TaskOutput | None = None
        async for item in decomposer.route_task(prompt):
            if isinstance(item, TaskOutput): sentinel = item
            else: events.append(item)
        assert sentinel is not None, "route_task() must yield exactly one TaskOutput sentinel"
        return events, sentinel
    ```
  - Add `mock_route_task()` helper for replacing `AsyncMock(return_value=TaskOutput(...))` patterns:
    ```python
    async def mock_route_task(*events: Event, sentinel: TaskOutput) -> AsyncIterator[Event | TaskOutput]:
        for e in events:
            yield e
        yield sentinel
    ```

- [ ] All test files referencing `route_task`: migrate to use the helpers above. Run `grep -rn "await.*route_task\|route_task.*return_value" tests/` to get the exact list. Confirmed ~281 occurrences across 12 test files.

**Tests**:
- [ ] All existing tests pass after Tasks 2.0 + 2.1 are both applied (atomic — no standalone checkpoint for 2.0).

**Checkpoint**: `uv run pytest -v` — full suite green (after Task 2.1 is also applied).

---

### Task 2.1 — Collect routing session events alongside the routing decision

- [ ] **Status**: Pending
- **Why**: `route_task()` currently discards all intermediate events from the routing session. This task converts it to an `AsyncIterator[Event | TaskOutput]` that yields each event immediately as it arrives (real-time delivery), followed by one `TaskOutput` sentinel as the final item. `Pipeline.send()` forwards events to Telegram one-by-one and captures the sentinel as `task_output`. No existing timeout/fallback/cleanup logic changes.

> **No early-return on Response**: The current `route_task()` implementation iterates ALL events from the orch session and captures the LAST `Response` as `raw_response` (line 324-327: `async for event in gen: if isinstance(event, Response): raw_response = event.content` — no break/return). The new generator preserves this behavior: it iterates ALL events, yields each one, and captures the last Response. With `max_turns=5` and MCP tools available, the orch session CAN produce multiple tool-call/response cycles. Using the first Response would capture an intermediate response instead of the final routing JSON.
- **Dependencies**: Task 0.1, **Task 2.0** (test migration).

**Deployment note**: Tasks 2.0, 2.1, 2.2, 2.3, 2.4, and 2.5 must land in a single PR/merged commit — releasing 2.1 alone would emit unstyled `source="router"` events with no rendering treatment, and omitting 2.5 would let `voice.py` capture router `Response` JSON as TTS text. The task numbers describe the implementation ORDER within a single unit of work, not separately deployable steps.

**Files**:
- [ ] `archon/ai/event_mapper.py`:
  - Add module-level helper: `def is_router_event(event: object) -> bool: return getattr(event, "source", "") == "router"`
  - Single canonical check — imported by `event_renderer.py` and `handler.py`, no local duplicates
- [ ] `archon/ai/decomposer.py`:
  - Convert `route_task()` from `async def → TaskOutput` to `AsyncIterator[Event | TaskOutput]` using `yield`
  - Yield each router event immediately as it arrives (before any local processing)
  - Iterate ALL events from the session; capture the LAST `Response` (not first — `max_turns=5` allows multi-Response cycles)
  - After the event loop: parse the last Response, run `_pending_turns.append()` + `_schedule_summary()` for large-scope results (preserving existing lines 353-354), then yield `TaskOutput` sentinel as the final item
  - All early-return fallback paths yield a fallback `TaskOutput` sentinel and return
  - All existing `asyncio.timeout`, `gen.aclose()` in `finally`, `_await_pending_summary()`, `_reset_router_if_needed()` — **all untouched**
- [ ] `archon/ai/pipeline.py` (`Pipeline.send()` at line ~204):
  - Replace `task_output = await self._decomposer.route_task(...)` with:
    ```python
    task_output: TaskOutput | None = None
    async for item in self._decomposer.route_task(prompt):  # only `prompt` — instruction is built internally
        if isinstance(item, TaskOutput):
            task_output = item          # captured, not yielded
        else:
            yield dataclasses.replace(item, source="router")
    ```

**Tests**:
- [ ] *Unit*: `test_is_router_event_returns_true_for_router_source` — `is_router_event(ToolStarted(..., source="router"))` → `True`
- [ ] *Unit*: `test_is_router_event_returns_false_for_orchestrator` — `is_router_event(ToolStarted(..., source="orchestrator"))` → `False`
- [ ] *Unit*: `test_route_task_yields_events_then_task_output` — mock routing session emitting `ToolStarted` + `ThinkingResult` + `Response`, assert all events yielded in order, `TaskOutput` yielded last
- [ ] *Unit*: `test_route_task_uses_last_response_not_first` — mock session emitting `Response("intermediate")` + `ToolStarted` + `Response("final routing JSON")`, assert `TaskOutput` is parsed from the LAST Response content, not the first
- [ ] *Unit*: `test_route_task_pending_turns_tracked_for_large_scope` — mock session with `Response` yielding `scope="large"` task output, assert `_pending_turns.append()` and `_schedule_summary()` are called BEFORE the `TaskOutput` sentinel is yielded
- [ ] *Unit*: `test_route_task_events_tagged_before_yield` — events yielded by `route_task()` still have `source="orchestrator"` (tagging to `"router"` happens in `Pipeline.send()`)
- [ ] *Unit*: `test_route_task_fallback_mid_stream` — mock session raising mid-stream, assert `TaskOutput` fallback sentinel still yielded as last item (path 5: `gen.send()` exception)
- [ ] *Unit*: `test_route_task_timeout_yields_fallback` — mock session that never yields `Response`, assert `TaskOutput` fallback sentinel yielded (timeout caught inside generator, not propagated) (path 4: `asyncio.timeout` fires)
- [ ] *Unit*: `test_route_task_timeout_mid_stream_partial_events` — mock session that yields `ToolStarted` + `ToolResult` then hangs forever; assert the yielded events are delivered in order AND `TaskOutput` fallback sentinel follows after timeout; assert inner generator properly closed (no leak) (path 4 mid-stream variant — the most realistic production failure mode)
- [ ] *Unit*: `test_route_task_reset_timeout_yields_fallback` — mock `_reset_router_if_needed()` raising `TimeoutError`, assert `TaskOutput` fallback sentinel yielded (path 1: reset timeout)
- [ ] *Unit*: `test_route_task_ensure_session_timeout_yields_fallback` — mock `_ensure_router_session()` raising `TimeoutError`, assert `TaskOutput` fallback sentinel yielded (path 3: ensure-session timeout)
- [ ] *Unit*: `test_route_task_reset_exception_yields_fallback` — mock `_reset_router_if_needed()` raising `Exception`, assert `TaskOutput` fallback sentinel yielded (path 2: reset exception)
- [ ] *Unit*: `test_route_task_real_time_ordering` — assert first event is yielded BEFORE `TaskOutput` sentinel, verifying no buffering
- [ ] *Unit*: `test_router_events_precede_main_session_events` — in `Pipeline.send()` output for a **task-scope** message (not `intent="chat"`, which never calls `route_task()`), all `source="router"` events must appear before any `source="orchestrator"` event (verifies the pipeline ordering contract: routing phase always completes before execution phase begins)
- [ ] *Integration*: `test_pipeline_yields_router_events_tagged` — `Pipeline` with mock `Decomposer`, assert `source="router"` events appear one-by-one in `Pipeline.send()` output, before main session events
- [ ] *Integration*: `test_pipeline_task_output_consumed_not_yielded` — assert `TaskOutput` never appears in `Pipeline.send()` output (consumed internally)
- [ ] *Integration*: `test_chat_intent_produces_no_router_events` — `Pipeline` with `intent="chat"` (high confidence), assert zero `source="router"` events in output (chat intent skips `route_task()` entirely)
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
- **Dependencies**: Task 2.1, **Task 2.2** (both tasks modify `event_renderer.py` — 2.2 must land first to establish the `ToolResult` router branch; 2.3 must not re-edit that branch).

**Files**:
- [ ] `archon/ai/event_renderer.py`: import `is_router_event` from `archon.ai.event_mapper`. For each event type, add `source="router"` branch using `is_router_event(event)`:
- [ ] `archon/ai/history_manager.py`:
  - Add `record_raw(user_id: int, content: str) -> None` — direct append to the current session file without event formatting; used only for structural markers (the `\n---\n` separator).
  - **Auto-separator in `record_event()`**: Track `self._last_source: dict[int, str]` (per user_id). In `record_event()`, when `is_router_event(event)` transitions to a non-router event (or vice versa), auto-insert `\n---\n` via `record_raw()` BEFORE recording the new event. This eliminates the need for handler.py and voice.py to independently track `saw_router_event` flags — the callee handles it.
  - Import `is_router_event` from `archon.ai.event_mapper`.

  | Event type | `source="router"` render |
  |---|---|
  | `ToolStarted` | `### 🔧 [Router] Tool: {name} [{id}] · {ts}` |
  | `ToolResult` | handled by Task 2.2 |
  | `ThinkingResult` | `### 💭 [Router] Thinking · {ts}\n\n{content}` (full content — history is always the complete record) |
  | `Response` | `### 🎯 Routing decision: · {ts}` |
  | `ErrorEvent` | `### ❌ [Router] Error: · {ts}` |

  `source="orchestrator"` and `source="sub-agent"` render exactly as before.

**History separator**: After all `[Router]` entries, emit `\n---\n` to mark the boundary between routing phase and execution phase.

> **Separator logic lives in `HistoryManager.record_event()`** — NOT in handler.py or voice.py. `HistoryManager` tracks `self._last_source[user_id]` and auto-inserts the separator when the source transitions from `"router"` to non-router. This avoids duplicating the `saw_router_event` flag in every event consumer (handler.py, voice.py, any future entry point). The separator is history-only (no Telegram message).

**Tests**:
- [ ] *Unit*: `test_render_router_tool_started` — assert `[Router]` prefix, correct heading
- [ ] *Unit*: `test_render_router_thinking` — assert `[Router] Thinking` heading AND full content present (not suppressed)
- [ ] *Unit*: `test_render_router_response` — assert `🎯 Routing decision:` heading, NOT `✅ Response:`
- [ ] *Unit*: `test_render_router_error` — assert `[Router]` prefix
- [ ] *Unit*: `test_render_main_session_unchanged` — `source="orchestrator"` events render identically (regression guard)
- [ ] *Unit*: `test_render_sub_agent_unchanged` — `source="sub-agent"` events render identically (regression guard)
- [ ] *Unit*: `test_history_manager_auto_separator_on_source_transition` — call `record_event()` with `source="router"` events then `source="orchestrator"` event, assert `\n---\n` separator appears in the file between the last router entry and first orchestrator entry
- [ ] *Unit*: `test_history_manager_no_separator_without_router_events` — call `record_event()` with only `source="orchestrator"` events, assert no `\n---\n` separator in the file
- [ ] *Unit*: `test_history_manager_separator_not_duplicated` — call `record_event()` with router→orchestrator→router→orchestrator sequence, assert exactly two separators (one per transition)
- [ ] *Integration*: `test_handler_separator_in_history_not_telegram` — emit `source="router"` events followed by `source="orchestrator"` events through handler, assert separator appears in history file but NOT in Telegram output
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

In `quiet`/`normal`: no Telegram for any `source="router"` event (history-only), **with one explicit exception: `ErrorEvent` source=router shows in ALL modes (including quiet)**.

> **`ErrorEvent` source=router in quiet/normal**: Routing errors must be visible to users in all modes — a routing failure that silently falls back to `scope="small"` could cause unexpected behavior. The implementation in `format_event()` must check `is_router_event(event)` FIRST, BEFORE the generic `isinstance(event, ErrorEvent)` branch, and carve out ErrorEvent for visibility in all modes. If the generic `ErrorEvent` branch fires first (before the router check), it will render without the `[Router]` prefix. Branch ordering matters.

**Quiet-mode beacon exclusion**: Router `ToolStarted` events must NOT increment `counts["tools"]` / `counts["thinking"]` in `handle_message()`. `voice.py` has no beacon counter — no changes needed there.

**Files**:
- [ ] `archon/chat/handler.py` — **TWO changes required**:

  **(a) `handle_message()` quiet-mode block** (around line 453):
  The quiet-mode early-exit block increments `counts["tools"]` / `counts["thinking"]` BEFORE calling `format_event()`. Router events that reach this block will inflate beacon counters. Add `is_router_event(event)` check before the counter increments:
  ```python
  # In the quiet-mode block, BEFORE the isinstance(event, ToolStarted) branch:
  if is_router_event(event):
      if not isinstance(event, ErrorEvent):  # ErrorEvent always falls through
          continue  # suppress router events in quiet mode without counting
  ```
  This must be placed as the FIRST check inside the quiet-mode block, before any `isinstance` branches that increment counters.

  **(b) `format_event()`**:
  - Import `is_router_event` from `archon.ai.event_mapper` (shared canonical check — no local duplicate)
  - Add a single early router check block at the TOP of `format_event()`, BEFORE any existing `isinstance` branches. This block handles ALL router events including the ErrorEvent carve-out:
    ```python
    if is_router_event(event):
        if isinstance(event, ErrorEvent):
            return [f"❌ [Router] Error: {html.escape(event.message)}"]  # visible in ALL modes
        if isinstance(event, Response):
            return []  # suppressed in ALL modes (history-only)
        if mode in ("quiet", "normal"):
            return []  # suppress non-error router events in quiet/normal
        # verbose/debug: render with [Router] prefix per table above
        ...
    ```
    > **IMPORTANT**: `html.escape()` is mandatory on `event.message` — the existing `ErrorEvent` branch at line 256 uses `html.escape(event.message)`. Error messages may contain user-controlled content via prompt injection; omitting escaping is an XSS vector in Telegram's HTML parse mode.
  - `source="orchestrator"` events render identically to before — no regression (they never enter the router block)

**Tests**:
- [ ] *Unit*: `test_format_router_events_quiet_returns_empty` — all router event types EXCEPT `ErrorEvent` in quiet mode return `[]` (suppress ToolStarted, ToolResult, ThinkingResult, Response)
- [ ] *Unit*: `test_format_router_events_normal_returns_empty` — all router event types EXCEPT `ErrorEvent` in normal mode return `[]`
- [ ] *Unit*: `test_format_router_error_event_all_modes_visible` — `ErrorEvent(source="router")` in quiet, normal, verbose, and debug modes all return non-empty output with `[Router]` prefix (routing errors are always shown)
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
  3. Verify in Telegram: `🔧 [Router] history_read`, `💭 [Router] Thinking:`, then `🔀 task_direct` (RoutingEvent — NOT the raw routing JSON, which is history-only), then `✅ Response:` (distinct)
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
- [ ] `archon/chat/voice.py`:
  - Verify `format_event()` import covers router event gating (it does — `format_event` is imported from `handler.py` line 18)
  - **TTS capture guard**: The TTS response capture at line ~237 (`if isinstance(event, Response): response_text = event.content`) must exclude router events. Without this, a `Response(source="router")` containing raw routing JSON would be captured as the TTS text, and in error paths (main session fails after routing), the user would hear JSON read aloud. Fix: `if isinstance(event, Response) and not is_router_event(event): response_text = event.content`
  - **History separator**: Handled by `HistoryManager.record_event()` auto-separator (Task 2.3) — no voice.py changes needed
  - No beacon counter changes needed — `voice.py` has none

**Tests**:
- [ ] *Unit*: `test_voice_format_router_events_normal_suppressed` — router events in normal mode produce no Telegram output
- [ ] *Unit*: `test_voice_format_router_events_verbose` — `[Router]` prefix present in verbose output
- [ ] *Unit*: `test_voice_main_session_events_unchanged` — regression guard
- [ ] *Unit*: `test_voice_tts_ignores_router_response` — emit `Response(source="router")` followed by `Response(source="orchestrator")`, assert TTS text is the orchestrator response content (not the routing JSON)
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

- [ ] `uv run pytest tests/ai/test_archon_router_mcp_server.py tests/ai/test_background_agent_manager.py tests/ai/test_decomposer.py tests/ai/test_event_renderer.py tests/chat/test_handler.py tests/chat/test_voice.py tests/ai/test_event_pipeline_router.py tests/ai/test_pipeline.py tests/gateway/test_shutdown.py tests/config/ -v`
- [ ] `uv run pytest` — full suite clean

---

## Task dependency graph

```
0.0 (SDK spike) ──────────────────────────────────────┐
0.1 (rename) ──────────────────────────────────────────┤
  └── 0.2 (restrict router MCP — allowed_tools=frozenset()) │
        └── 1.1 (bg agent → router MCP with BG_AGENT_ALLOWED_TOOLS) ◄── (0.0 + 0.1 + 0.2)
              └── 1.2 (eliminate third MCP server — per-route filtering)
0.1 ────── 2.0 (test migration) ── 2.1 (AsyncIterator route_task + is_router_event)
                                         ├── 2.2 (suppress content)
                                         │     └── 2.3 (EventRenderer + HistoryManager separator) ─┐── merge commit
                                         └── 2.4 (handler.py)                                      ─┘
                                               └── 2.5 (voice.py)
                                                     └── 3.1 (docs)
```

Tasks 0.0 and 0.1 are fully independent and can run in parallel. Task 0.2 depends only on 0.1. Task 1.1 depends on 0.0, 0.1, and 0.2. Task 1.2 depends on 1.1 (eliminates the third MCP server introduced by 1.1). Tasks 1.x and 2.x are independent of each other. **Task 2.0** prepares test infrastructure for the generator conversion. **Task 2.3 depends on 2.2** — both modify `event_renderer.py` and 2.2 must establish the `ToolResult` router branch first. Tasks 2.2 and 2.4 are independent of each other (different files). **2.0 + 2.1 + 2.2 + 2.3 + 2.4 + 2.5 must land as a single merged commit** — releasing 2.1 alone would emit unstyled `source="router"` events with no rendering treatment, and omitting 2.5 would let voice.py capture router Response JSON as TTS text.

---

## Summary

| Task | Title | Key files | Depends on |
|---|---|---|---|
| **0.0** | Verify SDK surfaces MCP tool calls as events | `test_sdk_mcp_event_emission.py` (new) | — |
| **0.1** | Rename routing session and its server | `archon_orch_mcp_server.py` → `archon_router_mcp_server.py`, `decomposer.py`, `pipeline.py`, `session_manager.py`, `gateway.py` | — |
| **0.2** | Restrict router MCP server — `allowed_tools=frozenset()` constructor param (history-only) | `archon_router_mcp_server.py` | 0.1 |
| **1.1** | Connect background agents to second router MCP server with `BG_AGENT_ALLOWED_TOOLS`; add per-user URL routing; `spawn_background_agent` blocked by architecture | `archon_router_mcp_server.py`, `background_agent_manager.py`, `gateway.py`, `config/loader.py` | 0.0, 0.1, 0.2 |
| **1.2** | Eliminate third MCP server — per-route tool filtering on single `ArchonRouterMCPServer` | `archon_router_mcp_server.py`, `gateway.py`, `config/loader.py` | 1.1 |
| **2.0** | Migrate test call sites for route_task generator conversion | `tests/conftest.py`, all test files referencing `route_task` | 0.1 |
| **2.1** | Stream routing session events in real time (last Response, preserve `_pending_turns`) | `decomposer.py`, `pipeline.py`, `event_mapper.py` (add `is_router_event`) | 0.1, **2.0** |
| **2.2** | Suppress routing tool result content in history | `event_renderer.py` | 2.1 |
| **2.3** | Render routing events distinctly in session history; auto-separator in `HistoryManager` | `event_renderer.py`, `history_manager.py` | 2.1, **2.2** |
| **2.4** | Deliver routing events to Telegram correctly (`html.escape` on ErrorEvent) | `handler.py` | 2.1 |
| **2.5** | voice.py routing event support (TTS guard for router Response) | `voice.py` | 2.4 |
| **3.1** | Update documentation | `CLAUDE.md`, Architecture docs | all |
