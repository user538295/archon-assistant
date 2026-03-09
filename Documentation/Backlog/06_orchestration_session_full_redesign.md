# Orchestration Session Full Redesign

**Status:** Planned
**Supersedes:** `04_add_conversation_context_to_orchestration_session.md` (partial solution)
**Created:** 2026-03-09

---

## Problem Statement

When the user says "rewrite the script from yesterday", the Decomposer's `_orch_session` generates a plan
that tells agents to scan the entire filesystem (`find / -maxdepth 5 ...`). Root cause: `_orch_session`
is created with `tools=[]` and `max_turns=1`, receives no cross-session history context, and is asked to
produce correct absolute file paths for agent prompts that it has never seen.

Three compounding failures:

1. **`_orch_session` is context-blind on new sessions.** `_context_summary` only covers the current
   running session (built by the Haiku summarizer from `answer()` turns). A new session always starts
   with `_context_summary = ""`. Yesterday's work lives in `~/.archon/history/` but nothing reads it
   into `_orch_session`.

2. **`route_task.md` says "include absolute paths" but the LLM cannot know them.** Without context,
   the only rational plan is "search for the file" — which the LLM does correctly given what it knows.

3. **`estimated_tools` routing bypass.** When classifier returns `estimated_tools > 1`, `route_task()`
   is called. For `estimated_tools <= 1`, the message goes straight to `_session.answer()` bypassing
   `_orch_session` entirely. The classifier's `estimated_tools` estimate is made without any workspace
   context — it is structurally unreliable.

Also discovered: `_session` receives history context via `startup_context_prompt() + get_recent_context()`
injection at session creation (in `SessionManager.get_or_create()`), but `Pipeline.inject_context()` only
routes to `_session`, never to `_orch_session`. The infrastructure exists; the wiring is missing.

---

## Design Decisions (from extended design session 2026-03-09)

### D1 — Classifier becomes binary-only
Remove `estimated_tools` from `Classification`. The classifier only outputs `{intent, confidence}`.
Routing: `chat + confidence >= 0.8` → `_session.answer()` directly.
Everything else (task, or chat below threshold) → `_orch_session.route_task()` always.
Remove the low-confidence `review()` path entirely — `_orch_session` handles ambiguity naturally.

### D2 — `_orch_session` gets history context at session start
The existing `HistoryCompactor.startup_context_prompt()` + `get_recent_context()` (last 2 compacted days
+ today's partial) must also be injected into `_orch_session` at `Decomposer.start()`. One extra
`inject_context()` call is all that's needed.

### D3 — `_orch_session` gets limited Read/Grep tools via `ArchonOrchestratorMCPServer`
`_orch_session` receives a new MCP server with only two tools:
- `history_read(path)` — reads a file; path MUST start with resolved `~/.archon/history/` (enforced
  in server handler code, not just prompt)
- `history_grep(pattern, path)` — greps a file; same path restriction

The server is read-only, has no side effects, no agent spawning, no user state.
Path restriction is enforced at the HTTP handler level — rejected paths return `isError: true`.

`max_turns` for `_orch_session` changes from 1 to 5.

System prompt addition: "Use history_read and history_grep if the user references past work or files.
You have at most 3 turns for research. Your final response MUST be valid JSON only — no explanation,
no markdown. Your output is machine-parsed; any non-JSON is treated as an error."

### D4 — Pipeline owns orchestration; `_orch_session` returns JSON text
`submit_plan` as a tool was considered and rejected. The pipeline calls `route_task()` and expects a
return value. The LLM calling a `submit_plan` tool inverts control — the pipeline would wait on LLM
discretion rather than a deterministic function return.

`_orch_session` researches with tools, then its FINAL TEXT RESPONSE is the JSON plan. The pipeline
parses it via the existing `_parse_task_output()` + `extract_json_object()` fallback chain.
`PlanExecutor` + `BackgroundAgentManager` continue to own agent spawning — no change.

Fallback chain: clean JSON → `extract_json_object()` strips surrounding text → `scope="small"` with
original prompt → `_session` handles it. User always gets a response.

### D5 — Dual-prompt format
When `_orch_session` enriches a prompt for `scope="small"`, the final prompt passed to `_session` is:
```
[Original user request]: {original_prompt}
[Resolved context]: {orch_resolved_prompt}
```
Only when the resolved prompt differs from the original. This prevents AI intent drift while still
providing resolved context to `_session`.

For `scope="large"` agents, `route_task.md` instructs `_orch_session` to embed the original request
summary AND the resolved task in each agent's task field.

### D6 — Background agents get `agents.md`
`BackgroundAgentManager._run_agent()` creates a bare `ClaudeSession` with no workspace awareness.
Fix: inject `agents.md` content into the new session immediately after `await session.start()`.
Same logic as `Decomposer._inject_workspace_agents()` — read `{cwd}/agents.md`, inject if present.
This gives every background agent the map to find history files.

### D7 — `ArchonOrchestratorMCPServer` naming and scope
The new MCP server is named `ArchonOrchestratorMCPServer`.
It is a research tool server only — NOT an orchestration server.
All orchestration (routing decisions, agent spawning, plan execution) remains in the pipeline.

### D8 — Today's partial compaction
`compact_today()` IS already called at gateway startup (verified). `2026-03-09-partial.md` EXISTS.
There is a race condition: if `compact_today()` hasn't finished when the first session is created,
`get_recent_context()` uses the partial from the PREVIOUS daemon run (acceptable) or `None` (first
ever start — acceptable). No fix needed; the partial from the last run is sufficient context.

---

## Architecture After Redesign

```
User message
    │
    ▼
Classifier (Haiku — binary only: {intent, confidence}, no estimated_tools)
    │
    ├── chat + confidence >= 0.8  ──────────────────────────────▶  _session.answer(original_prompt)
    │
    └── task  OR  chat < 0.8
            │
            ▼
    _orch_session.route_task()
        Context (injected at session start):
          - startup_context_prompt() — file structure map
          - get_recent_context() — last 2 compacted days + today partial
          - _context_summary — Haiku rolling summary of current session
        Tools (via ArchonOrchestratorMCPServer):
          - history_read(path) — path-restricted to ~/.archon/history/
          - history_grep(pattern, path) — same restriction
        max_turns: 5
        System prompt: research budget + JSON-only final response
        │
        ├── scope="small"  ──────────────────────────────────────▶  _session.answer(
        │                                                              "[Original user request]: {orig}\n"
        │                                                              "[Resolved context]: {resolved}"
        │                                                            )
        │
        └── scope="large"  ──────────────────────────────────────▶  PlanExecutor
                                                                        → BackgroundAgentManager.spawn()
                                                                          (each agent gets agents.md injected)
                                                                          (each agent task has original +
                                                                           resolved context)
```

---

## Implementation Plan

### Wave 1 — Independent, parallelizable

#### Step 1: Remove `estimated_tools` from Classifier + Classification

**Files:**
- `archon/ai/classification.py` — remove `estimated_tools` from `Classification` dataclass and `parse_classification()`
- `archon/ai/prompts/classifier.md` — remove `estimated_tools` from schema and estimation guidance
- `archon/ai/event_mapper.py` — remove `estimated_tools` from `ClassificationEvent`
- `archon/ai/pipeline.py` — remove `estimated_tools` variable (routing change is Step 4)

**Tests to update:**
- `tests/ai/test_classification.py` — remove `estimated_tools` parse tests; test field is absent
- `tests/ai/test_classifier.py` — remove `estimated_tools=0` from mock helpers and assertions
- `tests/ai/test_pipeline.py` — remove `estimated_tools` from `_mock_classifier()` helper and `ClassificationEvent` assertions

**Risk:** Verify `chat/handler.py` and `event_renderer.py` do not display `estimated_tools`.

---

#### Step 2: Create `ArchonOrchestratorMCPServer`

**Files:**
- New `archon/ai/archon_orch_mcp_server.py` — HTTP MCP server (aiohttp, JSON-RPC 2.0)
  - Tools: `history_read(path: str)`, `history_grep(pattern: str, path: str)`
  - Path validation: `Path(path).expanduser().resolve()` must be under
    `Path("~/.archon/history/").expanduser().resolve()` — enforced in handler, returns `isError: true` if not
  - No per-user routing (read-only, no state)
  - Methods: `start(host, port)`, `stop()`, `mcp_url` property
  - Pattern: follow `archon/ai/archon_mcp_server.py` exactly (same JSON-RPC dispatch, `_ok`/`_error` helpers)
- `archon/config/loader.py` — add `orch_mcp_port: int = 18183` to `BackgroundAgentsConfig`

**Tests to write:**
- New `tests/ai/test_archon_orch_mcp_server.py` (follow `test_archon_mcp_server.py` pattern):
  - `initialize` returns correct capabilities
  - `tools/list` returns exactly `history_read` and `history_grep`
  - `history_read` with valid path reads file content
  - `history_read` with path outside `~/.archon/history/` → `isError: true`
  - `history_grep` with valid path returns matching lines
  - `history_grep` with out-of-bounds path → `isError: true`
  - unknown tool → error response
  - `health` endpoint → 200

---

#### Step 7: Background agents get `agents.md`

**Files:**
- `archon/ai/background_agent_manager.py` — in `_run_agent()`, after `await session.start()`:
  read `{self._cwd}/agents.md` if it exists and call `session.inject_context(content)`
  Same logic as `Decomposer._inject_workspace_agents()` — graceful on FileNotFoundError

**Tests to write:**
- `tests/ai/test_background_agent_manager.py`:
  - When `cwd` has `agents.md`, `session.inject_context` is called with its content after `session.start()`
  - When `cwd` has no `agents.md`, `inject_context` is not called for agents.md

---

#### Step 9: Update `route_task.md` prompt

**Files:**
- `archon/ai/prompts/route_task.md` — add research guidance section:
  "If the user references past work, previously created files, or prior sessions, use `history_read` to
  read daily compacted summaries first (`~/.archon/history/daily/`), then `history_grep` to find specific
  paths in session logs only if needed. Reserve your final response turn for JSON output only."

---

### Wave 2 — After Wave 1

#### Step 3: `Decomposer` — add `context_provider` + `orch_mcp_url`, inject history into `_orch_session`

**Pre-step:** Read `archon/ai/claude_session.py` to verify the exact parameter name for MCP URLs
(currently uses `background_agent_mcp_url` — confirm if a list param or separate param is needed for
the orch MCP server).

**Files:**
- `archon/ai/decomposer.py`:
  - Add `context_provider: ContextProvider | None = None` parameter to `__init__`
  - Add `orch_mcp_url: str | None = None` parameter to `__init__`
  - Change `_orch_session` construction: remove `tools=[]`, change `max_turns=1` → `max_turns=5`,
    add `mcp_url` for `ArchonOrchestratorMCPServer`, add orchestrator system prompt
  - In `start()`: after `await self._orch_session.start()`, call
    `self._orch_session.inject_context(startup_context_prompt + get_recent_context)`
  - In `_inject_workspace_agents()`: also inject `agents.md` into `_orch_session`
  - After `_orch_session` reset in `_reset_orch_if_needed()`: re-inject history context

**Tests to update/write:**
- `tests/ai/test_decomposer.py`:
  - `_make_decomposer` helper: `_orch_session` construction must NOT use `tools=[]` anymore;
    assert `max_turns=5`; the 3 mock sessions remain 3 (no new ClaudeSession added)
  - New: `test_orch_session_receives_history_context_at_start` — `context_provider.startup_context_prompt()`
    and `inject_context` called on orch session during `start()`
  - New: `test_orch_session_re_receives_context_after_reset` — after `_reset_orch_if_needed` triggers,
    `inject_context` called again on the new orch session

---

### Wave 3 — After Step 3

#### Step 4: `Pipeline.send()` routing change + remove `review()` call

**Files:**
- `archon/ai/pipeline.py`:
  - Remove `confidence < _CONFIDENCE_THRESHOLD` block (review call)
  - Remove `estimated_tools > 1` branch
  - New routing: `intent == "chat" and confidence >= 0.8` → `_decomposer.answer(prompt)` directly;
    all else → `_decomposer.route_task(prompt)` (always flush pending context first)
  - Remove `ReviewEvent` import

**Tests to update:**
- `tests/ai/test_pipeline.py`:
  - Delete: all review flow tests, all `estimated_tools > 1` routing tests
  - Add: `test_chat_high_confidence_routes_to_answer_directly`
  - Add: `test_chat_low_confidence_routes_to_route_task`
  - Add: `test_task_any_confidence_routes_to_route_task`
  - Keep: all promotion monitor tests (orthogonal)

#### Step 6: Dual-prompt format in `Pipeline._yield_plan()`

**Files:**
- `archon/ai/pipeline.py` — in `_yield_plan()`, for `scope="small"`:
  when `task_output.prompt != original_prompt`, wrap as:
  `"[Original user request]: {original_prompt}\n[Resolved context]: {task_output.prompt}"`

**Tests to write:**
- `test_small_scope_dual_prompt_when_enriched` — different resolved prompt → dual format in AgentTask
- `test_small_scope_no_dual_prompt_when_same` — identical prompt → used as-is

---

### Wave 4 — After Step 4

#### Step 5: Remove `review()`, `ReviewResult`, `ReviewEvent`

**Files:**
- `archon/ai/decomposer.py` — delete `review()`, `_parse_review()`, `ReviewResult` dataclass
- `archon/ai/event_mapper.py` — delete `ReviewEvent` dataclass; update `Event` union type
- `archon/ai/prompts/review.md` — delete (dead)

**Tests:**
- `tests/ai/test_decomposer.py` — delete all `review()`-related tests
- Verify no remaining import of `ReviewEvent`, `ReviewResult` anywhere

---

### Wave 5 — After all above

#### Step 8: Gateway wiring

**Files:**
- `archon/gateway/gateway.py`:
  - Import `ArchonOrchestratorMCPServer`
  - Create `orch_mcp_server` instance (port from `cfg.background_agents.orch_mcp_port`)
  - Start/stop alongside `bg_mcp_server`
  - Thread `orch_mcp_url` through to `SessionManager`
- `archon/ai/session_manager.py` — add `orch_mcp_url: str | None = None`, pass to `Pipeline`
- `archon/ai/pipeline.py` — add `orch_mcp_url` + `context_provider` to `__init__`, pass to `Decomposer`

**Tests:**
- `tests/gateway/test_gateway.py` — assert `orch_mcp_server` started
- `tests/ai/test_session_manager.py` — `orch_mcp_url` threaded through

---

## Dependency Graph

```
Step 1 ──────────────────────┐
Step 2 ──────────────────────┤  (Wave 1 — parallel)
Step 7 ──────────────────────┤
Step 9 ──────────────────────┘
                             │
                             ▼
                          Step 3 (Wave 2)
                             │
                             ▼
                     Step 4 + Step 6 (Wave 3 — parallel)
                             │
                             ▼
                          Step 5 (Wave 4)
                             │
                             ▼
                          Step 8 (Wave 5)
```

---

## Known Risks

| Risk                                                               | Mitigation                                                                        |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `ClaudeSession` MCP URL parameter name unknown for orch session    | Read `claude_session.py` before Step 3                                            |
| `chat/handler.py` may reference `estimated_tools` or `ReviewEvent` | Verify before Steps 1 and 5                                                       |
| `_make_decomposer` in tests expects exactly 3 ClaudeSession mocks  | Step 3 must not add a 4th session                                                 |
| `_orch_session` with max_turns=5 + tools grows history faster      | Lower `_ORCH_RESET_THRESHOLD` or document as tuning item                          |
| `route_task()` exhausts 5 turns on research without producing JSON | Existing fallback: `extract_json_object()` → `scope="small"` with original prompt |

---

## Constraints

- TDD: write failing tests first, then implement, then all green
- All existing tests must pass throughout
- Coverage must stay ≥ 85%
- No backward-compatibility shims
- KISS — no premature abstractions

