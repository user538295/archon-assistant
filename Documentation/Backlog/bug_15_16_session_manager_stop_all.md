# Bug 15/16 — SessionManager.stop_all() sequential stops + eviction race

Status: FIXED

## Bug 15 — stop_all() sequential stops: one hanging session blocks all others

### Description

`SessionManager.stop_all()` iterates sessions in a `for` loop, calling `await session.stop()` sequentially. With N users, each `session.stop()` (which calls `Pipeline.stop()` → `Decomposer.stop()` → up to 4 SDK disconnects) must complete before the next starts. The gateway wraps `session_manager.stop_all()` in a single 5-second `wait_for`. If user 1's session disconnect takes 5 seconds, the timeout fires and user 2's sessions are NEVER stopped — their SDK subprocesses are orphaned.

### Root cause

```python
# session_manager.py — stop_all()
async def stop_all(self) -> None:
    for task in self._timers.values():
        task.cancel()
    ...
    for session in sessions:
        await session.stop()   # SEQUENTIAL — blocks on each session
```

With a 5-second gateway timeout and N users, each user gets effectively `5/N` seconds for their stop. In practice: user 1 gets ~5s, user 2 gets 0s (timeout already fired).

### Fix

Replace sequential loop with `asyncio.gather(..., return_exceptions=True)` so all sessions stop concurrently within the same 5-second window.

---

## Bug 16 — stop() pops lock BEFORE awaiting session.stop() — eviction race

### Description

`SessionManager.stop()` (called by the inactivity eviction timer) pops the per-user lock and session from their dicts BEFORE calling `await session.stop()`. During the async teardown, if a new message arrives, `get_or_create(user_id)` sees no lock for that user, creates a fresh one, creates a new session, and starts a new SDK subprocess — while the old subprocess is still being stopped. Briefly: two Claude Code subprocesses run for the same user.

### Root cause

```python
# session_manager.py — stop()
async def stop(self, user_id: int) -> None:
    if user_id in self._timers:
        self._timers.pop(user_id).cancel()
    self._started_at.pop(user_id, None)
    self._locks.pop(user_id, None)          # lock removed HERE
    session = self._sessions.pop(user_id, None)  # session removed HERE
    if session is not None:
        await session.stop()               # <-- yields; new message can arrive
        ...
```

Between `self._locks.pop()` and `await session.stop()`, asyncio can yield. An incoming message sees no lock → creates a new session → starts new subprocess. Race window is small (~100ms) but real.

The same issue exists in `stop_all()`: it clears all locks BEFORE the stop loop:
```python
self._locks.clear()   # all locks removed
for session in sessions:
    await session.stop()   # in-flight handlers can create new sessions here
```

### Fix for Bug 16

Move the lock removal to AFTER `session.stop()` completes:

```python
async def stop(self, user_id: int) -> None:
    if user_id in self._timers:
        self._timers.pop(user_id).cancel()
    self._started_at.pop(user_id, None)
    session = self._sessions.pop(user_id, None)
    if session is not None:
        await session.stop()              # stop FIRST
        logger.info("Session stopped for user %d", user_id)
    self._locks.pop(user_id, None)        # remove lock AFTER stop completes
```

## Observed symptoms

- Bug 15: After SIGTERM with 2+ active users, some users' Claude Code subprocesses remain running after daemon exits
- Bug 16: Rare — duplicate Claude Code subprocess briefly visible in `ps aux` during inactivity eviction of an active user
- Bug 16 also in `stop_all()`: in-flight Telegram message handlers can create new sessions during shutdown's stop loop

## Related issues

- Bug 14 (CronScheduler orphaned tasks): same class of "cleanup doesn't wait for all async work"

## Tasks

1. Read `archon/ai/session_manager.py` and verify both issues
2. Write failing tests:
   - Bug 15: mock multiple sessions with `asyncio.sleep` delays, verify `stop_all()` runs them concurrently (total time < sum of delays)
   - Bug 16: verify lock is still present in dicts while `session.stop()` is awaited
3. Fix Bug 15: use `asyncio.gather(*(s.stop() for s in sessions), return_exceptions=True)`
4. Fix Bug 16: move `self._locks.pop()` to after `await session.stop()`
5. Fix the same race in `stop_all()`: clear locks AFTER all stops complete
6. Run full test suite

## AI Notes

### Fix applied (2026-03-11)

**Bug 15 fix**: `archon/ai/session_manager.py` — replaced sequential `for` loop in `stop_all()` with `asyncio.gather(*(s.stop() for _, s in sessions), return_exceptions=True)`. Sessions snapshotted and cleared first, then all stop concurrently.

**Bug 16 fix**: `stop()` — moved `self._locks.pop(user_id, None)` to after `await session.stop()`. Lock remains in `_locks` for the full duration of teardown.

**Tests added** in `tests/ai/test_session_manager.py`:
- `test_stop_all_runs_session_stops_concurrently` — 3 sessions sleeping 0.15s complete in <0.35s total
- `test_stop_lock_present_during_session_stop` — lock still in `_locks` while `session.stop()` is running

All 2361 tests pass.
