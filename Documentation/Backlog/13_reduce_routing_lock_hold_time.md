# 13 — Reduce Routing Lock Hold Time (Option C)

**Purpose**: Move routing setup (`_await_pending_summary`, `_reset_orch_if_needed`, `_ensure_orch_session`) out from under `Pipeline._lock`, so the lock is held only during the actual event-streaming phase. Reduces worst-case routing overhead from 63s to ~10s.
**Audience**: Archon developers
**Status**: Pending
**Priority**: P2 (follow-up to epic 12)
**Estimated Effort**: 3 tasks, ~1 day
**Depends on**: Epic 12 fully merged
**Last reviewed**: 2026-03-19
**Next review**: 2026-04-19

---

## Background

`Pipeline.send()` holds `self._lock` for its full duration. After epic 12, routing events are delivered to Telegram under that lock. Worst-case timeline under the lock:

| Phase | Timeout | Runs under lock? |
|---|---|---|
| `_await_pending_summary()` | 3s (`_SUMMARY_WAIT_TIMEOUT`) | Yes — unnecessary |
| `_reset_orch_if_needed()` | 30s (`_ORCH_RESET_TIMEOUT_S`) | Yes — unnecessary |
| `_ensure_orch_session()` | 30s (`_ORCH_RESET_TIMEOUT_S`) | Yes — unnecessary |
| `orch.send()` + Telegram delivery | 60s (`_ORCH_TIMEOUT_S`) + N×100ms | Yes — required |

The first three phases produce no events and have no Telegram interaction — holding the lock during them blocks other messages for up to 63s with no user-visible benefit.

**Goal**: Lock held only during `orch.send()` streaming. Worst case under lock: ~10s (8s Haiku + 5 events × 100ms Telegram).

---

## Architecture

Split `route_task()` into two methods:

```python
# Decomposer — runs BEFORE Pipeline._lock
async def prepare_routing(self) -> TaskOutput | None:
    """Setup phase. Returns fallback TaskOutput on any failure, None on success."""
    await self._await_pending_summary()
    try:
        async with asyncio.timeout(_ORCH_RESET_TIMEOUT_S):
            await self._reset_router_if_needed()
    except (TimeoutError, Exception) as exc:
        logger.warning("reset failed: %s — fallback", exc)
        return TaskOutput(scope="small", prompt="", is_fallback=True, fallback_reason="")
    try:
        async with asyncio.timeout(_ORCH_RESET_TIMEOUT_S):
            await self._ensure_router_session()
    except (TimeoutError, Exception) as exc:
        logger.warning("ensure session failed: %s — fallback", exc)
        return TaskOutput(scope="small", prompt="", is_fallback=True, fallback_reason="")
    return None  # ready to stream

# Decomposer — runs INSIDE Pipeline._lock
async def route_task(self, prompt: str) -> AsyncIterator[Event | TaskOutput]:
    """Streaming phase only. Call only after prepare_routing() returns None."""
    # Builds instruction, calls orch.send(), yields events + TaskOutput sentinel.
    # All existing timeout/fallback/cleanup logic for orch.send() unchanged.
    ...
```

```python
# Pipeline.send() — updated call site
routing_fallback = await self._decomposer.prepare_routing()  # before lock
async with self._lock:
    if routing_fallback is not None:
        task_output = routing_fallback  # setup failed, skip streaming
    else:
        task_output = None
        try:
            async for item in self._decomposer.route_task(prompt, instruction):
                if isinstance(item, TaskOutput):
                    task_output = item
                else:
                    yield dataclasses.replace(item, source="router")
        finally:
            await self._decomposer.route_task(...).__aclose__()  # see Task 1
    ...
```

**Race condition note**: Between `prepare_routing()` and `route_task()` (outside and inside the lock), another message could increment `_router_call_count` or modify `_pending_turns`. `_reset_router_if_needed()` runs in `prepare_routing()` but the count check should be re-verified at the start of `route_task()`. Add a `_routing_prepared: bool` guard on `Decomposer` set by `prepare_routing()` and cleared by `route_task()` — if `route_task()` is called without `prepare_routing()` having succeeded, raise `RuntimeError`.

---

## Tasks

### Task 1 — Split `Decomposer.route_task()` into prepare + stream

- **Files**:
  - [ ] `archon/ai/decomposer.py`:
    - Extract `prepare_routing() -> TaskOutput | None`: contains `_await_pending_summary()`, `_reset_router_if_needed()`, `_ensure_router_session()` with their timeouts and fallback returns
    - `route_task()` retains only: instruction building, `orch.send()` streaming, event yield loop, `_ORCH_TIMEOUT_S` timeout, `aclose()` in finally, `_pending_turns` append, `_schedule_summary()`
    - Add `_routing_prepared: bool = False` guard; `prepare_routing()` sets it True; `route_task()` asserts it and clears it

- **Tests**:
  - [ ] *Unit*: `test_prepare_routing_returns_none_on_success` — all setup succeeds → returns `None`
  - [ ] *Unit*: `test_prepare_routing_returns_fallback_on_reset_timeout` — `_reset_router_if_needed()` times out → returns `TaskOutput(is_fallback=True)`
  - [ ] *Unit*: `test_prepare_routing_returns_fallback_on_ensure_timeout` — `_ensure_router_session()` times out → returns `TaskOutput(is_fallback=True)`
  - [ ] *Unit*: `test_route_task_raises_if_not_prepared` — calling `route_task()` without prior `prepare_routing()` raises `RuntimeError`
  - [ ] *Unit*: `test_route_task_clears_prepared_flag` — after `route_task()` completes, `_routing_prepared` is False
  - [ ] *Unit*: `test_route_task_streams_events_only` — setup steps (`_await_pending_summary`, `_reset_router_if_needed`) no longer called inside `route_task()`

- **Checkpoint**: `uv run pytest tests/ai/test_decomposer.py -v`

---

### Task 2 — Update `Pipeline.send()` to call `prepare_routing()` before lock

- **Files**:
  - [ ] `archon/ai/pipeline.py`:
    - Call `await self._decomposer.prepare_routing()` before `async with self._lock:`
    - Inside the lock: check return value; if fallback, use directly; otherwise iterate `route_task()`
    - Wrap `async for` in `try/finally` with explicit `aclose()` call

- **Tests**:
  - [ ] *Unit*: `test_prepare_routing_called_before_lock` — assert `prepare_routing()` is awaited before lock acquired (use mock with ordering assertion)
  - [ ] *Unit*: `test_fallback_from_prepare_used_directly` — if `prepare_routing()` returns `TaskOutput`, assert `route_task()` never called
  - [ ] *Integration*: `test_lock_hold_time_excludes_setup` — mock `_await_pending_summary()` with 2s sleep, mock `orch.send()` with instant response; assert lock hold time ≈ orch.send() time, not 2s+

- **Checkpoint**: `uv run pytest tests/ai/test_pipeline.py -v`

---

### Task 3 — Update documentation

- **Files**:
  - [ ] `CLAUDE.md`: `Decomposer` entry — note `prepare_routing()` runs before lock, `route_task()` is streaming-only
  - [ ] `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`: update `Decomposer` description
  - [ ] Move this document to `Documentation/Completed/`

- **Verification**:
  - [ ] Manual timing: send a message that triggers routing reset; confirm Telegram receives response within 15s
  - [ ] `uv run pytest` — full suite green

---

## Dependency graph

```
Epic 12 merged
    └── Task 1 (split Decomposer)
            └── Task 2 (update Pipeline)
                    └── Task 3 (docs)
```

## Summary

| Task | Key change | Files |
|---|---|---|
| **1** | `prepare_routing()` + `route_task()` split in `Decomposer` | `decomposer.py` |
| **2** | `Pipeline.send()` calls setup before lock | `pipeline.py` |
| **3** | Documentation | `CLAUDE.md`, Architecture docs |
