# FEAT-027-P8 — Watch Mode
**Purpose**: Automatically trigger incremental re-indexing when files in collection source directories change, eliminating the need for manual `archon rag sync` runs
**Audience**: Users with active project directories, sessions collection (daily history files), or documentation under active editing; users who want zero-maintenance RAG freshness
**Status**: To Do

---

## Background
Phases 1–7 added background indexing, progress visibility, resumability, incremental change detection, Telegram notifications, `archon doctor` integration, and ETA display. All of these improve visibility and efficiency of _manually triggered_ syncs. The user still needs to run `archon rag sync` or wait for the daemon restart to pick up new or modified files.

Phase 8 adds a filesystem watcher: when `[rag] watch = true`, a `watchdog`-based observer monitors each collection source directory. File changes are debounced (5s window) and trigger the Phase 4 incremental sync machinery (`_check_collection_changes` + `_apply_collection_changes`). The per-collection lock from Phase 1 prevents watcher-triggered and manual syncs from conflicting.

Full feature spec: `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 8 section.

## Goal
When `[rag] watch = true`, file additions, modifications, and deletions in any collection source directory are detected within 5 seconds and trigger an incremental re-index of the affected collection. `archon rag status` shows a `(watch)` indicator for collections with an active watcher. The `rag_status` MCP tool exposes a `watching` field per collection so Claude can report watch mode status.

---

## Scope

### In Scope
- `watchdog>=3.0` added to `[rag]` optional dependencies in `pyproject.toml`
- `watch: bool = False` field in `RagConfig` + loader
- `archon/rag/watcher.py` (new) — `_DebounceHandler`, `CollectionWatcher`, `WatcherManager`
- `RagCollectionSync.sync_collection(name, source_path)` — new public method for watcher-triggered targeted incremental sync; reuses `_check_collection_changes` + `_apply_collection_changes` from Phase 4
- `server.py` — instantiate and start `WatcherManager` when `cfg.rag.watch = True`; stop on shutdown
- `archon rag status` — shows `(watch)` suffix in status column for collections when watch mode is active
- `rag_status` MCP — adds `watching: bool` field to each collection dict

### Out of Scope
- Watch mode on Windows — `watchdog` supports it but is untested in CI; code runs but has no dedicated test coverage in this phase
- Watching remote (NFS/SMB) directories — explicitly unsupported; local directories only (enforced by `watchdog` FSEvents/inotify limitations)
- Configurable debounce interval per collection — 5s is fixed in this phase
- Real-time push to Telegram on each file change — too noisy; status is queryable, not pushed per-file
- Watcher health monitoring (`archon doctor`) — out of scope; watch status is visible via `archon rag status` only

---

## Acceptance criteria
- [ ] `watchdog>=3.0` in `[rag]` optional deps; installable with `uv sync --extra rag`
- [ ] `watch: bool = False` in `RagConfig`; reads `[rag] watch = true` from `config.toml`
- [ ] `CollectionWatcher` debounces file events by 5s per collection; rapid successive writes (even to different files) trigger only one ingest
- [ ] `CollectionWatcher.start()` logs a warning and returns without crashing when `watchdog` is not installed
- [ ] `WatcherManager.stop_all()` stops all watchers cleanly (async, uses `asyncio.to_thread`); no thread leaks
- [ ] `RagCollectionSync.sync_collection()` runs `_check_collection_changes` + `_apply_collection_changes` for the specified collection; is a no-op when `_state_store is None`
- [ ] `server.py` starts `WatcherManager` for all collections when `cfg.rag.watch = True`; skips it when `False`
- [ ] `server.py` calls `await watcher_manager.stop_all()` in the shutdown path; `stop_all()` drains in-flight sync coroutines (waits up to 10 seconds) before disconnecting the pipeline store
- [ ] New file added to a watched directory → ingested; `done` state updated within 5s + ingest time
- [ ] Existing file modified → old chunks removed; re-ingested; mtime updated in state
- [ ] File deleted from watched directory → chunks removed; path cleared from state
- [ ] `archon rag sync` (manual) does not conflict with an active watcher; per-collection lock serialises them
- [ ] `archon rag status` shows `done (watch)` (or `partial (watch)`) for collections when `watch=True` and service is running
- [ ] `rag_status` MCP JSON includes `watching: true` on each collection dict when `watch=True`; `watching: false` otherwise
- [ ] All existing tests continue to pass
- [ ] `_RAG_STATUS_SCHEMA` description mentions the `watching` field

---

## What does NOT change
- `CollectionProgress` and `IndexingState` dataclasses — no new fields
- State file schema — no new persisted fields; watcher status is ephemeral (derived from config + runtime)
- Phase 4 `_check_collection_changes` + `_apply_collection_changes` — called unchanged
- Per-collection `asyncio.Lock` in `RagCollectionSync` — unchanged; `sync_collection()` reuses it via `_apply_collection_changes`
- `archon rag sync` CLI behaviour — unchanged
- `archon doctor` output — unchanged (Phase 6 already handles in-progress state; no watch-specific additions)

---

## Known limitations / accepted trade-offs
- Watcher-triggered sync calls `_check_collection_changes` on the entire collection directory (not just the one changed file). This is safe and consistent with Phase 4 but slightly over-scans on large collections. Targeted single-file sync is a future optimisation.
- Debounce is per-collection (a single 5s timer per collection). Any file event within a collection resets the timer; all changes within the 5s window are merged into one sync call. Rapid edits to different files within the same collection are therefore coalesced into one sync.
- `watchdog` fires events in a background thread. Bridging to asyncio via `asyncio.run_coroutine_threadsafe` is safe but means event-loop scheduling latency (typically < 1ms) is added to the debounce.
- `watching: true` in `rag_status` MCP and `(watch)` in `archon rag status` indicate that `[rag] watch = true` is configured AND the RAG service is running. They do not confirm that individual watcher threads are healthy. If `watchdog` is not installed, the watcher will log a warning at startup and not actually watch.
- Directory removal/unmounting: `watchdog` raises `OSError` when the watched directory disappears. `CollectionWatcher` catches this, logs a warning, and stops watching that directory without crashing.
- On process restart: watcher re-initialises for all collections on startup — no "resume watching" needed since watchdog is stateless.
- Shutdown sets `_shutting_down=True` before stopping observer threads, preventing new syncs from starting. Already-submitted sync coroutines (from timers that fired in the narrow window before observer threads stop) drain with a 10-second timeout before the pipeline store disconnects.

---

## Architecture

### New file: `archon/rag/watcher.py`

Top-level lazy import guard:
```python
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False
```

#### `_DebounceHandler`
```python
class _DebounceHandler(FileSystemEventHandler):
    """Per-collection debounce handler. Fires async callback after quiet period."""

    def __init__(
        self,
        async_callback: Callable[[str], Coroutine],  # (collection_name) -> None
        loop: asyncio.AbstractEventLoop,
        collection_name: str,
        debounce_seconds: float = 5.0,
    ) -> None: ...

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Called from watchdog thread. Schedules/resets debounce timer."""
        # Skip directory events; extract path from src_path (or dest_path for moves)
        # Cancel existing timer (if any); start new threading.Timer(debounce_seconds, _fire)

    def _fire(self) -> None:
        """Called from threading.Timer thread after debounce. Submits coroutine to asyncio loop."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._async_callback(self._collection_name), self._loop
            )
            future.add_done_callback(_log_future_exception)
        except RuntimeError:
            logger.warning("Event loop closed, skipping watch-triggered sync for %r", self._collection_name)
        finally:
            with self._lock:
                self._timer = None

    def cancel_all(self) -> None:
        """Cancel the pending timer (if any). Called on stop."""
```

`_DebounceHandler` holds a single timer (`_timer: threading.Timer | None`) and a `_lock: threading.Lock` for thread-safety. Any file event in the collection cancels the current timer and starts a new one — all changes within the debounce window are coalesced into one sync call. A module-level `_log_future_exception(future)` helper logs at ERROR if the coroutine raised an exception, preventing silent swallowing of sync errors.

#### `CollectionWatcher`
```python
class CollectionWatcher:
    def __init__(
        self,
        collection_name: str,
        source_path: Path,
        on_change: Callable[[str], Coroutine],  # (collection_name) -> None
        loop: asyncio.AbstractEventLoop,
        debounce_seconds: float = 5.0,
    ) -> None: ...

    def start(self) -> None:
        """Start the watchdog observer. No-op with warning if watchdog unavailable."""

    def stop(self) -> None:
        """Stop observer and cancel pending timers. Blocks up to 5s for observer join.
        Logs a warning if the observer thread is still alive after the join timeout."""

    def is_alive(self) -> bool:
        """Return True if the observer is running."""
```

#### `WatcherManager`
```python
class WatcherManager:
    def __init__(
        self,
        on_change: Callable[[str], Coroutine],  # (collection_name) -> None
        loop: asyncio.AbstractEventLoop,
        debounce_seconds: float = 5.0,
    ) -> None:
        # Stores on_change; wraps it internally to track active syncs and honour _shutting_down
        # _active_syncs: set[asyncio.Task[None]] — internal
        # _shutting_down: bool = False — prevents new syncs during shutdown
        ...

    def add(self, collection_name: str, source_path: Path) -> None:
        """Add and start a watcher. No-op if already watching this collection."""

    async def stop_all(self) -> None:
        """Set _shutting_down=True; stop all watchers concurrently via asyncio.gather + asyncio.to_thread;
        drain in-flight syncs via asyncio.wait(_active_syncs, timeout=10.0); clear registry."""

    def is_watching(self, collection_name: str) -> bool:
        """Return True if the collection has a live watcher."""

    def watching_names(self) -> set[str]:
        """Return set of collection names with live watchers."""
```

When a file-change callback fires, `WatcherManager` wraps the provided `on_change` internally:
1. Checks `self._shutting_down` — if `True`, skips the sync and returns
2. Gets `current_task()` and adds it to `_active_syncs`
3. Calls the user's `on_change(col_name)` inside `try/except Exception` (logging errors)
4. Removes the task from `_active_syncs` in the `finally` block

### New method: `RagCollectionSync.sync_collection()`
```python
async def sync_collection(
    self,
    collection_name: str,
    source_path: Path,
) -> None:
    """Re-check and apply incremental changes for one collection (watcher-triggered).

    No-op when _state_store is None (change detection unavailable).
    Reuses _check_collection_changes + _apply_collection_changes from Phase 4.
    The per-collection asyncio.Lock is acquired inside _apply_collection_changes.
    """
```

Implementation:
1. Return early if `_state_store is None`
2. Read state, get `file_mtimes` and model/chunk-size metadata for the collection
3. Call `_check_collection_changes(name, source_path, file_mtimes, ...)`
4. If any changes found: call `await _apply_collection_changes(name, source_path, new_f, changed_f, deleted_p, file_mtimes)`
5. Log at INFO level on entry and after changes applied

### Changes in `archon/rag/server.py`

After the sync setup block, before `app = create_app(...)`:
```python
watcher_manager: WatcherManager | None = None
if cfg.rag.watch:
    from archon.rag.watcher import WatcherManager  # lazy import
    desired = sync.build_desired(cfg.rag.collections)
    loop = asyncio.get_running_loop()

    async def _on_change(col_name: str) -> None:
        path_str = desired.get(col_name)
        if path_str:
            await sync.sync_collection(col_name, Path(path_str))

    watcher_manager = WatcherManager(on_change=_on_change, loop=loop)
    for name, path_str in desired.items():
        watcher_manager.add(name, Path(path_str))
    logger.info("Watch mode active: monitoring %d collection(s)", len(desired))
```

Note: `WatcherManager` wraps this callback internally with error handling, `_shutting_down` guard, and in-flight task tracking. The closure itself is intentionally kept simple.

In `finally` block (before `pipeline.store.disconnect()`):
```python
if watcher_manager is not None:
    await watcher_manager.stop_all()
```

Note: `stop_all()` sets `_shutting_down=True`, stops all watchers concurrently, and drains in-flight sync coroutines (waits up to 10 seconds) before returning.

### Changes in `archon/config/loader.py`

In `RagConfig` dataclass, add:
```python
watch: bool = False
```

In loader `rag = RagConfig(...)` block, add:
```python
watch=bool(rag_data.get("watch", RagConfig.watch)),
```

### Changes in `archon/cli/rag_cmd.py`

Note: `watching` reflects config + process liveness, not individual watcher thread health. If `watchdog` is not installed or an observer thread crashed, `watching` may still report `True`. This is a known limitation documented in "Known limitations".

Modify `_run_status` to pass `watching` flag:
```python
watching = info.running and cfg.rag.watch
return _print_progress_table(state, collections, watching=watching)
```

Modify `_print_progress_table` signature:
```python
def _print_progress_table(
    state: IndexingState,
    collections: list,
    watching: bool = False,
) -> int:
```

Inside the loop, after `status_str` is set and before `print()`:
```python
if watching and progress is not None and progress.status in (
    IndexingStatus.DONE, IndexingStatus.IN_PROGRESS
):
    status_str += " (watch)"
```

### Changes in `archon/ai/archon_toolkit_rag.py`

Note: `watching` reflects config + process liveness, not individual watcher thread health. If `watchdog` is not installed or an observer thread crashed, `watching` may still report `true`. This is a known limitation documented in "Known limitations".

In `_handle_rag_status`, after building `col_dicts` for LanceDB collections, add `watching` to each dict:
```python
watch_mode = getattr(cfg.rag, "watch", False)
for d in col_dicts:
    d["watching"] = watch_mode
```

Update `_RAG_STATUS_SCHEMA` description to end with:
```
...includes optional eta_seconds (integer, estimated seconds remaining) for in-progress collections, and watching (bool) indicating whether file-system watch mode is active for this collection.
```

---

## Tests

### Task 8.1 tests — `tests/config/test_rag_config.py`
- **test_rag_config_watch_defaults_false** (unit): `RagConfig()` → `watch == False`
- **test_rag_config_watch_reads_from_toml** (unit): TOML with `[rag] watch = true` → `cfg.rag.watch == True`

### Task 8.2 tests — `tests/rag/test_watcher.py`
- **test_debounce_handler_schedules_callback** (unit): Mock `asyncio.run_coroutine_threadsafe`; fire `on_any_event` → timer scheduled; timer fires → callback submitted to loop
- **test_debounce_handler_resets_timer_on_rapid_events** (unit): Fire event for two DIFFERENT file paths within debounce window → first timer cancelled; single second timer active; callback fires once (per-collection timer resets regardless of which file changed)
- **test_debounce_handler_skips_directory_events** (unit): `is_directory=True` event → no timer scheduled
- **test_debounce_handler_cancel_all** (unit): Active timer → `cancel_all()` → timer cancelled; `_timer` set to `None`; no callback fires
- **test_debounce_handler_handles_moved_event** (unit): `on_any_event` with `event_type="moved"` and `dest_path="/some/file.md"` → timer scheduled (uses `dest_path` as the trigger)
- **test_debounce_handler_fire_wraps_loop_closed_error** (unit): Patch `asyncio.run_coroutine_threadsafe` to raise `RuntimeError`; call `_fire()` → no exception propagates; warning logged
- **test_log_future_exception_logs_on_error** (unit): Create a `Future` manually; set exception via `future.set_exception(ValueError("fail"))`; call `_log_future_exception(future)` → ERROR logged
- **test_log_future_exception_silent_on_success** (unit): Create a `Future`; set result `None`; call `_log_future_exception(future)` → no ERROR log emitted
- **test_collection_watcher_start_stop** (unit): Mock `Observer`; `start()` → observer started + scheduled; `stop()` → handler cancelled + observer stopped + joined
- **test_collection_watcher_is_alive** (unit): Before `start()` → `False`; after `start()` with mock alive observer → `True`; after `stop()` → `False`
- **test_collection_watcher_start_no_watchdog** (unit): Temporarily patch `_WATCHDOG_AVAILABLE = False`; `start()` → logs warning, does not raise, `is_alive()` returns `False`
- **test_collection_watcher_start_nonexistent_directory** (unit): Mock `Observer.start()` to raise `OSError`; `CollectionWatcher.start()` → logs warning; does not raise; `is_alive()` returns `False`
- **test_collection_watcher_stop_join_timeout_warning** (unit): Mock `observer.join()` to return without terminating thread; mock `observer.is_alive()` to return `True` after join; `stop()` called → warning logged: `"Observer thread did not terminate within 5s"`
- **test_collection_watcher_directory_disappears** (unit): Observer raises `OSError` after start → watcher logs warning; `stop()` does not propagate exception
- **test_collection_watcher_integration** (integration, optional): Use `tmp_path`; real `CollectionWatcher` with debounce `0.1s`; write a file; run asyncio loop for 0.5s; verify `on_change` callback called. Mark `@pytest.mark.integration`; skip if `watchdog` not installed.

### Task 8.3 tests — `tests/rag/test_watcher.py`
- **test_watcher_manager_add_starts_watcher** (unit): `add(name, path)` → `CollectionWatcher.start()` called; `is_watching(name) == True`
- **test_watcher_manager_add_is_idempotent** (unit): `add(name, path)` twice → only one watcher created; `CollectionWatcher.start()` called once
- **test_watcher_manager_stop_all** (unit, async test, `@pytest.mark.asyncio`): Two watchers added → `stop_all()` → both stopped; `watching_names()` returns empty set
- **test_watcher_manager_watching_names** (unit): Two collections added, one stopped manually → `watching_names()` returns only the live one

### Task 8.4 tests — `tests/rag/test_sync.py`
- **test_sync_collection_no_state_store** (unit): `RagCollectionSync` with `state_store=None`; `sync_collection(name, path)` → returns without calling `_check_collection_changes`
- **test_sync_collection_no_changes** (unit): `_check_collection_changes` returns empty lists → `_apply_collection_changes` NOT called
- **test_sync_collection_with_new_file** (unit): `_check_collection_changes` returns `new_files=[some_file]` → `_apply_collection_changes` called with the new file
- **test_sync_collection_with_deleted_file** (unit): `_check_collection_changes` returns `deleted_paths=["old"]` → `_apply_collection_changes` called with the deletion
- **test_sync_collection_lock_respected** (unit/integration): Lock already held → `sync_collection` waits; no concurrent mutation of state

### Task 8.5 tests — `tests/rag/test_server.py`
- **test_server_starts_watcher_manager_when_watch_true** (unit): `cfg.rag.watch = True`; mock `WatcherManager`; `main()` partial → `WatcherManager.__init__` called; `add()` called for each collection
- **test_server_skips_watcher_manager_when_watch_false** (unit): `cfg.rag.watch = False`; mock `WatcherManager`; `main()` partial → `WatcherManager.__init__` NOT called
- **test_server_stops_watcher_on_shutdown** (unit, async test, `@pytest.mark.asyncio`): `cfg.rag.watch = True`; mock `WatcherManager`; trigger shutdown path → `stop_all()` called
- **test_server_on_change_handles_sync_exception** (unit, async test, `@pytest.mark.asyncio`): `sync.sync_collection` raises `RuntimeError`; call `_on_change(name)` directly → exception caught; error logged; no exception propagates

### Task 8.6 tests — `tests/cli/test_rag_cmd.py` + `tests/ai/test_archon_toolkit_rag.py`
- **test_status_shows_watch_indicator_when_watching** (unit): `_print_progress_table(state, [], watching=True)`; DONE collection → output contains `(watch)`
- **test_status_no_watch_indicator_when_not_watching** (unit): `_print_progress_table(state, [], watching=False)`; DONE collection → output does NOT contain `(watch)`
- **test_status_watch_indicator_for_partial_but_not_failed** (unit): Parametrized — `IN_PROGRESS` → shows `(watch)`; `FAILED` → no `(watch)`; `PENDING` → no `(watch)` (watcher only annotates active/done states)
- **test_rag_status_mcp_includes_watching_true** (unit): `cfg.rag.watch = True`; parsed JSON collection dict contains `"watching": True`
- **test_rag_status_mcp_includes_watching_false** (unit): `cfg.rag.watch = False`; parsed JSON collection dict contains `"watching": False`

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 8 section: mark ✅ Done when complete
- [ ] `Documentation/Architecture/` (component catalog or config reference): document `[rag] watch` key
- [ ] `examples/config.toml.example`: add `# watch = false  # set true to auto-reindex on file changes`

---

## Task breakdown

### Phase 8 — Watch mode
> **Releasable**: after Task 8.5 — watch mode is fully functional; Task 8.6 adds status visibility on top

#### Task 8.1 — `watchdog` dependency + `watch` config field
- [x] **File**: `pyproject.toml`, `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - In `pyproject.toml` `[project.optional-dependencies] rag`, add `"watchdog>=3.0"` — alongside `lancedb`, `fastembed`, etc.
  - In `archon/config/loader.py` `RagConfig` dataclass, add `watch: bool = False`
  - In the loader block where `rag = RagConfig(...)` is constructed (around line 659), add `watch=bool(rag_data.get("watch", RagConfig.watch))`
  - No validation needed (bool field, any truthy value accepted)
- **Releasable**: `cfg.rag.watch` is accessible; `uv sync --extra rag` installs watchdog
- **Tests (TDD)** — `tests/config/test_rag_config.py`:
  - Unit: `test_rag_config_watch_defaults_false` — `RagConfig()` → `watch == False`
  - Unit: `test_rag_config_watch_reads_from_toml` — write `[rag]\nwatch = true` to tmp TOML → `load_config()` returns `cfg.rag.watch == True`
  - Checkpoint: `uv run pytest tests/config/ -v --no-cov -k "watch"`

#### Task 8.2 — `_DebounceHandler` + `CollectionWatcher` in `archon/rag/watcher.py`
- [x] **File**: `archon/rag/watcher.py` (new)
- **Depends on**: Task 8.1 (watchdog dep)
- **Description**:
  - Module-level lazy import guard:
    ```python
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileSystemEvent
        _WATCHDOG_AVAILABLE = True
    except ImportError:  # pragma: no cover
        _WATCHDOG_AVAILABLE = False
        FileSystemEventHandler = object  # type: ignore[assignment,misc]
    ```
  - `_DebounceHandler(FileSystemEventHandler)`:
    - `__init__(self, async_callback: Callable[[str], Coroutine], loop: asyncio.AbstractEventLoop, collection_name: str, debounce_seconds: float = 5.0)`
    - `_timer: threading.Timer | None` — single per-collection timer
    - `_lock: threading.Lock` — protects `_timer`
    - `on_any_event(self, event) -> None`: skip `event.is_directory`; use `event.dest_path if event.event_type == "moved" else event.src_path` (path used only for logging); under lock, cancel existing timer (if any); start new `threading.Timer(self._debounce_seconds, self._fire)`; store in `self._timer`
    - `_fire(self) -> None`: wrap `asyncio.run_coroutine_threadsafe(self._async_callback(self._collection_name), self._loop)` in `try/except RuntimeError` — if the event loop is closed, log a warning (`"Event loop closed, skipping watch-triggered sync for %r"`) and skip submitting the coroutine. On success, add `future.add_done_callback(_log_future_exception)` where `_log_future_exception` is a module-level helper that calls `future.exception()` and logs at ERROR if non-None. In a `finally` block, under lock, set `self._timer = None` to release the timer reference and prevent memory leak. This fires from a `threading.Timer` thread — must not touch asyncio directly.
    - `cancel_all(self) -> None`: acquire lock; cancel timer if present; set `self._timer = None`
  - `CollectionWatcher`:
    - `__init__(self, collection_name: str, source_path: Path, on_change: Callable[[str], Coroutine], loop: asyncio.AbstractEventLoop, debounce_seconds: float = 5.0)`
    - `start(self) -> None`: if not `_WATCHDOG_AVAILABLE`, log warning + return; create `_DebounceHandler`; create `Observer()`; `observer.schedule(handler, str(source_path), recursive=True)`; call `observer.start()` inside a `try/except OSError` — if an OS error occurs (e.g., path not found), log warning and return without setting `_observer`
    - `stop(self) -> None`: `handler.cancel_all()` if handler exists; `observer.stop() + observer.join(timeout=5.0)` if observer exists; after join, check `observer.is_alive()` — if still alive, log warning `"Observer thread did not terminate within 5s for collection %r"`; log warning if directory disappeared (catch `OSError`)
    - `is_alive(self) -> bool`: `self._observer is not None and self._observer.is_alive()`
- **Releasable**: `CollectionWatcher` is importable and unit-testable; event debounce works
- **Tests (TDD)** — `tests/rag/test_watcher.py`:
  - Unit: `test_debounce_handler_schedules_callback` — mock `asyncio.run_coroutine_threadsafe`; call `on_any_event` with file event → `threading.Timer` started; manually fire timer → coroutine submitted to loop
  - Unit: `test_debounce_handler_resets_timer_on_rapid_events` — call `on_any_event` for two DIFFERENT file paths within debounce window → first timer cancelled; single second timer active; callback fires once (per-collection timer is reset regardless of which file changed)
  - Unit: `test_debounce_handler_skips_directory_events` — `on_any_event` with `is_directory=True` → no timer created
  - Unit: `test_debounce_handler_cancel_all` — active timer; `cancel_all()` → timer cancelled; `_timer` set to `None`; no callback fires
  - Unit: `test_debounce_handler_handles_moved_event` — `on_any_event` with `event_type="moved"` and `dest_path="/some/file.md"` → timer scheduled (uses `dest_path` as the trigger)
  - Unit: `test_debounce_handler_fire_wraps_loop_closed_error` — patch `asyncio.run_coroutine_threadsafe` to raise `RuntimeError`; call `_fire()` → no exception propagates; warning logged
  - Unit: `test_log_future_exception_logs_on_error` — create a `Future` manually; set exception via `future.set_exception(ValueError("fail"))`; call `_log_future_exception(future)` → ERROR logged
  - Unit: `test_log_future_exception_silent_on_success` — create a `Future`; set result `None`; call `_log_future_exception(future)` → no ERROR log emitted
  - Unit: `test_collection_watcher_start_stop` — mock `Observer`; `start()` → observer started and scheduled; `stop()` → `cancel_all()` called + observer stopped + joined
  - Unit: `test_collection_watcher_is_alive` — before `start()` → `False`; mock observer `is_alive()=True` after `start()` → `True`; after `stop()` → `False`
  - Unit: `test_collection_watcher_start_no_watchdog` — patch `archon.rag.watcher._WATCHDOG_AVAILABLE = False`; `start()` → logs warning; no exception; `is_alive()` returns `False`
  - Unit: `test_collection_watcher_start_nonexistent_directory` — mock `Observer.start()` to raise `OSError`; `CollectionWatcher.start()` → logs warning; does not raise; `is_alive()` returns `False`
  - Unit: `test_collection_watcher_stop_join_timeout_warning` — mock `observer.join()` to return without terminating thread; mock `observer.is_alive()` to return `True` after join; `stop()` called → warning logged: `"Observer thread did not terminate within 5s"`
  - Unit: `test_collection_watcher_directory_disappears` — mock observer raises `OSError` on stop; `stop()` logs warning, does not propagate
  - Integration (optional): `test_collection_watcher_integration` — use `tmp_path`; create a real `CollectionWatcher` with debounce `0.1s`; write a file; run the asyncio event loop for 0.5s; verify the `on_change` callback was called. Mark `@pytest.mark.integration`; skip if `watchdog` not installed.
  - Checkpoint: `uv run pytest tests/rag/test_watcher.py -v --no-cov -k "debounce or watcher"`

#### Task 8.3 — `WatcherManager` in `archon/rag/watcher.py`
- [x] **File**: `archon/rag/watcher.py`
- **Depends on**: Task 8.2
- **Description**:
  - `WatcherManager`:
    - `__init__(self, on_change: Callable[[str], Coroutine], loop: asyncio.AbstractEventLoop, debounce_seconds: float = 5.0)`: stores `on_change`; wraps it internally so that when a callback fires it: (1) checks `_shutting_down` and returns early if `True`; (2) adds the current task to `_active_syncs`; (3) calls `on_change(col_name)` inside `try/except Exception` logging any errors; (4) removes the task from `_active_syncs` in a `finally` block
    - `_watchers: dict[str, CollectionWatcher]`
    - `_active_syncs: set[asyncio.Task[None]]` — internal; tracks in-flight sync tasks, managed entirely within `WatcherManager`
    - `_shutting_down: bool = False` — internal; set to `True` in `stop_all()` to prevent new syncs from starting
    - `add(self, collection_name: str, source_path: Path) -> None`: if `collection_name` already in `_watchers`, return (no-op); create `CollectionWatcher(collection_name, source_path, _wrapped_callback, loop, debounce_seconds)`; `watcher.start()`; store in `_watchers`
    - `async def stop_all(self) -> None`:
      ```python
      self._shutting_down = True  # prevent new syncs from starting
      await asyncio.gather(*(asyncio.to_thread(w.stop) for w in self._watchers.values()))
      if self._active_syncs:
          await asyncio.wait(self._active_syncs, timeout=10.0)
      self._watchers.clear()
      ```
    - `is_watching(self, collection_name: str) -> bool`: return `_watchers.get(collection_name, ...).is_alive()` — `False` if not present
    - `watching_names(self) -> set[str]`: return `{name for name, w in _watchers.items() if w.is_alive()}`
- **Releasable**: `WatcherManager` manages full lifecycle for multiple collections
- **Tests (TDD)** — `tests/rag/test_watcher.py`:
  - Unit: `test_watcher_manager_add_starts_watcher` — mock `CollectionWatcher`; `add(name, path)` → `start()` called; `is_watching(name) == True`
  - Unit: `test_watcher_manager_add_is_idempotent` — `add(name, path)` twice → `CollectionWatcher.__init__` called once; `start()` called once
  - Unit: `test_watcher_manager_stop_all` (async test, `@pytest.mark.asyncio`) — two watchers added; `stop_all()` → both `stop()` called; `watching_names()` returns empty set
  - Unit: `test_watcher_manager_watching_names` — two live watchers; mock one as `is_alive()=False` → `watching_names()` returns only the live one
  - Checkpoint: `uv run pytest tests/rag/test_watcher.py -v --no-cov -k "manager"`

#### Task 8.4 — `RagCollectionSync.sync_collection()` for watcher-triggered incremental sync
- [ ] **File**: `archon/rag/sync.py`
- **Depends on**: nothing (uses existing Phase 4 methods)
- **Description**:
  - Rename `_build_desired` to `build_desired` (remove leading underscore) to make it a public method — `server.py` uses it directly and private method access across files is a code smell.
  - Add `async def sync_collection(self, collection_name: str, source_path: Path) -> None` as a public method on `RagCollectionSync`
  - Body:
    1. `if self._state_store is None: return` — no-op without change detection
    2. Log at INFO: `"Watch-triggered sync for collection %r"`, collection_name
    3. `state = self._state_store.read()`; if `None`, return
    4. `file_mtimes = self._load_file_mtimes(collection_name, state=state)`
    5. `cp = state.collections.get(collection_name)`; extract `indexed_embedding_model` and `indexed_chunk_size` (default to `""` / `0` if absent)
    6. `new_f, changed_f, deleted_p = self._check_collection_changes(collection_name, source_path, file_mtimes, indexed_embedding_model=..., indexed_chunk_size=...)`
    7. If `new_f or changed_f or deleted_p`: `result = await self._apply_collection_changes(collection_name, source_path, new_f, changed_f, deleted_p, file_mtimes)`; if `result` is a non-None error string, log at WARNING level indicating partial failure
    8. Else: log DEBUG `"No changes detected for %r (watcher)"`, collection_name
  - No new imports needed — all helpers already exist
  - The per-collection lock is acquired inside `_apply_collection_changes`, so concurrent `archon rag sync` calls are safely serialised
- **Releasable**: `sync.sync_collection(name, path)` is callable and applies only real changes
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_sync_collection_no_state_store` — `RagCollectionSync(pipeline, state_store=None)`; `sync_collection(name, path)` → returns without error; `_check_collection_changes` NOT called
  - Unit: `test_sync_collection_no_changes` — mock `_check_collection_changes` returns `([], [], [])`; `sync_collection(name, path)` → `_apply_collection_changes` NOT called
  - Unit: `test_sync_collection_with_new_file` — mock `_check_collection_changes` returns `([Path("new.md")], [], [])`; `_apply_collection_changes` called with `new_files=[Path("new.md")]`
  - Unit: `test_sync_collection_with_deleted_file` — mock `_check_collection_changes` returns `([], [], ["/old/file.md"])`; `_apply_collection_changes` called with `deleted_paths=["/old/file.md"]`
  - Integration: `test_sync_collection_lock_respected` — mock `_apply_collection_changes` to acquire lock and hold; second `sync_collection` waits; only one runs at a time
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -v --no-cov -k "sync_collection"`

#### Task 8.5 — `server.py` integration
- [ ] **File**: `archon/rag/server.py`
- **Depends on**: Task 8.3 (WatcherManager), Task 8.4 (sync_collection)
- **Description**:
  - After the sync startup block (after `asyncio.create_task(sync.sync(...))` or `await asyncio.wait_for(...)`), before `app = create_app(...)`:
    ```python
    watcher_manager: WatcherManager | None = None
    if cfg.rag.watch:
        from archon.rag.watcher import WatcherManager  # lazy import — watchdog may not be installed
        desired = sync.build_desired(cfg.rag.collections)
        loop = asyncio.get_running_loop()

        async def _on_change(col_name: str) -> None:
            path_str = desired.get(col_name)
            if path_str:
                await sync.sync_collection(col_name, Path(path_str))

        watcher_manager = WatcherManager(on_change=_on_change, loop=loop)
        for col_name, path_str in desired.items():
            watcher_manager.add(col_name, Path(path_str))
        logger.info(
            "Watch mode active: monitoring %d collection(s) for file changes",
            len(desired),
        )
    ```
  - Note: `_on_change` is wrapped internally by `WatcherManager` which handles task tracking, `_shutting_down` guard, and error logging. The closure itself is intentionally kept simple.
  - In the `finally` block (before `await pipeline.store.disconnect()`):
    ```python
    if watcher_manager is not None:
        await watcher_manager.stop_all()
    ```
  - Note: `stop_all()` sets `_shutting_down=True`, stops all watchers concurrently, and drains in-flight sync coroutines (waits up to 10 seconds) before returning — no additional `asyncio.sleep(0)` needed.
  - Note: `sync.build_desired()` (public) is used here for DRY resolution of collection paths. The rename from `_build_desired` to `build_desired` is part of Task 8.4's file changes.
- **Releasable**: with `[rag] watch = true`, file changes in any collection directory trigger `sync_collection`
- **Tests (TDD)** — `tests/rag/test_server.py`:
  - Unit: `test_server_starts_watcher_manager_when_watch_true` — mock `WatcherManager`, `create_pipeline`, `IndexingStateStore`; call `main()` with `cfg.rag.watch = True`; assert `WatcherManager.__init__` called; `add()` called for each configured collection; `stop_all()` called in finally
  - Unit: `test_server_skips_watcher_manager_when_watch_false` — `cfg.rag.watch = False`; `WatcherManager.__init__` NOT called
  - Unit: `test_server_stops_watcher_on_shutdown` (async test, `@pytest.mark.asyncio`) — `cfg.rag.watch = True`; mock exception in `app.run_http_async()`; assert `stop_all()` called in the finally block
  - Unit: `test_server_on_change_handles_sync_exception` (async test, `@pytest.mark.asyncio`) — since `WatcherManager` wraps the callback, test by calling `_on_change` through the full manager path — mock `sync.sync_collection` to raise `RuntimeError`; verify error is logged; watcher continues operating
  - Checkpoint: `uv run pytest tests/rag/test_server.py -v --no-cov -k "watcher"`

#### Task 8.6 — `archon rag status` + `rag_status` MCP `watching` indicator
- [ ] **File**: `archon/cli/rag_cmd.py`, `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 8.1 (watch config field)
- **Description**:

  **`archon/cli/rag_cmd.py`**:
  - In `_run_status`, after `info = get_rag_service().status()` and `cfg = load_config(...)`, compute: `watching = info.running and cfg.rag.watch`
  - Pass `watching=watching` to `_print_progress_table`: `return _print_progress_table(state, collections, watching=watching)`
  - In `_print_progress_table`, add `watching: bool = False` parameter
  - Inside the for-loop, after `status_str` is assigned and before `print()`:
    ```python
    if watching and progress is not None and progress.status in (
        IndexingStatus.DONE, IndexingStatus.IN_PROGRESS
    ):
        status_str += " (watch)"
    ```
  - `FAILED` and `PENDING` do not get `(watch)` — they indicate the collection is not actively serving data or is awaiting first sync

  **`archon/ai/archon_toolkit_rag.py`**:
  - In `_handle_rag_status`, after `cfg = toolkit._config`, extract `watch_mode = getattr(cfg.rag, "watch", False)` (defensive `getattr` for environments where the field may not exist yet)
  - After building each `col_dicts` entry (both LanceDB-present and state-only), add `d["watching"] = watch_mode`
  - Update `_RAG_STATUS_SCHEMA["description"]` to end with: `...includes optional eta_seconds (integer, estimated seconds remaining) for in-progress collections, and watching (bool) indicating whether file-system watch mode is active for this collection — use this tool to check if watch mode is enabled.`

- **Releasable**: `archon rag status` shows `(watch)` annotation; Claude can check `watching` field in `rag_status`
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py` + `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_status_shows_watch_indicator_for_done` — `_print_progress_table(state, [], watching=True)`; DONE collection → output contains `"(watch)"`
  - Unit: `test_status_no_watch_indicator_when_not_watching` — `_print_progress_table(state, [], watching=False)`; DONE collection → output does NOT contain `"(watch)"`
  - Unit: `test_status_watch_indicator_for_in_progress_not_failed` — parametrized: `IN_PROGRESS` + `watching=True` → contains `"(watch)"`; `FAILED` + `watching=True` → no `"(watch)"`; `PENDING` + `watching=True` → no `"(watch)"`
  - Unit: `test_rag_status_mcp_includes_watching_true` — `cfg.rag.watch = True`; parsed JSON collection dict contains `"watching": True`
  - Unit: `test_rag_status_mcp_includes_watching_false` — `cfg.rag.watch = False`; parsed JSON collection dict contains `"watching": False`
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py tests/ai/test_archon_toolkit_rag.py -v --no-cov -k "watch"`
