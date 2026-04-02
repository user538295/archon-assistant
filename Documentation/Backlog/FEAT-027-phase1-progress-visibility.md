# FEAT-027-P1 — RAG Indexing Progress Visibility
**Purpose**: Add per-collection indexing progress tracking via a JSON state file, visible from CLI and MCP tool
**Audience**: Any user with RAG enabled, checking indexing status from terminal or Telegram
**Status**: To Do

---

## Background
RAG sync runs in the background (`sync_timeout_seconds=0` default), but there is no way to observe progress, detect failures, or see which collections are done. `archon rag status` and the `rag_status` MCP tool only show `doc_count`/`chunk_count` from LanceDB — no in-progress state.

This is Phase 1 of FEAT-027. Full spec: `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`.

## Goal
After this phase: `archon rag status` shows per-collection status (`pending`/`in_progress`/`done`/`failed`), file progress (`87/120`), and error messages. The `rag_status` MCP tool returns the same data as JSON so Claude can answer "is RAG ready?" from Telegram. Concurrent sync calls are prevented by per-collection locking.

---

## Scope

### In Scope
- New `archon/rag/progress.py` — `IndexingState` dataclass + `IndexingStateStore` (atomic JSON read/write)
- Per-collection `asyncio.Lock` in `RagCollectionSync` to prevent concurrent sync
- `RagCollectionSync.sync()` — write state before/during(batched)/after each collection via wrapped `progress_cb`
- `archon rag status` CLI — display state file data alongside existing collection info
- `rag_status` MCP tool — add `status`, `processed_files`, `total_files`, `error`, `error_count` per collection
- `install.py` — add status hint message on exit
- Exit code: `archon rag status` returns non-zero if any collection is `failed`

### Out of Scope
- Partial readiness / health check changes — Phase 2
- Pinned collections priority ordering — Phase 2
- Resumable indexing (`processed_paths`) — Phase 3
- ETA / time remaining — Phase 7
- Telegram notification on completion — Phase 5
- `archon doctor` integration — Phase 6

---

## Acceptance criteria
- [ ] State file `{cfg.rag.db_path}/.indexing_state.json` is written atomically (tmpfile + `os.replace`)
- [ ] State file is updated before sync starts (status=`PENDING`), during sync (status=`IN_PROGRESS`, batched every 50 files), and after sync (status=`DONE`/`FAILED`)
- [ ] Final state write computes accurate `processed_files` (ok only) and `error_count` from `list[IngestResult]`
- [ ] `archon rag status` displays per-collection status and file progress from state file
- [ ] `rag_status` MCP tool returns `status`, `processed_files`, `total_files`, `error`, `error_count` per collection in JSON
- [ ] Concurrent sync calls on the same collection are serialized via `asyncio.Lock`
- [ ] Missing or corrupt state file is handled gracefully (fallback to collection info only)
- [ ] `archon rag status` exits non-zero if any collection has status `FAILED`
- [ ] `install.py` prints _"RAG enabled. Indexing in background — run `archon rag status` to track progress."_ on successful RAG setup
- [ ] All existing tests pass; new tests cover all new code paths
- [ ] Zero-file directories handled: state transitions to `DONE` with `total_files=0, processed_files=0`
- [ ] Stale `IN_PROGRESS` entries reset to `PENDING` on `sync()` entry (crash recovery)
- [ ] State write failures do not abort sync — logged and skipped

---

## What does NOT change
- `RagPipeline.ingest_directory()` — no changes; `progress_cb` signature unchanged
- `CollectionMeta` in LanceDB — no schema changes
- `sync_manifest.json` — no changes
- `RagStore.list_collections()` — no changes
- Search routing logic — no changes
- `archon doctor` — no changes (Phase 6)

---

## Known limitations / accepted trade-offs
- **Batched writes (every 50 files)**: On crash, up to 50 files of progress are lost. Acceptable since Phase 3 adds resumability. For collections with fewer than 50 files, no mid-ingest state write occurs — only the final write. This is acceptable.
- **FTS during partial indexing**: `rebuild_fts_index()` runs only at the end of `ingest_directory()`. During indexing, vector search works but FTS/hybrid returns incomplete results. Documented, not fixed in this phase.
- **`os.replace()` on Windows**: Not guaranteed atomic when another process has the file open. Documented in spec; reads should use retry loop on Windows if needed.
- **`processed_files`/`error_count` computed from return value, not callback**: The `progress_cb(done_count, total)` signature carries no per-file result info. During ingestion, the batched state writes use `done_count` as `processed_files` (files attempted). The accurate `processed_files` (ok only) and `error_count` are computed from `ingest_directory()`'s returned `list[IngestResult]` in the final state write. Mid-ingest progress is best-effort.
- **Locking scope**: Per-collection `asyncio.Lock` lives on the `RagCollectionSync` instance and protects within the RAG server process only. MCP toolkit paths that create their own `RagCollectionSync` instances do not share these locks. This is acceptable because the RAG server is the primary writer; MCP-initiated syncs via separate instances are rare and short-lived. A file-level lock is not needed in Phase 1.
- **Unchanged collections not in state file**: `sync()` only ingests new collections. Previously-indexed collections appear via `store.list_collections()` in CLI/MCP output but have no state file entry. The CLI/MCP merge handles this gracefully.
- **Partially-ingested collections after crash**: If the server crashes mid-ingest, the collection exists in LanceDB (partially). On restart, `sync()` treats it as "existing" and skips it (places in `unchanged`). The state file is reset from `in_progress` to `pending` but no re-ingest occurs. Phase 3 (resumable indexing) and Phase 4 (change detection) address this properly.

---

## Architecture

### New module: `archon/rag/progress.py`

```python
class IndexingStatus(enum.StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"

@dataclass
class CollectionProgress:
    status: IndexingStatus
    total_files: int = 0
    processed_files: int = 0  # during ingest: files attempted; final write: ok files only
    started_at: str | None = None  # ISO 8601 UTC
    completed_at: str | None = None  # ISO 8601 UTC
    error: str | None = None  # last error message
    error_count: int = 0  # total failed files (set from IngestResult on final write)

@dataclass
class IndexingState:
    collections: dict[str, CollectionProgress]
    last_updated: str  # ISO 8601 UTC

class IndexingStateStore:
    def __init__(self, state_dir: Path): ...  # state_dir derived from cfg.rag.db_path
    def read(self) -> IndexingState | None: ...
    def write(self, state: IndexingState) -> None: ...  # atomic tmpfile swap
    def update_collection(self, name: str, progress: CollectionProgress) -> None: ...
    def remove_collection(self, name: str) -> None: ...
```

**State file path**: `{cfg.rag.db_path}/.indexing_state.json` — co-located with LanceDB data and `sync_manifest.json`. Both CLI and MCP tool derive the path from `cfg.rag.db_path`, which is already available in both code paths.

### Modified: `archon/rag/sync.py`
- `RagCollectionSync.__init__` gains `state_store: IndexingStateStore | None = None`
- New `_collection_locks: dict[str, asyncio.Lock]` for per-collection locking
- `sync()` wraps caller's `progress_cb` to write batched state updates internally

### Modified: `archon/cli/rag_cmd.py`
- `_run_status()` reads state file via `IndexingStateStore` and merges with `store.list_collections()`

### Modified: `archon/ai/archon_toolkit_rag.py`
- `_handle_rag_status()` reads state file and adds progress fields to JSON response

### Modified: `archon/rag/server.py`
- Creates `IndexingStateStore` and passes to `RagCollectionSync`

### Modified: `install.py`
- `_offer_rag_setup()` prints status hint message after successful RAG setup

### Data flow
```
server.py main()
  → RagCollectionSync(pipeline, state_store=IndexingStateStore(cfg.rag.db_path))
  → sync(collections)
    → on entry: read state, reset any stale in_progress → pending
    → for each collection in to_add:
      → acquire asyncio.Lock for collection
      → write state: status=pending, total_files=0
      → write state: status=in_progress
      → results = ingest_directory(path, name, progress_cb=wrapped_cb)
        → wrapped_cb: uses done_count as processed_files; every 50 files → state_store.update_collection(...)
        → then call caller's progress_cb if provided
        → (zero-file dir: callback never fires; ingest returns [])
      → compute final counts from list[IngestResult]: ok_count, error_count
      → write state: status=done (or failed if exception), processed_files=ok_count, error_count, total_files, completed_at
      → release lock
    → clean removed collections from state file
```

**Progress tracking split**: During ingestion, `processed_files` in batched writes = `done_count` from callback (files attempted, best-effort). In the **final write** after `ingest_directory` returns, `processed_files` and `error_count` are computed accurately from `list[IngestResult]` (`status == "ok"` vs `status == "error"`). This avoids changing the `progress_cb` signature.

**`done` vs `failed` semantics**: `failed` = `ingest_directory` raised an exception (path missing, store error, etc.). `done` = completed normally, possibly with `error_count > 0` (individual file parse errors that didn't raise). A collection with 12/50 file errors shows `done` with `error_count=12`, not `failed`.

**Zero-file edge case**: If a collection directory has no indexable files, `ingest_directory` returns `[]` without calling `progress_cb`. After it returns, `sync()` writes final state: `status=done, total_files=0, processed_files=0`.

---

## Tests

### Dataclasses (`tests/rag/test_progress.py::TestDataclasses`)
- **test_indexing_status_is_str_enum** (unit): values are strings, comparable with `==`
- **test_collection_progress_defaults** (unit): default values, serialization
- **test_indexing_state_construction** (unit): construction, serialization
- **test_to_dict_serialization** (unit): produces expected JSON structure; status as string
- **test_from_dict_valid** (unit): parses well-formed dict
- **test_from_dict_malformed** (unit): returns empty state on garbage
- **test_from_dict_missing_fields** (unit): missing fields get defaults
- **test_from_dict_unknown_status** (unit): unknown status string defaults to `PENDING`
- **test_from_dict_extra_fields_ignored** (unit): unknown keys ignored (forward compat)

### StateStore (`tests/rag/test_progress.py::TestIndexingStateStore`)
- **test_read_missing_file** (unit): returns None
- **test_read_corrupt_json** (unit): returns None on invalid JSON
- **test_read_empty_file** (unit): returns None
- **test_write_creates_dir_and_file** (unit): creates state_dir if absent
- **test_write_atomic_uses_tmp** (unit): uses tmpfile + os.replace
- **test_write_then_read_roundtrip** (unit): write then read preserves all fields
- **test_update_collection_existing_state** (unit): updates one collection, others untouched
- **test_update_collection_empty_state** (unit): creates state with single collection
- **test_remove_collection_present** (unit): collection removed from state
- **test_remove_collection_absent** (unit): no-op, no error
- **test_remove_collection_no_state_file** (unit): no-op, no error

### Sync locking (`tests/rag/test_sync.py::TestSyncLocking`)
- **test_get_lock_returns_same_lock_for_same_name** (unit)
- **test_get_lock_returns_different_lock_for_different_name** (unit)
- **test_sync_acquires_lock_per_collection** (unit)
- **test_concurrent_sync_same_collection_serialized** (unit)
- **test_concurrent_sync_different_collections_parallel** (unit)

### Sync progress (`tests/rag/test_sync.py::TestSyncProgress`)
- **test_sync_writes_pending_before_ingest** (unit): state has `PENDING` with `total_files=0`
- **test_sync_writes_in_progress_during_ingest** (unit): transitions to `IN_PROGRESS`
- **test_sync_total_files_set_from_first_callback** (unit): `total_files` from first callback
- **test_sync_writes_done_after_success** (unit): `DONE` with accurate counts from `IngestResult`
- **test_sync_writes_failed_on_exception** (unit): `FAILED` with error message
- **test_sync_error_count_from_ingest_results** (unit): computed from `IngestResult` list on final write
- **test_sync_processed_files_counts_ok_only** (unit): final `processed_files` = ok count
- **test_sync_done_with_error_count** (unit): 12/50 errors → `DONE`, `processed_files=38`, `error_count=12`
- **test_sync_failed_preserves_total_files_from_callback** (unit): exception mid-loop preserves callback-captured total
- **test_sync_multiple_collections_mixed_results** (unit): 3 collections with mixed results
- **test_sync_batched_writes_every_50** (unit): called at `done_count % 50 == 0`
- **test_sync_batched_writes_boundary_49_files** (unit): no mid-ingest write
- **test_sync_batched_writes_boundary_50_files** (unit): one batch + final
- **test_sync_batched_writes_boundary_51_files** (unit): one batch at 50 + final at 51
- **test_sync_batched_writes_boundary_1_file** (unit): only final write
- **test_sync_final_write_on_completion** (unit): always fires
- **test_sync_zero_file_directory** (unit): empty dir → `DONE` with 0/0
- **test_sync_wraps_caller_callback** (unit): caller's callback still invoked
- **test_sync_no_state_store_backward_compat** (unit): works when `state_store=None`
- **test_sync_resets_stale_in_progress** (unit): stale `IN_PROGRESS` → `PENDING` on entry
- **test_sync_cleans_removed_collections** (unit): removed from state file
- **test_sync_state_write_failure_does_not_abort** (unit): sync continues on write failure

### Server wiring (`tests/rag/test_server.py::TestServerStateStore`)
- **test_main_creates_state_store** (unit)
- **test_main_passes_state_store_to_sync** (unit)

### CLI (`tests/cli/test_rag_cmd.py::TestRunStatusProgress`)
- **test_run_status_with_progress_display** (integration): shows status table
- **test_run_status_without_state_file** (integration): falls back to existing format
- **test_run_status_failed_exit_code_nonzero** (integration): exits 1 when failed
- **test_run_status_done_exit_code_zero** (integration): exits 0 when all done
- **test_run_status_in_progress_exit_code_zero** (integration): exits 0 when in_progress
- **test_run_status_pending_shows_dash** (integration): pending shows `—`
- **test_run_status_error_message_shown** (integration): failed shows error
- **test_run_status_merge_state_and_collections** (integration): state entries not in LanceDB shown

### MCP tool (`tests/ai/test_archon_toolkit_rag.py::TestRagStatusProgress`)
- **test_rag_status_includes_progress_fields** (unit)
- **test_rag_status_without_state_file** (unit): backward compat
- **test_rag_status_merges_new_collections** (unit): state-only collections included
- **test_rag_status_error_fields** (unit): error and error_count

### Install (`tests/test_install.py`)
- **test_offer_rag_setup_prints_status_hint** (unit)

---

## Documentation update
- [ ] `CLAUDE.md`, section: `archon/ai/` module list — add `archon/rag/progress.py` reference
- [ ] `examples/config.toml.example` — no config changes needed in this phase

---

## Task breakdown

### Phase 1 — Progress visibility

> **Releasable**: After all tasks in this phase are complete, `archon rag status` and the `rag_status` MCP tool show per-collection indexing progress.

#### Task 1.1 — `CollectionProgress` and `IndexingState` dataclasses
- [x] **File**: `archon/rag/progress.py` (new)
- **Depends on**: nothing
- **Description**:
  - `IndexingStatus(enum.StrEnum)` with values: `PENDING = "pending"`, `IN_PROGRESS = "in_progress"`, `DONE = "done"`, `FAILED = "failed"`
  - `CollectionProgress` dataclass with fields:
    - `status: IndexingStatus` — type-safe status
    - `total_files: int = 0`
    - `processed_files: int = 0` — during ingest: files attempted; final write: ok files only
    - `started_at: str | None = None` — ISO 8601 UTC timestamp
    - `completed_at: str | None = None` — ISO 8601 UTC timestamp
    - `error: str | None = None` — last error message
    - `error_count: int = 0` — total failed files (computed from `IngestResult` on final write)
  - `IndexingState` dataclass with fields:
    - `collections: dict[str, CollectionProgress]` — keyed by collection name
    - `last_updated: str` — ISO 8601 UTC timestamp
  - `to_dict(state: IndexingState) -> dict` — serializes to JSON-compatible dict; `IndexingStatus` serializes as string value
  - `from_dict(data: dict) -> IndexingState` — deserializes from dict; returns empty state on malformed input (never raises); unknown `status` strings default to `PENDING`; unknown extra fields are ignored (forward compatibility)
- **Releasable**: After this task, the data model for indexing state is defined and importable
- **Tests (TDD)** — `tests/rag/test_progress.py`:
  - Unit: `test_indexing_status_is_str_enum` — values are strings, comparable with `==`
  - Unit: `test_collection_progress_defaults` — all optional fields default correctly
  - Unit: `test_indexing_state_construction` — state with one collection round-trips
  - Unit: `test_to_dict_serialization` — produces expected JSON structure; status serialized as string
  - Unit: `test_from_dict_valid` — parses well-formed dict back to dataclass
  - Unit: `test_from_dict_malformed` — returns empty state on garbage input, no exception
  - Unit: `test_from_dict_missing_fields` — missing optional fields get defaults
  - Unit: `test_from_dict_unknown_status` — unknown status string defaults to `PENDING`
  - Unit: `test_from_dict_extra_fields_ignored` — extra/unknown keys in dict are silently ignored (forward compat)
  - Checkpoint: `uv run pytest tests/rag/test_progress.py::TestDataclasses -v --no-cov`

#### Task 1.2 — `IndexingStateStore` read/write with atomic swap
- [x] **File**: `archon/rag/progress.py`
- **Depends on**: Task 1.1
- **Description**:
  - `IndexingStateStore` class:
    - `__init__(self, state_dir: Path)` — stores `state_dir`; state file path is `state_dir / ".indexing_state.json"`
    - `read(self) -> IndexingState | None` — reads and deserializes state file; returns `None` if file missing, unreadable, or corrupt JSON. Never raises.
    - `write(self, state: IndexingState) -> None` — serializes to JSON, writes to `.indexing_state.json.tmp`, then `os.replace()` to final path. Creates `state_dir` if missing.
    - `update_collection(self, name: str, progress: CollectionProgress) -> None` — reads current state (or creates empty), updates the named collection entry, sets `last_updated`, writes atomically.
    - `remove_collection(self, name: str) -> None` — reads current state, removes named entry if present, writes. No-op if state file missing or collection not found.
  - Error handling: all read failures log warning and return `None`; write failures log error and re-raise (caller must handle)
- **Releasable**: After this task, state can be persisted and read back from disk
- **Tests (TDD)** — `tests/rag/test_progress.py`:
  - Unit: `test_read_missing_file` — returns None
  - Unit: `test_read_corrupt_json` — returns None, logs warning
  - Unit: `test_read_empty_file` — returns None
  - Unit: `test_write_creates_dir_and_file` — state_dir created if absent
  - Unit: `test_write_atomic_uses_tmp` — verify `.tmp` file used (mock `os.replace`)
  - Unit: `test_write_then_read_roundtrip` — write state, read back, all fields match
  - Unit: `test_update_collection_existing_state` — updates one collection, others untouched
  - Unit: `test_update_collection_empty_state` — creates state with single collection
  - Unit: `test_remove_collection_present` — collection removed from state
  - Unit: `test_remove_collection_absent` — no-op, no error
  - Unit: `test_remove_collection_no_state_file` — no-op, no error
  - Checkpoint: `uv run pytest tests/rag/test_progress.py::TestIndexingStateStore -v --no-cov`

#### Task 1.3 — Per-collection `asyncio.Lock` in `RagCollectionSync`
- [x] **File**: `archon/rag/sync.py`
- **Depends on**: nothing
- **Description**:
  - Add `_collection_locks: dict[str, asyncio.Lock]` to `RagCollectionSync.__init__`
  - Add helper `_get_lock(self, name: str) -> asyncio.Lock` — returns existing lock or creates one for the collection name
  - In `sync()`, wrap the per-collection ingest block (lines 144–156) with `async with self._get_lock(name):` so that concurrent sync calls on the same collection are serialized
  - Different collections can still sync concurrently (separate locks)
  - No changes to `sync()` signature or return type
- **Releasable**: After this task, concurrent sync calls on the same collection are safe
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_get_lock_returns_same_lock_for_same_name` — same asyncio.Lock instance returned
  - Unit: `test_get_lock_returns_different_lock_for_different_name` — distinct locks
  - Unit: `test_sync_acquires_lock_per_collection` — verify lock acquisition during sync (mock)
  - Unit: `test_concurrent_sync_same_collection_serialized` — two concurrent sync calls on same collection: second waits for first
  - Unit: `test_concurrent_sync_different_collections_parallel` — two concurrent sync calls on different collections run concurrently
  - Checkpoint: `uv run pytest tests/rag/test_sync.py::TestSyncLocking -v --no-cov`

#### Task 1.4 — `sync()` progress state integration
- [x] **File**: `archon/rag/sync.py`
- **Depends on**: Task 1.1, Task 1.2, Task 1.3
- **Description**:
  - Add `state_store: IndexingStateStore | None = None` parameter to `RagCollectionSync.__init__`
  - **On sync() entry** (crash recovery): read existing state via `state_store.read()`; reset any `in_progress` entries to `pending`. This handles stale state from a crashed previous run.
  - **Before ingesting each collection**: write `CollectionProgress(status=PENDING, total_files=0)` via `state_store.update_collection()`
  - **At start of ingest_directory call**: update status to `IN_PROGRESS`, set `started_at`
  - **Wrap caller's `progress_cb`** with internal callback that:
    1. On first call: captures `total` from `progress_cb(done_count, total)` and updates `total_files` in state
    2. Uses `done_count` as `processed_files` (files attempted — best-effort; the callback has no access to `IngestResult`)
    3. Every 50 files (when `done_count % 50 == 0`): calls `state_store.update_collection()` with current progress
    4. Then calls caller's `progress_cb` if provided
  - **After `ingest_directory` returns** `list[IngestResult]`: compute accurate final counts:
    - `ok_count = sum(1 for r in results if r.status == "ok")`
    - `error_count = sum(1 for r in results if r.status != "ok")`
    - `total_files = len(results)` (or 0 if empty)
    - Write final state: `status=DONE`, `processed_files=ok_count`, `error_count=error_count`, `completed_at=now`
  - **Zero-file case**: if `ingest_directory` returns `[]` (no callback fired), write `status=DONE, total_files=0, processed_files=0`
  - **On exception from `ingest_directory`**: write `status=FAILED` with `error` message, `completed_at=now`. Use the callback-captured `total_files` (not `len(results)`) since the exception may have interrupted mid-loop. If no callback fired before the exception, `total_files` remains 0.
  - **Clean up state** for collections removed from config (in `to_remove` step): call `state_store.remove_collection(name)`
  - If `state_store is None`: all state writes are skipped (backward compat)
  - **Write failure handling**: `IndexingStateStore.write()` re-raises on failure; `update_collection()` propagates the error. `sync()` is the designated handler: it catches write failures, logs a warning, and continues. Sync must not abort due to state write failure. Final write failures are also logged but do not affect `SyncResult`.
- **Releasable**: After this task, background sync writes progress to state file
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_sync_writes_pending_before_ingest` — state has `PENDING` with `total_files=0` before `ingest_directory` runs
  - Unit: `test_sync_writes_in_progress_during_ingest` — state transitions to `IN_PROGRESS` during callback
  - Unit: `test_sync_total_files_set_from_first_callback` — `total_files` populated from first `progress_cb(1, total)` call
  - Unit: `test_sync_writes_done_after_success` — state shows `DONE` with accurate `processed_files` and `error_count` from `IngestResult`
  - Unit: `test_sync_writes_failed_on_exception` — state shows `FAILED` with error on exception
  - Unit: `test_sync_error_count_from_ingest_results` — `error_count` computed from `IngestResult` list on final write, not from callback
  - Unit: `test_sync_processed_files_counts_ok_only` — final `processed_files` only counts `IngestResult.status == "ok"`
  - Unit: `test_sync_batched_writes_every_50` — `state_store.update_collection` called when `done_count % 50 == 0`
  - Unit: `test_sync_batched_writes_boundary_49_files` — 49 files: no mid-ingest write, only final write
  - Unit: `test_sync_batched_writes_boundary_50_files` — 50 files: one batch write at 50 + final write
  - Unit: `test_sync_batched_writes_boundary_51_files` — 51 files: one batch write at 50 + final write at 51
  - Unit: `test_sync_batched_writes_boundary_1_file` — 1 file: no batch write, only final write
  - Unit: `test_sync_final_write_on_completion` — final write always happens regardless of batch boundary
  - Unit: `test_sync_zero_file_directory` — empty directory: state transitions pending → in_progress → done with 0/0
  - Unit: `test_sync_wraps_caller_callback` — caller's `progress_cb` still invoked with `(done, total)`
  - Unit: `test_sync_no_state_store_backward_compat` — sync works when `state_store=None`
  - Unit: `test_sync_resets_stale_in_progress` — stale `IN_PROGRESS` entries reset to `PENDING` on sync() entry (fresh instance, reads state from disk)
  - Unit: `test_sync_cleans_removed_collections` — removed collections also removed from state file via `remove_collection()`
  - Unit: `test_sync_state_write_failure_does_not_abort` — if `state_store.write()` raises, sync continues and returns normal `SyncResult`
  - Unit: `test_sync_done_with_error_count` — collection with 12/50 file errors: `status=DONE`, `processed_files=38`, `error_count=12`
  - Unit: `test_sync_failed_preserves_total_files_from_callback` — exception at file 30/100: `status=FAILED`, `total_files=100` (from callback), not 30
  - Unit: `test_sync_multiple_collections_mixed_results` — 3 collections: col1 succeeds, col2 raises exception, col3 succeeds. State shows `DONE`/`FAILED`/`DONE` respectively
  - Checkpoint: `uv run pytest tests/rag/test_sync.py::TestSyncProgress -v --no-cov`

#### Task 1.5 — `server.py` state store wiring
- [x] **File**: `archon/rag/server.py`
- **Depends on**: Task 1.4
- **Description**:
  - Import `IndexingStateStore` from `archon.rag.progress`
  - In `main()`, create `IndexingStateStore(cfg.rag.db_path)` — state file lives alongside LanceDB data at `~/.archon/rag/.indexing_state.json`
  - Pass `state_store` to `RagCollectionSync(pipeline, state_store=state_store)`
  - No other changes to `server.py`
- **Releasable**: After this task, the RAG server writes progress state during startup sync
- **Tests (TDD)** — `tests/rag/test_server.py`:
  - Unit: `test_main_creates_state_store` — verify `IndexingStateStore` is instantiated with correct path
  - Unit: `test_main_passes_state_store_to_sync` — verify `RagCollectionSync` receives `state_store`
  - Checkpoint: `uv run pytest tests/rag/test_server.py::TestServerStateStore -v --no-cov`

#### Task 1.6 — `archon rag status` CLI progress display
- [x] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `_run_status()`, after the existing service status check:
    1. Construct `IndexingStateStore(cfg.rag.db_path)` and call `read()`
    2. If state file exists: display per-collection status table:
       ```
       RAG service: running (pid=12345)

       Collection          Status        Progress
       ─────────────────────────────────────────
       sessions            in_progress   87 / 120 files
       my-project          done          340 / 340 files
       docs                pending       —
       old-notes           failed        12 / 50 files  (parse error)
       ```
    3. If state file missing/corrupt: fall back to existing `store.list_collections()` output (current behavior)
    4. Merge: if state file has entries not in LanceDB, show them anyway (they're new collections being indexed). If LanceDB has collections not in state file, show them with collection info only.
  - Return exit code 1 if any collection has `status == "failed"`, else 0 (currently always returns 0 when service is running)
- **Releasable**: After this task, `archon rag status` shows indexing progress
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_run_status_with_progress_display` — state file present: output shows status table with progress
  - Unit: `test_run_status_without_state_file` — no state file: falls back to existing format
  - Unit: `test_run_status_failed_exit_code_nonzero` — returns 1 when any collection failed
  - Unit: `test_run_status_done_exit_code_zero` — returns 0 when all collections done
  - Unit: `test_run_status_in_progress_exit_code_zero` — returns 0 when in_progress (not a failure)
  - Unit: `test_run_status_mixed_failed_and_done_exit_code` — returns 1 when mix of failed + done
  - Unit: `test_run_status_pending_shows_dash` — pending collections show `—` for progress
  - Unit: `test_run_status_error_message_shown` — failed collection shows error message
  - Unit: `test_run_status_merge_state_and_collections` — collections in state but not LanceDB still shown
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py::TestRunStatusProgress -v --no-cov`

#### Task 1.7 — `rag_status` MCP tool progress fields
- [x] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 1.2
- **Description**:
  - In `_handle_rag_status()`, after fetching collections from `store.list_collections()`:
    1. Read state file via `IndexingStateStore(cfg.rag.db_path).read()`
    2. For each collection in the response, merge progress fields if state data exists:
       - Add `"status"`, `"processed_files"`, `"total_files"`, `"error"`, `"error_count"` to each collection dict
    3. If state file missing: omit progress fields (backward compat — existing consumers won't break)
    4. Include collections from state file that aren't in LanceDB yet (new collections being indexed)
  - JSON response example with progress:
    ```json
    {
      "running": true,
      "pid": 12345,
      "collections": [
        {"name": "sessions", "doc_count": 340, "chunk_count": 5600, "status": "done", "processed_files": 340, "total_files": 340, "error": null, "error_count": 0}
      ]
    }
    ```
- **Releasable**: After this task, Claude can answer "is RAG ready?" with concrete per-collection status from Telegram
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_status_includes_progress_fields` — state file present: JSON includes status/processed/total
  - Unit: `test_rag_status_without_state_file` — no state file: JSON has collections without progress fields (backward compat)
  - Unit: `test_rag_status_merges_new_collections` — collection in state but not LanceDB included in response
  - Unit: `test_rag_status_error_fields` — failed collection includes error and error_count
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py::TestRagStatusProgress -v --no-cov`

#### Task 1.8 — `install.py` status hint message
- [ ] **File**: `install.py`
- **Depends on**: nothing
- **Description**:
  - In `_offer_rag_setup()`, change the success message (line 511) from:
    `"RAG enabled. Archon is restarting — RAG will be available shortly."`
    to:
    `"RAG enabled. Indexing in background — run 'archon rag status' to track progress."`
  - Single line change. No logic changes.
- **Releasable**: After this task, users know to check `archon rag status` after install
- **Tests (TDD)** — `tests/test_install.py`:
  - Unit: `test_offer_rag_setup_prints_status_hint` — success path prints the new hint message
  - Checkpoint: `uv run pytest tests/test_install.py::test_offer_rag_setup_prints_status_hint -v --no-cov`

#### Task 1.9 — CLAUDE.md documentation update
- [ ] **File**: `CLAUDE.md`
- **Depends on**: Task 1.1
- **Description**:
  - In the `archon/rag/` description area or the AI module listing, add a bullet for `progress.py`:
    `- `progress.py`: `IndexingStateStore` — atomic read/write of `.indexing_state.json`; `CollectionProgress` + `IndexingState` dataclasses for per-collection indexing state tracking`
  - No other documentation changes needed in this phase
- **Releasable**: After this task, CLAUDE.md reflects the new module
- **Tests (TDD)**: N/A — documentation only
  - Checkpoint: N/A
