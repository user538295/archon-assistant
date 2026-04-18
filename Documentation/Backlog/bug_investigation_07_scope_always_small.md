# Bug Investigation 7: Router/Decomposer Biased Toward "scope: small"

## Summary

The router (in `decomposer.py`) almost always classifies tasks as `"scope": "small"` even when they are clearly large/complex investigations or multi-step operations. This results in wrong routing decisions that underutilize the multi-agent system.

### Confirmed Examples
1. **Semantic Collapse Stanford RAG fact-check** — classified as `"scope": "small"` when it requires:
   - Finding and reading the Stanford paper
   - Verifying claims about RAG
   - Checking statistics and technical accuracy
   - Writing a report
   - **Expected**: scope="large" with multiple investigation agents

2. **Save lesson about source files** — classified as `"scope": "small"` despite involving investigation and synthesis

### Test Evidence
File: `tests/ai/test_decomposer_scope_live.py`

The live test suite contains **three expected-to-fail tests** (`@pytest.mark.xfail`) that confirm this bug:
- `test_decomposer_emits_plan_for_multimodule_refactoring_request()` (line 110)
- `test_decomposer_emits_plan_for_multi_target_investigation()` (line 163)
- `test_routing_event_reports_agent_plan_when_plan_emitted()` (line 250) — depends on the scope fix

All three document that complex tasks should route to `scope="large"` but currently route to `scope="small"`.

---

## Root Cause Analysis

### 1. Prompt Definitions Are Vague and Conflict

**File**: `archon/ai/prompts/route_task.md` (lines 35–37)

#### Current Definition of "small" (LINE 36):
```
- **small**: single file change, single API call, answer from context, one action suffices
```

**Problem**: This definition is too narrow but the LLM interprets it loosely:
- Fact-checking a paper requires "single API call" (query → read) in the LLM's interpretation
- Multiple investigation steps are collapsed into "one action" (the plan itself)
- The definition gives examples of trivial tasks but doesn't calibrate against actual complex work

#### Current Definition of "large" (LINE 37):
```
- **large**: multiple steps where output feeds the next, multi-file creation/validation, 
  external investigation before implementation, multiple independent sub-tasks benefiting 
  from parallel execution
```

**Problem**: This definition is **internally ambiguous**:
- "multiple steps where output feeds the next" could mean 2 steps or 20 steps
- "external investigation before implementation" is vague — what counts as "external"?
- "multiple independent sub-tasks benefiting from parallel execution" requires the LLM to estimate parallelization benefit, which it often underestimates
- No minimum threshold given (e.g., "≥3 distinct components" or "≥2 hours of work")

### 2. No Concrete Examples

The prompt provides **zero concrete examples** of:
- A task that IS scope="small" (beyond trivial operations)
- A task that IS scope="large" (with reasoning)
- Edge cases (3-file changes? 5-file changes? Single file with 3 investigation steps?)

This forces the LLM to invent its own thresholds, and it defaults conservatively to "small" because:
- Smaller scope = fewer agents spawned = less cost
- When uncertain, default to inline execution (safe path)

### 3. Router System Prompt (orchestrator.md) Not Analyzed

`orchestrator.md` is the actual system prompt for the router session. `route_task.md` is injected as user-turn instruction text, not as the system prompt. The orchestrator prompt's framing and 4-turn research budget interact with the scope decision: if the model spends turns on history research, it may anchor on a simple scope in order to complete within the turn budget. The orchestrator prompt should be reviewed alongside `route_task.md` when calibrating scope decisions.

### 4. JSON-Only Output and No Thinking

**File**: `archon/ai/decomposer.py` (line 252)

```python
session = ClaudeSession(
    cwd=self._cwd,
    model=self._model,
    background_agent_mcp_url=self._router_mcp_url,
    mcp_headers=self._router_mcp_headers,
    system_prompt=router_prompt,
    tools=[],
    max_turns=5,
    disable_thinking=True,  # <-- LINE 252: Extended thinking DISABLED
)
```

**Problem**: The router is configured with `disable_thinking=True`. This means:
- The LLM cannot reason through the scope decision step-by-step
- No visible reasoning chain to debug why a task was classified
- The LLM jumps directly to JSON output without introspection
- When uncertain (no examples), it picks the safest option: "small"

### 5. Fallback to "small" Is Silent Default

**File**: `archon/ai/decomposer.py` (lines 438–519)

The `_parse_task_output()` method uses "small" as the fallback for ALL errors:
- JSON parse failure → scope="small" (line 454)
- Malformed JSON → scope="small" (line 462)
- Invalid dict → scope="small" (line 470)
- Unknown scope value → scope="small" (line 516)
- Large scope with invalid agents → scope="small" (line 500)

This creates an incentive for the LLM: if unsure, return ANY valid JSON with scope="small" rather than attempt a large scope plan that might fail parsing.

---

## Why This Happens

1. **Vague prompt** → LLM must guess thresholds
2. **No examples** → No calibration
3. **Orchestrator framing + turn budget** → Research turns consumed before scope decision anchors
4. **No thinking** → No reasoning trail
5. **Silent fallback to small** → Safer than failing a large scope plan
6. **Cost sensitivity** → Smaller scopes spawn fewer agents, cost less

The LLM's decision is **locally rational** (safer to say "small" than risk a failed "large" plan), but **globally wrong** (tasks don't get the parallelism they need).

---

## Impact

### Where Scope Is Used

**File**: `archon/ai/decomposer.py`
- **Line 95**: `TaskOutput` dataclass stores scope
- **Line 432**: If scope="large", task is tracked for context summarization

**File**: `archon/ai/pipeline.py`
- **Lines 294–324**: Routing decision:
  - `scope="large"` → Spawn background agents (`_yield_plan()`)
  - `scope="trivial"/"small"` → Inline execution with tool promotion safety net

### Consequences of Bias

1. **Underutilization**: Multi-agent parallelism is disabled for complex tasks
2. **Timeouts**: Inline execution hits wall-clock limits (300s in `pipeline.py` line 58)
3. **Tool Promotion**: Tasks that should spawn agents instead get promoted late (reactive, not proactive)
4. **Cost**: Tool promotion with timeout recovery wastes compute

### Real-World Impact from Test Suite

From `tests/ai/test_decomposer_scope_live.py` (lines 8–24):

> BUG-3 (scope misjudgement): The Decomposer handled the 06:44:12 UTC
> "Make a comprehensive plan to refactor pipeline, classifier, decomposer,
> gateway..." message as scope=small (direct response).
> 
> The task met ALL THREE large-scope criteria:
> - ✓ Multiple steps where output feeds the next (read files → analyse → write document)
> - ✓ External investigation before implementation (14 files were read)
> - ✓ Multiple independent sub-tasks (each module is independent)

---

## Fix Options

### Option 1: Better Prompt Examples (Calibration)
**Pros**:
- Low implementation cost (text-only change)
- No code changes needed
- Can be iterated quickly

**Cons**:
- Examples alone don't solve vagueness
- LLM may still overfit to examples without clear thresholds
- Requires finding/creating good real examples
- Does not address the "no thinking" limitation

**Implementation**:
Add 3–5 concrete examples to `route_task.md`:

```markdown
### Example 1: Scope = SMALL
User: "Add type hints to archon/ai/classifier.py"
Summary: Add type annotations to a single module
Reasoning: Single file, straightforward change, no investigation needed

Response: {"scope": "small", "summary": "Add type hints", "prompt": "..."}

### Example 2: Scope = LARGE
User: "Fact-check the viral Stanford RAG claim about semantic collapse — verify the paper, 
check their statistics, and write a detailed report"
Summary: Multi-step investigation + synthesis
Reasoning: ≥2 distinct investigations (paper + statistics) + synthesis (report writing)
Measurable: ≥2 research targets + output artifact = LARGE

Response: {"scope": "large", "summary": "...", "agents": [
  {"id": "a1", "task": "Research Stanford paper..."},
  {"id": "a2", "task": "Verify statistics...", "depends_on": ["a1"]},
  {"id": "a3", "task": "Synthesize report...", "depends_on": ["a1", "a2"]}
]}

```

**Note**: Multi-target investigations that include an output artifact (e.g., "write a bug report", "produce a plan") should be classified as `scope="large"`. The xfail test `test_decomposer_emits_plan_for_multi_target_investigation` (line 163) confirms this: a multi-target investigation with "Write a bug report" is expected to return `scope="large"`.

**Limitation**: Examples help but don't solve the lack of explicit thresholds.

---

### Option 2: Explicit Scope Rubric + Decision Tree
**Pros**:
- Gives LLM objective decision criteria
- Can be calibrated with metrics (file count, investigation target count, etc.)
- More reproducible and testable

**Cons**:
- Longer prompt (token cost)
- Requires careful calibration (risk of over/under-triggering)
- Still biased by "no thinking" constraint

**Implementation**:
Replace lines 35–37 in `route_task.md`:

```markdown
Decision criteria:

**TRIVIAL** scope (instant answer from context, no tools):
  Examples: "what did we just do?", "summarise the plan", "thanks"
  → **ALWAYS choose trivial over small for conversational messages**

**SMALL** scope (inline execution, single focused action):
  Choose SMALL ONLY if ALL of these are true:
    ✓ ≤ 1 file modified (or ≤ 3 lines/100-char changes per file)
    ✓ ≤ 1 investigation target (one file read, one API call, one concept verified)
    ✓ No output artifact (or answer is inline; no "save to file" required)
    ✓ Estimated completion: < 30 seconds of inline execution
  Examples: single file rename, API response parsing, quick lookup

**LARGE** scope (background agents, parallel work):
  Choose LARGE if ANY of these are true:
    ✗ ≥ 2 files need modification (e.g., refactoring across modules)
    ✗ ≥ 2 investigation targets (multiple papers, multiple code paths, etc.)
    ✗ Requires output artifact + investigation (e.g., report, plan document)
    ✗ Investigation must precede implementation (external research before coding)
    ✗ Parallel sub-tasks present (independent work that can run in parallel)
  Examples: refactoring 4 modules, fact-checking with report, multi-file bug fix
```

**Limitation**: The ≥2-file threshold is a rough proxy — a trivial 2-file rename would trigger agent spawning. Calibrate thresholds based on observed false positives. Still doesn't enable thinking; the model may not apply the rubric perfectly without a reasoning step.

---

### Option 3: Enable Extended Thinking for Router Session
**Pros**:
- LLM can reason through scope decision
- Invisible to user (only affects internal routing)
- Compatible with any model (router already uses user-selected model, typically Sonnet)
- Produces traceable reasoning

**Cons**:
- Adds latency (thinking adds 60–90s per route decision, based on Sonnet thinking observed at the router's `_ROUTER_TIMEOUT_S = 180.0` annotation)
- Increases token cost for router session
- May over-think simple tasks
- Thinking blocks interact with `max_turns=5` — verify thinking blocks don't consume turns before finalizing JSON output

**Implementation**:
Change line 252 in `archon/ai/decomposer.py`:

```python
# Before:
disable_thinking=True,

# After:
disable_thinking=False,
```

Then update `route_task.md` to ask for thinking:

```markdown
Before outputting your final JSON, reason through the scope decision:
- How many distinct investigation targets exist?
- How many files need to be created or modified?
- Are there independent sub-tasks that can run in parallel?
- Estimated execution time and agent count?

Then output ONLY valid JSON.
```

**Trade-off**: This adds 60–90 seconds per routing decision but makes them much more accurate. Given that routing happens once per user message, it's a worthwhile trade if the task is genuinely complex.

---

### Option 4: Remove "scope" Concept Entirely (Long-term)
**Pros**:
- Eliminates ambiguity
- Always spawn agents for non-trivial tasks
- Simpler decision logic

**Cons**:
- Large refactor (affects decomposer, pipeline, tests)
- Waste spawn agents on trivial tasks
- Tool promotion becomes the only safety net

**Implementation**:
- Merge "small" and "large" into "task" (requires plan)
- Keep only "trivial" for pure chat
- Route everything else to background agents
- Router only decides: trivial vs. task (binary, not ternary)

---

## Recommendation

### Immediate (Week 1)
**Implement Option 2 (Explicit Scope Rubric) + Option 3 (Enable Thinking)**

Why together?
- Rubric gives LLM objective criteria
- Thinking lets it apply the rubric
- Both are additive (no breaking changes)
- Low risk, high confidence improvement

**Implementation**:
1. Update `archon/ai/prompts/route_task.md` with explicit rubric (copy from Option 2)
2. Change `disable_thinking=True` → `disable_thinking=False` in `archon/ai/decomposer.py` line 252
3. Update `tests/ai/test_decomposer.py` — change `test_router_session_constructed_with_thinking_disabled` assertion from `True` to `False`
4. Add thinking preamble to the routing instruction
5. Re-run live tests (`pytest -m live tests/ai/test_decomposer_scope_live.py`)
6. Verify the three xfail tests now pass
7. Verify trivial-scope tasks still classify as trivial (add a live test if one doesn't exist)
8. Verify small tasks with 2-file scope don't over-classify as large (boundary case)
9. Run full unit test suite: `uv run pytest tests/ai/test_decomposer.py`

**Effort**: ~2 hours
**Token cost increase**: ~10% per route decision (due to thinking)
**Confidence**: High

---

### Follow-up (Week 2)
**Add Real Examples (Option 1)** to calibrate further

Extract examples from:
- The confirmed BUG-3 message (refactoring pipeline, classifier, decomposer, gateway)
- The Stanford RAG fact-check message
- Fact-check investigations (multi-target research + report)

Add to `route_task.md` as concrete examples with reasoning.

---

### Long-term (Backlog)
- Monitor scope decisions after rubric fix
- If bias persists despite rubric + thinking, review `orchestrator.md` for turn-budget framing that may anchor scope decisions conservatively
- If tool promotion becomes excessive, consider Option 4 (remove scope, always agent-based)

---

## Files to Change

1. **`archon/ai/prompts/route_task.md`** (lines 35–37)
   - Replace vague criteria with explicit rubric
   - Add real examples
   - Add thinking preamble

2. **`archon/ai/decomposer.py`** (line 252)
   - Change `disable_thinking=True` → `disable_thinking=False`

3. **`tests/ai/test_decomposer.py`**
   - Change `test_router_session_constructed_with_thinking_disabled` assertion from `True` to `False`

4. **`tests/ai/test_decomposer_scope_live.py`**
   - After fix, remove `@pytest.mark.xfail` from the three tests (lines 110, 163, 250)
   - Verify all three tests pass in CI

---

## References

- **Live Tests**: `tests/ai/test_decomposer_scope_live.py`
- **Router Code**: `archon/ai/decomposer.py` (lines 236–253, 437–519)
- **Router System Prompt**: `archon/ai/prompts/orchestrator.md` (loaded via `load_prompt("orchestrator")` as the router session's system prompt)
- **Routing Instruction**: `archon/ai/prompts/route_task.md` (injected as user-turn instruction text, not system prompt)
- **Pipeline (scope usage)**: `archon/ai/pipeline.py` (lines 294–324)
- **Related Bug**: Test comment references "BUG-3" from 2026-03-01 history log

**Note on live test helper gap**: `_call_decomposer_direct()` in live tests creates a raw `ClaudeSession` directly, bypassing `Decomposer._ensure_router_session()` (actual model, MCP tools, context injection). Consider adding an integration test via `Decomposer.route_task()` for full-path coverage.

