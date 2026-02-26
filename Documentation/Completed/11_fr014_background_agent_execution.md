**Purpose**: TDD implementation plan for FR.014 background agent execution
**Audience**: Backend engineers
**Status**: Completed
**Last reviewed**: 2026-02-26
**Next review**: 2027-02-26

---

## Stories

### S15.1–S15.6: Background Agent Execution

**Status**: Completed ✅
**Priority**: High
**Estimated effort**: L

**User Story**: As a Telegram user, I want Claude to be able to spawn isolated background agents for long-running subtasks, so that the main conversation stays interactive while parallel work happens in the background.

For full acceptance criteria and technical notes per story, see [25_epic14_session_diagnostics.md](./25_epic14_session_diagnostics.md) and the source stories in individual epic files. The section below documents implementation decisions and divergences from the original plan.

---

## What was built

Background agent execution lets the main Claude session spawn isolated sub-agents as asyncio tasks while the conversation stays interactive. The mechanism is exposed to Claude via an Archon-hosted MCP tool (`spawn_background_agent`) served over HTTP on `localhost:18182`. All five epic stories (S15.1–S15.6) were completed.

**Key components shipped:**
- `BackgroundAgentsConfig` dataclass in `loader.py` — `spawn_rule`, `max_parallel`, `host`, `port`, `beacon_interval_minutes`
- `ClaudeSession.inject_context()` + `_pending_context` — one-shot context prepend before next `send()`
- `BackgroundAgentManager` — spawns `AgentRun` asyncio tasks, manages name pool, delivers Telegram notifications
- `ArchonMCPServer` (aiohttp) — JSON-RPC 2.0 endpoint at `/mcp/{user_id}`; routes `spawn_background_agent` calls to the manager
- `/running_agents` command with inline `[Cancel {name}]` buttons
- Live E2E test (S15.6): real `BackgroundAgentManager` + real `ClaudeSession`, no Telegram mock

## Implementation divergences from this plan

The following items differ between this plan and the final implementation. Treat the Architecture docs and source code as authoritative; this plan is historical only.

1. **`BackgroundAgentsConfig` has no `enabled` field.** Phase 3 of this plan shows `enabled: bool = False`. The actual dataclass has no `enabled` field — `BackgroundAgentManager` and `ArchonMCPServer` are always instantiated unconditionally by the gateway. Any `enabled = false` key in `[background_agents]` `config.toml` is silently ignored. *(Source: `archon/config/loader.py` lines 90–108; `archon/gateway/gateway.py` lines 239–254)*

2. **`BackgroundAgentsConfig` has a `beacon_interval_minutes` field not in this plan.** The shipped dataclass adds `beacon_interval_minutes: int = 2` to support the FR.15 per-agent beacon (the spawned-message is edited in-place with live tool/thinking counts). This field is absent from Phase 3's dataclass definition.

3. **`inject_context()` is NOT called by `BackgroundAgentManager._run_agent()`.** Phase 2 and Phase 4 state that on successful completion the manager calls `inject_context()` on the main session. The actual `_run_agent()` implementation calls only `_notify_success()` (Telegram notification) — context injection is not performed. *(Source: `archon/ai/background_agent_manager.py` lines 341–391)*

4. **Telegram notification format differs.** Phase 2 specifies `✅ Background agent **{name}** completed\n{result[:800]}` (Markdown bold, 800-char cap). The actual `_notify_success()` sends `✅ 🤖 Agent <b>{name}</b> completed` (HTML parse mode, includes 🤖 emoji, splits full result into ≤4000-char chunks — no 800-char cap). *(Source: `archon/ai/background_agent_manager.py` lines 455–488)*

5. **Gateway wiring is unconditional, not `if enabled`.** Phase 3 and Phase 5 say `if cfg.background_agents.enabled:` guards instantiation. The gateway always creates both `BackgroundAgentManager` and `ArchonMCPServer` regardless of config.

---

# Plan: FR.014 — Background Agent Execution

**Feature**: Enable Claude (the main Archon orchestrator) to spawn long-running subtasks as
isolated background agents — separate `ClaudeSession` instances running as asyncio tasks —
while keeping the main conversation fully interactive.  Background agents report results via
Telegram notification and inject their output into the main session's next `send()` call.
The mechanism is exposed to Claude via an Archon-hosted MCP tool `spawn_background_agent`.

**Methodology**: TDD — all tests written *first* (red), then implementation (green).
Test order: happy paths → edge cases → error paths.  Unit → integration → E2E → live.

---

## Phase 0: Documentation Discovery — COMPLETE ✅

All facts confirmed by reading source files.  No assumptions.

### Verified APIs & Exact Signatures

#### `archon/ai/claude_session.py`

| Symbol | Location | Signature / Value |
|--------|----------|-------------------|
| `ClaudeSession.__init__` | line 55 | `(cwd, skills, model, plugins, agents, qmd_url) → None` |
| `self._qmd_url` | line 69 | `str \| None` |
| `mcp_servers` local dict | line 160–162 | `{"qmd": {"type": "http", "url": ...}}` when qmd_url set |
| `ClaudeAgentOptions(mcp_servers=...)` | line 164–177 | already accepts `mcp_servers` dict |
| `ClaudeAgentOptions(disallowed_tools=...)` | line 175 | currently `["EnterPlanMode", "ExitPlanMode"]` |
| `ClaudeAgentOptions(system_prompt=...)` | line 167 | `str \| None` |
| `_pending_skills` | line 70 | `list[Skill]` — injected at `send()` start |
| `_build_system_prompt(skills)` | line 39 | `str \| None` — extend to include spawn_rule hint |
| `send()` skill-block prefix | line 219–225 | pattern to follow for context injection |

**Key insight**: The QMD MCP pattern at line 160-162 is the exact pattern for adding the
Archon background-agent MCP server.  A second entry is simply added to `mcp_servers`.

#### `archon/ai/session_manager.py`

| Symbol | Location | Signature |
|--------|----------|-----------|
| `SessionManager.__init__` | line 43 | `(timeout, cwd, session_factory, skill_loader, plugin_loader, agent_loader, qmd_url)` |
| `_default_factory` | line 61–81 | closure that builds `ClaudeSession`; must gain `background_agent_mcp_url` |
| `qmd_url` thread | line 57, 80 | pattern to copy for `background_agent_mcp_url` |

#### `archon/gateway/gateway.py`

| Symbol | Location | Note |
|--------|----------|------|
| `qmd_url` setup | line 259–266 | pattern to copy for MCP server setup |
| `session_manager` wiring | line 268–278 | must gain `background_agent_manager` |
| `_setup_dp()` | line 121–145 | must gain `background_agent_manager` dispatch key |
| `cron_scheduler.start()` | line 293 | pattern: `await mcp_server.start()` before polling |
| `finally` block | line 298–309 | must `await mcp_server.stop()` |

#### `archon/config/loader.py`

| Symbol | Location | Existing fields |
|--------|----------|-----------------|
| `Config` dataclass | line 117 | `qmd: QmdConfig` — add `background_agents: BackgroundAgentsConfig` |
| `load_config()` | line 174 | must parse `[background_agents]` section |

#### `archon/chat/commands.py`

| Pattern | Note |
|---------|------|
| Existing command handlers | `async def X_command(message, session_manager, ...)` |
| `/agents` pattern | Shows list with inline cancel buttons — follow for `/running_agents` |

#### `archon/chat/bot.py`

| Symbol | Note |
|--------|------|
| `BOT_COMMANDS` | Extend with `/running_agents` entry |
| `create_dispatcher()` | Registers command handlers via `router.message.register(...)` |

---

## Phase 1: Architecture

### Why MCP over HTTP?

The Claude Agent SDK is **single-session and blocking** — `ClaudeSDKClient.query()` is a
serial, non-concurrent protocol.  There is no SDK API for:
- Sending a second prompt while the first is in flight
- Starting an isolated session for a subtask
- Observing external tasks from within a session's tool loop

The solution: Archon hosts a **local HTTP MCP server** at `http://localhost:18182/mcp/{user_id}`.
Claude's main session sees `spawn_background_agent` as an ordinary MCP tool call.
Archon handles the call by starting a *separate* `ClaudeSession` as an `asyncio.Task` and
returning immediately — making the tool call instant and non-blocking for the main conversation.

MCP over HTTP uses JSON-RPC 2.0 (POST body).  The three required methods are:
`initialize`, `tools/list`, `tools/call`.  `aiohttp.web` is used for the HTTP layer
(aiogram already depends on aiohttp, so it is available; add it explicitly to pyproject.toml).

### Component map

```
Telegram user ──► handle_message ──► ClaudeSession.send()
                                           │
                              ┌── MCP tool call ──────────────────────────┐
                              │  spawn_background_agent(task, context, name?)│
                              └───────────────────────────────────────────┘
                                           │
                                    ArchonMCPServer
                                    (aiohttp, port 18182)
                                           │
                                  BackgroundAgentManager.spawn()
                                   ─► asyncio.create_task(_run_agent)
                                           │
                          ┌────────────────┴────────────────┐
                          │  Isolated ClaudeSession          │
                          │  (no hooks, fresh context)       │
                          └────────────────┬────────────────┘
                                           │ done
                             ┌─────────────┴──────────────┐
                             │  bot.send_message(user_id)  │  Telegram notification
                             │  main_session.inject_context│  context for next send()
                             └────────────────────────────┘
```

### Spawn rule behaviour

| `spawn_rule` | Behaviour |
|---|---|
| `"eager"` | System prompt directs Claude to proactively use background agents for any multi-step or parallelisable task |
| `"auto"` | System prompt makes the tool available; Claude uses its own judgment |
| `"manual"` | System prompt restricts use to explicit user requests only |

---

## Phase 2: New Files

### `archon/ai/background_agent_manager.py`

```python
@dataclass
class AgentRun:
    run_id: str            # uuid4 hex
    name: str              # human-readable name (from _AGENT_NAMES pool)
    task: str              # task description (truncated to 200 chars in display)
    context: str           # context snippet passed at spawn time
    user_id: int
    started_at: float      # time.monotonic()
    _task_ref: asyncio.Task | None = None
    status: str = "running"       # "running" | "completed" | "failed" | "cancelled"
    result: str | None = None
    error: str | None = None

class BackgroundAgentManager:
    def __init__(
        self,
        bot: Bot,
        session_manager: SessionManager,
        max_parallel: int = 5,
        model: str | None = None,
        cwd: str | None = None,
        qmd_url: str | None = None,
    ) -> None: ...

    async def spawn(
        self,
        user_id: int,
        task: str,
        context: str = "",
        name: str | None = None,
    ) -> AgentRun:
        """Start a background agent; returns immediately.

        Raises RuntimeError if max_parallel agents already running for user_id.
        """

    def list_running(self, user_id: int) -> list[AgentRun]:
        """Return all AgentRuns for user_id with status="running"."""

    def list_all(self, user_id: int) -> list[AgentRun]:
        """Return all AgentRuns for user_id regardless of status."""

    async def cancel(self, run_id: str) -> bool:
        """Cancel an in-progress agent. Returns True if found and cancelled."""

    def get_run(self, run_id: str) -> AgentRun | None: ...

    async def stop_all(self) -> None:
        """Cancel all running agents (called at shutdown)."""

    # Internal
    async def _run_agent(self, run: AgentRun) -> None:
        """Run the isolated ClaudeSession; on finish notify and inject context."""
```

**Name pool**: Reuses `_AGENT_NAMES` from `archon/ai/claude_session.py` (imported).
The manager tracks names in use across ALL users to avoid duplicates globally.

**Result injection format**:
```
[Background agent {name} completed]
Task: {task}
Response:
{result}
[End agent {name}]
```

**Telegram notification format**:
- Success: `✅ Background agent **{name}** completed\n{result[:800]}`
- Failure: `❌ Background agent **{name}** failed\n{error[:400]}`

### `archon/ai/archon_mcp_server.py`

```python
class ArchonMCPServer:
    """Minimal HTTP MCP server serving the spawn_background_agent tool.

    Each user's main ClaudeSession is given the URL:
        http://localhost:{port}/mcp/{user_id}

    The user_id in the URL path allows the server to route spawn requests
    to the correct BackgroundAgentManager entry.
    """

    def __init__(
        self,
        manager: BackgroundAgentManager,
        host: str = "localhost",
        port: int = 18182,
    ) -> None: ...

    async def start(self) -> None:
        """Start the aiohttp web server."""

    async def stop(self) -> None:
        """Gracefully stop the web server."""

    def mcp_url_for(self, user_id: int) -> str:
        """Return the MCP endpoint URL for a specific user session."""
        return f"http://{self._host}:{self._port}/mcp/{user_id}"

    # HTTP handler (internal)
    async def _handle_post(self, request: web.Request) -> web.Response:
        """Handle all JSON-RPC 2.0 MCP requests."""
```

**MCP methods implemented**:
- `initialize` — returns server capabilities (no special caps needed)
- `tools/list` — returns the `spawn_background_agent` tool descriptor
- `tools/call` — executes the tool, returns `{"agent_name": str, "run_id": str, "status": "started"}`

**Tool descriptor** (`tools/list` response):
```json
{
  "name": "spawn_background_agent",
  "description": "Spawn a background agent to run a task asynchronously while the main conversation remains interactive. The agent runs in an isolated session and you will receive its result as context injected into your next message.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "task":    {"type": "string", "description": "The task for the agent to perform"},
      "context": {"type": "string", "description": "Relevant context or data the agent needs", "default": ""},
      "name":    {"type": "string", "description": "Optional human-readable name (random if omitted)"}
    },
    "required": ["task"]
  }
}
```

---

## Phase 3: Modified Files

### `archon/config/loader.py`

**New dataclass** (add after `QmdConfig`):
```python
@dataclass
class BackgroundAgentsConfig:
    enabled: bool = False
    spawn_rule: str = "auto"     # "eager" | "auto" | "manual"
    max_parallel: int = 5        # max concurrent background agents per user
    host: str = "localhost"
    port: int = 18182
```

**`Config` dataclass** — add field:
```python
background_agents: BackgroundAgentsConfig = field(default_factory=BackgroundAgentsConfig)
```

**`load_config()`** — parse `[background_agents]` section.

### `archon/ai/claude_session.py`

**`__init__`** — add parameters:
```python
background_agent_mcp_url: str | None = None,
spawn_rule: str | None = None,         # "eager" | "auto" | "manual" | None
```

**`start()`** — add background MCP server to `mcp_servers`:
```python
if self._background_agent_mcp_url is not None:
    mcp_servers["archon"] = {"type": "http", "url": self._background_agent_mcp_url}
```

Add `"Task"` to `disallowed_tools` when background agents are enabled:
```python
disallowed = ["EnterPlanMode", "ExitPlanMode"]
if self._background_agent_mcp_url is not None:
    disallowed.append("Task")
```

**`_build_system_prompt()`** — extend to accept `spawn_rule` and append hint:
```python
SPAWN_RULE_HINTS = {
    "eager":  "When a task involves multiple independent steps or parallel workstreams, proactively use the `spawn_background_agent` MCP tool to run subtasks in the background.",
    "auto":   "You have access to a `spawn_background_agent` MCP tool. Use it when running a task in the background would keep the conversation more responsive.",
    "manual": "You have access to a `spawn_background_agent` MCP tool. Only use it when the user explicitly asks you to run something in the background.",
}
```

**New method `inject_context(text: str) -> None`**:
```python
def inject_context(self, text: str) -> None:
    """Queue text to be prepended to the next outgoing send() call (one-shot)."""
    self._pending_context.append(text)
```

**`send()`** — prepend context blocks (analogous to skill injection):
```python
if self._pending_context:
    context_block = "\n\n".join(self._pending_context)
    full_prompt = f"{context_block}\n\n{prompt}"
    self._pending_context.clear()
```

Context blocks are prepended **before** skill blocks so the order is:
`[context blocks] → [skill blocks] → [user prompt]`

### `archon/ai/session_manager.py`

`SessionManager.__init__` — add:
```python
background_agent_mcp_url: str | None = None,
```

Update `_default_factory` to pass `background_agent_mcp_url` and `spawn_rule` to `ClaudeSession`.

### `archon/gateway/gateway.py`

In `_run()`:
1. If `cfg.background_agents.enabled`:
   - Instantiate `BackgroundAgentManager`
   - Instantiate `ArchonMCPServer(manager, host, port)`
   - `await mcp_server.start()`
   - Build per-user MCP URL lambda: `lambda user_id: mcp_server.mcp_url_for(user_id)`
   - Pass URL factory to `SessionManager` (or use a fixed URL without user_id — see below)
2. In `finally` block: `await mcp_server.stop()`, `await background_agent_manager.stop_all()`

**Note on user_id in URL**: The MCP URL is set when `ClaudeSession.start()` is called.
The factory receives `user_id` via `get_or_create(user_id)` → `_factory(cwd, user_id?)`.
The `session_factory` signature must be updated to pass `user_id` through, OR the
`mcp_server.mcp_url_for(user_id)` is called inside `get_or_create` before session creation.

**Chosen approach**: Extend `SessionManager.get_or_create(user_id)` to call
`self._background_agent_mcp_server.mcp_url_for(user_id)` if server is set,
then pass that URL to the session factory.  This keeps the factory signature clean.

### `archon/chat/commands.py`

New command handler `running_agents_command`:
```
/running_agents
```
Shows a message listing all running background agents for the user.
Each entry includes:
- Agent name, task (truncated to 60 chars), elapsed time
- An inline `[Cancel {name}]` button with callback data `cancel_agent:{run_id}`

Also registers `cancel_agent_callback` for the inline button.

### `archon/chat/bot.py`

Add `/running_agents` to `BOT_COMMANDS`.

### `pyproject.toml`

Add `aiohttp>=3.9` to dependencies (aiogram already pulls it in transitively, but we
use it directly in `ArchonMCPServer`).

---

## Phase 4: Test Plan

### Unit Tests (all in `tests/`)

**`tests/config/test_loader.py`** (extend existing):
- `BackgroundAgentsConfig` defaults: `enabled=False`, `spawn_rule="auto"`, `max_parallel=5`
- Parse `[background_agents]` section from TOML: all fields
- Missing section → defaults applied, no `ConfigError`

**`tests/ai/test_claude_session.py`** (extend existing):
- `inject_context()` queues text; cleared after `send()`
- Multiple `inject_context()` calls: all prepended in order
- Context prepended before skill block in `send()`
- `"Task"` in `disallowed_tools` when `background_agent_mcp_url` is set
- `"Task"` NOT in `disallowed_tools` when `background_agent_mcp_url` is None
- `spawn_rule` hint appended to system prompt for each rule value
- No hint when `spawn_rule` is None

**`tests/ai/test_background_agent_manager.py`** (new file):
- `AgentRun` dataclass fields
- `BackgroundAgentManager.spawn()` — returns `AgentRun` with `status="running"`
- `spawn()` — asyncio task created (mock ClaudeSession)
- `list_running(user_id)` — returns only running agents for that user
- `list_all(user_id)` — returns all regardless of status
- `cancel(run_id)` — cancels task, sets `status="cancelled"`, returns `True`
- `cancel(unknown_id)` — returns `False`
- `stop_all()` — cancels all running tasks
- `_run_agent()` success — sets `status="completed"`, stores result, notifies Telegram, calls `inject_context()`
- `_run_agent()` failure — sets `status="failed"`, stores error, notifies Telegram (error format)
- Max parallel limit — `spawn()` raises `RuntimeError` when limit exceeded
- Name assignment — each agent gets a name from `_AGENT_NAMES`; no duplicates
- Name released — after completion, name can be reused

**`tests/ai/test_archon_mcp_server.py`** (new file):
- `ArchonMCPServer.start()` / `stop()` — server starts and stops
- `mcp_url_for(user_id)` — returns correct URL string
- `initialize` JSON-RPC method — returns correct capabilities
- `tools/list` — returns `spawn_background_agent` descriptor with correct schema
- `tools/call spawn_background_agent` — happy path: calls `manager.spawn()`, returns `{"agent_name": ..., "run_id": ..., "status": "started"}`
- `tools/call` — unknown tool name → JSON-RPC error response
- `tools/call` — missing required `task` param → JSON-RPC error response
- `tools/call` — unknown method → JSON-RPC error
- Invalid JSON body → 400 response
- `user_id` extracted from URL path correctly

**`tests/chat/test_commands.py`** (extend existing):
- `/running_agents` with no running agents → "No background agents currently running"
- `/running_agents` with 2 running agents → formatted list with Cancel buttons
- `cancel_agent_callback` — known run_id → cancels, edits message
- `cancel_agent_callback` — unknown run_id → replies "Agent not found"
- `/running_agents` when `background_agent_manager` not wired → graceful message

### Integration Tests

**`tests/ai/test_background_agent_integration.py`** (new file):
- `ArchonMCPServer` + `BackgroundAgentManager` together: HTTP POST to `tools/call` spawns an agent task
- Successful agent run: result injected via `inject_context()` on mock main session
- Telegram notification sent on completion
- Max parallel enforced through MCP: 6th `spawn` → error response
- Cancel via MCP: POST `tools/call` with a `cancel_agent` tool (future) — or direct cancel test

### E2E Tests

**`tests/ai/test_background_agent_e2e.py`** (new file):
- Full flow with mock `ClaudeSession` for both orchestrator and background agent:
  - Send message → mock session yields a `ToolStarted("mcp__archon__spawn_background_agent")` event
  - MCP server receives call, spawns background ClaudeSession (mocked)
  - Background task completes → `inject_context()` called on main session
  - Next `send()` includes the injected context prefix

### Live Tests

**`tests/ai/test_background_agent_live.py`** (new file, `@pytest.mark.live`):
- `BackgroundAgentManager` with real `ClaudeSession` (no Telegram mock)
- Agent runs a trivial prompt ("Say 'done' and nothing else") within 30s
- `status` transitions: `"running"` → `"completed"`
- `result` is non-empty
- `inject_context()` was called (check `_pending_context` on main session mock)

---

## Phase 5: Implementation Order (TDD)

```
1. Write ALL tests (red)
2. BackgroundAgentsConfig in loader.py         [unit tests pass]
3. inject_context() + spawn_rule in ClaudeSession  [unit tests pass]
4. BackgroundAgentManager                     [unit + integration tests pass]
5. ArchonMCPServer                            [unit + integration tests pass]
6. SessionManager wiring                      [integration tests pass]
7. Gateway wiring                             [E2E tests pass]
8. /running_agents command                    [command unit tests pass]
9. Live tests                                 [live tests pass]
10. Full test suite + coverage check
```

---

## Anti-Patterns to Avoid

1. **Don't block the main session**: `BackgroundAgentManager.spawn()` must return
   immediately after creating the asyncio task — never `await` the agent's completion.

2. **Don't share the SDK client**: Each background agent gets its own `ClaudeSession`
   with its own `ClaudeSDKClient`.  Never reuse the main session's client.

3. **Don't use `asyncio.Queue` for result delivery**: Results are delivered via
   `inject_context()` on the main session + Telegram notification.  No queue needed.

4. **Don't persist context across multiple sends**: `inject_context()` is one-shot.
   The `_pending_context` list is cleared at the start of each `send()`.

5. **Don't make `aiohttp` version conflict**: `aiogram >= 3.0` requires `aiohttp >= 3.8`.
   Pin `aiohttp >= 3.9` in `pyproject.toml`.

6. **Don't forget stop_all() at shutdown**: `BackgroundAgentManager.stop_all()` must be
   called in the gateway `finally` block before `session_manager.stop_all()`.

---

## Related Documents

- [120 Services and Integration Architecture](../Architecture/120_services_and_integration_architecture.md) — full documentation of `ArchonMCPServer`, `BackgroundAgentManager`, and the MCP JSON-RPC 2.0 protocol flow
- [ADR-06: Background Agents via Local MCP HTTP Server](../ADRs/06_background_agents_via_mcp_http.md) — the architectural decision record explaining why this approach was chosen over the SDK's native `Task` tool
