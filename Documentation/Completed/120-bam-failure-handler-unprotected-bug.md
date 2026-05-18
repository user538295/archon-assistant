# Bug 18 — BAM failure handler: unprotected history_manager.record_event() call

Status: FIXED

## Description

In `BackgroundAgentManager._run_agent()`, the `except Exception` failure handler calls `self._history_manager.record_event(...)` WITHOUT any try/except wrapping. If `record_event()` raises (e.g., disk full, permission error, filesystem timeout), the exception propagates out of the failure handler. The agent's status is already set to "failed" and the `_release_name`/`done.set()` in the outer `finally` block still run — but the `OSError` (or whatever) escapes as an unhandled task exception, confusingly replacing the original agent failure on the asyncio task.

## Observed symptoms

- Background agent fails; error logged correctly via `logger.exception()`
- `_notify_failure()` sends the ❌ Telegram notification (safe — internally guarded)
- `history_manager.record_event()` raises (e.g., disk full)
- The asyncio task ends with an unhandled `OSError` instead of the original agent exception
- `asyncio` logs: "Task exception was never retrieved" with the disk error, obscuring the real failure

## Root cause

Asymmetric error handling between the success and failure paths:

```python
# SUCCESS path — correctly wrapped
try:
    await self._history_manager.record_event(
        run.user_id,
        Response(content=f"[Agent {run.name}]\n{result}"),
    )
except Exception:
    logger.warning("Failed to record agent result to history", ...)

# FAILURE path — NOT wrapped
except Exception as exc:
    run.status = "failed"
    run.error = str(exc)
    logger.exception(...)
    await self._notify_failure(run)
    if self._history_manager is not None:
        await self._history_manager.record_event(   # <-- can raise, not caught
            run.user_id,
            ErrorEvent(message=f"Agent {run.name} failed: {run.error}"),
        )
```

The success path (written later) correctly added try/except around `record_event()`. The failure path was not updated to match.

## Reproduction scenario

1. Configure Archon with a history manager backed by a filesystem path.
2. Trigger a background agent failure (e.g., invalid task, SDK error).
3. Fill the disk or revoke write permissions on the history directory.
4. `record_event()` raises `OSError("No space left on device")`.
5. The OSError propagates out of the `except Exception` block.
6. `asyncio` logs an unhandled task exception with the disk error, not the original agent failure.

## Tasks

1. Read `archon/ai/background_agent_manager.py` and locate the `except Exception` handler in `_run_agent()`
2. Write a failing test:
   - Mock `history_manager.record_event()` to raise `OSError` during agent failure path
   - Verify the run completes cleanly with `run.status == "failed"`
   - Verify NO additional exception escapes from the task
3. Fix: wrap `history_manager.record_event()` in the failure handler with try/except, matching the success path pattern
4. Run test suite

## AI Notes

### Fix applied (2026-03-11)

**Changes**: `archon/ai/background_agent_manager.py` — wrapped `history_manager.record_event()` in the `except Exception` failure handler with `try/except Exception: logger.warning(...)`, matching the pattern already in the success path.

**Test added**: `TestHistoryManagerFailureGuard::test_history_manager_record_event_raises_in_failure_path_does_not_propagate` in `tests/ai/test_background_agent_manager.py`.

All 2361 tests pass.
