# FEAT-027-P3 — Resumable Indexing
**Purpose**: A crashed or timed-out sync resumes from where it stopped instead of restarting from scratch — critical for large collections
**Audience**: Users with Search enabled, especially with large (1k+ file) collections
**Status**: Done ✅

---

## Background
Phase 1 (Done) added per-collection progress visibility via `.indexing_state.json`.
Phase 2 (Done) added pinned-first ordering and partial-readiness health checks.

Phase 3 adds file-level resume capability: on service restart after a crash or timeout, sync skips files already processed in the previous run and continues from where it left off. Without this, a crash at 90% of a 2-hour index forces a full restart.

Full spec: `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`, Phase 3 section.

## Goal
After this phase: when a sync is interrupted (crash, timeout, machine sleep), restarting the service resumes indexing from the last processed file. No file is ingested twice. `archon search reindex <collection>` clears the resume state and forces a full re-index. State writes remain batched at the existing 50-file cadence.

---

## Scope

### In Scope
- Add `processed_paths: list[str]` to `CollectionProgress` — per-collection list of absolute path strings for files that completed ingest successfully
- `ingest_directory` in `pipeline.py` — two new optional parameters: `exclude_paths` (skip already-processed files) and `on_file_complete` (callback called only for successfully ingested files)
- `SearchCollectionSync.sync()` — load `processed_paths` on sync start; pass to `ingest_directory` as `exclude_paths`; accumulate newly processed paths and flush to state at every 50-file batch boundary and on completion; resume collections that are in `existing & desired` with status `PENDING` or `FAILED` (not just newly added collections); update `_reset_stale_in_progress()` to preserve `processed_paths`
- CLI `_run_collection_reindex` — clear `processed_paths` (and full collection state) from state file before running the re-index
- MCP `_handle_search_collection_reindex` — clear `processed_paths` (and full collection state) from state file before running the re-index

### Out of Scope
- Deletion detection — Phase 4
- Hash-based or mtime-based change detection — Phase 4
- 24h TTL auto-expiry of `processed_paths` — not in this phase
- Resuming mid-file (partial document ingest) — not feasible; file-level granularity only
- `archon search status` display changes — progress display already handles the resume offset via existing `processed_files`/`total_files` fields

---

## Acceptance criteria
- [x] `CollectionProgress` has a `processed_paths: list[str]` field (default empty list)
- [x] `to_dict` serialises `processed_paths`; `from_dict` deserialises it safely (invalid type → empty list)
- [x] `ingest_directory` accepts `exclude_paths: frozenset[str] | None = None`; excluded files are not ingested and do not appear in results
- [x] `ingest_directory` accepts `on_file_complete: Callable[[Path], None] | None = None`; called ONLY for files where `ingest_file` returns `status='ok'` — errored files do NOT trigger the callback and are retried on next sync
- [x] `progress_cb` `total` reflects only non-excluded files; excluded files are not counted
- [x] `SearchCollectionSync.sync()` loads `processed_paths` from state at collection sync start
- [x] Files in `processed_paths` are skipped (not re-ingested) on the next sync run
- [x] Newly processed file paths are accumulated in memory and flushed to state every 50 files and on completion/failure
- [x] `processed_files` in state reflects the combined count: already-done + newly-done files
- [x] `total_files` in state reflects the total file count including skipped paths
- [x] When all files are excluded (full resume overlap), DONE state shows `total_files = resume_offset`, `processed_files = resume_offset` — not 0
- [x] Collections in `existing & desired` with `status != DONE` are resumed (re-ingested from their `processed_paths` offset) rather than treated as `unchanged` — only truly DONE collections are skipped
- [x] `_reset_stale_in_progress()` preserves `processed_paths` when resetting IN_PROGRESS → PENDING: `CollectionProgress(status=PENDING, total_files=cp.total_files, processed_files=cp.processed_files, processed_paths=cp.processed_paths)`
- [x] CLI `archon search reindex <collection>` clears all per-collection state (including `processed_paths`) before re-indexing
- [x] MCP `search_collection_reindex` clears all per-collection state before re-indexing
- [x] Step 7 `unchanged` excludes `to_resume` collections — only DONE collections appear in `result.unchanged`
- [x] Initial `IN_PROGRESS` write includes `processed_paths = resume_paths` (not default `[]`)
- [x] All existing tests pass; new tests cover all new code paths

---

## What does NOT change
- `IndexingStatus` enum values — no new statuses
- `IndexingStateStore.read()` / `write()` / `update_collection()` / `remove_collection()` — no API changes
- `progress_cb` signature — `(done_count: int, total: int) -> None | Awaitable[None]` unchanged
- Existing callers of `ingest_directory` with no new parameters — zero behaviour change
- `archon search status` display logic — `processed_files`/`total_files` already drive the display
- State file schema top-level structure — additive only (`processed_paths` per collection)
- `_reset_stale_in_progress()` existing behavior (IN_PROGRESS → PENDING reset) — preserved; only the field-copying is updated to include `processed_paths`

---

## Known limitations / accepted trade-offs
- **Path-based, not content-based**: if a file's content changes between a crash and restart, it is still skipped (same path = already processed). Phase 4 fixes this with mtime/hash detection.
- **Directory relocation**: if the collection source directory is moved between runs, `processed_paths` contains stale absolute paths that won't match any current files — effective full re-index occurs automatically (correct behaviour).
- **Up to 50 files of progress lost on crash**: batched writes mean at most 50 files are re-processed after a crash. Acceptable given Phase 4 will add mtime detection to make re-processing cheap.
- **`exclude_paths` not validated against disk**: paths that no longer exist on disk are silently ignored when building the set.
- **New files on completed collections**: Phase 3 resume only applies during first-time ingest crash recovery. A DONE collection that later has new files added to its source directory will NOT have those files indexed automatically — they are still classified as `unchanged` by `sync()`. Users must run `archon search reindex <collection>` to pick up new files until Phase 4 (file-level change detection) is implemented.
- **Errored files are retried, not skipped**: files that fail ingest (`status='error'`) are never added to `processed_paths` and will be retried on the next sync. Only `reindex` clears a permanently-stuck file from the retry cycle.
- **FAILED collections auto-resumed**: collections with `FAILED` status are auto-resumed on the next `sync()` (Step 6.5). There is no backoff or retry limit — a collection that fails due to a persistent error (corrupt file, permission denied) will be retried on every startup. Users should run `archon search reindex <collection>` if a collection fails repeatedly. Phase 4 may add retry limiting.
- **Sequential collection processing**: `sync()` processes collections sequentially (not in parallel). `update_collection()` does a read-modify-write on the shared state file, which is not safe for concurrent writes. Do not parallelize the ingestion loops without adding file-level locking to `IndexingStateStore`.

---

## Architecture

### New / changed components

**`archon/search/progress.py`**
- `CollectionProgress`: new field `processed_paths: list[str] = field(default_factory=list)`
- `to_dict`: serialise `processed_paths` as a JSON array
- `from_dict`: parse `processed_paths`; validate it is a list of strings; fall back to `[]` on any type error

**`archon/search/pipeline.py`** — `SearchPipeline.ingest_directory`
```python
async def ingest_directory(
    self,
    path: Path,
    collection: str,
    glob_pattern: str = "**/*",
    progress_cb: Callable[[int, int], None | Awaitable[None]] | None = None,
    force_regenerate_description: bool = False,
    exclude_paths: frozenset[str] | None = None,
    on_file_complete: Callable[[Path], None] | None = None,
) -> list[IngestResult]:
```
- After collecting and sorting `files`, apply `exclude_paths` filter: `files = [f for f in files if str(f) not in exclude_paths]` (no-op if `exclude_paths` is `None`)
- `total` passed to `progress_cb` reflects only the non-excluded files
- After each file is processed, call `on_file_complete(file_path)` if provided — ONLY when `ingest_file` returns `status='ok'`. Errored files do NOT trigger the callback; they will be retried on next sync.

**`archon/search/sync.py`** — `SearchCollectionSync.sync()` (inner ingestion loop)

**State write consolidation**: ALL state writes go through `on_file_complete`. The `progress_cb` wrapper does NOT write to state.

- Before ingesting a collection, load `processed_paths` from state and pre-compute `total_new`:
  ```python
  resume_paths: list[str] = self._load_processed_paths(name)
  resume_offset: int = len(resume_paths)
  exclude_set: frozenset[str] = frozenset(resume_paths)
  ```
- **`total_new` is computed directly** by enumerating the source directory and subtracting excluded files — NOT captured indirectly through `progress_cb`. This avoids timing issues where `on_file_complete` fires before `progress_cb` has had a chance to set the value. The `progress_cb` wrapper is still passed through to the caller but does NOT write state and is NOT relied upon for `total_new`.
- In-memory accumulator for this run: `new_paths: list[str] = []`
- `on_file_complete` callback (the SOLE state writer per batch): append `str(file_path)` to `new_paths`; if `len(new_paths) % 50 == 0`, call `_safe_state_update` with a complete `CollectionProgress` including:
  - `processed_paths = resume_paths + new_paths`
  - `processed_files = resume_offset + len(new_paths)`
  - `total_files = resume_offset + total_new`
- On completion (DONE): final `_safe_state_update` includes `processed_paths = resume_paths + new_paths`, `total_files = resume_offset + total_new`, `processed_files = resume_offset + len(new_paths)`
- On FAILED (exception): final `_safe_state_update` includes `processed_paths = resume_paths + new_paths` (retain partial progress for next run)
- When `ingest_directory` returns an empty list (all files excluded — full resume overlap): write DONE with `total_files = resume_offset`, `processed_files = resume_offset` (NOT using `len(results)` which would be 0)
- New helper `_load_processed_paths(name: str) -> list[str]` — reads state, returns `cp.processed_paths` or `[]` if state store is None, file is missing, or collection is absent
- **Step 6.5 — Resume incomplete collections**: After the `to_add` loop, compute `to_resume = {name for name in existing & desired.keys() if collection state status != DONE}` (state read failure → treat as DONE, i.e., skip — avoids accidental full re-ingest on corrupt state). For each collection in `to_resume`, run the same resume ingestion path: acquire lock, load `processed_paths`, pass `exclude_paths` and `on_file_complete` to `ingest_directory`, and ingest on top of the existing LanceDB table (do NOT drop/recreate it). Successfully resumed collections go into `result.added`; failures go into `result.errors`. Step 7 must be updated: `unchanged = (existing & desired.keys()) - to_resume` — only truly DONE collections appear in `result.unchanged`.
- **`_reset_stale_in_progress()` update**: when constructing the PENDING replacement for an IN_PROGRESS collection, preserve `processed_paths`:
  ```python
  state.collections[name] = CollectionProgress(
      status=IndexingStatus.PENDING,
      total_files=cp.total_files,
      processed_files=cp.processed_files,
      processed_paths=cp.processed_paths,  # preserve for resume
  )
  ```

**`archon/cli/search_cmd.py`** — `_run_collection_reindex`
- Before calling `pipeline.ingest_directory`, instantiate `IndexingStateStore(Path(cfg.search.db_path))` and call `state_store.remove_collection(col_name)` to wipe all prior state
- Wrap the call in a `try/except` — state clear failure is non-fatal (log warning; proceed with reindex)
- Import `IndexingStateStore` from `archon.search.progress`

**`archon/ai/archon_toolkit_search.py`** — `_handle_search_collection_reindex`
- After resolving `col_name` and before ingesting, instantiate `IndexingStateStore(Path(cfg.search.db_path))` and call `remove_collection(col_name)`
- Non-fatal: log warning on failure; continue with ingest

### State schema change (additive)
```json
{
  "collections": {
    "sessions": {
      "status": "in_progress",
      "total_files": 1000,
      "processed_files": 850,
      "started_at": "2026-04-03T10:00:00Z",
      "completed_at": null,
      "error": null,
      "error_count": 0,
      "processed_paths": [
        "/Users/me/.archon/history/sessions/2026-01-01.md",
        "..."
      ]
    }
  },
  "last_updated": "2026-04-03T10:08:30Z"
}
```

---

## Tests

- **`test_collection_progress_processed_paths_default`** (unit): default `processed_paths` is an empty list
- **`test_to_dict_includes_processed_paths`** (unit): `to_dict` serialises the field
- **`test_from_dict_parses_processed_paths`** (unit): valid list deserialised correctly
- **`test_from_dict_invalid_processed_paths_type_falls_back`** (unit): non-list value → empty list
- **`test_from_dict_mixed_type_processed_paths_falls_back`** (unit): list containing non-strings → empty list
- **`test_ingest_directory_exclude_paths_skips_files`** (unit): excluded files are not ingested; not in results
- **`test_ingest_directory_exclude_paths_adjusts_total`** (unit): `progress_cb` `total` excludes filtered files
- **`test_ingest_directory_on_file_complete_called_per_file`** (unit): callback fired for each successfully processed file
- **`test_ingest_directory_on_file_complete_only_for_ok_results`** (unit): callback not called for files where `ingest_file` returns `status='error'`; only successful files trigger the callback
- **`test_ingest_directory_no_new_files_returns_empty`** (unit): all files excluded → empty result list
- **`test_sync_resumes_from_processed_paths`** (integration): state with `processed_paths` → excluded from next `ingest_directory` call
- **`test_sync_accumulates_new_paths_in_state`** (integration): after sync, `processed_paths` contains newly processed absolute paths
- **`test_sync_processed_files_offset_correct`** (integration): `processed_files` reflects resume_offset + new files
- **`test_sync_total_files_correct_with_resume`** (integration): `total_files` = resume count + new files count
- **`test_sync_batched_path_flush_every_50_files`** (integration): paths flushed to state at 50-file boundary
- **`test_sync_final_state_contains_all_paths`** (integration): on DONE, state contains all processed paths
- **`test_sync_failed_state_contains_paths_processed_before_failure`** (integration): on FAILED, paths from before failure are retained
- **`test_sync_all_files_already_processed_state_correct`** (integration): all files excluded (full resume overlap) → DONE with `total_files = resume_offset`, `processed_files = resume_offset` (not 0)
- **`test_sync_errored_file_not_in_processed_paths`** (integration): `ingest_file` returns `status='error'` → that file NOT in `processed_paths`; retried on next sync
- **`test_sync_resumes_existing_collection_with_pending_status`** (integration): collection in `existing` with PENDING status → resume ingestion runs (not skipped as `unchanged`); appears in `result.added`
- **`test_sync_resumed_collection_not_in_unchanged`** (integration): PENDING collection in `existing & desired` → NOT in `result.unchanged`
- **`test_reset_stale_preserves_processed_paths`** (unit, `tests/search/test_sync.py`): IN_PROGRESS state with `processed_paths=["a","b"]`; after `_reset_stale_in_progress()`, status=PENDING and `processed_paths=["a","b"]`
- **`test_load_processed_paths_state_store_none`** (unit): `_state_store=None` → `_load_processed_paths` returns `[]`
- **`test_load_processed_paths_no_state_file`** (unit): state file missing → returns `[]`
- **`test_load_processed_paths_collection_absent`** (unit): collection not in state → returns `[]`
- **`test_run_collection_reindex_clears_state`** (unit): CLI reindex calls `remove_collection` on state store before `ingest_directory`; also verify `ingest_directory` does NOT receive a non-empty `exclude_paths` (confirms full re-ingest enabled)
- **`test_handle_search_collection_reindex_clears_state`** (unit): MCP tool clears state before ingesting

---

## Documentation update
- [x] `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`, Phase 3: mark as Done ✅ when complete

---

## Task breakdown

### Phase 3 — Resumable Indexing
> **Releasable**: after Task 3.3 (sync is resumable end-to-end); Tasks 3.4 and 3.5 are independently usable once 3.1 is complete

#### Task 3.1 — `CollectionProgress.processed_paths` field + serialization
- [x] **File**: `archon/search/progress.py`
- **Depends on**: nothing
- **Description**:
  - Add `processed_paths: list[str] = field(default_factory=list)` to `CollectionProgress` *(pre-implemented in Phase 1 — field already present in `progress.py`)*
  - `to_dict`: add `"processed_paths": cp.processed_paths` to each collection dict
  - `from_dict`: parse `processed_paths` from raw dict; validate it is `list` and all items are `str`; fall back to `[]` on any type mismatch (non-list value, or list containing non-strings)
- **Releasable**: after this task, `CollectionProgress` can carry and persist `processed_paths`; state file round-trips cleanly
- **Tests (TDD)** — `tests/search/test_progress.py`:
  - Unit: `test_collection_progress_processed_paths_default` — default field is `[]`
  - Unit: `test_to_dict_includes_processed_paths` — serialised dict contains `"processed_paths"` key with correct value
  - Unit: `test_from_dict_parses_processed_paths` — list of strings round-trips correctly
  - Unit: `test_from_dict_invalid_processed_paths_type_falls_back` — non-list value (e.g. `42`) → `[]`
  - Unit: `test_from_dict_mixed_type_processed_paths_falls_back` — list with non-string item → `[]`
  - Checkpoint: `uv run pytest tests/search/test_progress.py -v --no-cov`

#### Task 3.2 — `ingest_directory` — `exclude_paths` and `on_file_complete` parameters
- [x] **File**: `archon/search/pipeline.py`
- **Depends on**: nothing (pure API extension; existing callers unaffected)
- **Description**:
  - Add `exclude_paths: frozenset[str] | None = None` parameter
  - Add `on_file_complete: Callable[[Path], None] | None = None` parameter
  - After collecting and sorting files via `path.glob(...)`, apply filter: `if exclude_paths is not None: files = [f for f in files if str(f) not in exclude_paths]`
  - The `total` passed to `progress_cb` is `len(files)` after filtering (excluded files do not count)
  - `on_file_complete` is called ONLY for files where `ingest_file` returns `status='ok'`. Errored files do NOT trigger the callback and will be re-tried on the next sync. Excluded files also do not trigger the callback.
  - No change to return type or any other behaviour
- **Releasable**: after this task, callers can skip already-processed files and be notified per successfully-completed file
- **Tests (TDD)** — `tests/search/test_pipeline.py` (or existing pipeline test file):
  - Unit: `test_ingest_directory_exclude_paths_skips_files` — `exclude_paths` containing a file's absolute path → that file not in results; `progress_cb` called with reduced total
  - Unit: `test_ingest_directory_exclude_paths_adjusts_total` — `progress_cb` receives `total` equal to non-excluded file count
  - Unit: `test_ingest_directory_on_file_complete_called_per_file` — callback fired once for each successfully processed file with correct `Path`
  - Unit: `test_ingest_directory_on_file_complete_only_for_ok_results` — callback not called for files where `ingest_file` returns `status='error'`; errored files are absent from callback invocations
  - Unit: `test_ingest_directory_no_new_files_returns_empty` — all files excluded → empty result, `progress_cb` never called
  - Unit: `test_ingest_directory_no_exclude_paths_unchanged` — `exclude_paths=None` → identical to current behaviour
  - Checkpoint: `uv run pytest tests/search/test_pipeline.py -v --no-cov`

#### Task 3.3 — `SearchCollectionSync.sync()` — resumable indexing
- [x] **File**: `archon/search/sync.py`
- **Depends on**: Task 3.1, Task 3.2
- **Description**:
  a. **Update `_reset_stale_in_progress()`**: when constructing the PENDING replacement for an IN_PROGRESS collection, pass `processed_paths=cp.processed_paths` to preserve resume state across service restarts:
     ```python
     state.collections[name] = CollectionProgress(
         status=IndexingStatus.PENDING,
         total_files=cp.total_files,
         processed_files=cp.processed_files,
         processed_paths=cp.processed_paths,  # preserve for resume
     )
     ```
  b. **Add `_load_processed_paths(self, name: str) -> list[str]`**: reads state via `_state_store.read()`; returns `state.collections[name].processed_paths` or `[]` if state store is `None`, state file is missing, or collection is absent
  c. **Step 6.5 — Resume incomplete collections**: after the `to_add` loop, compute `to_resume = {name for name in existing & desired.keys() if collection state status != DONE}` (state read failure → treat as DONE to avoid accidental full re-ingest). For each collection in `to_resume`, run the same resume ingestion path. Successfully resumed collections go into `result.added`; failures into `result.errors`. **Step 7 update**: `unchanged = (existing & desired.keys()) - to_resume` — `to_resume` collections are NOT in `result.unchanged`.
  d. **Unify state writes — `on_file_complete` as the sole state writer**: `progress_cb` does NOT write to state; it is only passed through to the caller. `total_new` is computed directly by enumerating and filtering the source directory (same logic as `ingest_directory` file collection + `exclude_paths`), not captured from `progress_cb`. All state writes go through `on_file_complete`:
     - In-memory accumulator: `new_paths: list[str] = []`
     - `on_file_complete(file_path)`: append `str(file_path)` to `new_paths`; if `len(new_paths) % 50 == 0`, write complete `CollectionProgress` with `processed_paths = resume_paths + new_paths`, `processed_files = resume_offset + len(new_paths)`, `total_files = resume_offset + total_new`
  e. **Handle full-resume overlap**: when `ingest_directory` returns an empty list (all files were in `exclude_paths`), write DONE with `total_files = resume_offset`, `processed_files = resume_offset` — not `len(results)` which would give 0
  f. On DONE: final `_safe_state_update` includes `processed_paths = resume_paths + new_paths`
  g. On FAILED (exception): final `_safe_state_update` includes `processed_paths = resume_paths + new_paths` (retain partial progress for next run)
  h. The initial `IN_PROGRESS` write (before `ingest_directory` is called) sets `total_files = resume_offset`, `processed_files = resume_offset`, **and `processed_paths = resume_paths`** — this is critical: omitting `processed_paths` would reset it to `[]` (field default) and erase all resume state if a crash occurs before the first batch flush
- **Releasable**: after this task, sync is fully resumable — crash recovery skips already-ingested files, and collections interrupted mid-sync are correctly resumed on restart
- **Tests (TDD)** — `tests/search/test_sync.py`:
  - Unit: `test_reset_stale_preserves_processed_paths` — IN_PROGRESS state with `processed_paths=["a","b"]`; after `_reset_stale_in_progress()`, status=PENDING and `processed_paths=["a","b"]`
  - Unit: `test_load_processed_paths_state_store_none` — `_state_store=None` → returns `[]`
  - Unit: `test_load_processed_paths_no_state_file` — state file missing → returns `[]`
  - Unit: `test_load_processed_paths_collection_absent` — collection not in state → returns `[]`
  - Integration: `test_sync_resumes_from_processed_paths` — state contains `processed_paths=["/a/file.md"]`; mock `ingest_directory` captures `exclude_paths`; assert `"/a/file.md"` is in `exclude_paths`
  - Integration: `test_sync_accumulates_new_paths_in_state` — after sync, state `processed_paths` contains the file paths that were processed
  - Integration: `test_sync_processed_files_offset_correct` — resume_offset=5, 3 new files: state shows `processed_files=8`
  - Integration: `test_sync_total_files_correct_with_resume` — resume_offset=5, total_new=3: state shows `total_files=8`
  - Integration: `test_sync_batched_path_flush_every_50_files` — 100 files processed; state write called at file 50 with 50 paths; final write with 100 paths
  - Integration: `test_sync_final_state_contains_all_paths` — DONE state has `processed_paths` listing all ingested files
  - Integration: `test_sync_failed_state_contains_paths_processed_before_failure` — exception at file 60/100; FAILED state retains the 60 paths processed before failure
  - Integration: `test_sync_no_resume_on_empty_processed_paths` — fresh collection (no state): `exclude_paths` is empty / `frozenset()`
  - Integration: `test_sync_all_files_already_processed_state_correct` — all files excluded (full resume overlap): DONE with `total_files = resume_offset`, `processed_files = resume_offset` (not 0)
  - Integration: `test_sync_errored_file_not_in_processed_paths` — `ingest_file` returns `status='error'` for one file → that file NOT in `processed_paths` after sync; a subsequent sync would retry it
  - Integration: `test_sync_resumes_existing_collection_with_pending_status` — collection exists in LanceDB (`existing`) with PENDING status → Step 6.5 runs resume ingestion; collection is NOT placed in `result.unchanged` but in `result.added`
  - Integration: `test_sync_resumed_collection_not_in_unchanged` — PENDING collection in `existing & desired`: verify `result.unchanged` does NOT contain it
  - Checkpoint: `uv run pytest tests/search/test_sync.py -v --no-cov`

#### Task 3.4 — Clear collection state on reindex (CLI)
- [x] **File**: `archon/cli/search_cmd.py`
- **Depends on**: Task 3.1
- **Description**:
  - In `_run_collection_reindex`: after loading `cfg` and before calling `pipeline.ingest_directory`, instantiate `IndexingStateStore(Path(cfg.search.db_path))` and call `state_store.remove_collection(col_name)` to wipe all prior state for the collection (including `processed_paths`)
  - Wrap the call in a `try/except` — state clear failure is non-fatal (log warning; proceed with reindex)
  - Import `IndexingStateStore` from `archon.search.progress`
- **Releasable**: after this task, `archon search reindex` guarantees a clean full re-index with no stale resume state
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Unit: `test_run_collection_reindex_clears_state` — mock `IndexingStateStore`; assert `remove_collection(col_name)` called before `ingest_directory`; also assert `ingest_directory` does NOT receive a non-empty `exclude_paths` (confirms state clear enables full re-ingest)
  - Unit: `test_run_collection_reindex_state_clear_failure_non_fatal` — `remove_collection` raises; reindex proceeds; no exception propagated
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -v --no-cov -k "reindex"`

#### Task 3.5 — Clear collection state on reindex (MCP)
- [x] **File**: `archon/ai/archon_toolkit_search.py`
- **Depends on**: Task 3.1
- **Description**:
  - In `_handle_search_collection_reindex`: after resolving `col_name` and before ingesting, instantiate `IndexingStateStore(Path(cfg.search.db_path))` and call `remove_collection(col_name)` to wipe all prior state
  - Non-fatal: log warning on failure; continue with ingest
- **Releasable**: after this task, `search_collection_reindex` MCP tool guarantees a clean full re-index with no stale resume state
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_search.py`:
  - Unit: `test_handle_search_collection_reindex_clears_state` — mock `IndexingStateStore`; assert `remove_collection(col_name)` called before ingest
  - Unit: `test_handle_search_collection_reindex_state_clear_failure_non_fatal` — `remove_collection` raises; ingest proceeds normally
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_search.py -v --no-cov -k "reindex"`
