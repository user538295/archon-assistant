# Multi-Agent Pipeline — Phase 2 Implementation Plan

> **Source docs:** `03_multi-agent-architecture.md` (full spec), `03_multi-agent-discussion-notes.md` (agreed decisions), `03_multi-agent-implementation-plan.md` (Phase 1 — complete)
> **Scope:** Phase 2 — Decomposer scope decision, agent plan generation, dependency-aware execution, result collection
> **Prerequisite:** Phase 1 complete (Pipeline, Classifier, Decomposer, ClassificationEvent all working)

---

## Architecture Summary

Phase 1 established: Classifier (Haiku) → Pipeline → Decomposer (user model). The Decomposer currently handles all tasks directly ("Phase 1 scope" restriction in `decomposer.md`).

Phase 2 lifts that restriction. The Decomposer now decides scope (`small` vs `large`). For large tasks, it outputs a structured agent plan. A new `PlanExecutor` component (Python, not LLM) manages the dependency graph and spawns workers via the existing `BackgroundAgentManager`.

```
User message (Telegram)
  → Pipeline
    → Classifier (Haiku) → {intent, confidence}
    → Decomposer (user model) receives: prompt + classification
      ├─ chat → conversational response (unchanged)
      ├─ task, small → handles directly (unchanged)
      └─ task, large → outputs agent plan JSON
          → Pipeline detects plan → yields PlanEvent
          → Handler starts PlanExecutor (async task)
            → PlanExecutor resolves dependency graph
            → Spawns workers via BackgroundAgentManager
            → Waits for each wave to complete
            → Passes upstream log file paths to dependent workers
            → Sends aggregated results to user via Telegram
```

### Key principle: sub-agents never block

`Pipeline.send()` yields the `PlanEvent` and **returns immediately**. Plan execution runs as a detached `asyncio.Task`. The main session stays free for new user messages while agents work.

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| PlanExecutor is Python code, not an LLM | Gateway is the control plane (architecture spec principle). Deterministic dependency resolution, concurrency control, error handling. |
| Pipeline detects plan in Decomposer's Response | Decomposer can use tools (read files, search code) before deciding scope. Plan is the final Response. No mid-stream detection needed. |
| PlanExecutor spawns via BackgroundAgentManager | Reuses existing agent lifecycle: spawn notifications, log files, beacon, cancellation. No new spawning infrastructure. |
| Handler starts PlanExecutor, not Pipeline | Handler has access to all dependencies (BAM, bot, agent_logger). Pipeline stays focused on classification + routing. |
| `asyncio.Event` on AgentRun for completion signaling | Cleanest way for PlanExecutor to wait for agents. No polling, no callbacks. |
| PlanExecutor respects BAM's `max_parallel` limit | If a wave has more agents than remaining slots, PlanExecutor spawns up to the limit and waits for slots to free before spawning more. This prevents plan execution from exhausting the per-user agent pool. |
| Upstream log file paths passed as prompt context | Workers are self-contained. Same-run data via file paths (architecture spec). QMD for historical data. |
| Phase 2 delivers concatenated results, not synthesized | Synthesizer is Phase 3. Phase 2 collects results and sends them directly. |

---

## Agent Plan JSON Schema

The Decomposer outputs this as its **entire** final Response when scope is `large`:

```json
{
  "scope": "large",
  "summary": "I'll break this into 3 tasks: research, implementation, and testing.",
  "agents": [
    {
      "id": "a1",
      "task": "Research best practices for retry logic in async Python."
    },
    {
      "id": "a2",
      "task": "Implement retry logic in archon/ai/claude_session.py based on a1's findings.",
      "depends_on": ["a1"]
    },
    {
      "id": "a3",
      "task": "Write unit tests for the retry logic implemented by a2.",
      "depends_on": ["a2"]
    }
  ]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `scope` | `"large"` | yes | Must be `"large"` to trigger plan execution |
| `summary` | string | yes | Human-readable explanation shown to the user |
| `agents` | array | yes | 1+ agent definitions |
| `agents[].id` | string | yes | Unique ID within the plan (e.g., `"a1"`, `"a2"`) |
| `agents[].task` | string | yes | Self-contained task prompt for the worker (architecture spec uses `prompt` — we use `task` to match BAM's `spawn(task=...)` parameter) |
| `agents[].depends_on` | string[] | no | IDs of agents that must complete first (default: `[]`) |

### Detection heuristic

`parse_agent_plan(raw)` returns an `AgentPlan` if **all** conditions are met:
1. `raw` parses as valid JSON
2. Root is a dict with `"scope": "large"`
3. Contains `"agents"` array with at least 1 entry
4. Each agent has `"id"` and `"task"` strings

Returns `None` otherwise — the Response is delivered to the user as normal text.

---

## Tasks

### Layer 1 — Data layer (no behavior change)

- [x] **#1 — Agent plan schema + parser**
  - `archon/ai/agent_plan.py`:
    - `AgentTask` frozen dataclass: `id: str`, `task: str`, `depends_on: list[str]`
    - `AgentPlan` frozen dataclass: `scope: str`, `summary: str`, `agents: list[AgentTask]`
    - `parse_agent_plan(raw: str) -> AgentPlan | None` — returns `None` if not a valid plan (not JSON, missing required fields, invalid structure). Logs warning on parse failure.
    - `validate_dependency_graph(plan: AgentPlan) -> bool` — checks: no unknown depends_on IDs, no cycles (topological sort). Returns `False` + logs warning on invalid graph.
    - `topological_sort(plan: AgentPlan) -> list[list[AgentTask]]` — returns execution waves: agents in the same wave can run in parallel; each wave depends on the previous. Raises `ValueError` on cyclic graph.
  - `tests/ai/test_agent_plan.py`:
    - Parse happy path: valid JSON with all fields
    - Parse with no depends_on (all parallel)
    - Parse with linear chain (a1 → a2 → a3)
    - Parse with diamond dependency (a1,a2 → a3)
    - Parse returns None: not JSON, missing scope, missing agents, empty agents, missing agent.id, missing agent.task
    - Validate: unknown depends_on ID → False
    - Validate: cycle detection → False
    - Topological sort: parallel agents → single wave
    - Topological sort: chain → N waves of 1
    - Topological sort: mixed parallel/sequential
  - TDD: tests first → implement → green
  - Tests: unit
  - Checkpoint: `uv run pytest tests/ai/test_agent_plan.py && uv run pytest`

### Layer 2 — Pipeline detects plans (classification visible, no execution yet)

- [x] **#2 — PlanEvent + Pipeline plan detection** *(blocked by #1)*
  - `archon/ai/event_mapper.py`:
    - `PlanEvent` dataclass: `plan: AgentPlan`, `summary: str`, `source: str = "pipeline"`
    - Add to `Event` union type (now 9 dataclasses)
  - `archon/ai/pipeline.py`:
    - Intermediate events (ThinkingResult, ToolStarted, ToolResult) are yielded immediately (no buffering — streaming preserved for all messages)
    - Only the final `Response` is intercepted: check `parse_agent_plan(response.content)`
    - If plan detected: yield `PlanEvent(plan=plan, summary=plan.summary)` instead of Response
    - If not a plan: yield Response as before (unchanged)
    - **Implementation**: wrap the Decomposer's event stream — yield non-Response events immediately, hold Response for plan detection
  - `archon/chat/handler.py`:
    - `format_event()` handles `PlanEvent`: `"📋 Plan: {summary}\n🔄 Spawning {N} agents..."`
    - Always visible (like Response — never suppressed by notification mode)
  - `tests/ai/test_pipeline.py`:
    - Decomposer returns normal text → yields Response (unchanged)
    - Decomposer returns valid plan JSON → yields PlanEvent
    - Decomposer returns invalid JSON → yields Response (graceful fallback)
  - `tests/chat/test_handler.py`:
    - PlanEvent formatting test
    - PlanEvent visibility: always shown regardless of notification mode
  - TDD: tests first → implement → green
  - Tests: unit + integration
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/chat/test_handler.py && uv run pytest`
  - **User sees:** `📋 Plan: ...` message when Decomposer outputs a plan. No execution yet.

- [x] **#3 — Update Decomposer prompt for scope decision** *(blocked by #1 — needs plan schema for JSON example)*
  - `archon/ai/prompts/decomposer.md`:
    - Remove "Phase 1 scope" section
    - Add scope heuristics (from architecture spec):
      - `small`: single file, single API call, answer from existing context, no step dependencies
      - `large`: multiple steps, file creation + validation, output of one step feeds another, external investigation
    - Add plan output format: when scope is `large`, output ONLY the plan JSON as your entire response
    - Add instruction: when scope is `small`, handle directly (current behavior)
    - Add instruction: include self-contained prompts for each agent (the worker only sees its task field)
    - Add instruction: reference upstream agents by ID in depends_on agents' task descriptions (e.g., "Based on a1's findings...")
  - `tests/ai/test_prompts.py`:
    - Decomposer prompt contains scope heuristics
    - Decomposer prompt contains plan JSON schema
    - Decomposer prompt does NOT contain "Phase 1 scope" restriction
  - TDD: tests first → implement → green
  - Tests: unit
  - Checkpoint: `uv run pytest tests/ai/test_prompts.py && uv run pytest`

### Layer 3 — Execution infrastructure

- [x] **#4 — AgentRun completion signaling** *(independent, can start in parallel with #2)*
  - `archon/ai/background_agent_manager.py`:
    - Add `_done: asyncio.Event` field to `AgentRun` (field init via `default_factory=asyncio.Event`)
    - Set `_done` in `_run_agent()` finally block (after status update, before name release)
    - Add `log_path: Path | None = None` field to `AgentRun` — populated when AgentLogger creates the file
  - `archon/ai/agent_logger.py`:
    - `record_event()` returns the `Path` when handling `SubagentStarted` (file creation)
    - OR: add `get_log_path(agent_id: str) -> Path | None` method
  - `archon/ai/background_agent_manager.py`:
    - After `SubagentStarted` logged, capture log path into `run.log_path`
  - `tests/ai/test_background_agent_manager.py`:
    - `_done` event is set after agent completes
    - `_done` event is set after agent fails
    - `_done` event is set after agent is cancelled
    - `log_path` is populated after agent starts
  - TDD: tests first → implement → green
  - Tests: unit
  - Checkpoint: `uv run pytest tests/ai/test_background_agent_manager.py && uv run pytest`

### Layer 4 — Plan execution

- [x] **#5 — PlanExecutor: dependency resolution and agent spawning** *(blocked by #2, #4)*
  - `archon/ai/plan_executor.py`:
    - `PlanExecutor` class:
      - `__init__(bam: BackgroundAgentManager, bot: Bot, user_id: int, cwd: str)`
      - `async execute(plan: AgentPlan) -> None` — main entry point, runs as async task
    - Execution flow:
      1. Send plan summary notification: `"📋 Executing plan: {summary} ({N} agents)"`
      2. Compute waves via `topological_sort(plan)`
      3. For each wave:
         a. Spawn all agents in the wave via `bam.spawn()`, passing upstream log file paths in context
         b. `await asyncio.gather(*[run._done.wait() for run in wave_runs])`
         c. Check results: if any agent failed, mark dependents as skipped
      4. Collect results from all completed agents
      5. Send final summary notification: `"✅ Plan completed: {N}/{total} agents succeeded"`
         - Include each agent's result (truncated) in the notification
    - Context injection for dependent agents:
      - For each agent with `depends_on`, prepend to its task prompt:
        ```
        [Upstream agent outputs]
        Agent a1 output: /path/to/log.md
        Agent a2 output: /path/to/log.md
        [End upstream outputs]

        {original task prompt}
        ```
  - `tests/ai/test_plan_executor.py`:
    - All-parallel plan: all agents spawned simultaneously
    - Linear chain: a1 → a2 → a3, spawned sequentially
    - Diamond: a1,a2 → a3 (a1 and a2 parallel, a3 waits)
    - Upstream log paths passed to dependent agents
    - Agent failure: dependents are skipped
    - Results collected correctly
    - Telegram notifications sent (plan start, plan complete)
  - TDD: tests first → implement → green
  - Tests: unit + integration
  - Checkpoint: `uv run pytest tests/ai/test_plan_executor.py && uv run pytest`

- [x] **#6 — Handler wiring: PlanEvent triggers PlanExecutor** *(blocked by #5)*
  - `archon/chat/handler.py`:
    - In `handle_message()` event loop, detect `PlanEvent`:
      ```python
      if isinstance(event, PlanEvent):
          executor = PlanExecutor(bam=..., bot=..., user_id=user_id, cwd=cwd)
          asyncio.create_task(executor.execute(event.plan))
      ```
    - PlanEvent is formatted and sent to Telegram (shows summary)
    - PlanExecutor runs detached — `send()` generator returns normally
  - `archon/chat/handler.py`:
    - `handle_message` needs access to `BackgroundAgentManager` (add to dispatcher wiring if not already there)
  - `tests/chat/test_handler.py`:
    - PlanEvent triggers PlanExecutor creation
    - PlanExecutor runs as async task (handle_message returns without waiting)
    - Normal Response events still work (no regression)
  - TDD: tests first → implement → green
  - Tests: unit + integration
  - Checkpoint: `uv run pytest tests/chat/test_handler.py && uv run pytest`

### Layer 5 — Error handling + hardening

- [x] **#7 — Plan execution error handling** *(blocked by #6)*
  - `archon/ai/plan_executor.py`:
    - Agent failure (status="failed"): skip all agents that depend on it (transitively)
    - Send failure notification: `"⚠️ Agent {name} failed: {error}. Skipping {N} dependent agents."`
    - Collect partial results from completed agents
    - Final notification includes which agents succeeded, which failed, which were skipped
    - PlanExecutor itself is wrapped in try/except — a crash in the executor sends an error notification
  - `archon/ai/plan_executor.py`:
    - Invalid dependency graph (cycles, unknown IDs): detect before spawning, send error notification, abort plan
  - `tests/ai/test_plan_executor.py`:
    - Agent a1 fails → a2 (depends on a1) is skipped → a3 (depends on a2) is skipped
    - Agent a1 fails → a2 (independent) still runs
    - Partial results delivered to user
    - Cyclic graph → plan aborted with error message
    - PlanExecutor crash → error notification to user
  - TDD: tests first → implement → green
  - Tests: unit + integration
  - Checkpoint: `uv run pytest tests/ai/test_plan_executor.py && uv run pytest`

### Layer 6 — End-to-end verification

- [x] **#8 — E2E smoke test: full plan execution flow** *(blocked by #7)*
  - `tests/ai/test_plan_executor_e2e.py`: patches at SDK level only
    - Happy path: user message → classify → decompose → plan → 2 agents run → results delivered
    - Dependency chain: a1 → a2, a1 completes first, a2 gets log path
    - Mixed: agent fails, partial results delivered
    - Small scope: Decomposer handles directly (no plan, existing behavior)
    - Two plans: user sends second message while first plan is running
  - TDD: tests first → green
  - Tests: E2E
  - Checkpoint: `uv run pytest tests/ai/test_plan_executor_e2e.py && uv run pytest`
  - **Phase 2 complete.** Full test pyramid: unit + integration + E2E.

---

## Test Pyramid Progression

| Task | Unit | Integration | E2E | User value |
|------|------|-------------|-----|------------|
| #1   | ✅   |             |     | internal (plan schema) |
| #2   | ✅   | ✅           |     | 📋 plan visible in Telegram |
| #3   | ✅   |             |     | 🎯 scope decision enabled |
| #4   | ✅   |             |     | internal (completion signaling) |
| #5   | ✅   | ✅           |     | 🚀 agents actually execute |
| #6   | ✅   | ✅           |     | 🔌 handler wiring complete |
| #7   | ✅   | ✅           |     | 🛡 partial results on failure |
| #8   |      |             | ✅   | ✅ production-ready |

---

## Files Created / Modified

| File | Task | Action |
|------|------|--------|
| `archon/ai/agent_plan.py` | #1 | new |
| `tests/ai/test_agent_plan.py` | #1 | new |
| `archon/ai/event_mapper.py` | #2 | modify (PlanEvent + Event union) |
| `archon/ai/pipeline.py` | #2 | modify (plan detection in send) |
| `archon/chat/handler.py` | #2, #6 | modify (PlanEvent formatting + PlanExecutor wiring) |
| `tests/ai/test_pipeline.py` | #2 | extend (plan detection tests) |
| `tests/chat/test_handler.py` | #2, #6 | extend (PlanEvent tests) |
| `archon/ai/prompts/decomposer.md` | #3 | modify (scope heuristics, plan format) |
| `tests/ai/test_prompts.py` | #3 | extend |
| `archon/ai/background_agent_manager.py` | #4 | modify (_done event, log_path) |
| `archon/ai/agent_logger.py` | #4 | modify (expose log path) |
| `tests/ai/test_background_agent_manager.py` | #4 | extend |
| `archon/ai/plan_executor.py` | #5, #7 | new |
| `tests/ai/test_plan_executor.py` | #5, #7 | new |
| `tests/ai/test_plan_executor_e2e.py` | #8 | new |

---

## What Phase 2 does NOT include (deferred to Phase 3)

- **Synthesizer agent** — Phase 2 concatenates results. Phase 3 adds a dedicated Synthesizer agent with a Decomposer-generated synthesis prompt.
- **Hierarchical synthesis** — Phase 3 handles context window overflow via hierarchical summarization or Haiku compression.
- **Decomposer re-planning on failure** — Phase 2 skips dependents and reports partial results. Phase 3 sends failure context back to the Decomposer, which decides to inject a fix agent or abort.
- **Low-confidence QMD query** — The architecture spec says the Classifier queries QMD when confidence < 0.80. This is independent of Phase 2 and can be added as a separate enhancement.
- **Agent model selection** — The architecture spec mentions Workers can use Haiku or Sonnet. Phase 2 uses the BAM default model. Phase 3 can add per-agent model selection in the plan schema.

---

## Dependency Graph

```
#1 (plan schema) ─┬─→ #2 (PlanEvent + detection) ──→ #5 (PlanExecutor) ──→ #6 (handler wiring) ──→ #7 (error handling) ──→ #8 (E2E)
                   │                                       ↑
                   ├─→ #3 (decomposer prompt)              │
                   │                                       │
                   └── #4 (completion signaling) ──────────┘
```

**Parallelism opportunities:**
- After #1: tasks #2, #3, and #4 can all be implemented in parallel
- #5 requires both #2 and #4 to be complete
- #3 is independent of #2 — prompt changes don't need PlanEvent to exist
