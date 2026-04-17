# FIX-031 — Ping message incorrectly promoted to background agent after timeout recovery
**Purpose**: Prevent chat-classified messages from being promoted to background agents during timeout recovery in `Pipeline._task_direct_monitored()`.
**Audience**: Internal — Archon maintainers
**Status**: To Do

---

## Background

On 2026-04-17, a "Ping" message was classified as `intent=chat` with `confidence=0.98` and then
timed out in `_task_direct_monitored()` after 300 seconds. Instead of retrying inline, the
recovery path promoted it to a background agent ("Sage") with 0 tool calls. This is wrong
behaviour: trivial chat messages should never spawn background agents.

Root cause: the timeout recovery block at `archon/ai/pipeline.py:477` gates promotion on
`self._has_bam` only — a boolean feature flag. It has no awareness of the message's intent. The
classification result (`intent=chat`) was computed before `_task_direct_monitored()` was entered,
but was not passed into the method, so the recovery path could not consult it.

See `Documentation/Backlog/bug_investigation_03_ping_promoted_background.md` for the full
investigation including all five fix options, the session log evidence, and the open question
about why a chat message hung for 300 seconds.

## Goal

After this fix: any message classified as `intent=chat` that times out during
`_task_direct_monitored()` will be retried inline rather than promoted to a background agent.
Task-classified messages that time out with at least some tool call progress will continue to be
promoted as before. No other pipeline behaviour changes.

---

## Scope

### In Scope
- Add `intent: str` parameter to `_task_direct_monitored()` (no default — forces both call sites to be explicit)
- Update both call sites in `Pipeline.send()` to pass the correct intent
- Change the recovery promotion condition from `if self._has_bam` to `if self._has_bam and intent != "chat"`
- Add a `pipeline_with_bam` test fixture and `patch_decomposer_timeout` helpers
- Three new unit tests: chat timeout → no promotion; task timeout with tools → promotion preserved; task timeout with 0 tools → still promotes (pure Option 5 behaviour)

### Out of Scope
- The optional `tool_count > 0` gate (Option 2 extension) — deferred; see Known Limitations
- Investigating why a chat message hung for 300 seconds (separate investigation)
- Option 1 (dispatch loop re-routing in `send()`) — long-term refactor, not an immediate fix
- Handler or voice code changes (`handler.py`, `voice.py`) — Option 5 stops yielding `PromotionEvent` for chat recoveries; no callers need updating

---

## Acceptance criteria
- [ ] `_task_direct_monitored(prompt, intent)` requires `intent` — both call sites pass it explicitly
- [ ] After timeout recovery, `intent="chat"` → no `PromotionEvent` is yielded; inline retry path is taken
- [ ] After timeout recovery, `intent="task"` → `PromotionEvent` is still yielded when `self._has_bam` is True (promotion behaviour preserved)
- [ ] All existing pipeline tests pass unchanged
- [ ] Three new tests cover: chat suppression, task promotion preserved, task with 0 tools still promotes

---

## What does NOT change
- Tool-count-based promotion path (`pipeline.py:409–432`) — untouched
- `handler.py` and `voice.py` — no changes needed
- Config schema — no new keys
- Normal chat routing (the `intent == "chat"` branch in `send()` that skips the router) — untouched
- `RecoveryEvent` dataclass and existing recovery phases

---

## Known limitations / accepted trade-offs
- **Task with 0 tool calls still promotes (Option 5 only)**: A genuinely complex task that hangs
  during its thinking phase (before any tool call) has `tool_count == 0`. Under pure Option 5 it
  is still promoted (intent is `"task"`). If the optional Option 2 gate (`tool_count > 0`) is
  ever adopted it will fall to the inline retry path instead, which risks a cascading timeout
  (~500 s worst case). Deferred as an explicit decision.
- **Why the chat message hung**: Not investigated by this fix. If it was a corrupt session,
  `recover_session()` (stop + start) may not fully clear it — unlike `_recover_session_in_clean_task()`
  which does a `force_kill + restart in a clean asyncio task`. The retry after recovery may also
  hang; the worst-case wait is approximately 500 seconds before an `ErrorEvent` is emitted.

---

## Architecture

### Change summary
`_task_direct_monitored` gains one required parameter `intent: str`. The recovery block's promotion
condition becomes `if self._has_bam and intent != "chat"`. Two call sites in `send()` each pass
the intent that was computed by the classifier.

### Affected files
| File | Change |
|------|--------|
| `archon/ai/pipeline.py` | Add `intent: str` param; update condition; update both call sites |
| `tests/ai/test_pipeline.py` | Add `_make_pipeline_with_bam`, `_make_timeout_decomposer`, tests |

### Method signature (after fix)
```python
# archon/ai/pipeline.py
async def _task_direct_monitored(
    self,
    prompt: str,
    intent: str,          # "chat" | "task" — gates recovery promotion
) -> AsyncGenerator[Event, None]:
```

### Call sites (after fix)
```python
# Call site 1 — chat path (line ~234)
async for event in self._task_direct_monitored(prompt, intent="chat"):
    yield event

# Call site 2 — task/router path (line ~323)
async for event in self._task_direct_monitored(resolved, intent=intent):
    yield event
```

### Recovery block condition (after fix)
```python
# Line ~477 — was: if self._has_bam:
if self._has_bam and intent != "chat":
    yield RecoveryEvent(phase="promoting", ...)
    ...
else:
    yield RecoveryEvent(phase="retrying", ...)
    ...   # existing inline retry path (unchanged)
```

---

## Tests

- **test_timeout_recovery_does_not_promote_chat_message** (unit): chat-classified message times out → no `PromotionEvent` in event stream
- **test_timeout_recovery_chat_yields_recovery_events** (unit): same scenario → `RecoveryEvent` events are present in event stream
- **test_timeout_recovery_still_promotes_task_with_tool_progress** (unit): task with tools > 0 times out → `PromotionEvent` is still yielded
- **test_timeout_recovery_task_zero_tools_promotes_under_option5** (unit): task with 0 tool calls times out → `PromotionEvent` is yielded (pure Option 5, not Option 2)

---

## Documentation update
- N/A — no user-visible behaviour change for task messages; chat messages now get inline retry instead of background agent, which is correct.

---

## Task breakdown

### Phase 1 — Implementation and tests
> **Releasable**: after this phase — the bug is fixed and covered by automated tests.

#### Task 1.1 — Add test infrastructure: `_make_pipeline_with_bam` and timeout helpers
- [x] **File**: `tests/ai/test_pipeline.py`
- **Depends on**: nothing
- **Description**:
  - Add `_make_pipeline_with_bam(classifier=None, decomposer=None)` helper that creates a `Pipeline`
    with `_has_bam = True` and a mocked `BackgroundAgentManager` stub. The simplest approach: call
    `_make_pipeline(...)`, then set `pipeline._has_bam = True` on the returned instance.
  - Add `_make_timeout_decomposer(tool_events=None)` helper:
    - Accepts an optional list of `ToolStarted` events to yield before timing out
    - `answer()` yields any provided `tool_events`, then raises `TimeoutError`
    - `recover_session = AsyncMock()` (already in `_mock_decomposer`; reuse)
    - Returns a decomposer mock suitable for passing to `_make_pipeline()` or `_make_pipeline_with_bam()`
  - No new module — add these helpers alongside existing helpers in `test_pipeline.py` (after the
    `_make_pipeline` definition, before the test functions)
- **Releasable**: after this task, the fixture and helpers are available for tests in Tasks 1.2–1.4.
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - No separate tests for helpers — they are validated by Tasks 1.2–1.4
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py --no-cov -q --tb=short`

#### Task 1.2 — Test: chat timeout → no PromotionEvent
- [ ] **File**: `tests/ai/test_pipeline.py`
- **Depends on**: Task 1.1
- **Description**:
  - `test_timeout_recovery_does_not_promote_chat_message`: Uses `_make_pipeline_with_bam()` with a
    classifier that returns `intent="chat", confidence=0.99` and a `_make_timeout_decomposer()` with
    no tool events.
  - Collects all events from `pipeline.send("Ping")`.
  - Asserts `not any(isinstance(e, PromotionEvent) for e in events)`.
  - `test_timeout_recovery_chat_yields_recovery_events`: Same setup; asserts that at least one
    `RecoveryEvent` is present in the event stream (recovery still runs, it just does not promote).
  - **Note**: because `_task_direct_monitored()` does not yet accept `intent`, these tests will fail
    until Task 1.3 is complete. Write them first (TDD).
- **Releasable**: after this task + Task 1.3, the chat suppression path is verified.
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_timeout_recovery_does_not_promote_chat_message`
  - Unit: `test_timeout_recovery_chat_yields_recovery_events`
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "test_timeout_recovery_does_not_promote_chat or test_timeout_recovery_chat_yields_recovery" --no-cov -q --tb=short`

#### Task 1.3 — Implementation: add `intent` param and fix recovery condition
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.2 (tests written; now make them green)
- **Description**:
  - Change `_task_direct_monitored(self, prompt: str)` signature to
    `_task_direct_monitored(self, prompt: str, intent: str)`. No default value — the type checker
    will catch any call site that does not pass it.
  - Update call site 1 (chat path, line ~234):
    `async for event in self._task_direct_monitored(prompt, intent="chat"):`
  - Update call site 2 (task/router path, line ~323):
    `async for event in self._task_direct_monitored(resolved, intent=intent):`
    (the local variable `intent` is already in scope at that point in `send()`)
  - Change recovery condition at line ~477 from `if self._has_bam:` to
    `if self._has_bam and intent != "chat":`.
  - No other changes to the method body.
- **Releasable**: after this task, Tasks 1.2 tests pass and the bug is fixed.
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Run tests from Task 1.2 to verify they pass
  - Run full suite to verify no regressions
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py --no-cov -q --tb=short`

#### Task 1.4 — Tests: task promotion preserved + task zero-tools promotes (Option 5)
- [ ] **File**: `tests/ai/test_pipeline.py`
- **Depends on**: Task 1.3
- **Description**:
  - `test_timeout_recovery_still_promotes_task_with_tool_progress`: Uses `_make_pipeline_with_bam()`
    with a classifier returning `intent="task", confidence=0.9` and a `_make_timeout_decomposer()`
    with `tool_events=[ToolStarted(name="Read", input={})]` (simulates 1 tool call before timeout).
    Asserts `any(isinstance(e, PromotionEvent) for e in events)` — promotion is preserved for tasks.
  - `test_timeout_recovery_task_zero_tools_promotes_under_option5`: Same setup but
    `_make_timeout_decomposer()` with no tool events (`tool_count == 0`). Under pure Option 5
    (no tool_count gate), intent="task" → still promotes.
    Asserts `any(isinstance(e, PromotionEvent) for e in events)`.
  - Both tests also assert `decomposer.recover_session.called` or the presence of `RecoveryEvent`
    to confirm recovery ran before the promotion decision.
- **Releasable**: after this task, regression coverage is complete.
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_timeout_recovery_still_promotes_task_with_tool_progress`
  - Unit: `test_timeout_recovery_task_zero_tools_promotes_under_option5`
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py --no-cov -q --tb=short`
