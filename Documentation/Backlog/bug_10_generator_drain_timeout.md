# Bug 10 — "Generator drain timed out after 5s" warning

Status: FIXED

## Description

From archon.log:
```
2026-03-10 19:38:30,223 archon WARNING Generator drain timed out after 5s — ResultMessage metadata may not have been captured for this turn
```

This occurred after a task was promoted to a background agent (Agent Jade). The generator drain timed out when trying to consume remaining SDK stream events after promotion.

## Root cause hypothesis

When `_task_direct_monitored` promotes a task to a background agent, it calls `await gen.aclose()` to close the decomposer's answer generator. But the underlying SDK stream may have more events queued. The "generator drain" logic waits up to 5s for the stream to complete naturally, then times out.

This likely means:
1. The SDK stream is still running when the generator is forcefully closed
2. The ResultMessage (which carries usage stats / cost) is not captured because the drain timed out

## Impact

- Usage stats for the current turn may be incomplete (cost tracking affected)
- Warning clutter in logs

## Tasks

1. Find the "generator drain" code — likely in claude_session.py or decomposer.py
2. Understand when this drain happens and why it times out
3. Assess if 5s is too short for this operation
4. Fix: either increase timeout, handle the timeout gracefully, or redesign how generator cleanup works when promoting
5. Write test
6. Fix

## DA Review (2026-03-11)

All findings below were verified by reading actual source code, not assumed.

### Verified (Bug 10 fix)

1. **`intercept_gen` is in scope in the finally block.** It is declared as `intercept_gen = None` at line 250 (before the `try`), then assigned at line 324 inside the try. The `if intercept_gen is not None` guard at line 336 correctly handles the case where the assignment was never reached.

2. **`await intercept_gen.aclose()` is called after drain timeout.** Confirmed at lines 350-353: after `except asyncio.TimeoutError` logs the warning (lines 343-347), `aclose()` is called inside a nested try/except that swallows all exceptions. This ensures the underlying `_intercept()` async generator and its `self._client.receive_response()` iteration are cleaned up.

3. **`aclose()` on an async generator stuck in `asyncio.sleep(60)` does NOT hang.** When `aclose()` is called, Python throws `GeneratorExit` into the generator at its current yield/await point. `asyncio.sleep()` is cancellable -- it raises `CancelledError`, which becomes `GeneratorExit` propagation. The generator exits immediately. Verified: no `try/except GeneratorExit` or `try/except CancelledError` in `_intercept()` that could suppress this.

4. **Double-close is harmless.** When `asyncio.wait_for` times out, it cancels `_drain()`. The cancellation propagates into `intercept_gen.__anext__()` inside the `async for` loop, which triggers `aclose()` on the generator as part of Python's async generator cleanup. The subsequent explicit `await intercept_gen.aclose()` at line 351 is therefore redundant but safe -- `aclose()` on an already-closed async generator is a documented no-op (PEP 525).

5. **Lock release is always reached.** `self._send_lock.release()` at line 368 is inside the outer `finally` block, outside the drain try/except. It executes regardless of drain outcome.

6. **Exception handling structure is correct.**
   - `except asyncio.TimeoutError` (line 343): handles only the timeout, calls `aclose()`
   - `except Exception` (line 354): catches any other drain error (generator already closed, SDK stream error)
   - No bare `except:` clauses -- `BaseException` (KeyboardInterrupt, SystemExit) correctly propagates

7. **Test coverage is adequate.**
   - `test_early_aclose_does_not_hang` (line 2255): creates a slow SDK stream (60s sleep), consumes one event, then calls `gen.aclose()` with an 8s timeout. Exercises the drain-timeout-then-aclose path.
   - `test_usage_stats_updated_after_early_generator_exit` (line 1774): exercises the happy path where drain succeeds and ResultMessage metadata is captured.

### Issues Found

1. **[INFO] Comment at line 355 is slightly misleading.** `"generator already closed; nothing to drain"` implies only the "already closed" case, but this `except Exception` also catches SDK errors during draining (e.g., `OSError` from transport). A more accurate comment would be `"drain failed or generator already closed; cleanup not critical"`.

2. **[INFO] The 5s timeout is hardcoded** (line 342). Not configurable. Acceptable for now.

### Conclusion

Fix is correct. The drain-timeout-then-aclose flow properly closes the underlying SDK stream/subprocess when the drain exceeds 5s. No critical or major issues.

## AI Notes

### Fix applied (2026-03-11)

**Root cause confirmed**: After the 5s drain timeout in `ClaudeSession.send()`'s `finally` block, the `_intercept()` async generator (wrapping `self._client.receive_response()`) was abandoned without being closed. The SDK stream continued running in the background, causing resource leaks and the "Generator drain timed out after 5s" warning.

**Fix** in `archon/ai/claude_session.py` drain `except asyncio.TimeoutError` handler:
- After logging the timeout warning, call `await intercept_gen.aclose()` inside a nested `try/except Exception`
- `aclose()` throws `GeneratorExit` into the generator, which propagates through any pending `asyncio.sleep()` / SDK await and terminates it immediately

**Tests added** in `tests/ai/test_claude_session.py`:
- `test_early_aclose_does_not_hang` — creates a slow SDK stream (60s sleep), consumes one event, then calls `gen.aclose()` under an 8s timeout, verifying no hang

