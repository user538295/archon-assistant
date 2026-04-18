# FIX-032 — Router scope bias: always classifies tasks as "small"

**Purpose**: Fix the Decomposer router's systematic bias toward `scope="small"` for tasks that should be classified as `scope="large"` and routed to background agents.
**Audience**: Archon developers; end users benefit indirectly through correct multi-agent routing.
**Status**: To Do

---

## Background

The router session inside `Decomposer` classifies every incoming task as `trivial`, `small`, or `large`. Three live tests (BUG-3) are currently marked `@pytest.mark.xfail` because the router consistently returns `scope="small"` even for clearly large tasks — confirmed by the 2026-03-01 history log where a 4-module refactoring plan was routed as a direct response.

Root causes (from `bug_investigation_07_scope_always_small.md`):
1. **Vague criteria** — the three one-line definitions in `route_task.md` (lines 35–37) leave no objective threshold; the LLM invents its own and picks the safest option.
2. **No examples** — nothing calibrates the model's intuition about where the small/large boundary lies.
3. **No thinking** — `disable_thinking=True` (decomposer.py:252) prevents chain-of-thought reasoning over the rubric.
4. **Silent fallback** — every parse error in `_parse_task_output()` falls back to `scope="small"`, creating no incentive to attempt a large scope plan.

Fix: replace vague criteria with an explicit objective rubric, add a thinking preamble, and enable extended thinking for the router session.

---

## Goal

After this fix, the three xfail tests at lines 110, 163, and 250 of `test_decomposer_scope_live.py` pass without their `xfail` marks. Tasks with ≥2 investigation targets or ≥2 files to modify route to background agents. Single-action, single-file tasks continue to route inline. No existing passing tests regress.

---

## Scope

### In Scope
- Replace `route_task.md` decision criteria (lines 35–37) with an explicit, measurable rubric.
- Add a thinking preamble to `route_task.md` instructing the model to reason before outputting JSON.
- Change `disable_thinking=True` → `False` in `Decomposer._ensure_router_session()` (decomposer.py:252).
- Update unit test `test_router_session_constructed_with_thinking_disabled` to assert `False`.
- Remove `@pytest.mark.xfail` from the three BUG-3 live tests once the fix passes them.

### Out of Scope
- Adding real-world examples from history logs to `route_task.md` (Week 2 follow-up per investigation).
- Analysing or modifying `orchestrator.md` (long-term recommendation).
- Removing the `scope` concept entirely (Option 4 / long-term backlog).
- Changes to `_parse_task_output()` fallback behaviour (the silent fallback to `small` is a safety net, not this fix's target).

---

## Acceptance criteria

- [ ] `uv run pytest tests/ai/test_decomposer.py -v` passes with no failures.
- [ ] `uv run pytest -m live tests/ai/test_decomposer_scope_live.py -v` passes all five tests including the three formerly-xfail ones.
- [ ] `test_decomposer_emits_plan_for_multimodule_refactoring_request` (line 110) — passes, no `xfail`.
- [ ] `test_decomposer_emits_plan_for_multi_target_investigation` (line 163) — passes, no `xfail`.
- [ ] `test_routing_event_reports_agent_plan_when_plan_emitted` (line 250) — passes, no `xfail`.
- [ ] Baseline tests `test_decomposer_direct_response_for_single_question` and `test_decomposer_direct_response_for_single_file_fix` still pass (no regression on small-scope tasks).
- [ ] `uv run pytest tests/ -q --no-cov` passes (full suite, no regressions).

---

## What does NOT change

- `orchestrator.md` — the router's system prompt is not modified.
- `_parse_task_output()` fallback logic — all error paths still return `scope="small"`.
- `TaskOutput` dataclass, `AgentPlan`, or any event type.
- `pipeline.py` routing logic — scope values and their dispatch remain unchanged.
- All other `ClaudeSession` parameters for the router (`model`, `max_turns=5`, `tools=[]`, etc.).
- The `_ROUTER_TIMEOUT_S = 180.0` constant — already sized for extended thinking.

---

## Known limitations / accepted trade-offs

- **Latency increase**: enabling extended thinking on Sonnet adds 60–90s per routing decision (per `_ROUTER_TIMEOUT_S` annotation). The 180s timeout accommodates this. Acceptable given routing happens once per user message.
- **Rough proxy thresholds**: the ≥2-file threshold in the rubric will over-trigger for trivial 2-file renames. Monitoring after rollout will determine if calibration is needed.
- **thinking + max_turns interaction**: not yet proven that thinking blocks don't consume turns from `max_turns=5`. If the router exhausts turns during history research, JSON output may be cut short. This is a known risk; `_parse_task_output()` fallback catches it safely. Measure via live test latency and `_ROUTER_RESET_THRESHOLD` count.
- **Rubric only, no examples**: real-world examples are deferred to Week 2 (follow-up per investigation).

---

## Architecture

### Modified files
- `archon/ai/prompts/route_task.md` — decision criteria section replaced; thinking preamble added.
- `archon/ai/decomposer.py` — single parameter change: `disable_thinking=True` → `False`.
- `tests/ai/test_decomposer.py` — one assertion changed: `is True` → `is False`.
- `tests/ai/test_decomposer_scope_live.py` — three `@pytest.mark.xfail` decorators removed.

### No new modules, classes, config keys, or API surfaces introduced.

### Data flow (unchanged)
`route_task()` → `_ensure_router_session()` → `ClaudeSession(disable_thinking=False)` → SDK sends `route_task.md` rubric as user-turn instruction → model reasons (thinking block, invisible to user) → outputs JSON → `_parse_task_output()` → `TaskOutput(scope=...)` → `pipeline.py` dispatches.

---

## Tests

- **`test_router_session_constructed_with_thinking_enabled`** (unit): assert `disable_thinking` kwarg is `False` when `_ensure_router_session()` constructs the router `ClaudeSession`.
- **`test_decomposer_emits_plan_for_multimodule_refactoring_request`** (live e2e): 4-module refactoring prompt → `PlanEvent` with ≥2 agents, no direct `Response`.
- **`test_decomposer_emits_plan_for_multi_target_investigation`** (live e2e): 3-source investigation + bug report → `PlanEvent` with ≥2 agents.
- **`test_routing_event_reports_agent_plan_when_plan_emitted`** (live e2e): full Pipeline → `RoutingEvent.routing == "agent_plan"`.
- **`test_decomposer_direct_response_for_single_question`** (live e2e, existing baseline): single question → no `PlanEvent`, one `Response`.
- **`test_decomposer_direct_response_for_single_file_fix`** (live e2e, existing baseline): single-file rename → no `PlanEvent`.

---

## Documentation update

- [ ] `Documentation/Backlog/bug_investigation_07_scope_always_small.md` — update status note to "fixed in FIX-032" once all xfail tests pass. (Optional — investigation doc, not architecture doc.)

---

## Task breakdown

### Phase 1 — Prompt calibration
> **Releasable**: after Task 1.1 completes; the rubric improvement is live even before thinking is enabled. Baseline unit tests are unaffected.

#### Task 1.1 — Replace vague decision criteria + add thinking preamble in route_task.md
- [x] **File**: `archon/ai/prompts/route_task.md`
- **Depends on**: nothing
- **Description**:
  Replace lines 34–37 (the `Decision criteria:` block) with the following explicit rubric:

  ```markdown
  Before outputting your final JSON, reason through the scope decision:
  - How many distinct investigation targets exist? (files to read, external sources, code paths)
  - How many files need to be created or modified?
  - Is there an output artifact required? (a report, a plan document, a new file)
  - Are there independent sub-tasks that can run in parallel?

  Decision criteria:

  **TRIVIAL** scope (instant answer from context, no tools):
    Examples: "what did we just do?", "summarise the plan", "thanks", "good job"
    → **ALWAYS choose trivial over small for conversational messages**

  **SMALL** scope (inline execution, single focused action):
    Choose SMALL ONLY if ALL of these are true:
      ✓ ≤ 1 file modified
      ✓ ≤ 1 investigation target (one file read, one API call, one concept verified)
      ✓ No output artifact required (answer is inline; no "save to file" or "write a report")
      ✓ Task is a single, self-contained action
    Examples: single file rename, quick lookup, answering a question about one module

  **LARGE** scope (background agents, parallel work):
    Choose LARGE if ANY of these are true:
      ✗ ≥ 2 files need modification (even if trivial per file)
      ✗ ≥ 2 independent investigation targets (e.g. multiple files to read, multiple sources)
      ✗ Requires an output artifact AND investigation (e.g. "write a report", "save a plan")
      ✗ Investigation must precede implementation (research before coding)
      ✗ Independent sub-tasks exist that can run in parallel
    Examples: refactoring across 2+ modules, fact-checking with a written report, multi-source investigation
  ```

  The existing lines before the decision criteria (JSON format examples, history tool instructions, JSON enforcement line) and after (rules for agent plans) are unchanged.

  **Key behaviours**:
  - The thinking preamble ("Before outputting your final JSON, reason through...") is deferred to Task 2.1 when `disable_thinking=False` is enabled — adding it here with thinking disabled would conflict with the "Output ONLY valid JSON" instruction and cause parse warnings on every request.
  - "ALWAYS choose trivial over small for conversational messages" is reinforced with a bold directive.
  - Each scope level has both an objective ALL/ANY checklist and examples.
  - SMALL criterion clarified: "≤ 1 independent investigation topic" (reading 2 related files for one question remains SMALL).
  - LARGE criterion clarified: "≥ 2 independent investigation topics that benefit from parallel research".
  - SMALL examples include "creating a blank file" to cover artifact-only tasks with no investigation.
  - The ≥2-file threshold is a deliberate rough proxy; known limitation accepted.

- **Releasable**: after this task, the router uses an objective rubric. Scope accuracy improves even without thinking enabled. No code changes — no test impact.
- **Tests (TDD)** — `tests/ai/test_decomposer_scope_live.py`:
  - Live E2E: `test_decomposer_direct_response_for_single_question` — verify baseline still passes (single question → no PlanEvent).
  - Live E2E: `test_decomposer_direct_response_for_single_file_fix` — verify baseline still passes (single-file rename → no PlanEvent).
  - *The BUG-3 xfail tests remain marked xfail at this phase — they are unblocked in Phase 2 after thinking is enabled.*
  - Checkpoint: `uv run pytest -m live tests/ai/test_decomposer_scope_live.py -k "single" -v`

---

### Phase 2 — Enable extended thinking for router session
> **Releasable**: after Task 2.1 completes; the router can now reason step-by-step before producing JSON. Combined with the Phase 1 rubric, this is the complete fix. The three BUG-3 live tests can be unblocked in Phase 3.

#### Task 2.1 — Change disable_thinking=True → False in Decomposer + update unit test
- [x] **File**: `archon/ai/decomposer.py`
- [x] **File**: `tests/ai/test_decomposer.py`
- **Depends on**: Task 1.1
- **Description**:
  **`archon/ai/decomposer.py`** — in `_ensure_router_session()` at line 252, change:
  ```python
  # Before:
  disable_thinking=True,

  # After:
  disable_thinking=False,
  ```
  No other parameter changes. The `_ROUTER_TIMEOUT_S = 180.0` constant already accommodates 60–90s thinking time.

  **`tests/ai/test_decomposer.py`** — rename and update `test_router_session_constructed_with_thinking_disabled` (line 154):
  - Rename to `test_router_session_constructed_with_thinking_enabled`
  - Update docstring: `"""Router session must be constructed with disable_thinking=False (extended thinking enabled)."""`
  - Change assertion at line 176 from:
    ```python
    assert router_kwargs.get("disable_thinking") is True
    ```
    to:
    ```python
    assert router_kwargs.get("disable_thinking") is False
    ```
  - All other test logic (patch targets, call count check, `call_kwargs[1]` router identification) remains unchanged.

- **Releasable**: after this task, the router reasons through the rubric before outputting JSON. The complete fix (rubric + thinking) is in place.
- **Tests (TDD)** — `tests/ai/test_decomposer.py`:
  - Unit: `test_router_session_constructed_with_thinking_enabled` — `ClaudeSession` is constructed with `disable_thinking=False` for the router session (second call, index `[1]`).
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py::test_router_session_constructed_with_thinking_enabled -v`

---

### Phase 3 — Remove xfail markers and verify
> **Releasable**: after Task 3.1 completes; all live tests pass clean, confirming the fix is effective end-to-end.

#### Task 3.1 — Remove @pytest.mark.xfail from BUG-3 live tests
- [ ] **File**: `tests/ai/test_decomposer_scope_live.py`
- **Depends on**: Task 2.1
- **Description**:
  Run the three BUG-3 live tests first to confirm they now pass before removing the marks:
  ```
  uv run pytest -m live tests/ai/test_decomposer_scope_live.py \
    -k "multimodule or multi_target or routing_event" -v
  ```
  If all three pass, remove the `@pytest.mark.xfail(...)` decorator lines:
  - Line 110: above `test_decomposer_emits_plan_for_multimodule_refactoring_request`
  - Line 163: above `test_decomposer_emits_plan_for_multi_target_investigation`
  - Line 250: above `test_routing_event_reports_agent_plan_when_plan_emitted`

  Update the module docstring (lines 7–8) to remove the "CURRENT STATUS: BUG-3 tests are EXPECTED TO FAIL" notice. Replace with: `"BUG-3 FIXED (FIX-032): scope rubric and extended thinking enabled in router session."`.

  Do NOT remove any other test, helper, or comment. The baseline tests (`single_question`, `single_file_fix`) are already passing and remain unchanged.

  If any BUG-3 test still fails after the Phase 1–2 fix, do NOT remove its xfail mark. Investigate the failure and return to Phase 2 for adjustment.

- **Releasable**: all five live tests pass without xfail marks. Full suite clean.
- **Tests (TDD)** — `tests/ai/test_decomposer_scope_live.py`:
  - Live E2E: `test_decomposer_emits_plan_for_multimodule_refactoring_request` — `PlanEvent` emitted, `plan.agents ≥ 2`, no direct `Response`.
  - Live E2E: `test_decomposer_emits_plan_for_multi_target_investigation` — `PlanEvent` emitted, `plan.agents ≥ 2`.
  - Live E2E: `test_routing_event_reports_agent_plan_when_plan_emitted` — `RoutingEvent.routing == "agent_plan"`.
  - Live E2E: `test_decomposer_direct_response_for_single_question` — baseline still passes.
  - Live E2E: `test_decomposer_direct_response_for_single_file_fix` — baseline still passes.
  - Checkpoint: `uv run pytest -m live tests/ai/test_decomposer_scope_live.py -v`
  - Full suite: `uv run pytest tests/ -q --no-cov`
