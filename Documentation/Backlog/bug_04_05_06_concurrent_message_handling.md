# Bug 04/05/06 — Concurrent messages cause shifted responses, stuck "Processing...", and silent processing

Status: FIXED (2026-03-11)

## Description

**Bug 04**: Messages get processed in the wrong order. When the user sent "Ping" followed by 2 more messages, "Ping" was answered with the response to the first question, then Ping was answered later.

**Bug 05**: After sending multiple messages, Archon got stuck showing "⏳ Processing..." repeatedly with no activity.

**Bug 06**: Multiple "Processing..." messages shown with zero actual response or activity:
```
> Archon: ⏳ Processing...
> Archon: ⏳ Processing...
> Archon: ⏳ Processing...
```

## Root cause (confirmed from logs)

At 18:34:44 UTC, the user sent 3 messages simultaneously ("Ping", "Yesterday...", "You can check sessions..."). All 3 arrived before any processing had started, so `session.is_processing` was False for all of them. All 3 called `Pipeline.send()` concurrently, triggering 3 parallel classifier calls.

From archon.log:
```
19:34:44,496 archon INFO Message received from user 154643621 (4 chars)
19:34:44,496 archon INFO Message received from user 154643621 (201 chars)
19:34:44,497 archon INFO Message received from user 154643621 (31 chars)
...
19:36:34,718 archon INFO Classification: intent=task confidence=0.85 duration=109.5s
19:36:37,169 archon INFO Classification: intent=chat confidence=0.92 duration=112.0s
19:36:39,854 archon INFO Classification: intent=chat confidence=0.94 duration=114.6s
```

3 classifiers ran in parallel, each taking 109-115 seconds instead of the normal 2.5s. This is pure resource starvation from concurrent SDK subprocess load.

## Code location

`archon/chat/handler.py` — `handle_message()` calls `session.send(message.text)` with no serialization.

The `is_processing` check at line 311 only sends a notification, it does NOT prevent the concurrent send() call.

`archon/ai/pipeline.py` — `Pipeline.send()` has NO asyncio.Lock, so concurrent calls run in parallel.

## Required fix

Add per-user serialization to ensure only one message is processed at a time. Options:
1. Add an `asyncio.Lock` inside `Pipeline.send()` (or `ClaudeSession.send()`)
2. Add a per-user queue in the SessionManager or handler

The simplest fix (KISS): Add an `asyncio.Lock` in `Pipeline` that's acquired at the start of `send()`.

When a user sends a message while another is processing:
1. `is_processing` → True → send "queued" notification ✓ (already done)
2. `Pipeline.send()` acquires lock → waits for previous to finish
3. Processes in order

## Tasks

1. Verify there is no existing serialization lock (grep for asyncio.Lock in pipeline.py, session.py, handler.py)
2. Add `asyncio.Lock` to `Pipeline.send()` to serialize concurrent calls per pipeline instance
3. Verify the `is_processing` flag properly reflects lock-held state so the handler can send the right notification
4. Write tests for concurrent message handling
5. Fix and verify

## AI Notes

## Implementation (2026-03-11)

### What was changed

**`archon/ai/pipeline.py`**:

1. Added `import asyncio` at the top.
2. Added `self._lock = asyncio.Lock()` in `Pipeline.__init__()`.
3. Wrapped the entire body of `Pipeline.send()` with `async with self._lock:`. The lock is held for the full duration of the generator (including streaming). Python's async generator semantics guarantee the `async with` exit runs even if the caller calls `aclose()` mid-stream, so the lock is always released.
4. Updated `is_processing` property: `return self._lock.locked() or self._decomposer.is_processing`. This ensures `is_processing` returns `True` from the moment `send()` acquires the lock — before the decomposer has even started. This means the handler's existing "queued" notification fires correctly even for the window between lock acquisition and the first decomposer call.

### Why this fixes all three bugs

- **Bug 04** (wrong-order responses): Serialization guarantees messages are processed one at a time in the order they acquire the lock.
- **Bug 05** (stuck "Processing..."): With serialization, the second/third calls queue behind the lock rather than running concurrently. No resource starvation means classification completes in ~2.5s instead of 109s+.
- **Bug 06** (multiple "Processing..." with no activity): The `is_processing` check in `handler.py` now correctly detects the lock state, so the "queued" notification is sent, and the subsequent `send()` waits rather than spawning a second concurrent pipeline.

### Tests added

Three new tests appended to `tests/ai/test_pipeline.py`:

- `test_send_serializes_concurrent_calls` — verifies that when two `send()` calls are gathered concurrently, the second one starts only after the first has finished (checked via execution_order list).
- `test_is_processing_true_while_lock_held` — verifies `is_processing` returns `True` immediately after the first event is yielded (lock held, decomposer not yet started).
- `test_send_lock_released_on_generator_abandon` — verifies that calling `aclose()` mid-stream releases the lock so subsequent calls do not deadlock.

### Verification

- All 3 new tests: PASS
- Full suite (2296 tests): PASS (0 regressions)
- mypy `archon/ai/pipeline.py`: no new errors

## DA Review (2026-03-11)

Reviewer: Devil's Advocate agent. All findings below are based on reading the actual code in `archon/ai/pipeline.py`, `archon/chat/handler.py`, `archon/chat/voice.py`, and `tests/ai/test_pipeline.py`.

### Verdict: FIX IS CORRECT

The implementation is sound. No critical or major issues found. Minor observations below.

---

### Question 1: Is the asyncio.Lock correctly placed?

**Yes.** `Pipeline.send()` (line 117) is an `async def` returning `AsyncGenerator[Event, None]`. The entire body is wrapped in `async with self._lock:` (line 125). In Python's async generator semantics, the `async with` context manager holds the lock from the moment the generator is first advanced (`__anext__()`) until either:
- The generator is fully exhausted (falls off the end or hits `return`), or
- The generator receives `aclose()` which throws `GeneratorExit`, triggering `__aexit__` on the lock.

This means the lock IS held for the full streaming duration, not just at function definition time. Verified: the generator yields at multiple points (lines 131, 133, 148-149, 184, 191-192) and the lock remains held across all of them because `async with` in an async generator suspends the context manager exit until the generator itself terminates.

**Important nuance:** The lock is NOT acquired when `pipeline.send("hello")` is called. It is acquired when the caller first iterates the returned generator (first `__anext__()` call). This is correct behavior for this use case because `handler.py` line 376 immediately enters `async for event in session.send(message.text):` which triggers the first `__anext__()`.

### Question 2: Is the lock released on aclose()?

**Yes.** PEP 525 guarantees that `aclose()` on an async generator throws `GeneratorExit` into the generator body, which causes `async with` to execute its `__aexit__` (which calls `self._lock.release()`). The test `test_send_lock_released_on_generator_abandon` (line 1501) directly verifies this: it advances the generator once, calls `aclose()`, then confirms a new `send()` completes without deadlocking (2-second timeout).

### Question 3: Is is_processing returning the correct value?

**Yes.** `Pipeline.is_processing` (line 265-266):
```python
return self._lock.locked() or self._decomposer.is_processing
```
`asyncio.Lock.locked()` returns `True` when the lock is acquired and not yet released. The `or` with `self._decomposer.is_processing` is defensive: it covers the edge case where `ClaudeSession._processing` might be True from a previous cycle even after the Pipeline lock is released (though in practice this shouldn't happen since the decomposer is called within the lock).

The test `test_is_processing_true_while_lock_held` (line 1477) verifies this correctly: mock decomposer has `is_processing = False`, so the property returns True solely due to `_lock.locked()`.

### Question 4: Could the lock cause a deadlock?

**No.** Verified by grep: there is no recursive call to `self.send()` or `pipeline.send()` within `Pipeline.send()`. The internal call graph is:
- `send()` calls `self._classifier.classify()` (awaited, returns)
- `send()` calls `self._task_direct_monitored()` (yields events, does not call `send()`)
- `send()` calls `self._decomposer.route_task()` (awaited, returns)
- `_task_direct_monitored()` calls `self._decomposer.answer()` (yields events)

None of these paths re-enter `Pipeline.send()`. The lock is non-reentrant (`asyncio.Lock`), so a recursive call WOULD deadlock, but no such path exists.

### Question 5: Are the new tests actually testing what they claim?

**Yes, with one observation.**

1. **`test_send_serializes_concurrent_calls`** (line 1440): Uses `asyncio.gather` to launch two concurrent `send()` calls. The custom `_slow_answer` logs `start:{prompt}` and `end:{prompt}` with an `await asyncio.sleep(0)` between them. The assertion checks that `end:first` appears before `start:second` in execution_order. This correctly verifies serialization because the `asyncio.sleep(0)` yields control to the event loop, giving the second task a chance to start -- but the lock prevents it.

2. **`test_is_processing_true_while_lock_held`** (line 1477): Manually advances the generator with `__anext__()`, checks `is_processing`, then drains. Correct.

3. **`test_send_lock_released_on_generator_abandon`** (line 1501): Advances once, calls `aclose()`, then verifies a new `send()` completes within 2 seconds. Correct.

**Observation on test 1:** The test uses `_mock_classifier(intent="chat", confidence=0.95)` which routes through `_task_direct_monitored`. The custom `_slow_answer` replaces `decomposer.answer` but `decomposer.route_task` is also mocked (unused in the chat path). This is correct for the chat routing path. However, the test does NOT cover the task routing path (intent="task") which goes through `route_task` + `_task_direct_monitored`. The lock coverage is the same either way since both paths are inside `async with self._lock`, so this is not a gap -- just a completeness note.

### Question 6: Does the fix interact correctly with the PromotionEvent path?

**Yes.** In `_task_direct_monitored()` (line 227), when tool count exceeds the threshold, the code calls `await gen.aclose()` on the decomposer's inner generator and then `return`s. This `return` from `_task_direct_monitored` causes the `async for event in self._task_direct_monitored(resolved)` loop in `send()` (line 191) to complete normally, and then `send()` itself returns, exiting the `async with self._lock` block and releasing the lock. No issue here.

If the CALLER of `Pipeline.send()` calls `aclose()` during the PromotionEvent yield (line 217-221 of `_task_direct_monitored`), GeneratorExit propagates through both generators and the lock's `__aexit__` runs. Also correct.

### Question 7: Race condition between is_processing check and lock acquisition?

**No race in asyncio's cooperative model.** The `is_processing` check in `handler.py` (line 311) and the `session.send()` call (line 376) are in the same coroutine with no `await` between the check and the start of iteration. In single-threaded asyncio, there is no preemption between synchronous operations. The check is purely informational (sends a "queued" notification) and does not gate whether `send()` is called. Even if the check were stale, the lock itself provides the real serialization guarantee. This is a correct design.

### Blind spot: voice.py missing queued notification

`archon/chat/voice.py` line 184-188 calls `session.send(text)` without checking `session.is_processing` first. If a user sends a voice message while a text message is being processed (or vice versa), the voice handler will block on the lock (correct behavior -- serialization works) but will NOT send the "queued" notification to the user. The user gets no feedback that their voice message is waiting.

**Severity: Minor UX gap, not a correctness bug.** The lock still serializes correctly. The user just won't see the "your message is queued" notification when the conflict is voice-vs-text.

### Blind spot: FIFO ordering is not guaranteed by asyncio.Lock

The implementation doc (line 79) claims: "Serialization guarantees messages are processed one at a time in the order they acquire the lock." This is slightly misleading. `asyncio.Lock` does NOT guarantee FIFO ordering among waiters. CPython's current implementation uses a `collections.deque` which happens to be FIFO, but this is an implementation detail, not a documented guarantee. In practice, with the small number of concurrent messages expected (2-3), this is extremely unlikely to matter, and even out-of-order processing is vastly better than the original concurrent execution. **Not a bug, but the doc claim is technically stronger than what asyncio guarantees.**

### Summary

| Question | Answer | Confidence |
|----------|--------|------------|
| Lock correctly placed? | Yes | High |
| Lock released on aclose()? | Yes (PEP 525 guarantee) | High |
| is_processing correct? | Yes | High |
| Deadlock possible? | No (no recursive send) | High |
| Tests valid? | Yes | High |
| PromotionEvent interaction? | Correct | High |
| Race condition? | None (cooperative scheduling) | High |

**Action items (optional, non-blocking):**
1. Consider adding `is_processing` check + queued notification to `voice.py:_process_and_respond()` for UX parity with `handler.py`.
2. Soften the FIFO ordering claim in the doc to "in approximately the order they arrive" or note the CPython implementation detail.

## Post-DA Improvements (2026-03-11)

Three additional improvements applied to `pipeline.py` and `tests/ai/test_pipeline.py` after second and third DA review rounds:

1. **`gen.aclose()` timeout wrapper** (`_task_direct_monitored` finally block): Wrapped bare `await gen.aclose()` with `asyncio.wait_for(..., timeout=_ACLOSE_TIMEOUT_S)` + `except Exception` with `exc_info=True` warning log. Prevents pipeline from hanging indefinitely if the SDK stream's cleanup hangs. `_ACLOSE_TIMEOUT_S: float = 10.0` added as a module-level constant.

2. **`exc_info=True` in aclose warning**: When `gen.aclose()` fails for a non-timeout reason (RuntimeError etc.), the actual exception and traceback are now preserved in the warning log.

3. **Test `test_timeout_does_not_deadlock_next_call` strengthened**: Rewrote twice:
   - Round 2: Added real `asyncio.Lock` inside the mock `answer()` generator to prove `aclose()` is called
   - Round 3: Changed the test to route through `pipeline.send()` (not `_task_direct_monitored()` directly) so `pipeline._lock` is actually acquired and the `assert not pipeline._lock.locked()` assertion has real discriminating power

All 2315 tests pass. Status: **FULLY RESOLVED**.
