# FIX-031 — Session stuck: wrong recovery promotion guard
**Purpose**: Fix unconditional BAM promotion after timeout, so trivial messages and `chat`-intent requests are never promoted to background agents regardless of what caused the timeout.
**Audience**: Internal maintainers
**Status**: To Do

---

## Background

After a JSON buffer overflow crash (Bug 8, 2026-04-17), a session became unresponsive for ~28 minutes
wall clock. The user's "Ping" message was eventually promoted to background agent "Sage" with
`tool_count=0` — which is semantically absurd. The immediate cause: in `_task_direct_monitored`, the
timeout recovery path at line 477 checks only `if self._has_bam:` — always True when BAM is
configured — and promotes unconditionally, regardless of message intent or how much work was done.

Two distinct guards are needed:

- **Option C (immediate)**: Only promote when `tool_count > 0`. Zero tool calls means no meaningful
  progress was made; promoting such messages creates spurious agents and hides the real failure from
  the user. When `tool_count == 0`, retry inline instead (same path as non-BAM).

- **Option D (complementary)**: Never promote `chat`-classified messages, regardless of tool count.
  The classifier already identified intent with high confidence; that information should be used to
  prevent absurd promotions even if a `chat` handler somehow accumulated tool calls.

A third option (Option A — session restart on serious errors in `decomposer.py`) is **out of scope**
for this plan. It requires first resolving the open question about the 18:44–19:03 gap (whether the
root cause is session corruption or lock contention). See
`Documentation/Backlog/bug_investigation_09_session_stuck_recovery.md` for details.

## Goal

After this fix, the timeout recovery path in `_task_direct_monitored` applies two guards before
promoting to BAM: (1) at least one tool was called, and (2) the classifier did not identify the
message as `chat` intent. Messages that fail either check are retried inline on the recovered
session. The retry path is extracted from the non-BAM else-branch into a shared private method to
eliminate code duplication. All existing promotion tests continue to pass.

---

## Scope

### In Scope
- Extracting the non-BAM retry logic (lines 494–535 in `pipeline.py`) into `_retry_after_timeout()`
- Adding `tool_count > 0` guard to the timeout promotion branch (Option C)
- Threading `Classification` through to `_task_direct_monitored` (signature change + two call sites)
- Adding `classification.intent != "chat"` to the promotion guard (Option D)
- Tests for all new guard conditions

### Out of Scope
- Option A: session restart on serious errors in `decomposer.py` — deferred pending gap investigation
- Changing `_TASK_DIRECT_TIMEOUT_S` (Option B) — not part of this fix
- Differentiated timeouts by intent — a follow-up improvement
- Any change to the threshold-based mid-stream promotion (lines 409–432) — that path already fires
  only when `tool_count >= _tool_promotion_threshold > 0`

---

## Acceptance criteria

- [ ] A `chat`-classified message (e.g., "Ping") that times out with `tool_count == 0` yields a
  `RecoveryEvent(phase="retrying")`, NOT a `PromotionEvent`.
- [ ] A `task`-classified message that times out with `tool_count > 0` still yields `PromotionEvent`
  (existing behavior preserved).
- [ ] A `chat`-classified message that times out with `tool_count > 0` does NOT yield `PromotionEvent`
  — it retries inline.
- [ ] The retry path after Option C/D uses `_retry_after_timeout()` — the same logic as the
  non-BAM path (deadline, inner timeout, secondary recovery, generator cleanup).
- [ ] `_task_direct_monitored` accepts a `classification: Classification` parameter; both call sites
  in `send()` pass the classification result.
- [ ] All existing tests pass (no regression on mid-stream promotion, lock release, etc.).

---

## What does NOT change

- Mid-stream promotion logic (lines 409–432): already guarded by `tool_count >= threshold > 0`
- `_task_direct_monitored`'s internal timeout mechanism (rolling deadline, `_safe_anext`)
- `send()` external signature, return type, and event ordering — the two internal call sites of `_task_direct_monitored` gain a new argument, but `send()` itself is unchanged from callers' perspective
- `PromotionEvent` dataclass fields
- Non-BAM else-branch behavior — now delegates to the extracted `_retry_after_timeout()`
- `_build_promotion_prompt` and `_build_retry_prompt` helpers
- **Mid-stream tool-count promotion intent guard**: the threshold-based promotion at line 409 fires
  during active streaming (the session is healthy and making progress). The intent guard is only
  meaningful in the timeout recovery path (the session failed). A `chat`-intent message that
  legitimately calls 10+ tools should still be promoted — it is doing real work. The timeout-path
  guard is about preventing promotion of messages that made zero progress.

---

## Known limitations / accepted trade-offs

- **Root cause not fixed**: the 5-minute hang (session corruption or lock contention) is not
  addressed. Option C/D only prevents incorrect promotion after the timeout fires.
- **Gentle recovery remains**: the timeout path still uses `recover_session()` (gentle `stop()`/`start()`)
  rather than `force_kill_for_recovery()` (SIGKILL). On a truly deadlocked session this gentle path
  may itself fail; `_RECOVERY_TIMEOUT_S` prevents infinite hang, but recovery failure causes the retry
  to never fire. Replacing with `force_kill_for_recovery()` is a follow-up to Option A.
- **`Classification` coupled to promotion guard**: threading `Classification` into
  `_task_direct_monitored` adds coupling between the classifier layer and the promotion decision.
  This is intentional and semantically correct.
- **Classifier timeout fallback not protected**: when the classifier itself times out, the fallback
  is `Classification(intent='task', confidence=0.0)`. A `chat` message whose classification timed
  out will receive `intent='task'` and will be eligible for promotion if `tool_count > 0`. This is
  an accepted limitation — fixing it requires either a more reliable classifier or a different
  fallback strategy.
- **Low-confidence `chat` intent routed through task path**: when the classifier returns `intent="chat"` but `confidence < _CONFIDENCE_THRESHOLD`, the chat shortcut at line 232 is skipped and the message is routed through the task path (`route_task()`, call site 2 at line 323). The `classification` object passed to `_task_direct_monitored` still carries `intent="chat"`. As a result, if this message times out, the intent guard blocks promotion — even though the router treated it as a task. This is intentional: any `chat` classification, regardless of confidence, is considered ineligible for promotion. If future evidence shows that low-confidence `chat` classifications should be promotable, the guard can be tightened to require `classification.confidence >= _CONFIDENCE_THRESHOLD` in addition to `intent != "chat"`.
- **`tool_count` counts starts, not completions**: `tool_count` increments on each `ToolStarted` event (not on `ToolResult`). A message that started one tool and immediately hung has `tool_count == 1` and will be eligible for promotion, even though no tool returned a result. This is an accepted approximation — a started tool is evidence of real work beginning. Adding a "completed tool" guard would add complexity without clear benefit.

---

## Architecture

### Extracted helper: `_retry_after_timeout()` (`pipeline.py`)

```python
async def _retry_after_timeout(
    self,
    tool_pairs: list[tuple[ToolStarted, ToolResult | None]],
    prompt: str,
) -> AsyncGenerator[Event, None]:
```

Contains the retry loop currently at lines 499–535 (non-BAM else-branch, excluding the `RecoveryEvent(phase="retrying")` yield at line ~495 which stays with the caller):
rolling `_RETRY_TIMEOUT_S` deadline → `_safe_anext` loop → secondary `recover_session()` on retry
timeout → `ErrorEvent` → `finally` generator cleanup.

Called from:
1. Non-BAM path (was inline, now delegates to this method)
2. BAM path when `tool_count == 0 or classification.intent == "chat"` (new)

### Updated `_task_direct_monitored` signature

```python
async def _task_direct_monitored(
    self,
    prompt: str,
    classification: Classification,
) -> AsyncGenerator[Event, None]:
```

Both call sites in `send()` (lines 234 and 323) pass `result.classification`.

### Updated promotion guard (lines 476–492)

```python
# 4. Promote or retry
if self._has_bam and tool_count > 0 and classification.intent != "chat":
    # promote — task made real progress and is not a chat message
    yield RecoveryEvent(phase="promoting", message="Promoting task to background agent...")
    ...
    yield PromotionEvent(...)
    ...
else:
    # retry — no progress made, or chat intent: inline retry on recovered session
    yield RecoveryEvent(phase="retrying", message="Retrying with simplified approach...")
    async for event in self._retry_after_timeout(tool_pairs, prompt):
        yield event
```

**Note: the guard uses `!= 'chat'` (negative check) rather than `== 'task'` (positive check).** This
is intentional: if a future classifier adds a third intent (e.g., `'search'`), it will be eligible
for promotion by default — which is the safer behavior than silently dropping it to the retry path.
If the intent type is extended, this decision should be revisited.

### Connection to existing components

- `send()` at lines 234 and 323 already has `result.classification` in scope; passing it adds one
  argument to two existing call sites.
- `Classification` is already imported in `pipeline.py` (line 202 fallback path). Add to module-level
  imports if not already present.
- `_retry_after_timeout` is a private method — no external interface change.

---

## Tests

- **test_timeout_zero_tool_count_retries_not_promotes** (unit): `tool_count == 0` after timeout with BAM enabled → `RecoveryEvent(phase="retrying")` yielded, no `PromotionEvent`
- **test_timeout_nonzero_tool_count_promotes_task** (unit): `tool_count > 0`, `intent="task"` after timeout with BAM → `PromotionEvent` yielded
- **test_timeout_chat_intent_retries_not_promotes** (unit): `tool_count > 0`, `intent="chat"` after timeout with BAM → retry path, no `PromotionEvent`
- **test_timeout_chat_zero_tool_count_retries** (unit): `tool_count == 0`, `intent="chat"` → retry, no `PromotionEvent` (double-guard case)
- **test_retry_after_timeout_helper_streams_events** (unit): `_retry_after_timeout()` yields events from decomposer on recovered session
- **test_retry_after_timeout_handles_secondary_timeout** (unit): retry itself times out → secondary `recover_session()` called, `ErrorEvent` yielded
- **test_retry_after_timeout_closes_generator_on_exception** (unit): exception in retry loop → retry generator is always closed in finally
- **test_chat_direct_promotes_when_threshold_exceeded** (unit, existing): mid-stream promotion unchanged — must still pass
- **test_chat_direct_no_promotion_below_threshold** (unit, existing): must still pass
- **test_timeout_does_not_deadlock_next_call** (unit, existing): lock release after timeout — must still pass
- **test_generator_abandoned_on_promotion** (unit, existing): mid-stream generator cleanup — must still pass
- **test_promotion_aclose_timeout_releases_lock** (unit, existing): aclose timeout on promotion — must still pass
- **test_timeout_recovery_promotes_to_background** (existing, `test_timeout_recovery.py`, **must be updated** in Task 1.2): currently uses `hang_forever=True` with no tool events → `tool_count=0` → after Task 1.2, hits the retry path instead of promotion. Must be updated to yield a `ToolStarted` event before the hang so `tool_count=1`.
- **test_timeout_recovery_tracks_context** (existing, `test_timeout_recovery.py`, **must be updated** in Task 1.2): uses `hang_forever=True` with BAM, asserts `track_context` and `flush_pending_context` called — both are promotion-specific. After Task 1.2, `tool_count=0` → retry path → neither is called. Must be updated similarly to yield a `ToolStarted` before the hang.
- **test_retry_after_timeout_uses_build_retry_prompt** (unit): `_retry_after_timeout(tool_pairs, prompt)` calls `_build_retry_prompt(tool_pairs, prompt)` and passes the result to `_decomposer.answer()`
- **test_retry_after_timeout_secondary_recovery_timeout** (unit): `answer()` hangs (retry times out), then `recover_session()` also hangs (secondary recovery times out) → `TimeoutError` wrapped in `ErrorEvent`, no propagation
- **test_retry_after_timeout_secondary_recovery_fails** (unit): `answer()` hangs (retry times out), `recover_session()` raises RuntimeError → wrapped in `ErrorEvent`, no propagation; `recover_session.await_count == 1`
- **test_retry_path_does_not_reclose_original_gen** (unit): outer `finally` calls `gen.aclose()` exactly once (in timeout handler), never a second time — verified with a counted mock generator triggered via natural timeout
- **test_mid_stream_promotion_unaffected_by_intent_guard** (unit): `intent="chat"` + `tool_count >= threshold` → `PromotionEvent` present AND no `RecoveryEvent(phase="timeout_detected")` — confirms mid-stream path, not timeout path
- **test_timeout_zero_tools_retry_succeeds** (integration): BAM enabled, `tool_count=0` after timeout, retry succeeds → `Response` in output, no `PromotionEvent`
- **test_timeout_zero_tool_count_no_lock_leak** (integration): BAM enabled, `tool_count=0` after timeout → pipeline lock released after retry completes (no deadlock)
- **test_timeout_task_intent_nonzero_still_promotes** (unit): `tool_count=1`, `intent="task"`, BAM enabled → `PromotionEvent` yielded (regression: Option D does not break task promotion)
- **test_timeout_task_via_router_path_still_promotes** (unit): `intent="task"`, `tool_count > 0`, BAM enabled, router-resolved call path → `PromotionEvent` emitted
- **test_timeout_chat_classification_retries_not_promotes** (unit): BAM enabled, decomposer hanging, `Classification(intent='chat')` → no `PromotionEvent` (verifies `classification` parameter is read)
- **test_timeout_promotion_includes_partial_results** (existing, `test_timeout_recovery.py`, **must be updated** in Task 2.2): existing test with `tool_count > 0` and BAM enabled that asserts `len(promotions) == 1`. After Task 2.2 adds the intent guard, `intent="chat"` (default) blocks promotion. Must switch to `_mock_classifier(intent="task")` and configure `_RouteTaskGenMock` per the Task 2.2 mandate.

**Test placement rule**: Tests focused on classification routing, timeout mechanics, and direct guard logic belong in `tests/ai/test_pipeline.py` (which uses a simplified `_make_pipeline` helper without BAM by default). Tests that require the full `pipeline.send()` path with BAM enabled and a running background agent manager belong in `tests/ai/test_timeout_recovery.py`, which has the BAM-aware `_make_pipeline` helper. Both files use `pipeline.send()` — the distinction is BAM context, not whether the call is direct or indirect.

---

## Documentation update

- [ ] `Documentation/Backlog/bug_investigation_09_session_stuck_recovery.md`: mark Option C and D implemented after merge
- [ ] `CLAUDE.md`: no change needed (pipeline architecture section already current)

---

## Task breakdown

### Phase 1 — Extract retry helper + tool_count guard (Option C)
> **Releasable**: after Task 1.2 — zero-tool-count timeouts no longer produce spurious BAM promotions.

#### Task 1.1 — Extract `_retry_after_timeout()` from non-BAM else-branch
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: nothing
- **Description**:
  - Add private async generator method:
    ```python
    async def _retry_after_timeout(
        self,
        tool_pairs: list[tuple[ToolStarted, ToolResult | None]],
        prompt: str,
    ) -> AsyncGenerator[Event, None]:
    ```
  - Move the entire body of the `else` branch (lines 494–535) into this method, including the retry
    prompt construction via `_build_retry_prompt(tool_pairs, prompt)`. The prompt transformation is
    part of the retry logic. Also move the retry deadline loop, inner `TimeoutError` handler,
    secondary `recover_session()`, `ErrorEvent`, and the `finally` block that closes `retry_gen`.
    **Exception**: the initial `RecoveryEvent(phase="retrying")` yield (currently at line ~495) stays with the **caller**, not inside the helper. The helper is responsible only for the retry loop, secondary recovery, and generator cleanup. The caller yields `RecoveryEvent(phase="retrying")` then delegates to `_retry_after_timeout` for all subsequent events. This matches the Architecture pseudocode in this plan.
  - In the `else` branch (non-BAM path), replace the moved body with:
    ```python
    yield RecoveryEvent(phase="retrying", message="Retrying with simplified approach...")
    async for event in self._retry_after_timeout(tool_pairs, prompt):
        yield event
    ```
  - Behavior of the non-BAM path is identical; this is a pure refactor.
  - The new method has the same `_RETRY_TIMEOUT_S`, `_RECOVERY_TIMEOUT_S`, and `_ACLOSE_TIMEOUT_S`
    constants already in scope as module-level names.
  - **Important**: by the time `_retry_after_timeout` is called, `gen` is already closed and
    `gen_closed = True` (set at line 454 in the timeout handler). The extracted method does NOT
    interact with `gen` or `gen_closed` — it creates and manages its own `retry_gen`. The outer
    `finally` block correctly skips `gen.aclose()` because `gen_closed` was set before the retry
    path. Confirm this invariant is preserved after extraction.
- **Releasable**: after this task, the module compiles with identical behavior; `_retry_after_timeout`
  is callable as a shared helper.
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Unit: `test_retry_after_timeout_helper_streams_events` — mock `_decomposer.answer()` to yield a
    `Response`; call `_retry_after_timeout([], "prompt")`; assert `Response` event in output.
  - Unit: `test_retry_after_timeout_handles_secondary_timeout` — mock `_decomposer.answer()` to
    never yield (hangs); patch `_RETRY_TIMEOUT_S=0.01`; assert `ErrorEvent` in output; assert `recover_session.await_count == 1` (the secondary recovery attempt was made exactly once within the helper).
  - Unit: `test_retry_after_timeout_secondary_recovery_timeout` — mock `_decomposer.answer()` to hang (never yield); patch `_RETRY_TIMEOUT_S=0.01` so the retry times out quickly; mock `recover_session()` to hang (`AsyncMock(side_effect=lambda: asyncio.sleep(999))`); patch `_RECOVERY_TIMEOUT_S=0.01` so the secondary recovery also times out; assert `ErrorEvent` is yielded and no exception propagates out of the helper. Note: `answer()` must hang to trigger the `_RETRY_TIMEOUT_S` timeout, which is what leads to the secondary `recover_session()` call.
  - Unit: `test_retry_after_timeout_secondary_recovery_fails` — call `_retry_after_timeout` directly (not via `send()`); mock `_decomposer.answer()` to hang; patch `_RETRY_TIMEOUT_S=0.01`; mock `recover_session()` to raise `RuntimeError("boom")`; assert `ErrorEvent` is yielded; assert `recover_session.await_count == 1` (helper attempted secondary recovery exactly once). This test distinguishes from `test_retry_after_timeout_secondary_recovery_timeout` by the recovery outcome (raises vs hangs).
  - Unit: `test_retry_path_does_not_reclose_original_gen` — verify that the outer `finally` block in `_task_direct_monitored` calls `gen.aclose()` exactly once (during the timeout handler at line ~451-454, which sets `gen_closed=True`), and never a second time in the `finally`. To implement: use a mock async generator for the decomposer's answer that hangs; patch `_TASK_DIRECT_TIMEOUT_S=0.05` to trigger the timeout naturally; wrap the mock generator's `aclose()` with a call counter; run `_task_direct_monitored` end-to-end; assert `gen.aclose()` was called exactly 1 time total. Do NOT attempt to set `gen_closed` directly — it is a local variable; test it by triggering the real timeout path and counting `aclose()` invocations.
  - Unit: `test_retry_after_timeout_closes_generator_on_exception` — mock `_decomposer.answer()` to return an async generator whose first `__anext__()` call raises `RuntimeError("iter error")` (note: `answer()` itself returns the generator object successfully; the exception occurs during iteration); assert the retry generator's `aclose()` is called in the `finally` block despite the exception.
  - Unit: `test_retry_after_timeout_uses_build_retry_prompt` — mock `_build_retry_prompt` and
    `_decomposer.answer()`; call `_retry_after_timeout(tool_pairs, 'original prompt')`; assert
    `_build_retry_prompt` was called with `(tool_pairs, 'original prompt')` and that the result
    was passed to `decomposer.answer()`.
  - Regression: `test_timeout_does_not_deadlock_next_call` (existing) — must still pass.
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py -k "retry_after_timeout or deadlock or no_bam_retries" -v`
  - Full regression: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py --no-cov -q`

#### Task 1.2 — Add `tool_count > 0` guard to BAM promotion path
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change line 477 from:
    ```python
    if self._has_bam:
    ```
    to:
    ```python
    if self._has_bam and tool_count > 0:
    ```
  - Expand the existing `else` branch (which now delegates to `_retry_after_timeout`) to cover
    both the no-BAM case AND the BAM-but-zero-tool-count case:
    ```python
    if self._has_bam and tool_count > 0:
        yield RecoveryEvent(phase="promoting", message="Promoting task to background agent...")
        agent_prompt = _build_promotion_prompt(tool_pairs, prompt)
        yield PromotionEvent(
            agent_prompt=agent_prompt,
            original_prompt=prompt,
            tool_count=tool_count,
        )
        self._decomposer.track_context(
            prompt,
            f"[Task timed out after {_TASK_DIRECT_TIMEOUT_S:.0f}s and promoted to background agent]",
        )
        self._decomposer.flush_pending_context()
    else:
        # No BAM, or BAM but zero tools called (no progress made) — retry inline
        yield RecoveryEvent(phase="retrying", message="Retrying with simplified approach...")
        async for event in self._retry_after_timeout(tool_pairs, prompt):
            yield event
    ```
  - The `RecoveryEvent(phase="promoting")` that was previously inside the BAM block (line 478–480)
    stays inside the new `if` branch — no change to its position.
  - **Update `test_timeout_recovery_promotes_to_background`** in `test_timeout_recovery.py`: this
    test currently uses `hang_forever=True` with no tool events → `tool_count=0` → after this
    change it will hit the retry path instead. Update the mock decomposer to yield a `ToolStarted`
    event before hanging so `tool_count=1`, ensuring promotion still fires.
  - **Update `test_timeout_recovery_tracks_context`** in `test_timeout_recovery.py`: same issue —
    `hang_forever=True` with no tool events → `tool_count=0` → retry path → `track_context` and
    `flush_pending_context` not called. Update similarly to yield a `ToolStarted` before the hang.
    These tests currently use `hang_forever=True` with no tool events → `tool_count=0` → they will
    hit the retry path after this change.
  - **Note on yield-then-hang pattern**: the existing `_mock_decomposer(hang_forever=True)` hangs immediately without yielding any events. To yield a `ToolStarted` before hanging, build a custom `answer()` coroutine inline in each test rather than using the shared helper parameter. Example pattern: `decomposer.answer = _make_answer_with_tool_then_hang(ToolStarted(name="Read", input={}))` where `_make_answer_with_tool_then_hang` is a local async generator factory that yields the given event then awaits `asyncio.sleep(999)`.
- **Releasable**: after this task, "Ping"-style zero-tool-count timeouts with BAM enabled produce
  a retry instead of a spurious `PromotionEvent`.
- **Tests (TDD)**:
  - Unit (`tests/ai/test_pipeline.py`): `test_timeout_zero_tool_count_retries_not_promotes` —
    mock `_decomposer.answer()` to hang (no yields); patch `_TASK_DIRECT_TIMEOUT_S=0.05`; BAM
    enabled; assert no `PromotionEvent`, assert `RecoveryEvent(phase="retrying")` present.
  - Unit (`tests/ai/test_pipeline.py`): `test_timeout_nonzero_tool_count_promotes_task` — mock
    answer to yield one `ToolStarted` then hang; patch timeout short; BAM enabled; assert
    `PromotionEvent` yielded with `tool_count=1`.
  - Integration (`tests/ai/test_timeout_recovery.py`): `test_timeout_zero_tool_count_no_lock_leak`
    — pipeline with BAM enabled, decomposer hangs with no events; patch
    `_TASK_DIRECT_TIMEOUT_S=0.05`; run `send()` end-to-end; assert pipeline lock released after
    completion (not held). Place in `test_timeout_recovery.py`.
  - Integration (`tests/ai/test_timeout_recovery.py`): `test_timeout_zero_tools_retry_succeeds` —
    BAM enabled; first `answer()` call hangs (no events, `tool_count=0`); second call (retry) yields
    a `Response`; patch `_TASK_DIRECT_TIMEOUT_S=0.05`; run `send()` end-to-end; assert `Response`
    event in collected output and no `PromotionEvent`. Confirms the retry path actually delivers a
    response, not just that promotion is suppressed.
  - Regression: `test_chat_direct_promotes_when_threshold_exceeded` — must still pass (mid-stream
    promotion not affected by this change).
  - Regression: `test_generator_abandoned_on_promotion` — must still pass.
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py -k "zero_tool or nonzero_tool or promotes_when_threshold or abandoned or no_lock_leak or promotes_to_background or tracks_context" -v`
  - Full regression: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py --no-cov -q`

---

### Phase 2 — Intent-based promotion guard (Option D)
> **Releasable**: after Task 2.2 — `chat`-classified messages are never promoted regardless of tool count.

#### Task 2.1 — Add `classification` parameter to `_task_direct_monitored`
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.1
- **Description**:
  - Update the method signature:
    ```python
    async def _task_direct_monitored(
        self,
        prompt: str,
        classification: "Classification",
    ) -> AsyncGenerator[Event, None]:
    ```
  - `Classification` is already used in `send()` (line 202); add it to the module-level import from
    `archon.ai.classification` if it is currently imported only inside the fallback block. Move the
    import to the top of the file.
  - Update both call sites in `send()`:
    - Line 234 (chat path): `async for event in self._task_direct_monitored(prompt, result.classification):`
    - Line 323 (router-resolved context path): `async for event in self._task_direct_monitored(resolved, result.classification):`
  - The `classification` parameter is not yet used inside the method body in this task — that is Task 2.2.
    Storing it as a local variable is acceptable if needed for clarity.
  - **Update all existing direct-call tests of `_task_direct_monitored`** in
    `tests/ai/test_pipeline.py` to pass a `classification` argument. The following call sites in
    that file will fail with a `TypeError` after this signature change — find all occurrences by running `grep -n 'task_direct_monitored(' tests/ai/test_pipeline.py` and updating each actual call site (not comments or docstrings): Note: do NOT rely on any line numbers in this plan, as prior tasks may shift them:
    - Use `Classification(intent='task', confidence=0.95)` as the default for task-oriented tests.
    - Use `Classification(intent='chat', confidence=0.95)` for chat-oriented tests.
    **Default rule**: use `Classification(intent='task', confidence=0.95)` for all found sites unless the test name contains `chat_direct` or the test is specifically asserting that no promotion occurs due to chat intent. Tests that expect `PromotionEvent` to fire (any mid-stream or timeout promotion test) must use `intent='task'`, since after Task 2.2 `intent='chat'` will block promotion. If uncertain, default to `intent='task'` — it is always safe for the promotion path.
- **Releasable**: after this task, the interface is updated and both call sites pass classification;
  no behavior change yet.
- **Tests (TDD)** — `tests/ai/test_pipeline.py`:
  - Regression: all existing `test_chat_direct_*` and `test_trivial_scope_*` tests — must still pass
    (call sites updated, signature accepted).
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "direct or trivial_scope or small_scope" -v`
  - Full regression: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py --no-cov -q`

#### Task 2.2 — Extend promotion guard: `classification.intent != "chat"`
- [x] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 1.2 AND Task 2.1
- **Description**:
  - Extend the guard from Task 1.2:
    ```python
    if self._has_bam and tool_count > 0 and classification.intent != "chat":
        # promote
        ...
    else:
        # retry inline
        ...
    ```
  - A `chat`-classified message that somehow accumulated tool calls (unlikely but possible) now
    falls into the retry path — not promoted.
  - The `classification.intent` check uses the exact string `"chat"` (same value the classifier
    and `send()` use at line 232: `if intent == "chat" and confidence >= _CONFIDENCE_THRESHOLD`).
  - No other guard conditions change.
  - **Update `test_timeout_recovery_promotes_to_background`** and **`test_timeout_recovery_tracks_context`**:
    Task 1.2 updated these tests to yield a `ToolStarted` before the hang (so `tool_count=1`). However,
    they still use the default `_mock_classifier(intent="chat")`. After this task, `intent="chat"` blocks
    promotion regardless of tool count. Update these tests to use `_mock_classifier(intent="task")` so
    they remain on the promotion path. Note: switching from `intent="chat"` to `intent="task"` changes
    the routing path (chat direct → router path via `_RouteTaskGenMock`). This means `_task_direct_monitored`
    receives the resolved prompt (e.g. `"Do the thing"` from `TaskOutput.prompt`) rather than the raw
    user prompt.
    **Mandate**: configure `_RouteTaskGenMock` to return `TaskOutput(prompt=<original-user-prompt>)` where `<original-user-prompt>` matches the string passed to `pipeline.send()` in each test. This makes the resolved prompt equal to the original user prompt, so no assertion changes are needed for `PromotionEvent.original_prompt` or `track_context(args[0])`. Do NOT update assertions to expect the resolved string — that approach couples the test to routing internals.
    > **Implementation**: override `decomposer.route_task` individually in each affected test after calling `_mock_decomposer()`: `decomposer.route_task = _RouteTaskGenMock(TaskOutput(scope="small", summary="Quick task", prompt=<test-input-prompt>))`. Do NOT modify the shared `_mock_decomposer()` helper — per-test overrides keep the shared helper stable.
  - **Test hygiene**: `_mock_classifier` in `test_timeout_recovery.py` hardcodes `raw_response='{"intent": "chat", "confidence": 0.95}'` regardless of the `intent` parameter. After switching these tests to `intent="task"`, the `raw_response` will be inconsistent. Update `_mock_classifier` **in `tests/ai/test_timeout_recovery.py`** to interpolate the `intent` parameter into `raw_response` (the version in `tests/ai/test_pipeline.py` already interpolates correctly and needs no change): `raw_response=f'{{"intent": "{intent}", "confidence": {confidence}}}'`. This does not affect test behavior (nothing reads `raw_response` in the timeout path) but improves test fidelity.
  - **Update `test_timeout_promotion_includes_partial_results`** in `test_timeout_recovery.py`: this
    test's `_make_pipeline()` uses `_mock_classifier()` which defaults to `intent="chat"`. After this
    task, `intent="chat"` blocks promotion, so `assert len(promotions) == 1` will fail. Update the
    pipeline to use `_mock_classifier(intent="task")`. Per C2-F-4, also configure `_RouteTaskGenMock`
    to return `TaskOutput(prompt=<test-input-prompt>)` to align the resolved prompt with test
    expectations. The tool-event assertions (`"Read" in agent_prompt`, etc.) should still hold
    because they come from `tool_pairs`, not from the prompt string.
- **Releasable**: after this task, all three guard conditions (`has_bam`, `tool_count > 0`,
  `intent != "chat"`) are enforced; the fix is complete.
- **Tests (TDD)**:
  - Unit (`tests/ai/test_timeout_recovery.py`): `test_timeout_chat_intent_retries_not_promotes` —
    mock answer to yield one `ToolStarted` then hang;
    `classification=Classification(intent="chat", confidence=0.98)`; BAM enabled; patch timeout
    short; assert no `PromotionEvent`, assert `RecoveryEvent(phase="retrying")`.
  - Unit (`tests/ai/test_timeout_recovery.py`): `test_timeout_chat_zero_tool_count_retries` —
    `tool_count=0` + `intent="chat"`; assert retry, no promotion (both guards fire simultaneously).
  - Unit (`tests/ai/test_timeout_recovery.py`): `test_timeout_task_intent_nonzero_still_promotes` —
    pipeline with BAM enabled; mock answer to yield one `ToolStarted` then hang;
    `Classification(intent="task", confidence=0.95)`; patch `_TASK_DIRECT_TIMEOUT_S=0.05`; assert
    `PromotionEvent` yielded with `tool_count=1`. Placed in `test_timeout_recovery.py` because it
    requires BAM-enabled pipeline setup.
  - Unit (`tests/ai/test_timeout_recovery.py`):
    `test_timeout_task_via_router_path_still_promotes` — BAM enabled; `_mock_classifier(intent="task")`; mock `decomposer.answer()` to yield one `ToolStarted` then hang; configure `_RouteTaskGenMock(TaskOutput(prompt=<test-input-prompt>))` per C3-F-4 mandate; patch `_TASK_DIRECT_TIMEOUT_S=0.05`; call `pipeline.send(<test-input-prompt>)` end-to-end; assert `PromotionEvent` emitted. This exercises the full router-resolved path (line 323) and guards against the intent guard breaking task promotion via the router.
  - Unit (`tests/ai/test_timeout_recovery.py`):
    `test_timeout_chat_classification_retries_not_promotes` — call `_task_direct_monitored` directly
    with BAM enabled, decomposer hanging, `Classification(intent='chat', confidence=0.95)`; assert
    no `PromotionEvent`. This verifies the `classification` parameter is actually read and not
    ignored. (Replaces the weaker `test_task_direct_monitored_passes_classification` from Task 2.1.)
  - Unit (`tests/ai/test_pipeline.py`): `test_mid_stream_promotion_unaffected_by_intent_guard` — set `_tool_promotion_threshold=1`; mock decomposer to yield `ToolStarted`, `ToolResult`, then another `ToolStarted` (triggering threshold); send with `Classification(intent="chat", confidence=0.95)`; assert `PromotionEvent` is yielded AND no `RecoveryEvent` with `phase="timeout_detected"` is present in events. The absence of `phase="timeout_detected"` confirms promotion fired mid-stream via the threshold path (line 409), not the timeout recovery path. (`"timeout_detected"` is the definitive phase string emitted at pipeline.py line ~458 when the timeout fires.)
  - Update (`tests/ai/test_timeout_recovery.py`): `test_timeout_promotion_includes_partial_results` — switch `_mock_classifier()` to `_mock_classifier(intent="task")`; configure `_RouteTaskGenMock(TaskOutput(prompt=<test-input-prompt>))` per the mandate above. The tool-pair assertions (`"Read" in agent_prompt`, `"file contents here" in agent_prompt`) remain valid because they come from `tool_pairs`, not the prompt string.
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py -k "chat_intent or zero_tool or task_intent_nonzero or router_path or chat_classification or partial_results or promotes_to_background or tracks_context" -v`
  - Full regression: `uv run pytest tests/ai/test_pipeline.py tests/ai/test_timeout_recovery.py --no-cov -q`
