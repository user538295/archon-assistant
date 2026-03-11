# Bug 13 — `_send_lock` permanently held when CancelledError occurs during drain

Status: FIXED

## Description

When a background agent task is cancelled (e.g., at shutdown or user cancellation) while `ClaudeSession.send()` is in its `finally` block running the drain, a `CancelledError` (which is a `BaseException`, not an `Exception`) propagates through the drain's `await asyncio.wait_for(...)` call and bypasses both except clauses. The `_send_lock.release()` and `self._processing = False` calls at the end of the finally block are **never executed**.

Result: the lock is permanently held. Every subsequent `send()` call for that user hangs forever at `await self._send_lock.acquire()`.

## Observed symptoms

- After task cancellation during drain, all subsequent messages for the user receive no response
- `is_processing` returns `True` forever (stuck session)
- `SessionManager._evict_after()` defers eviction indefinitely because `is_processing` never clears
- Session is never cleaned up — memory leak + permanent deadlock per user

## Root cause

In `archon/ai/claude_session.py`, the `send()` method's `finally` block structure:

```python
finally:
    if intercept_gen is not None:
        try:
            async def _drain():
                async for _ in intercept_gen:
                    pass
            await asyncio.wait_for(_drain(), timeout=5.0)   # <-- CancelledError can escape here
        except asyncio.TimeoutError:    # catches TimeoutError only
            ...
            try:
                await asyncio.wait_for(intercept_gen.aclose(), timeout=2.0)
            except Exception:           # catches Exception, NOT BaseException
                ...
        except Exception:               # catches Exception, NOT BaseException
            pass
    # ...reminder tracking...
    self._processing = False            # SKIPPED on CancelledError
    self._send_lock.release()          # SKIPPED on CancelledError — permanent deadlock
```

`asyncio.CancelledError` in Python 3.9+ is a `BaseException`, not an `Exception`. When it propagates through the drain's `await`, neither `except asyncio.TimeoutError` nor `except Exception` catches it. Execution jumps out of the drain block entirely, skipping the lock release.

## Scope

This bug also manifests in the orch session in `archon/ai/decomposer.py` (`route_task()`), which uses the same `ClaudeSession.send()` code. A deadlocked orch session means:
1. Next `route_task()` call hangs at `_send_lock.acquire()`
2. `route_task()` is called while `Pipeline._lock` is held
3. `Pipeline._lock` is held forever → no messages processed for that user

The orch session is a long-lived singleton; a deadlock there blocks ALL future task routing for the user.

## Reproduction scenario

1. Start Archon with a background agent running.
2. Send SIGTERM (normal shutdown) while the agent's `ClaudeSession.send()` is mid-stream.
3. `stop_all()` cancels the agent task → CancelledError injected.
4. `send()` finally block starts draining. `CancelledError` escapes the drain's except clauses.
5. `_send_lock.release()` is skipped.
6. For the main session: start Archon again (or if session reused) — all messages deadlock.

More specifically, triggered by:
- Any `asyncio.wait_for(gen.aclose(), timeout=N)` call in `Pipeline._task_direct_monitored` that fires while the generator's `send()` finally block is running the drain.

## Related issues

- Bug 10 (generator drain timeout): that fix added the 5s drain timeout, but missed that `CancelledError` bypasses all the existing except clauses. BUG-13 is a regression introduced by the Bug 10 fix structure.
- Bug 11 (session disconnect cancel scope): similar class of CancelledError handling issue.

## Tasks

1. Verify the bug in `archon/ai/claude_session.py` finally block
2. Write a failing test that:
   - Injects CancelledError during the drain phase of `send()`
   - Verifies `_send_lock` is NOT permanently held afterward
   - Verifies a subsequent `send()` call can proceed
3. Fix by wrapping `self._processing = False` and `self._send_lock.release()` in a nested `finally` clause that catches `BaseException` (not just `Exception`)
4. Run full test suite

## AI Notes

### Fix approach (2026-03-11)

Move the reminder tracking + lock release into a nested `finally` inside the existing `finally` block:

```python
finally:
    try:
        # ... drain logic (unchanged) ...
    except asyncio.TimeoutError:
        ...
    except Exception:
        pass
    finally:
        # GUARANTEED to run even on CancelledError/BaseException
        if self._reminder is not None and _user_message_queued:
            self._reminder.record_message()
            ...
        self._processing = False
        self._send_lock.release()
```

The nested `finally` ensures the lock release runs regardless of whether CancelledError (or any other BaseException) escapes the drain block.

### Fix applied (2026-03-11)

**Changes**: `archon/ai/claude_session.py` — restructured `send()` finally block with a nested `try/finally`. The drain logic runs in the outer `try`, while `_processing = False` and `_send_lock.release()` are in the inner `finally` that runs unconditionally on any exception including `CancelledError`.

**Test added**: `test_send_lock_released_after_cancelled_error_in_drain` in `tests/ai/test_claude_session.py` — injects `CancelledError` during drain, verifies lock is released and next `send()` is not deadlocked.

All 2361 tests pass.
