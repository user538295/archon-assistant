# Bug 14 — JobScheduler.stop() orphans running scheduled job tasks

Status: FIXED

## Description

`JobScheduler.stop()` cancels only `self._task` (the 60-second tick loop), but does NOT cancel the tasks in `self._tasks` (the set of actively-running scheduled job tasks). When SIGTERM arrives while a scheduled job is executing, `stop()` returns almost immediately (tick loop cancels fast), but the job tasks continue running with their `ClaudeSession` subprocesses — they are permanently orphaned.

## Observed symptoms

- Scheduled job SDK subprocesses continue running after Archon daemon stops
- Gateway shutdown appears clean (no errors) but Claude Code subprocesses remain alive
- Logs show no cancellation of running job tasks at shutdown

## Root cause

In `archon/ai/job_scheduler.py`, `stop()` only cancels the scheduler loop:

```python
async def stop(self) -> None:
    if self._task is not None:
        self._task.cancel()
        ...
    # MISSING: self._tasks contains running job asyncio.Tasks — never cancelled
```

Compare with `BackgroundAgentManager.stop_all()` which correctly cancels all task refs:

```python
async def stop_all(self) -> None:
    tasks = []
    for run in list(self._runs.values()):
        if run.status == "running" and run._task_ref is not None:
            run._task_ref.cancel()
            tasks.append(run._task_ref)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

`JobScheduler` maintains `self._tasks: set[asyncio.Task]` of running job tasks but `stop()` ignores it entirely.

## Reproduction scenario

1. Configure a scheduled job that runs a prompt taking 30+ seconds.
2. Wait for the scheduled job to fire.
3. Send SIGTERM during job execution.
4. `job_scheduler.stop()` returns in <1s (tick loop cancelled).
5. The job's `ClaudeSession.send()` continues running; its `asyncio.Task` and Claude Code subprocess are orphaned.

## Key constraint violated

From CLAUDE.md: `stop_all()` must complete within 5 seconds. But the orphaned scheduled job tasks are not waited on, so "completion" is illusory — the SDK subprocess outlives the daemon's cleanup.

## Related issues

- BackgroundAgentManager.stop_all() correctly handles this; JobScheduler has the same problem but a simpler fix since it doesn't need per-task status tracking.

## Tasks

1. Read `archon/ai/job_scheduler.py` and verify `self._tasks` is maintained but not cancelled in `stop()`
2. Write a failing test:
   - Start a scheduled job with a delayed mock session
   - Call `stop()` while job is running
   - Verify the job task is cancelled (not still running)
3. Fix `stop()` to cancel all tasks in `self._tasks` and gather them before returning:
   ```python
   if self._tasks:
       for task in list(self._tasks):
           task.cancel()
       await asyncio.gather(*self._tasks, return_exceptions=True)
       self._tasks.clear()
   ```
4. Run test suite

## AI Notes

### Fix applied (2026-03-11)

**Changes**: `archon/ai/job_scheduler.py` — added 4 lines to `stop()` that cancel all tasks in `self._tasks` and gather them with `return_exceptions=True` before clearing the set.

**Test added**: `test_stop_cancels_running_job_tasks` in `tests/schedule/test_job_scheduler.py` — starts a long-running job, calls `stop()`, verifies the job task is cancelled.

All 2361 tests pass.
