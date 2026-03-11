# Bug 19 — Cost data silently lost on orch/summary session resets + eager orch pre-start

Status: FIXED

## Bug 19a — Cost data lost on orch/summary session resets

### Description

When `_reset_orch_if_needed()` stops the old orch session and creates a new one, the old session's accumulated `_total_cost_usd` is permanently discarded. `Decomposer.usage_stats` reads only the CURRENT session's cost (`_sub_stats(self._orch_session)`). Pre-reset costs vanish. The same applies to summary session resets every 30 calls.

After the first orch reset (20 messages), the `/context` command will show orch cost as near-zero, even if significant API costs were incurred by the first 20 routing calls.

### Root cause

```python
# decomposer.py — _reset_orch_if_needed()
async def _reset_orch_if_needed(self) -> None:
    if self._orch_call_count < _ORCH_RESET_THRESHOLD:
        return
    old_session = self._orch_session
    self._orch_session = None
    await old_session.stop()    # old session's cost DISCARDED
    self._orch_call_count = 0
    await self._ensure_orch_session()

# Decomposer.usage_stats
def _sub_stats(s: ClaudeSession | None) -> dict:
    if s is None:
        return {"cost_usd": 0.0, ...}
    u = s.usage_stats or {}
    return {"cost_usd": u.get("total_cost_usd", 0.0), ...}   # only current session
```

### Fix

Add carryover accumulators in `__init__`:
```python
self._orch_cost_carryover: float = 0.0
self._summary_cost_carryover: float = 0.0
```

Before stopping old sessions, accumulate their cost:
```python
if self._orch_session is not None:
    stats = self._orch_session.usage_stats or {}
    self._orch_cost_carryover += stats.get("total_cost_usd", 0.0)
```

Include carryover in `usage_stats`:
```python
def _sub_stats(s, carryover=0.0):
    current = (s.usage_stats or {}).get("total_cost_usd", 0.0) if s else 0.0
    return {"cost_usd": current + carryover, ...}
```

---

## Bug 19b — Eager orch session pre-start during reset wastes Pipeline lock time

### Description

`_reset_orch_if_needed()` ends with `await self._ensure_orch_session()`, which eagerly creates and starts a fresh orch session immediately after stopping the old one. This SDK subprocess spawn (2-5 seconds) happens while `Pipeline._lock` is held (via `route_task()` → `_reset_orch_if_needed()`). All other messages queue behind this lock.

The eager pre-start is redundant: the very next line in `route_task()` calls `_ensure_orch_session()` anyway, which would lazy-create it identically without holding the Pipeline lock for 2-5 extra seconds.

### Root cause

```python
async def _reset_orch_if_needed(self) -> None:
    ...
    await old_session.stop()
    self._orch_call_count = 0
    await self._ensure_orch_session()   # <-- redundant eager pre-start
    # (route_task() will call _ensure_orch_session() again right after this returns)
```

### Fix

Remove the final `await self._ensure_orch_session()` from `_reset_orch_if_needed()`. Let the caller (`route_task()`) trigger lazy creation at the appropriate time.

---

## Observed symptoms

- Bug 19a: `/context` command shows orch/summary cost dropping to near-zero after every 20/30 messages
- Bug 19b: Every 20th message experiences a 2-5s delay (users notice sporadic slow responses)

## Tasks

1. Read `archon/ai/decomposer.py` and verify both issues
2. Write failing tests:
   - Bug 19a: after orch reset, verify `usage_stats` reflects accumulated pre-reset costs
   - Bug 19b: verify `_reset_orch_if_needed()` does NOT call `session.start()` eagerly (ensure session creation happens lazily in the next `route_task()` call)
3. Fix Bug 19a: add cost carryover fields and include them in `usage_stats`
4. Fix Bug 19b: remove the eager `_ensure_orch_session()` call from `_reset_orch_if_needed()`
5. Run full test suite

## AI Notes

### Fix applied (2026-03-11)

**Bug 19a changes**: `archon/ai/decomposer.py`:
- Added `_orch_cost_carryover: float = 0.0` and `_summary_cost_carryover: float = 0.0` to `__init__`
- `_reset_orch_if_needed()`: accumulates old session cost before stopping
- `_refresh_summary()`: accumulates old summary session cost before reset
- `_sub_stats()`: adds carryover to current session cost

**Bug 19b changes**: Removed `await self._ensure_orch_session()` from end of `_reset_orch_if_needed()`. Orch session now lazy-created by `route_task()` on next call (saves 2-5s while Pipeline lock is held).

**Tests added** in `tests/ai/test_decomposer.py`:
- `test_orch_reset_preserves_cost_in_usage_stats`
- `test_summary_reset_preserves_cost_in_usage_stats`
- `test_reset_orch_does_not_eagerly_start_new_session`
- 4 existing orch-reset tests updated to verify lazy behavior (not eager pre-start)

All 2361 tests pass.
