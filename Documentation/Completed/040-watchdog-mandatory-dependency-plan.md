# FEAT-040 — Make watchdog a mandatory dependency
**Purpose**: Promote `watchdog` from an optional soft-dependency to a required package so that filesystem watching is always available and the codebase no longer carries the conditional-import scaffolding that exists solely to handle its absence.
**Audience**: archon-search developers and operators
**Status**: To Do

---

## Background

`watchdog` enables `CollectionWatcher` to receive OS-level filesystem events (FSEvents on macOS, inotify on Linux, ReadDirectoryChangesW on Windows) and trigger automatic re-indexing whenever collection files change. Without it, syncs must be triggered manually.

Currently `watcher.py` treats `watchdog` as optional: it wraps the import in `try/except ImportError`, defines a no-op stub `Observer` class, sets a module-level `_WATCHDOG_AVAILABLE` flag, and guards `CollectionWatcher.start()` with that flag. This adds ~25 lines of dead-path code and causes the feature to silently degrade rather than failing fast at install time. One integration test (`test_collection_watcher_integration`) is permanently skipped in CI because the try/except guard calls `pytest.skip` when watchdog is absent.

## Goal

`watchdog` is declared in `archon-search/pyproject.toml` as a required runtime dependency. The optional-import scaffolding (`try/except`, stub `Observer`, `_WATCHDOG_AVAILABLE`) is removed from `watcher.py`. All type-ignore comments that existed only because of the stubs are removed. All tests that previously patched `_WATCHDOG_AVAILABLE` are rewritten to mock `watchdog` internals directly. The integration test runs when `-m integration` is included. The full test suite passes with no regressions.

---

## Scope

### In Scope
- Add `watchdog>=3.0` to `[project] dependencies` in `packages/archon-search/pyproject.toml`
- Remove `try/except ImportError` block, stub `Observer` class, `FileSystemEventHandler = object` fallback alias, and `_WATCHDOG_AVAILABLE` flag from `watcher.py`
- Remove the `_WATCHDOG_AVAILABLE=False` guard branch from `CollectionWatcher.start()`
- Update all type annotations and signatures that were weakened to accommodate the stubs
- Update all tests in `tests/test_watcher.py` that patch `_WATCHDOG_AVAILABLE` to mock `watchdog` internals instead
- Remove the entire `try/except` guard block (including `pytest.skip`) from `test_collection_watcher_integration`
- Remove all `# pragma: no cover` annotations that existed only for the stub code

### Out of Scope
- Changing `CollectionWatcher` or `WatcherManager` behaviour
- Changing debounce logic
- Adding new watcher features
- Changes to any file other than `packages/archon-search/pyproject.toml`, `archon_search/watcher.py`, and `tests/test_watcher.py`
- The root `pyproject.toml` already pins `watchdog>=3.0` in `[project.optional-dependencies] search` and `[dependency-groups] dev` — no change required there (N/A)

---

## Acceptance criteria
- [x] `watchdog>=3.0` appears in `[project] dependencies` in `packages/archon-search/pyproject.toml`
- [x] `archon_search/watcher.py` contains no `try/except ImportError`, no `_WATCHDOG_AVAILABLE`, no stub `Observer` class, no `FileSystemEventHandler = object` fallback, and no `# pragma: no cover` on removed paths
- [x] `archon_search/watcher.py` has no `# type: ignore` comments that existed solely due to the stubs (union-attr on observer/event attributes, no-redef on stub Observer, misc on _DebounceHandler base class)
- [x] `tests/test_watcher.py` contains no patches of `archon_search.watcher._WATCHDOG_AVAILABLE`
- [x] `tests/test_watcher.py::test_collection_watcher_integration` runs and passes without a `try/except` guard or `pytest.skip` (when invoked with `-m integration`)
- [x] `uv run pytest tests/test_watcher.py --no-cov -v` — all tests pass
- [x] Full suite passes with 0 failures; test count equals baseline minus 1 (one test deleted: `test_collection_watcher_start_no_watchdog`)
- [x] `uv run pytest -q` passes (coverage enforced by `addopts` in pyproject.toml at ≥85%)

---

## What does NOT change
- `CollectionWatcher` public API (`start()`, `stop()`, `is_alive()`)
- `WatcherManager` public API (`add()`, `stop_all()`, `is_watching()`, `watching_names()`)
- Debounce behaviour and timer logic in `_DebounceHandler`
- OSError handling added in Task 12.5 (`observer.schedule()` and `observer.start()` error paths)
- All other test files — only `tests/test_watcher.py` changes

---

## Known limitations / accepted trade-offs
- Network filesystems (NFS, CIFS) may not deliver reliable OS events to watchdog. This is an operational concern, not a dependency concern — watchdog degrades gracefully at runtime on those mounts. No code change is needed.
- `watchdog` pulls in a small native extension (`watchdog` uses C extensions on macOS/Linux for performance). This is acceptable — the library is stable and widely deployed.

---

## Architecture

### Changes to `packages/archon-search/pyproject.toml`
Add `"watchdog>=3.0"` to `[project] dependencies`.

**Version floor rationale**: `watchdog>=3.0` is chosen because:
- The code uses only `Observer`, `FileSystemEventHandler`, and `FileSystemEvent` — all stable since watchdog 1.x.
- No 4.0-specific API is used.
- The root `pyproject.toml` already pins `watchdog>=3.0` in its `search` optional-dependency group and dev group; matching that floor keeps both files consistent.
- Using `>=4.0` would be an unjustified version bump with no corresponding code requirement.

**Root `pyproject.toml`**: already references `watchdog>=3.0` in `[project.optional-dependencies] search` and `[dependency-groups] dev`. No change required.

### Changes to `archon_search/watcher.py`
Remove lines 14–39 (the `try/except` block, the `FileSystemEventHandler = object` fallback alias, and stub `Observer`). Replace with direct imports:
```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent
```
Note: `# noqa: F401` on `FileSystemEvent` is removed because `FileSystemEvent` is now used as a type annotation in `on_any_event`.

Remove the `_WATCHDOG_AVAILABLE` flag and the guard block in `CollectionWatcher.start()`:
```python
# DELETE:
if not _WATCHDOG_AVAILABLE:
    _log.warning(
        "watchdog is not installed; file watching disabled for collection %r",
        self._collection_name,
    )
    return
```

Additional type cleanup — remove these stub-induced artefacts:
- `FileSystemEventHandler = object  # type: ignore[assignment,misc]` (the fallback alias on line 21 — deleted with the try/except block)
- `# type: ignore[no-redef]` on the stub `Observer` class definition (line 23 — deleted with the stub)
- `# type: ignore[misc]` on `class _DebounceHandler(FileSystemEventHandler):` (line 63) — unnecessary once `FileSystemEventHandler` is always the real class
- Change `on_any_event(self, event: object)` to `on_any_event(self, event: FileSystemEvent)` — removes 4 `# type: ignore[union-attr]` comments on `event.is_directory`, `event.src_path`, `event.event_type`, `event.dest_path` (lines 83, 87–89)
- Change `self._observer: object | None = None` to `self._observer: Observer | None = None` — removes `# type: ignore[union-attr]` on observer method calls (lines 197, 206, 210, 220)

No other logic changes. OSError handling, debounce, and all other behaviour is unchanged.

### Changes to `tests/test_watcher.py`
- **Delete** `test_collection_watcher_start_no_watchdog` entirely — it tests a code path that no longer exists.
- For every remaining test that patches `archon_search.watcher._WATCHDOG_AVAILABLE`: remove that patch line. There are **10 such patches** (all with `True`). The `Observer` mock that accompanies each patch stays — only the `_WATCHDOG_AVAILABLE` patch line is removed.
- `test_collection_watcher_integration`: remove the **entire** `try/except` guard block — both the `try:`, the `from watchdog.observers import Observer` inside it, the `except ImportError:`, and the `pytest.skip("watchdog not installed")` line. Replace with nothing (the test body already uses `CollectionWatcher` directly; no explicit Observer import is needed in the test).

---

## Tests

- **`test_collection_watcher_start_no_watchdog` deleted** (unit): test for removed code path — delete it
- **All 10 `patch("archon_search.watcher._WATCHDOG_AVAILABLE", True)` patches removed** (unit): replaced with direct `Observer` mock only; existing `test_collection_watcher_start_stop` and other Observer-mocked tests already cover the happy path for `CollectionWatcher.start()`
- **`test_collection_watcher_integration`** (integration): entire try/except guard block removed; runs when `-m integration` is included (still marked `@pytest.mark.integration` and excluded from default `addopts`); verifies a real OS observer starts and fires a callback on file creation

---

## Documentation update
- N/A — no user-facing documentation changes required

---

## Task breakdown

### Phase 1 — Make watchdog mandatory and clean up dead code
> **Releasable**: after Task 1.2 — full suite passes with watchdog as a hard dependency

#### Task 1.1 — Add watchdog to pyproject.toml dependencies
- [x] **File**: `packages/archon-search/pyproject.toml`
- **Depends on**: nothing
- **Description**:
  - Add `"watchdog>=3.0"` to the `[project] dependencies` list (append after existing entries or in alphabetical order)
  - Version floor is `>=3.0` — matches the root `pyproject.toml` pins; no 4.0-specific API is used (see Architecture for rationale)
  - The root `pyproject.toml` already has `watchdog>=3.0` in both `[project.optional-dependencies] search` and `[dependency-groups] dev` — no change needed there
  - Run `uv sync` to install it into the venv
  - This updates `uv.lock` — stage both `pyproject.toml` and `uv.lock` in the commit
  - No other files change in this task
- **Releasable**: after this task, `import watchdog` succeeds in the project venv
- **Tests (TDD)** — no dedicated test file; verified by subsequent tasks
  - Checkpoint: `uv run python -c "import watchdog; print(watchdog.__version__)"`

#### Task 1.2 — Remove all optional-import scaffolding from watcher.py AND update all tests simultaneously
- [x] **Files**: `archon_search/watcher.py` AND `tests/test_watcher.py` (committed together — one atomic commit)
- **Depends on**: Task 1.1
- **Description** (`watcher.py`):
  - Replace the `try/except ImportError` block (lines 14–39) with direct imports:
    ```python
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
    ```
  - Remove the `_WATCHDOG_AVAILABLE = True/False` assignments entirely
  - Remove the `FileSystemEventHandler = object  # type: ignore[assignment,misc]` fallback alias
  - Remove the stub `Observer` class (the `class Observer:` block with `# type: ignore[no-redef]` and no-op methods)
  - In `CollectionWatcher.start()`: remove the `if not _WATCHDOG_AVAILABLE:` guard block (4 lines including `_log.warning` and `return`)
  - Remove `# type: ignore[misc]` from `class _DebounceHandler(FileSystemEventHandler):`
  - Change `on_any_event(self, event: object)` to `on_any_event(self, event: FileSystemEvent)` and remove the 4 `# type: ignore[union-attr]` comments on event attribute accesses
  - Change `self._observer: object | None = None` to `self._observer: Observer | None = None` and remove `# type: ignore[union-attr]` comments on observer method calls
  - Remove `# noqa: F401` from the `FileSystemEvent` import (now used as a type annotation)
  - Remove all `# pragma: no cover` annotations that appeared on stub methods
  - No logic changes — OSError handling, debounce, and all other behaviour is unchanged
- **Description** (`tests/test_watcher.py`):
  - **Delete** `test_collection_watcher_start_no_watchdog` entirely
  - For every test with `patch("archon_search.watcher._WATCHDOG_AVAILABLE", True)`: remove that one patch line (10 patches total). The accompanying `Observer` mock stays.
  - In `test_collection_watcher_integration`: remove the **entire** `try/except` guard block — the `try:` line, the `from watchdog.observers import Observer` inside it, the `except ImportError:` line, and the `pytest.skip("watchdog not installed")` line. The test body is otherwise unchanged.
- **Rationale for atomic commit**: Task 1.2 in isolation would leave the test suite broken (9 tests patch a symbol that no longer exists). The project mandate requires all tests to pass after every commit. Both files must be committed together.
- **Tests (TDD)**:
  - After applying both file changes, the test count is baseline minus 1 (one test deleted: `test_collection_watcher_start_no_watchdog`, none added)
  - All watcher unit tests pass; no `_WATCHDOG_AVAILABLE` patches remain
  - `test_collection_watcher_integration` passes when run with `-m integration`
  - Checkpoint (unit tests): `uv run pytest tests/test_watcher.py --no-cov -v`
  - Checkpoint (integration): `uv run pytest tests/test_watcher.py --no-cov -v -m integration`
  - Checkpoint (full suite): `uv run pytest --no-cov -q`
  - Checkpoint (integration suite): `uv run pytest --no-cov -q -m "benchmark or integration"`
  - Checkpoint (coverage): `uv run pytest -q`
