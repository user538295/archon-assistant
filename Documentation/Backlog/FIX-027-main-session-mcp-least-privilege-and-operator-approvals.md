# FIX-027 — Main-session MCP least privilege and operator approvals
**Purpose**: Remove unrestricted administrative MCP access from model-driven sessions, expose only a minimal allowlisted subset of tools, and require explicit operator approval before any privileged daemon action executes.
**Audience**: Archon operators and maintainers responsible for daemon security.
**Status**: To Do

---

## Background

The current implementation gives the main Claude session direct access to the background-agent MCP server:

- `SessionManager` threads `ArchonMCPServer.mcp_url_for(uid)` and bearer headers into every new main session pipeline in `archon/ai/session_manager.py`.
- `ClaudeSession.start()` registers that URL under `mcp_servers["archon"]` in `archon/ai/claude_session.py`.
- `ClaudeSession` also runs with `permission_mode="bypassPermissions"`, so Claude Code permission prompts are intentionally skipped in the headless daemon process.
- `ArchonMCPServer._handle_tools_list()` currently appends the full `ArchonToolkit.tool_definitions` list, and `_handle_tools_call()` delegates any registered toolkit tool without a per-tool allowlist.
- `ArchonToolkit` registers privileged handlers including `archon_restart`, `set_config`, and `send_file`.

This creates a direct prompt-injection path from untrusted inputs (repository content, attachment-derived text, retrieved context, and tool output) to daemon-level side effects. Once the model decides to call one of those tools, the SDK permission layer does not stop it because the session runs in bypass mode by design.

There is already a verified precedent for least-privilege MCP exposure in the codebase: `ArchonRouterMCPServer` accepts `allowed_tools` and filters both `tools/list` and `tools/call` for background-agent history access. The fix should extend that pattern to the main-session MCP surface and remove direct model access to privileged actions entirely.

---

## Goal

After this fix, no model-driven Archon session can directly invoke privileged administrative actions such as config writes, daemon restart, or file sending. The main session receives only a small, explicit allowlist of safe tools, and privileged actions can proceed only through a separate operator-confirmed approval flow.

---

## Scope

### In Scope
- Define capability tiers for Archon toolkit tools and derive per-surface allowlists from them
- Filter `ArchonMCPServer` `tools/list` and `tools/call` the same way `ArchonRouterMCPServer` already filters toolkit exposure
- Remove direct model-visible registration of `archon_restart`, `set_config`, and `send_file`
- Add operator-approval request tools for privileged actions
- Add Telegram approval/rejection callbacks that execute the underlying private handlers only after approval
- Update architecture docs and security wording to match the real MCP exposure model

### Out of Scope
- Replacing `permission_mode="bypassPermissions"` with interactive SDK prompts
- Changing whitelist middleware or Telegram user access control
- Reworking the existing `spawn_background_agent` execution model
- Broad redesign of every toolkit tool; this fix focuses on tool exposure and privileged side effects
- Solving every possible prompt-injection class beyond tool-surface containment

---

## Acceptance criteria
- [ ] `tools/list` for the main-session `ArchonMCPServer` no longer exposes `archon_restart`, `set_config`, or `send_file`
- [ ] `tools/call` for those three names returns an unknown-tool error on model-facing MCP routes
- [ ] The main session only sees an explicit minimal allowlist of safe tools plus privileged request tools
- [ ] Background-agent MCP allowlists are derived from the same capability-tier source, not ad hoc constants in `gateway.py`
- [ ] `request_archon_restart`, `request_set_config`, and `request_send_file` create pending approval requests and do not execute side effects immediately
- [ ] Telegram approval callbacks execute the underlying private handler exactly once after approval and never on reject/expiry
- [ ] Rejected or expired approvals leave config files, restart state, and Telegram file delivery unchanged
- [ ] All updated unit and integration tests pass

---

## What does NOT change
- `ClaudeSession` continues to use `permission_mode="bypassPermissions"`; the security boundary moves to MCP tool exposure rather than interactive SDK prompts
- `spawn_background_agent` remains available through `ArchonMCPServer`
- `Task`, `EnterPlanMode`, and `ExitPlanMode` remain disallowed in `ClaudeSession.start()`
- Bearer-token authentication and user-ID route scoping on both MCP servers stay in place
- The private implementations of `_handle_archon_restart()`, `_handle_set_config()`, and `_handle_send_file()` remain the execution logic; they are no longer directly model-callable

---

## Known limitations / accepted trade-offs
- Operator approval reduces but does not eliminate prompt-injection risk; a malicious prompt can still request a privileged action and rely on human error at approval time
- Safe read-only tools can still disclose information already permitted by their schemas; least privilege here focuses on side effects first
- The first version should cover the currently identified privileged actions (`archon_restart`, `set_config`, `send_file`) only; a wider review of `send_notification`, scheduling mutators, and log access can follow separately
- Pending approvals introduce a second-step UX for some workflows; this is an intentional trade-off for daemon safety

---

## Architecture

**Capability tiers**
- New module: `archon/ai/tool_capabilities.py`
- Define:
  - `ToolSurface = Literal["main_session", "background_agent"]`
  - `SAFE_MAIN_SESSION_TOOLS: frozenset[str]`
  - `SAFE_BACKGROUND_AGENT_TOOLS: frozenset[str]`
  - `PRIVILEGED_DIRECT_TOOLS: frozenset[str] = frozenset({"archon_restart", "set_config", "send_file"})`
  - `PRIVILEGED_REQUEST_TOOLS: frozenset[str] = frozenset({"request_archon_restart", "request_set_config", "request_send_file"})`
  - `allowed_tools_for(surface: ToolSurface) -> frozenset[str]`
- `allowed_tools_for("main_session")` returns the minimal model-visible set. Initial target:
  - `archon_status`
  - `list_running_agents`
  - `get_agent_status`
  - `read_agent_log`
  - `get_agent_by_name`
  - `get_session_status`
  - `get_context_stats`
  - `get_model`
  - `list_skills`
  - `get_version`
  - `request_archon_restart`
  - `request_set_config`
  - `request_send_file`
- `allowed_tools_for("background_agent")` preserves current read-only/background operations where justified, but replaces any direct privileged tool with the corresponding request tool

**Main-session MCP filtering**
- `archon/ai/archon_mcp_server.py`
- Add constructor parameter:
  - `allowed_tools: frozenset[str] = frozenset()`
- Store `self._allowed_tools`
- `_handle_tools_list()` returns:
  - `_SPAWN_TOOL`
  - toolkit tool definitions whose names are in `self._allowed_tools`
- `_handle_tools_call()` rejects toolkit calls not present in `self._allowed_tools`
- This should match the existing enforcement style already used by `ArchonRouterMCPServer`

**Privileged approval domain**
- New module: `archon/ai/operator_approval.py`
- Define:
  - `ApprovalAction = Literal["archon_restart", "set_config", "send_file"]`
  - `ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "executed"]`
  - `ApprovalRequest` dataclass with fields:
    - `request_id: str`
    - `action: ApprovalAction`
    - `arguments: dict[str, Any]`
    - `requested_by_user_id: int | None`
    - `created_at: float`
    - `expires_at: float`
    - `status: ApprovalStatus`
    - `approver_user_id: int | None`
  - `OperatorApprovalManager` with methods:
    - `create_request(action: ApprovalAction, arguments: dict[str, Any], *, requested_by_user_id: int | None) -> ApprovalRequest`
    - `get_request(request_id: str) -> ApprovalRequest | None`
    - `approve_request(request_id: str, *, approver_user_id: int) -> ApprovalRequest`
    - `reject_request(request_id: str, *, approver_user_id: int) -> ApprovalRequest`
    - `mark_executed(request_id: str) -> ApprovalRequest`
    - `expire_requests(*, now: float | None = None) -> int`
- Default approval TTL: 10 minutes via module constant `_APPROVAL_TTL_SECONDS = 600`

**Toolkit request tools**
- `archon/ai/archon_toolkit.py`
- Stop registering the direct model-callable tools:
  - `archon_restart`
  - `set_config`
  - `send_file`
- Keep their private handler methods intact for post-approval execution
- Register instead:
  - `request_archon_restart`
  - `request_set_config`
  - `request_send_file`
- New helper methods:
  - `_create_approval_request(action: ApprovalAction, arguments: dict[str, Any], *, user_id: int | None) -> str`
  - `_send_approval_prompt(request: ApprovalRequest) -> Awaitable[None]`
  - `execute_approved_request(request_id: str, *, approver_user_id: int) -> Awaitable[str]`
- `execute_approved_request()` dispatches internally:
  - `archon_restart` -> `_handle_archon_restart()`
  - `set_config` -> `_handle_set_config()`
  - `send_file` -> `_handle_send_file()`
- Execution happens only after the approval manager reports `approved`

**Telegram approval UI**
- `archon/chat/commands.py` and `archon/chat/bot.py`
- Add callback routes:
  - `approve_tool:<request_id>`
  - `reject_tool:<request_id>`
- Approval prompt message uses inline buttons and includes:
  - action name
  - sanitized argument summary
  - requesting user ID
  - expiry time
- Approval callback:
  - marks approval
  - executes the underlying private handler through `toolkit.execute_approved_request()`
  - edits the approval message with final outcome
- Reject callback:
  - marks rejected
  - edits the approval message accordingly

**Gateway wiring**
- `archon/gateway/gateway.py`
- Build both model-facing allowlists from `tool_capabilities.allowed_tools_for(...)`
- Pass `allowed_tools=allowed_tools_for("main_session")` to `ArchonMCPServer`
- Pass `allowed_tools=allowed_tools_for("background_agent")` to `ArchonRouterMCPServer`
- Construct `OperatorApprovalManager` once in the gateway and inject it into `ArchonToolkit`

---

## Tests
- **`test_allowed_tools_for_main_session_excludes_privileged_direct_tools`** (unit): capability policy returns no direct `archon_restart`, `set_config`, `send_file`
- **`test_allowed_tools_for_background_agent_uses_request_send_file`** (unit): background-agent policy allows the request variant and excludes direct file send
- **`test_bg_mcp_tools_list_filters_toolkit_to_allowed_tools`** (integration): main-session MCP route lists only spawn + allowlisted tools
- **`test_bg_mcp_rejects_disallowed_tool_call`** (integration): direct `set_config` / `send_file` / `archon_restart` calls fail on the main-session MCP server
- **`test_request_set_config_creates_pending_approval_without_writing_file`** (unit): request tool returns pending result and config file remains unchanged
- **`test_request_archon_restart_creates_pending_approval_without_scheduling_restart`** (unit): restart coordinator is untouched until approval
- **`test_request_send_file_creates_pending_approval_without_sending_document`** (unit): bot `send_document` is not called on request creation
- **`test_approve_request_executes_underlying_handler_once`** (unit): approving a request executes exactly one private handler call
- **`test_reject_request_does_not_execute_handler`** (unit): rejection never calls the underlying privileged handler
- **`test_expired_request_cannot_be_executed`** (unit): expiry prevents execution
- **`test_approval_callback_executes_and_edits_message`** (integration): Telegram callback path runs approval and updates the message text
- **`test_reject_callback_marks_request_rejected`** (integration): rejection callback updates stored status and UI text
- **`test_gateway_wires_main_session_allowlist_into_archon_mcp_server`** (integration): gateway constructs `ArchonMCPServer` with the main-session policy, not full toolkit access

---

## Documentation update
- [ ] `Documentation/Architecture/010_engineering_principles_and_constraints.md`, section "Session permission mode": clarify that `bypassPermissions` is acceptable only because model-visible MCP tools are explicitly allowlisted
- [ ] `Documentation/Architecture/120_services_and_integration_architecture.md`, sections "MCP Server Injection" and "Archon MCP Server": document per-surface tool allowlists and operator approval flow
- [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`, AI layer entries for `ArchonMCPServer` and `ArchonToolkit`: update responsibilities to mention allowlisting and approval requests
- [ ] `README.md`, architecture/security wording: state that privileged daemon actions require operator approval

---

## Task breakdown

### Phase 1 — Main-session least privilege
> **Releasable**: after Task 1.3 — the main session can no longer invoke privileged toolkit actions directly

#### Task 1.1 — Define shared MCP capability tiers
- [ ] **File**: `archon/ai/tool_capabilities.py`
- **Depends on**: nothing
- **Description**:
  - Add `ToolSurface = Literal["main_session", "background_agent"]`
  - Define frozenset constants for:
    - direct privileged tools
    - privileged request tools
    - main-session safe tools
    - background-agent safe tools
  - Implement:
    - `def allowed_tools_for(surface: ToolSurface) -> frozenset[str]:`
  - Rules:
    - `allowed_tools_for("main_session")` must exclude `archon_restart`, `set_config`, and `send_file`
    - `allowed_tools_for("background_agent")` must exclude direct privileged tools and use request variants where needed
    - Keep the function pure and deterministic; no config or global state reads
- **Releasable**: after this task, the project has a single source of truth for model-visible tool exposure
- **Tests (TDD)** — `tests/ai/test_tool_capabilities.py`:
  - Unit: `test_allowed_tools_for_main_session_excludes_privileged_direct_tools` — assert the direct privileged set is disjoint from the main-session allowlist
  - Unit: `test_allowed_tools_for_background_agent_uses_request_send_file` — assert direct `send_file` is excluded and `request_send_file` is present
  - Unit: `test_allowed_tools_for_unknown_surface_is_unreachable` — enforce exhaustive typing or explicit failure path if implementation uses a runtime guard
  - Checkpoint: `uv run pytest tests/ai/test_tool_capabilities.py --no-cov -v`

#### Task 1.2 — Enforce allowlists in `ArchonMCPServer`
- [ ] **File**: `archon/ai/archon_mcp_server.py`
- **Depends on**: Task 1.1
- **Description**:
  - Extend `ArchonMCPServer.__init__()` with:
    - `allowed_tools: frozenset[str] = frozenset()`
  - Store `self._allowed_tools`
  - Update:
    - `_handle_tools_list(self) -> dict[str, Any]`
    - `_handle_tools_call(self, params: Any, user_id: int) -> dict[str, Any]`
  - Behavior:
    - `spawn_background_agent` remains always available
    - toolkit tools are listed only when `tool_name in self._allowed_tools`
    - disallowed toolkit names return `_RpcError(_INVALID_PARAMS, f"Unknown tool: {tool_name!r}")`
  - Match the filtering semantics already present in `ArchonRouterMCPServer`
- **Releasable**: after this task, the background-agent MCP server can enforce a per-route allowlist instead of exposing the entire toolkit
- **Tests (TDD)** — `tests/ai/test_archon_mcp_server.py`:
  - Integration: `test_tools_list_returns_spawn_plus_allowed_tools_only` — toolkit contains extra tools, but the response lists only allowed names plus `spawn_background_agent`
  - Integration: `test_tools_call_rejects_disallowed_tool` — call `set_config` through the server and assert JSON-RPC error
  - Integration: `test_tools_call_allows_allowed_tool` — call an allowlisted safe tool and assert success
  - Checkpoint: `uv run pytest tests/ai/test_archon_mcp_server.py --no-cov -v -k "allowed_tools or rejects_disallowed or allows_allowed"`

#### Task 1.3 — Wire the main-session allowlist at gateway construction
- [ ] **File**: `archon/gateway/gateway.py`
- **Depends on**: Task 1.1, Task 1.2
- **Description**:
  - Import `allowed_tools_for` from `archon.ai.tool_capabilities`
  - Replace inline `BG_AGENT_ALLOWED_TOOLS` constant usage with shared policy
  - Construct:
    - `ArchonMCPServer(..., allowed_tools=allowed_tools_for("main_session"))`
    - `ArchonRouterMCPServer(..., allowed_tools=allowed_tools_for("background_agent"))`
  - Remove ad hoc per-surface allowlist definitions from the gateway body
  - Preserve existing bearer-token wiring and startup order
- **Releasable**: after this task, main-session tool exposure is constrained in production wiring
- **Tests (TDD)** — `tests/gateway/test_background_agent_gateway_integration.py`:
  - Integration: `test_gateway_wires_main_session_allowlist_into_archon_mcp_server` — patch constructors and assert `allowed_tools` equals `allowed_tools_for("main_session")`
  - Integration: `test_gateway_wires_background_agent_allowlist_into_router_mcp_server` — assert the router server uses `allowed_tools_for("background_agent")`
  - Checkpoint: `uv run pytest tests/gateway/test_background_agent_gateway_integration.py --no-cov -v -k "allowlist"`

---

### Phase 2 — Privileged request flow
> **Releasable**: after Task 2.3 — privileged actions can be requested safely, but still require human approval before execution

#### Task 2.1 — Add the operator approval state manager
- [ ] **File**: `archon/ai/operator_approval.py`
- **Depends on**: nothing
- **Description**:
  - Add:
    - `ApprovalAction = Literal["archon_restart", "set_config", "send_file"]`
    - `ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "executed"]`
    - `_APPROVAL_TTL_SECONDS = 600`
    - `ApprovalRequest` dataclass
    - `OperatorApprovalManager`
  - Implement methods:
    - `create_request(...)`
    - `get_request(...)`
    - `approve_request(...)`
    - `reject_request(...)`
    - `mark_executed(...)`
    - `expire_requests(...)`
  - Requirements:
    - use `uuid.uuid4().hex` request IDs
    - reject second approval/rejection/execution attempts on non-pending requests
    - status transitions must be explicit and testable
- **Releasable**: after this task, privileged requests can be stored and transitioned without executing side effects
- **Tests (TDD)** — `tests/ai/test_operator_approval.py`:
  - Unit: `test_create_request_sets_pending_status_and_expiry`
  - Unit: `test_approve_request_transitions_pending_to_approved`
  - Unit: `test_reject_request_transitions_pending_to_rejected`
  - Unit: `test_expire_requests_marks_stale_pending_requests`
  - Unit: `test_non_pending_request_cannot_be_approved_twice`
  - Checkpoint: `uv run pytest tests/ai/test_operator_approval.py --no-cov -v`

#### Task 2.2 — Replace direct privileged MCP tools with request tools
- [ ] **File**: `archon/ai/archon_toolkit.py`
- **Depends on**: Task 2.1
- **Description**:
  - Inject `approval_manager: OperatorApprovalManager | None = None` into `ArchonToolkit.__init__()`
  - Stop registering:
    - `archon_restart`
    - `set_config`
    - `send_file`
  - Register instead:
    - `request_archon_restart`
    - `request_set_config`
    - `request_send_file`
  - Add schemas and handlers:
    - `_REQUEST_ARCHON_RESTART_SCHEMA`
    - `_REQUEST_SET_CONFIG_SCHEMA`
    - `_REQUEST_SEND_FILE_SCHEMA`
    - `_handle_request_archon_restart(self, arguments: dict[str, Any], *, user_id: int | None = None) -> str`
    - `_handle_request_set_config(...) -> str`
    - `_handle_request_send_file(...) -> str`
  - Add helper:
    - `async def execute_approved_request(self, request_id: str, *, approver_user_id: int) -> str:`
  - Behavior:
    - request handlers validate arguments enough to build a request, create a pending approval, send an operator prompt, and return a pending message
    - request handlers must not call `_handle_archon_restart()`, `_handle_set_config()`, or `_handle_send_file()` directly
    - `execute_approved_request()` dispatches to the existing private handlers and then marks the request executed
- **Releasable**: after this task, no privileged daemon side effect is directly model-callable through the toolkit
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_service.py`, `tests/ai/test_archon_toolkit_config.py`:
  - Unit: `test_request_archon_restart_creates_pending_approval_without_scheduling_restart`
  - Unit: `test_request_set_config_creates_pending_approval_without_writing_file`
  - Unit: `test_request_send_file_creates_pending_approval_without_sending_document`
  - Unit: `test_execute_approved_request_runs_private_handler_once`
  - Integration: `test_tools_list_omits_direct_privileged_tools_and_includes_request_tools`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_service.py tests/ai/test_archon_toolkit_config.py --no-cov -v -k "request_ or execute_approved or omits_direct_privileged"`

#### Task 2.3 — Inject approval-aware tool policies into gateway surfaces
- [ ] **File**: `archon/gateway/gateway.py`
- **Depends on**: Task 1.3, Task 2.2
- **Description**:
  - Construct a single `OperatorApprovalManager` in `_run()`
  - Pass it into `ArchonToolkit(...)`
  - Ensure the shared per-surface allowlists now reference the request tool names introduced in Task 2.2
  - For background-agent routes, replace direct `send_file` exposure with `request_send_file`
  - Preserve existing late dependency wiring (`toolkit.set_late_deps(...)`)
- **Releasable**: after this task, both main-session and background-agent surfaces consistently use approval-aware privileged tooling
- **Tests (TDD)** — `tests/gateway/test_background_agent_gateway_integration.py`:
  - Integration: `test_gateway_constructs_toolkit_with_operator_approval_manager`
  - Integration: `test_background_agent_allowlist_uses_request_send_file_not_send_file`
  - Checkpoint: `uv run pytest tests/gateway/test_background_agent_gateway_integration.py --no-cov -v -k "approval_manager or request_send_file"`

---

### Phase 3 — Operator confirmation UI
> **Releasable**: after Task 3.2 — privileged requests can be approved or rejected end-to-end from Telegram

#### Task 3.1 — Register approval callback routes
- [ ] **File**: `archon/chat/bot.py`
- **Depends on**: Task 2.2
- **Description**:
  - Register two new callback handlers:
    - `dp.callback_query.register(approve_tool_callback, F.data.startswith("approve_tool:"))`
    - `dp.callback_query.register(reject_tool_callback, F.data.startswith("reject_tool:"))`
  - Keep the existing callback registration order stable; add the new handlers near other tool-action callbacks
  - Do not change whitelist middleware placement
- **Releasable**: after this task, Telegram callback routing can reach the approval handlers
- **Tests (TDD)** — `tests/chat/test_bot.py`:
  - Unit: `test_bot_registers_approve_tool_callback`
  - Unit: `test_bot_registers_reject_tool_callback`
  - Checkpoint: `uv run pytest tests/chat/test_bot.py --no-cov -v -k "approve_tool or reject_tool"`

#### Task 3.2 — Implement approve/reject callbacks and message updates
- [ ] **File**: `archon/chat/commands.py`
- **Depends on**: Task 2.1, Task 2.2, Task 3.1
- **Description**:
  - Add:
    - `async def approve_tool_callback(callback: CallbackQuery, ...) -> None`
    - `async def reject_tool_callback(callback: CallbackQuery, ...) -> None`
  - Parse `request_id` from callback data
  - Approval path:
    - call `toolkit.execute_approved_request(request_id, approver_user_id=callback.from_user.id)`
    - edit the approval message with success/failure text
    - answer the callback query with a short confirmation
  - Rejection path:
    - mark the request rejected through `OperatorApprovalManager`
    - edit the message to reflect rejection
    - answer the callback query
  - Handle invalid, expired, or already-resolved request IDs with user-visible errors and no side effects
  - Sanitise displayed argument summaries the same way the request tools sanitise prompt text
- **Releasable**: after this task, operator approval is a complete end-to-end flow
- **Tests (TDD)** — `tests/chat/test_commands.py`:
  - Integration: `test_approve_tool_callback_executes_request_and_edits_message`
  - Integration: `test_reject_tool_callback_marks_request_rejected`
  - Integration: `test_approve_tool_callback_rejects_expired_request`
  - Integration: `test_approve_tool_callback_handles_unknown_request_id`
  - Checkpoint: `uv run pytest tests/chat/test_commands.py --no-cov -v -k "approve_tool_callback or reject_tool_callback"`

---

### Phase 4 — Documentation and regression coverage
> **Releasable**: after each task

#### Task 4.1 — Update architecture and README security documentation
- [ ] **File**: `Documentation/Architecture/010_engineering_principles_and_constraints.md`
- **Depends on**: Task 1.3, Task 2.2
- **Description**:
  - Update the session permission-mode section to say the daemon runs in bypass mode but constrains model actions via MCP allowlisting and operator approval for privileged side effects
- **Releasable**: after this task, the key engineering principles doc no longer implies bypass mode is safe on its own
- **Tests (TDD)**: N/A
  - Checkpoint: N/A

#### Task 4.2 — Document per-surface MCP exposure and approval flow
- [ ] **File**: `Documentation/Architecture/120_services_and_integration_architecture.md`
- **Depends on**: Task 1.3, Task 3.2
- **Description**:
  - Update the Archon MCP section so it reflects real tool exposure:
    - main session -> minimal allowlisted tools + request tools
    - background agents -> separate allowlist
    - privileged operations -> operator-confirmed approval path
  - Add a short sequence diagram or prose flow for:
    - model requests privileged action
    - Telegram approval message appears
    - operator approves/rejects
    - action executes or is discarded
- **Releasable**: after this task, the main integration architecture doc matches production behavior
- **Tests (TDD)**: N/A
  - Checkpoint: N/A

#### Task 4.3 — Update component catalog and README summary
- [ ] **File**: `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`
- **Depends on**: Task 1.3, Task 2.2, Task 3.2
- **Description**:
  - Update `ArchonMCPServer` and `ArchonToolkit` responsibilities to mention tool filtering and approval requests
  - Update `README.md` architecture/security wording with one concise statement that privileged daemon actions require operator approval
- **Releasable**: after this task, the contributor-facing summaries match the implementation
- **Tests (TDD)**: N/A
  - Checkpoint: N/A
