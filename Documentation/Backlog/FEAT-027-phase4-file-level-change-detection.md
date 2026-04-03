# FEAT-027-P4 — File-Level Change Detection
**Purpose**: `archon update` only re-indexes files that have actually changed — large stable collections sync in seconds instead of minutes
**Audience**: Users with RAG enabled, especially with large or frequently-changing collections
**Status**: To Do

---

## Background
Phase 1 (Done) added per-collection progress visibility via `.indexing_state.json`.
Phase 2 (Done) added pinned-first ordering and partial-readiness health checks.
Phase 3 (Done) added resumable indexing — crash recovery skips already-processed files via `processed_paths`.

Phase 3 treats all files as either "already processed" or "new". It has no concept of a file that was already indexed but whose content has changed, or a file that was deleted from the source directory. Phase 4 adds mtime-based change detection (with opt-in sha256 hash fallback) and deletion detection.

Full spec: `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 4 section.

## Goal
After this phase: when `sync()` runs on an existing collection, it compares each file's current mtime against the stored value. Unchanged files are skipped entirely. Changed files have their old chunks removed and are re-ingested. Deleted files have their chunks removed from LanceDB. If the configured embedding model differs from what was used to index the collection, all mtimes are invalidated and a full re-index is triggered automatically. Chunk size mismatches are warned about by default, with an opt-in config flag to auto-reindex.

---

## Scope

### In Scope
- Add `file_mtimes: dict[str, float]` (path → mtime) to `CollectionProgress`
- Add `file_hashes: dict[str, str]` (path → sha256) to `CollectionProgress` — **schema placeholder only**; hash-based detection is not implemented in Phase 4, but the field is added now to avoid a state schema migration later
- Add `indexed_embedding_model: str` and `indexed_chunk_size: int` to `CollectionProgress` — tracks what config was used to build the index
- Add `auto_reindex_on_chunk_size_change: bool = False` to `RagConfig`
- Extract shared `_iter_eligible_files(path: Path) -> list[Path]` helper — shared by `_ingest_collection`, `_check_collection_changes`, eliminating duplicated filter logic (skip symlinks, hidden dirs, binary extensions)
- `RagStore.delete_by_source_path(collection, source_path)` — delete all chunks for a given source file (computes `doc_id` from path, delegates to `delete_document`)
- Restructure `sync()` algorithm: existing DONE collections enter a `to_check` path that scans for file changes instead of being placed in `unchanged`
- New `_check_collection_changes()` method — per-file mtime comparison; returns lists of new, changed, deleted files
- New `_apply_collection_changes()` method — delete chunks for changed/deleted files, re-ingest new/changed files, update state; FTS index rebuilt once at the end (not per-file)
- Embedding model change detection: `indexed_embedding_model != config.rag.embedding_model` → invalidate all mtimes, force full re-index
- Chunk size change detection: `indexed_chunk_size != config.rag.chunk_size` → warn (default) or auto-invalidate (opt-in)
- `archon doctor` chunk size mismatch warning display
- `SyncResult.updated` — new field for collections with file changes applied
- `archon rag reindex` — already clears all per-collection state (Phase 3); Phase 4 adds `file_mtimes` and `file_hashes` to the cleared fields (automatic via `remove_collection`)
- CLI and MCP sync output reflects the new `updated` field
- `_reset_stale_in_progress()` — preserve new Phase 4 fields when resetting IN_PROGRESS → PENDING
- `_ingest_collection()` — populate `file_mtimes` in the DONE state for new collections (without this, the second sync would treat every file as "new")
- Config injection via constructor: `RagCollectionSync.__init__` receives `embedding_model`, `chunk_size`, `auto_reindex_on_chunk_size_change` — avoids per-call parameter passing and matches the existing `pinned_collections` constructor pattern
- New `RagPipeline.recompute_collection_meta(collection: str)` method — reads all vectors from LanceDB for the collection, recomputes centroid, updates doc/chunk counts, and writes `CollectionMeta`; extracted from `ingest_directory`'s inline metadata update logic
- New `RagStore.get_all_vectors(collection: str) -> list` method — thin LanceDB query helper used by `recompute_collection_meta`

### Out of Scope
- Hash-based detection runtime (`file_hashes` is schema-only; hash comparison deferred to a future phase when per-collection config is added)
- Directory-level change detection — file-level is sufficient
- Tracking renamed files (rename = delete + add)
- Content-addressable deduplication across collections — not planned
- Per-collection hash config in `config.toml` — deferred
- Watch mode (filesystem events) — Phase 8
- Parallel collection processing — state file is not safe for concurrent writes
- Parallel collection scanning — `_check_collection_changes` is pure reads and could run in parallel, but complexity not warranted in Phase 4

---

## Acceptance criteria
- [x] `CollectionProgress` has `file_mtimes: dict[str, float]` field (default empty dict)
- [x] `CollectionProgress` has `file_hashes: dict[str, str]` field (default empty dict) — schema placeholder only
- [x] `CollectionProgress` has `indexed_embedding_model: str` field (default `""`)
- [x] `CollectionProgress` has `indexed_chunk_size: int` field (default `0`)
- [x] `to_dict` serialises all new fields; `from_dict` deserialises them safely (invalid types → defaults)
- [x] `RagConfig` has `auto_reindex_on_chunk_size_change: bool = False`
- [x] Config loader parses `auto_reindex_on_chunk_size_change` from `[rag]` section
- [x] `RagStore.delete_by_source_path(collection, source_path)` computes `doc_id` and delegates to `delete_document()`
- [x] Shared `_iter_eligible_files(path: Path) -> list[Path]` used by both `_ingest_collection` and `_check_collection_changes`
- [x] `sync()` checks existing DONE collections for file changes instead of treating them as `unchanged`
- [x] Files with unchanged mtime are skipped (not re-ingested)
- [x] Files with changed mtime are re-ingested: old chunks deleted first, then new content ingested
- [x] Files present in `file_mtimes` but either missing from disk OR no longer eligible (e.g., renamed to binary extension) have their chunks deleted and are removed from state
- [x] New files (not in `file_mtimes`) are ingested and added to state
- [x] After successful ingest of a file, its mtime is stored in `file_mtimes`
- [x] `_ingest_collection()` populates `file_mtimes` in the DONE state for newly ingested collections
- [x] All `file_mtimes` keys are stored as `str(path.resolve())` — matching the path form used by `ingest_file` to compute `doc_id`
- [x] `_reset_stale_in_progress()` preserves `file_mtimes`, `file_hashes`, `indexed_embedding_model`, `indexed_chunk_size` when resetting IN_PROGRESS → PENDING
- [x] `indexed_embedding_model` and `indexed_chunk_size` are written to state on each collection sync
- [x] If `config.rag.embedding_model != state.indexed_embedding_model`: all mtimes invalidated, full re-index triggered; files deleted from disk still detected and their chunks removed
- [x] If `config.rag.chunk_size != state.indexed_chunk_size` and `auto_reindex_on_chunk_size_change = true`: all mtimes invalidated; files deleted from disk still detected and their chunks removed
- [x] If `config.rag.chunk_size != state.indexed_chunk_size` and `auto_reindex_on_chunk_size_change = false`: warning logged, no auto-reindex
- [ ] `archon doctor` shows chunk size mismatch warning for affected collections
- [x] `SyncResult` has `updated: list[str]` field; collections with file changes appear there
- [x] Collections with zero file changes appear in `result.unchanged` (as before)
- [x] Collections that had file changes successfully applied appear in the sync manifest (and are retained on the next sync instead of being re-ingested as new)
- [x] `_apply_collection_changes` rebuilds FTS index once at the end, not per-file
- [x] If `path.stat()` raises `OSError` in `_check_collection_changes`, the file is treated as changed (not skipped)
- [ ] CLI sync output shows `updated` collections
- [ ] MCP `rag_sync` response includes `updated` field
- [x] Config passed via `RagCollectionSync` constructor (not per-call `sync()` parameters)
- [x] After `_apply_collection_changes` completes, `pipeline.recompute_collection_meta(name)` is called, updating centroid, doc_count, chunk_count in `CollectionMeta`
- [x] In `_apply_collection_changes`, `file.stat().st_mtime` is wrapped in `try/except OSError`; on error, the old mtime is retained for changed files, and no mtime entry is added for new files
- [x] After `_apply_collection_changes` completes, `total_files` and `processed_files` in the DONE state reflect the post-change collection size
- [x] `_ingest_collection()` writes FAILED state with partial `file_mtimes` (for files successfully ingested before the failure)
- [x] All existing tests pass; new tests cover all new code paths

---

## What does NOT change
- `IndexingStatus` enum values — no new statuses
- `IndexingStateStore.read()` / `write()` / `update_collection()` / `remove_collection()` — no API changes
- `progress_cb` signature — unchanged
- Existing callers of `ingest_directory` — zero behaviour change
- `archon rag status` display logic — existing fields drive the display
- State file schema top-level structure — additive only
- The `to_add` and `to_remove` logic in `sync()` — unchanged
- The resume logic (Step 6.5) — unchanged; resume handles IN_PROGRESS/PENDING/FAILED; Phase 4 handles DONE

---

## Known limitations / accepted trade-offs
- **Mtime precision**: some filesystems (FAT32) have 2-second mtime resolution. A file written twice within 2 seconds may not be detected as changed. sha256 hash mode is the workaround.
- **`os.stat()` cost on large collections**: scanning 10k files for mtime costs ~50ms on SSD, ~500ms on HDD — acceptable.
- **Source path SQL injection**: `delete_by_source_path` does NOT embed the raw path in a SQL predicate. It computes `doc_id = sha256(path)` and delegates to `delete_document(doc_id)`, which validates the hex format. Safe by design.
- **File renames**: treated as delete + add. The old chunks are removed, the file is re-ingested at the new path. No content deduplication.
- **Concurrent file writes during scan**: if a file is being written while its mtime is checked, the scan may pick up a partial write. On the next sync, the mtime will differ again and the file will be re-ingested with the complete content.
- **State file size**: `file_mtimes` for 10k files ≈ 1MB (path + float per entry). Combined with `processed_paths` from Phase 3, the state file may reach 2–3MB for large collections. Acceptable; documented in the feature brief. Future optimisation: `file_mtimes` keys subsume `processed_paths` for DONE collections (a path in `file_mtimes` implies it was processed) — this deduplication can halve state file size but is deferred to avoid breaking Phase 3 resume logic.
- **`file_hashes` is a schema placeholder**: the field is added to `CollectionProgress` and serialised, but no hash computation or comparison runs in Phase 4. This avoids a state schema migration when hash-based detection is implemented later.
- **Full re-index with deletions**: when a model or chunk size change triggers a full re-index, files that were deleted from disk since the last sync are detected and cleaned up in the same pass. `deleted_paths` is always computed regardless of model/chunk-size change detection.
- **CollectionMeta update**: `_apply_collection_changes` calls `pipeline.recompute_collection_meta(name)` after FTS rebuild. This requires reading all vectors from LanceDB (full table scan), which adds overhead proportional to collection size. For 10k-file collections, this is acceptable. `recompute_collection_meta` is wrapped in its own `try/except` so that a transient metadata failure does NOT mark the entire apply as FAILED (all file data was correctly ingested). On metadata failure: log a warning, skip the DONE state's centroid fields (leave stale values), and still write DONE state. The next sync run will detect zero file changes (all mtimes match) and skip re-ingestion, so the stale centroid is the only lasting effect until a full reindex.
- **File eligibility changes**: if a file is renamed to have a binary extension (or otherwise stops being eligible for indexing), its old chunks are treated as deleted and removed. The new file under the new name will not be indexed (ineligible). This is the correct behavior.
- **State file deletion causes full re-index**: if the state file is deleted, `indexed_embedding_model` defaults to `""` and the guard skips model change detection. The next sync will treat all files as "new" (no `file_mtimes` entries) and re-index. This is safe (correct result) but slower than necessary. Accepted trade-off vs. storing model info in LanceDB `CollectionMeta`.
- **FTS rebuild strategy**: `_apply_collection_changes` calls `ingest_file` with `rebuild_fts=False` per file and rebuilds FTS once at the end. For large change sets this is significantly faster than per-file rebuilds.

---

## Architecture

### New / changed components

**`archon/rag/progress.py`**
- `CollectionProgress`: four new fields:
  ```python
  file_mtimes: dict[str, float] = field(default_factory=dict)     # path → mtime
  file_hashes: dict[str, str] = field(default_factory=dict)       # path → sha256
  indexed_embedding_model: str = ""
  indexed_chunk_size: int = 0
  ```
- `to_dict`: serialise all new fields
- `from_dict`: parse new fields; validate types; fall back to defaults on mismatch

**`archon/config/loader.py`**
- `RagConfig`: new field `auto_reindex_on_chunk_size_change: bool = False`
- Config parser: read `auto_reindex_on_chunk_size_change` from `[rag]` section

**`archon/rag/store.py`**
- New method:
  ```python
  async def delete_by_source_path(self, collection: str, source_path: str) -> int:
      """Delete all chunks for a source file. Computes doc_id from path."""
      doc_id = hashlib.sha256(str(Path(source_path).resolve()).encode()).hexdigest()
      return await self.delete_document(collection, doc_id)
  ```

**`archon/rag/sync.py`**

- `RagCollectionSync.__init__`: new constructor parameters:
  ```python
  def __init__(
      self,
      pipeline: RagPipeline,
      state_store: IndexingStateStore | None = None,
      pinned_collections: list[str] | None = None,
      embedding_model: str = "",
      chunk_size: int = 0,
      auto_reindex_on_chunk_size_change: bool = False,
  ) -> None:
  ```
  Stored as `self._embedding_model`, `self._chunk_size`, `self._auto_reindex_on_chunk_size_change`. Matches the existing `pinned_collections` constructor pattern — config is set once, not passed per-call.

- `SyncResult`: new field `updated: list[str] = field(default_factory=list)`

- New `_iter_eligible_files(self, path: Path) -> list[Path]`: shared helper that applies the standard filter (skip symlinks, non-files, hidden dirs, binary extensions). Replaces the inline `_BINARY_EXTENSIONS` filter logic in `_ingest_collection` and is reused by `_check_collection_changes`. Single source of truth for file eligibility. Returns a sorted list of `Path` objects; callers that store paths as keys must use `str(path.resolve())`.

- New `_load_file_mtimes(name: str, state: IndexingState | None = None) -> dict[str, float]`: if `state` is provided, extracts from it; otherwise reads state file. Returns `cp.file_mtimes` or `{}` on failure.

- New `_check_collection_changes(name, source_path, file_mtimes, indexed_embedding_model, indexed_chunk_size) -> tuple[list[Path], list[Path], list[str]]`:
  - Uses `_iter_eligible_files` to scan source directory
  - **Path normalization**: file paths are stored and compared as `str(path.resolve())` — the same form used by `ingest_file` to compute `doc_id`. This ensures `delete_by_source_path` targets the correct chunks even when the source directory is a symlink or paths contain `..` segments.
  - Compares each file's `stat().st_mtime` against `file_mtimes[str(file_path.resolve())]`; wraps `path.stat().st_mtime` in `try/except OSError` — on `OSError`, treats the file as changed (force re-ingest on next sync; do not skip)
  - Returns `(new_files, changed_files, deleted_paths)`:
    - `new_files`: paths not in `file_mtimes`
    - `changed_files`: paths where mtime differs
    - `deleted_paths`: paths in `file_mtimes` where `not (Path(p).is_file() and not Path(p).is_symlink())` **OR** the path is no longer in the `_iter_eligible_files` output (e.g., renamed to binary extension). Using the eligible set as the reference ensures renamed-to-binary files have their old chunks cleaned up.
  - If `self._embedding_model != indexed_embedding_model` and `indexed_embedding_model != ""`: all files treated as "changed" (full re-index); `deleted_paths` is still computed using the same eligibility rule as normal detection (existence + eligibility check) — the early return does NOT skip deletion detection
  - If `self._chunk_size != indexed_chunk_size` and `indexed_chunk_size != 0`:
    - If `self._auto_reindex_on_chunk_size_change`: all files treated as "changed"; `deleted_paths` is still computed as above
    - Else: log warning, no invalidation

- New `_apply_collection_changes(name, source_path, new_files, changed_files, deleted_paths, file_mtimes, progress_cb) -> str | None`:
  - Acquire per-collection lock
  - Write IN_PROGRESS state
  - For each deleted path: `store.delete_by_source_path(collection, path)`, remove from `file_mtimes` and `processed_paths`
  - For each changed file: call `pipeline.ingest_file(path, collection, rebuild_fts=False)` directly — `ingest_file` handles delete-before-insert internally; explicit `delete_by_source_path` is NOT needed for changed files. Then try `file_mtimes[str(path.resolve())] = path.stat().st_mtime`; on `OSError` (file deleted between ingest and mtime write), leave `file_mtimes` unchanged (old mtime retained — file will be re-ingested on next sync, which is safe).
  - For each new file: `pipeline.ingest_file(path, collection, rebuild_fts=False)`, add to `processed_paths`; try to set `file_mtimes[str(path.resolve())] = path.stat().st_mtime`; on `OSError`, skip the `file_mtimes` entry (file will appear as "new" on next sync, which is safe).
  - After all files: `store.rebuild_fts_index(collection)` — single FTS rebuild at the end
  - After FTS rebuild: call `pipeline.recompute_collection_meta(name)` wrapped in its own `try/except` — a transient metadata failure does NOT fail the entire apply. On metadata failure: log a warning, leave centroid stale, and continue to write DONE state. On success: centroid, doc_count, chunk_count are updated. Description regeneration logic (`_should_regenerate`) applies as in `ingest_directory`.
  - Write state at 50-file batch boundaries (same cadence as Phase 1/3)
  - On completion: write DONE state with `indexed_embedding_model = self._embedding_model`, `indexed_chunk_size = self._chunk_size`, `total_files = len(file_mtimes)` (reflects current collection size after deletions and additions), `processed_files = len(file_mtimes)`
  - On exception (from file processing, not from metadata update): write FAILED state preserving partial `file_mtimes` updates
  - Returns `None` on success, error string on failure

- `_ingest_collection()` updated: on DONE state write, populate `file_mtimes` from `new_paths` (compute mtime for each path) and set `indexed_embedding_model` / `indexed_chunk_size`. Without this, the second sync would treat every file as "new". Uses `_iter_eligible_files` instead of inline filter logic.

- `_reset_stale_in_progress()` updated: preserve `file_mtimes`, `file_hashes`, `indexed_embedding_model`, `indexed_chunk_size` when constructing the PENDING replacement

- `sync()` Step 7 restructured:
  ```
  # Current: unchanged = (existing & desired) - to_resume
  # Phase 4:
  to_check = (existing & desired) - to_resume    # DONE collections to scan
  to_update = set()
  successfully_updated: set[str] = set()
  for name in sorted_to_check:
      # Read state once to get both file_mtimes and indexed config fields
      state = self._state_store.read() if self._state_store else None
      file_mtimes = self._load_file_mtimes(name, state=state)  # reuses pre-read state
      cp = state.collections.get(name) if state else None
      indexed_model = cp.indexed_embedding_model if cp else ""
      indexed_cs = cp.indexed_chunk_size if cp else 0
      new_f, changed_f, deleted_p = self._check_collection_changes(
          name, p, file_mtimes,
          indexed_embedding_model=indexed_model,
          indexed_chunk_size=indexed_cs,
      )
      if new_f or changed_f or deleted_p:
          to_update.add(name)
          error = await self._apply_collection_changes(...)
          if error is None:
              result.updated.append(name)
              successfully_updated.add(name)  # tracked for Step 8 manifest
          else:
              result.errors.append(error)
  # Step 8: unchanged = to_check - to_update
  unchanged = to_check - to_update
  result.unchanged = sorted(unchanged)
  # Step 9: update manifest to retain successfully_updated collections
  # (existing manifest write must include: successfully_added | successfully_updated | unchanged)
  # Key change from pre-Phase-4: successfully_updated is added to the manifest condition
  ```

**`archon/cli/rag_cmd.py`**
- `_run_sync`: log `result.updated` alongside `result.added`/`result.removed`

**`archon/ai/archon_toolkit_rag.py`**
- `_handle_rag_sync`: include `updated` in JSON response

**`archon/cli/doctor.py`**
- `_check_rag_health`: read `indexed_chunk_size` from state; compare against `config.rag.chunk_size`; if mismatch: `⚠️ {name} — chunk size mismatch (indexed: {indexed}, config: {configured})`

### State schema change (additive)
```json
{
  "collections": {
    "sessions": {
      "status": "done",
      "total_files": 340,
      "processed_files": 340,
      "started_at": "2026-04-03T10:00:00Z",
      "completed_at": "2026-04-03T10:05:30Z",
      "error": null,
      "error_count": 0,
      "processed_paths": ["/Users/me/.archon/history/sessions/2026-01-01.md", "..."],
      "file_mtimes": {
        "/Users/me/.archon/history/sessions/2026-01-01.md": 1743681234.567,
        "...": 0
      },
      "file_hashes": {},
      "indexed_embedding_model": "BAAI/bge-small-en-v1.5",
      "indexed_chunk_size": 512
    }
  },
  "last_updated": "2026-04-03T10:05:30Z"
}
```

---

## Tests

### Unit — `tests/rag/test_progress.py`
- `test_collection_progress_file_mtimes_default` — default `file_mtimes` is `{}`
- `test_collection_progress_file_hashes_default` — default `file_hashes` is `{}`
- `test_collection_progress_indexed_embedding_model_default` — default is `""`
- `test_collection_progress_indexed_chunk_size_default` — default is `0`
- `test_to_dict_includes_file_mtimes` — serialised dict contains `file_mtimes` key
- `test_to_dict_includes_file_hashes` — serialised dict contains `file_hashes` key
- `test_to_dict_includes_indexed_embedding_model` — serialised dict contains `indexed_embedding_model`
- `test_to_dict_includes_indexed_chunk_size` — serialised dict contains `indexed_chunk_size`
- `test_from_dict_parses_file_mtimes` — valid dict round-trips correctly
- `test_from_dict_parses_file_hashes` — valid dict round-trips correctly
- `test_from_dict_parses_indexed_embedding_model` — string round-trips correctly
- `test_from_dict_parses_indexed_chunk_size` — int round-trips correctly
- `test_from_dict_invalid_file_mtimes_type` — non-dict value → `{}`
- `test_from_dict_invalid_file_hashes_type` — non-dict value → `{}`
- `test_from_dict_invalid_indexed_embedding_model_type` — non-string → `""`
- `test_from_dict_invalid_indexed_chunk_size_type` — non-int → `0`
- `test_from_dict_file_mtimes_with_non_float_values` — dict with non-float values → `{}`
- `test_from_dict_file_mtimes_with_int_values` — `{"a": 1}` (int mtime) → `{"a": 1.0}` (converted to float, NOT rejected)

### Unit — `tests/config/test_config.py`
- `test_rag_auto_reindex_on_chunk_size_change_default` — default is `False`
- `test_rag_auto_reindex_on_chunk_size_change_true` — parsed correctly from TOML

### Unit — `tests/rag/test_store.py`
- `test_delete_by_source_path_computes_doc_id` — doc_id matches `sha256(resolved_path)`
- `test_delete_by_source_path_delegates_to_delete_document` — calls `delete_document` with correct doc_id
- `test_delete_by_source_path_returns_count` — returns count from `delete_document`
- `test_delete_by_source_path_collection_not_found` — returns 0

### Unit — `tests/rag/test_sync.py`
- `test_check_collection_changes_no_changes` — all mtimes match → empty lists
- `test_check_collection_changes_new_file` — file not in mtimes → in `new_files`
- `test_check_collection_changes_changed_mtime` — mtime differs → in `changed_files`
- `test_check_collection_changes_deleted_file` — path in mtimes but not on disk → in `deleted_paths`
- `test_check_collection_changes_deleted_symlink` — path in mtimes, replaced by symlink → in `deleted_paths`
- `test_check_collection_changes_embedding_model_changed` — all files treated as changed
- `test_check_collection_changes_chunk_size_changed_auto_reindex` — all files treated as changed
- `test_check_collection_changes_chunk_size_changed_no_auto_reindex` — only warning logged, no invalidation
- `test_check_collection_changes_first_sync_embedding_model_guard_skipped` — `indexed_embedding_model=""`, `self._embedding_model="bge"` → guard skipped, normal per-file detection proceeds (no full re-index)
- `test_check_collection_changes_first_sync_chunk_size_guard_skipped` — `indexed_chunk_size=0`, `self._chunk_size=512` → no warning, normal detection proceeds
- `test_load_file_mtimes_state_store_none` — returns `{}`
- `test_load_file_mtimes_no_state_file` — returns `{}`
- `test_load_file_mtimes_collection_absent` — returns `{}`
- `test_iter_eligible_files_skips_symlinks_hidden_binary` — filter logic matches expectations
- `test_reset_stale_preserves_phase4_fields` — IN_PROGRESS state with `file_mtimes`, `indexed_embedding_model` → preserved after reset

### Integration — `tests/rag/test_sync.py`
- `test_sync_detects_new_files_in_existing_collection` — new file in directory → ingested, mtime stored
- `test_sync_detects_changed_files` — modified file (mtime differs) → old chunks deleted, re-ingested
- `test_sync_detects_deleted_files` — file removed from disk → chunks deleted, removed from state
- `test_sync_skips_unchanged_files` — mtime matches → file not in any ingest call
- `test_sync_updates_file_mtimes_in_state` — after sync, `file_mtimes` contains all current files
- `test_sync_result_includes_updated` — collection with changes → in `result.updated`
- `test_sync_unchanged_collection_not_in_updated` — collection with zero changes → in `result.unchanged`, not `updated`
- `test_sync_embedding_model_change_triggers_full_reindex` — model mismatch → all files treated as changed
- `test_sync_chunk_size_change_warns_only` — `auto_reindex=false` → warning logged, no re-ingest
- `test_sync_chunk_size_change_auto_reindex` — `auto_reindex=true` → full re-ingest triggered
- `test_sync_stores_indexed_model_and_chunk_size` — after sync, state has `indexed_embedding_model` and `indexed_chunk_size`
- `test_sync_new_collection_populates_file_mtimes` — new collection ingested → DONE state has `file_mtimes` for all files
- `test_sync_apply_changes_batched_state_writes` — state written every 50 files during change application
- `test_sync_apply_changes_failed_midway` — 3 files to process (1 delete, 2 new); second new file's `ingest_file` raises; FAILED state has: deleted path removed from `file_mtimes`, first new file added to `file_mtimes`, second new file NOT in `file_mtimes` (failure before mtime update)
- `test_sync_apply_changes_fts_rebuilt_once` — FTS rebuild called once at end, not per-file
- `test_sync_apply_changes_updates_collection_meta` — after `_apply_collection_changes` completes, `pipeline.recompute_collection_meta` is called once
- `test_sync_mixed_changes` — same sync: one file new, one changed, one deleted, two unchanged → correct result
- `test_sync_resume_then_change_detection` — crash during initial ingest, resume completes, next sync correctly detects changes only in files modified after the resume
- `test_sync_apply_changes_deletion_only` — only deletions, no new/changed files → `rebuild_fts_index` called once, `ingest_file` NOT called
- `test_sync_apply_changes_processed_paths_consistent` — after successful apply, every key in `file_mtimes` is in `processed_paths` for DONE collections
- `test_sync_apply_changes_error_not_in_unchanged` — collection with detected changes but failed apply: in `result.errors`, NOT in `result.unchanged`
- `test_sync_apply_changes_ingest_failure_preserves_old_mtime` — `ingest_file` fails for a changed file: `file_mtimes` retains the OLD mtime (ensures retry on next sync)
- `test_ingest_collection_failed_state_has_partial_file_mtimes` — `_ingest_collection` exception mid-ingest: FAILED state has `file_mtimes` for successfully ingested files
- `test_sync_file_vanishes_between_check_and_apply` — file detected as "new" in `_check_collection_changes` but deleted before `ingest_file` is called: handled gracefully (no crash, no mtime entry added)

### Unit — `tests/cli/test_rag_cmd.py`
- `test_run_sync_output_includes_updated` — CLI sync output lists updated collections

### Unit — `tests/cli/test_doctor.py`
- `test_doctor_chunk_size_mismatch_warning` — state has `indexed_chunk_size=512`, config has `chunk_size=256` → warning displayed

### Unit — `tests/ai/test_archon_toolkit_rag.py`
- `test_handle_rag_sync_response_includes_updated` — MCP response JSON has `updated` field

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 4: mark as Done ✅ when complete
- [ ] `examples/config.toml.example`, `[rag]` section: add `auto_reindex_on_chunk_size_change` with comment
- [ ] `CLAUDE.md`, `archon/rag/` section: mention `_iter_eligible_files` and `delete_by_source_path` if surfaced in the component catalog

---

## Task breakdown

### Phase 4 — File-Level Change Detection
> **Releasable**: after Task 4.6 (sync detects and applies file changes end-to-end); Tasks 4.7–4.10 are independently releasable reporting/display improvements

#### Task 4.1 — `CollectionProgress` new fields + serialization
- [x] **File**: `archon/rag/progress.py`
- **Depends on**: nothing
- **Description**:
  - Add four new fields to `CollectionProgress`:
    ```python
    file_mtimes: dict[str, float] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)
    indexed_embedding_model: str = ""
    indexed_chunk_size: int = 0
    ```
  - `to_dict`: add all four fields to the serialised dict
  - `from_dict`: parse each new field with type validation:
    - `file_mtimes`: must be `dict` with all `str` keys and `float`/`int` values; fall back to `{}` on any type error (convert int values to float)
    - `file_hashes`: must be `dict` with all `str` keys and `str` values; fall back to `{}` on any type error
    - `indexed_embedding_model`: must be `str`; fall back to `""` on any type error
    - `indexed_chunk_size`: use `_safe_int()` with default `0`
- **Releasable**: after this task, `CollectionProgress` can carry and persist file change tracking data; state file round-trips cleanly
- **Tests (TDD)** — `tests/rag/test_progress.py`:
  - Unit: `test_collection_progress_file_mtimes_default` — default is `{}`
  - Unit: `test_collection_progress_file_hashes_default` — default is `{}`
  - Unit: `test_collection_progress_indexed_embedding_model_default` — default is `""`
  - Unit: `test_collection_progress_indexed_chunk_size_default` — default is `0`
  - Unit: `test_to_dict_includes_file_mtimes` — key present with correct value
  - Unit: `test_to_dict_includes_file_hashes` — key present with correct value
  - Unit: `test_to_dict_includes_indexed_embedding_model` — key present
  - Unit: `test_to_dict_includes_indexed_chunk_size` — key present
  - Unit: `test_from_dict_parses_file_mtimes` — `{"a": 1.0}` round-trips
  - Unit: `test_from_dict_parses_file_hashes` — `{"a": "abc123"}` round-trips
  - Unit: `test_from_dict_parses_indexed_embedding_model` — `"BAAI/bge-small-en-v1.5"` round-trips
  - Unit: `test_from_dict_parses_indexed_chunk_size` — `512` round-trips
  - Unit: `test_from_dict_invalid_file_mtimes_type` — non-dict → `{}`
  - Unit: `test_from_dict_invalid_file_hashes_type` — non-dict → `{}`
  - Unit: `test_from_dict_invalid_indexed_embedding_model_type` — `42` → `""`
  - Unit: `test_from_dict_invalid_indexed_chunk_size_type` — `"abc"` → `0`
  - Unit: `test_from_dict_file_mtimes_with_non_float_values` — `{"a": "bad"}` → `{}`
  - Unit: `test_from_dict_file_mtimes_with_int_values` — `{"a": 1}` (int mtime) → `{"a": 1.0}` (converted to float, NOT rejected)
  - Checkpoint: `uv run pytest tests/rag/test_progress.py -v --no-cov`

#### Task 4.2 — `auto_reindex_on_chunk_size_change` config flag
- [x] **File**: `archon/config/loader.py`
- **Depends on**: nothing
- **Description**:
  - Add `auto_reindex_on_chunk_size_change: bool = False` to `RagConfig` dataclass
  - In the config parsing section, read `bool(rag_data.get("auto_reindex_on_chunk_size_change", False))` and pass to `RagConfig` constructor
  - No validation needed (bool); `bool()` cast handles non-bool TOML values
- **Releasable**: after this task, the config flag is available for Task 4.5 to use
- **Tests (TDD)** — `tests/config/test_config.py`:
  - Unit: `test_rag_auto_reindex_on_chunk_size_change_default` — absent from TOML → `False`
  - Unit: `test_rag_auto_reindex_on_chunk_size_change_true` — `auto_reindex_on_chunk_size_change = true` in TOML → `True`
  - Checkpoint: `uv run pytest tests/config/test_config.py -v --no-cov -k "auto_reindex"`

#### Task 4.3 — `RagStore.delete_by_source_path()`
- [x] **File**: `archon/rag/store.py`
- **Depends on**: nothing
- **Description**:
  - Add method to `RagStore`:
    ```python
    async def delete_by_source_path(self, collection: str, source_path: str) -> int:
        """Delete all chunks for a source file by computing its doc_id."""
        doc_id = hashlib.sha256(str(Path(source_path).resolve()).encode()).hexdigest()
        return await self.delete_document(collection, doc_id)
    ```
  - Import `hashlib` if not already imported in `store.py`
  - No SQL injection risk: delegates to `delete_document` which validates `doc_id` format
- **Releasable**: after this task, any caller can delete all chunks for a source file by path
- **Tests (TDD)** — `tests/rag/test_store.py`:
  - Unit: `test_delete_by_source_path_computes_doc_id` — mock `delete_document`; verify called with `sha256(str(Path(source_path).resolve()))`
  - Unit: `test_delete_by_source_path_delegates_to_delete_document` — return value from `delete_document` is propagated
  - Unit: `test_delete_by_source_path_returns_count` — returns count from `delete_document`
  - Unit: `test_delete_by_source_path_collection_not_found` — `delete_document` returns 0 → returns 0
  - Checkpoint: `uv run pytest tests/rag/test_store.py -v --no-cov -k "delete_by_source_path"`

#### Task 4.4 — `_iter_eligible_files` shared helper + `_reset_stale_in_progress` update
- [x] **File**: `archon/rag/sync.py`
- **Depends on**: Task 4.1
- **Description**:
  - Extract `_iter_eligible_files(self, path: Path) -> list[Path]`: shared helper that applies the standard filter logic currently duplicated inline in `_ingest_collection` (skip symlinks, non-files, hidden dirs, binary extensions via `_BINARY_EXTENSIONS`). Returns sorted list matching the existing behaviour. Callers that store paths as keys must use `str(path.resolve())`.
  - Refactor `_ingest_collection` to use `_iter_eligible_files` instead of its inline filter loop (lines ~292-303). This is a pure refactor — behaviour is identical.
  - Update `_reset_stale_in_progress()` to preserve Phase 4 fields when constructing the PENDING replacement:
    ```python
    state.collections[name] = CollectionProgress(
        status=IndexingStatus.PENDING,
        total_files=cp.total_files,
        processed_files=cp.processed_files,
        processed_paths=cp.processed_paths,
        file_mtimes=cp.file_mtimes,           # NEW
        file_hashes=cp.file_hashes,           # NEW
        indexed_embedding_model=cp.indexed_embedding_model,  # NEW
        indexed_chunk_size=cp.indexed_chunk_size,            # NEW
    )
    ```
- **Releasable**: after this task, filter logic is shared (no duplication risk), and crash recovery preserves Phase 4 state
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_iter_eligible_files_skips_symlinks_hidden_binary` — verify filter behaviour with temp dir containing symlink, hidden file, `.pyc` file, and valid `.md` file
  - Unit: `test_iter_eligible_files_returns_sorted` — output is sorted by path
  - Unit: `test_reset_stale_preserves_phase4_fields` — IN_PROGRESS state with `file_mtimes={"a": 1.0}`, `indexed_embedding_model="bge"`, `indexed_chunk_size=512`; after reset, PENDING state preserves all four new fields
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -v --no-cov -k "iter_eligible or reset_stale_preserves_phase4"`

#### Task 4.5 — `SyncResult.updated` + `_load_file_mtimes` + `_check_collection_changes` + constructor config
- [x] **File**: `archon/rag/sync.py`
- **Depends on**: Task 4.4
- **Description**:
  - Add `updated: list[str] = field(default_factory=list)` to `SyncResult`
  - Add constructor parameters to `RagCollectionSync.__init__`:
    ```python
    def __init__(
        self,
        pipeline: RagPipeline,
        state_store: IndexingStateStore | None = None,
        pinned_collections: list[str] | None = None,
        embedding_model: str = "",
        chunk_size: int = 0,
        auto_reindex_on_chunk_size_change: bool = False,
    ) -> None:
    ```
    Stored as `self._embedding_model`, `self._chunk_size`, `self._auto_reindex_on_chunk_size_change`.
  - Add `_load_file_mtimes(self, name: str, state: IndexingState | None = None) -> dict[str, float]`: if `state` is provided, extracts `file_mtimes` from the pre-read state; otherwise reads state via `_state_store.read()`. Returns `state.collections[name].file_mtimes` or `{}` on any failure. The `state` parameter allows Step 7 to read state once and reuse it for both `file_mtimes` and `indexed_embedding_model`/`indexed_chunk_size`, avoiding a double filesystem read.
  - Add `_check_collection_changes(self, name: str, source_path: Path, file_mtimes: dict[str, float], indexed_embedding_model: str, indexed_chunk_size: int) -> tuple[list[Path], list[Path], list[str]]`:
    - If `self._embedding_model != indexed_embedding_model` and `indexed_embedding_model != ""`: log info "Embedding model changed (%s → %s), full re-index"; return all source files (via `_iter_eligible_files`) as `changed_files`, empty `new_files`; `deleted_paths` is STILL computed (do NOT return empty — paths in `file_mtimes` that no longer exist on disk must be cleaned up)
    - If `self._chunk_size != indexed_chunk_size` and `indexed_chunk_size != 0`:
      - If `self._auto_reindex_on_chunk_size_change`: log info "Chunk size changed, full re-index"; return all files as changed; `deleted_paths` is STILL computed as above
      - Else: log warning "Chunk size mismatch (indexed: %d, config: %d) — run `archon rag reindex` to update" — no invalidation
    - Use `_iter_eligible_files` to scan source directory
    - For each file: compare `path.stat().st_mtime` against `file_mtimes.get(str(path.resolve()))`; wrap `path.stat()` in `try/except OSError` — on `OSError`, treat as changed (not skipped)
      - Not in `file_mtimes` → `new_files`
      - Mtime differs → `changed_files`
      - `OSError` from `stat()` → `changed_files` (force re-ingest)
      - Mtime matches → skip
    - `deleted_paths`: keys in `file_mtimes` where `not (Path(p).is_file() and not Path(p).is_symlink())` **OR** the path is no longer in the `_iter_eligible_files` output (e.g., renamed to binary extension). Using the eligible set as the reference ensures renamed-to-binary files have their old chunks cleaned up.
    - Returns `(new_files, changed_files, deleted_paths)`
- **Releasable**: after this task, the change detection logic is testable in isolation; `SyncResult` carries the `updated` field; config is injectable via constructor
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Unit: `test_sync_result_has_updated_field` — `SyncResult()` has `updated` defaulting to `[]`
  - Unit: `test_load_file_mtimes_state_store_none` — returns `{}`
  - Unit: `test_load_file_mtimes_no_state_file` — returns `{}`
  - Unit: `test_load_file_mtimes_collection_absent` — returns `{}`
  - Unit: `test_check_collection_changes_no_changes` — all mtimes match → `([], [], [])`
  - Unit: `test_check_collection_changes_new_file` — file not in mtimes → `([new_path], [], [])`
  - Unit: `test_check_collection_changes_changed_mtime` — mtime differs → `([], [changed_path], [])`
  - Unit: `test_check_collection_changes_deleted_file` — path in mtimes, not on disk → `([], [], [deleted_path])`
  - Unit: `test_check_collection_changes_deleted_symlink` — path in mtimes, replaced by symlink → `([], [], [deleted_path])`
  - Unit: `test_check_collection_changes_embedding_model_changed` — all files treated as changed
  - Unit: `test_check_collection_changes_chunk_size_changed_auto_reindex` — all files treated as changed
  - Unit: `test_check_collection_changes_chunk_size_changed_no_auto_reindex` — warning logged, no invalidation, change detection still runs normally
  - Unit: `test_check_collection_changes_first_sync_embedding_model_guard_skipped` — `indexed_embedding_model=""`, `self._embedding_model="bge"` → guard skipped, normal per-file detection proceeds (no full re-index)
  - Unit: `test_check_collection_changes_first_sync_chunk_size_guard_skipped` — `indexed_chunk_size=0`, `self._chunk_size=512` → no warning, normal detection proceeds
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -v --no-cov -k "check_collection_changes or load_file_mtimes or sync_result_has_updated"`

#### Task 4.6 — `_apply_collection_changes` + `_ingest_collection` mtime population + `sync()` restructure
- [x] **File**: `archon/rag/sync.py`
- **Depends on**: Task 4.3, Task 4.5
- **Description**:
  a. **`_apply_collection_changes`**: new method:
    ```python
    async def _apply_collection_changes(
        self, name: str, source_path: Path,
        new_files: list[Path], changed_files: list[Path], deleted_paths: list[str],
        file_mtimes: dict[str, float], progress_cb: ...,
    ) -> str | None:
    ```
    - Acquire per-collection lock
    - Read current state to get `processed_paths`
    - Write IN_PROGRESS state (preserving `file_mtimes` and `processed_paths`)
    - Process deletions: for each path in `deleted_paths`, call `store.delete_by_source_path(name, path)`, remove from `file_mtimes` and `processed_paths`
    - Process changed files: for each file in `changed_files`, call `pipeline.ingest_file(file, name, rebuild_fts=False)` directly — `ingest_file` handles delete-before-insert internally. Explicit `delete_by_source_path` is NOT called for changed files (would cause a double-delete). Then try `file_mtimes[str(file.resolve())] = file.stat().st_mtime`; on `OSError` (file deleted between ingest and mtime write), leave `file_mtimes` unchanged (old mtime retained — file will be re-ingested on next sync, which is safe).
    - Process new files: for each file in `new_files`, call `pipeline.ingest_file(file, name, rebuild_fts=False)`, add to `processed_paths`; try to set `file_mtimes[str(file.resolve())] = file.stat().st_mtime`; on `OSError`, skip the `file_mtimes` entry (file will appear as "new" on next sync, which is safe).
    - **Note**: `delete_by_source_path` is only used for deleted files (no re-ingest step). Changed files use `ingest_file` directly.
    - After all files processed: `store.rebuild_fts_index(name)` — single FTS rebuild
    - After FTS rebuild: call `pipeline.recompute_collection_meta(name)` wrapped in its own `try/except` — a metadata failure does NOT fail the entire apply. On failure: log warning, continue to DONE. Implementation of `recompute_collection_meta`: query all vectors from LanceDB (e.g., `table.query().select(["vector", "doc_id"]).to_list()`), use existing `_compute_centroid(all_vectors)`, query distinct `doc_id` count for `doc_count`, total rows for `chunk_count`, write via `store.update_collection_meta(CollectionMeta(...))`. Add `RagStore.get_all_vectors(collection)` as a thin LanceDB query helper. `_compute_centroid` and `store.update_collection_meta` already exist; only `recompute_collection_meta` and `get_all_vectors` are new.
    - Batch state writes every 50 files (same cadence as Phase 1/3)
    - On completion: write DONE state with `file_mtimes`, `processed_paths`, `indexed_embedding_model = self._embedding_model`, `indexed_chunk_size = self._chunk_size`, `total_files = len(file_mtimes)` (reflects current collection size after deletions and additions), `processed_files = len(file_mtimes)`
    - On exception (from file processing): write FAILED state preserving partial `file_mtimes` updates
    - Returns `None` on success, error string on failure

  b. **`_ingest_collection` update**: populate `file_mtimes` in the DONE state for new collections. After successful ingestion, build `file_mtimes` from `new_paths`:
    ```python
    final_mtimes = {}
    for p_str in (resume_paths + new_paths):
        try:
            resolved = str(Path(p_str).resolve())
            final_mtimes[resolved] = Path(resolved).stat().st_mtime
        except OSError:
            pass  # file may have been deleted between ingest and state write
    ```
    Include `file_mtimes=final_mtimes`, `indexed_embedding_model=self._embedding_model`, `indexed_chunk_size=self._chunk_size` in both DONE and FAILED state writes.

  c. **`sync()` Step 7 restructure**: replace `unchanged = existing_and_desired - to_resume` with:
    ```python
    # Step 7: check existing DONE collections for file changes
    to_check = existing_and_desired - to_resume
    to_update: set[str] = set()
    sorted_to_check = self._sort_ingestion_order(to_check, desired)
    for name in sorted_to_check:
        path_str = desired[name]
        p = Path(path_str)
        if not p.exists():
            result.errors.append(f"path does not exist: {path_str}")
            continue
        state = self._state_store.read() if self._state_store else None
        file_mtimes = self._load_file_mtimes(name, state=state)  # reuses pre-read state
        cp = state.collections.get(name) if state else None
        indexed_model = cp.indexed_embedding_model if cp else ""
        indexed_cs = cp.indexed_chunk_size if cp else 0
        new_f, changed_f, deleted_p = self._check_collection_changes(
            name, p, file_mtimes,
            indexed_embedding_model=indexed_model,
            indexed_chunk_size=indexed_cs,
        )
        if new_f or changed_f or deleted_p:
            to_update.add(name)
            error = await self._apply_collection_changes(
                name, p, new_f, changed_f, deleted_p, file_mtimes, progress_cb,
            )
            if error is None:
                result.updated.append(name)
            else:
                result.errors.append(error)
    # Step 8: unchanged = to_check - to_update
    unchanged = to_check - to_update
    result.unchanged.extend(sorted(unchanged))
    # Step 9: update manifest to retain successfully_updated collections
    # (existing manifest write must include: successfully_added | successfully_updated | unchanged)
    # Key change from pre-Phase-4: successfully_updated is added to the manifest condition
    ```
- **Releasable**: after this task, `sync()` detects and applies file-level changes end-to-end — the core Phase 4 functionality is complete
- **Tests (TDD)** — `tests/rag/test_sync.py`:
  - Integration: `test_sync_detects_new_files_in_existing_collection` — new file in DONE collection → ingested, in `result.updated`
  - Integration: `test_sync_detects_changed_files` — modified file → old chunks deleted, re-ingested, in `result.updated`
  - Integration: `test_sync_detects_deleted_files` — file removed → chunks deleted, removed from state
  - Integration: `test_sync_skips_unchanged_files` — mtime matches → not in any ingest/delete call
  - Integration: `test_sync_updates_file_mtimes_in_state` — after sync, `file_mtimes` has correct values
  - Integration: `test_sync_result_includes_updated` — collection with changes in `result.updated`
  - Integration: `test_sync_unchanged_collection_not_in_updated` — zero changes → `result.unchanged`
  - Integration: `test_sync_embedding_model_change_triggers_full_reindex` — model mismatch → all files re-ingested
  - Integration: `test_sync_chunk_size_change_warns_only` — auto=false → warning, no re-ingest
  - Integration: `test_sync_chunk_size_change_auto_reindex` — auto=true → full re-ingest
  - Integration: `test_sync_stores_indexed_model_and_chunk_size` — state has correct `indexed_embedding_model` and `indexed_chunk_size`
  - Integration: `test_sync_new_collection_populates_file_mtimes` — new collection ingested → DONE state has `file_mtimes` for all files with correct mtime values
  - Integration: `test_sync_apply_changes_batched_state_writes` — state written at 50-file boundary
  - Integration: `test_sync_apply_changes_failed_midway` — 3 files to process (1 delete, 2 new); second new file's `ingest_file` raises; FAILED state has: deleted path removed from `file_mtimes`, first new file added to `file_mtimes`, second new file NOT in `file_mtimes` (failure before mtime update)
  - Integration: `test_sync_apply_changes_fts_rebuilt_once` — `rebuild_fts_index` called once at end, `ingest_file` called with `rebuild_fts=False`
  - Integration: `test_sync_apply_changes_updates_collection_meta` — after `_apply_collection_changes` completes, `pipeline.recompute_collection_meta` is called once
  - Integration: `test_sync_mixed_changes` — new + changed + deleted + unchanged → correct categorisation
  - Integration: `test_sync_resume_then_change_detection` — crash during initial ingest (state has partial `processed_paths` + partial `file_mtimes`), resume completes, next sync correctly detects changes only in files modified after the resume
  - Integration: `test_sync_apply_changes_deletion_only` — only deletions, no new/changed files → `rebuild_fts_index` called once, `ingest_file` NOT called
  - Integration: `test_sync_apply_changes_processed_paths_consistent` — after successful apply, every key in `file_mtimes` is in `processed_paths` for DONE collections
  - Integration: `test_sync_apply_changes_error_not_in_unchanged` — collection with detected changes but failed apply: in `result.errors`, NOT in `result.unchanged`
  - Integration: `test_sync_apply_changes_ingest_failure_preserves_old_mtime` — `ingest_file` fails for a changed file: `file_mtimes` retains the OLD mtime (ensures retry on next sync)
  - Integration: `test_ingest_collection_failed_state_has_partial_file_mtimes` — `_ingest_collection` exception mid-ingest: FAILED state has `file_mtimes` for successfully ingested files
  - Integration: `test_sync_file_vanishes_between_check_and_apply` — file detected as "new" in `_check_collection_changes` but deleted before `ingest_file` is called: handled gracefully (no crash, no mtime entry added)
  - Checkpoint: `uv run pytest tests/rag/test_sync.py -v --no-cov -k "test_sync_detects or test_sync_skips or test_sync_updates_file or test_sync_result_includes_updated or test_sync_unchanged_collection_not_in_updated or test_sync_embedding or test_sync_chunk_size or test_sync_stores_indexed or test_sync_new_collection_populates or test_sync_apply_changes or test_sync_mixed or test_sync_resume_then or test_ingest_collection_failed or test_sync_file_vanishes"`

#### Task 4.7 — Wire config parameters through `RagCollectionSync` constructors
- [ ] **File**: `archon/rag/server.py`, `archon/cli/rag_cmd.py`, `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 4.5 (constructor change)
- **Description**:
  - Update all sites that construct `RagCollectionSync(...)` to pass the new constructor parameters:
    - `server.py`: `embedding_model=config.rag.embedding_model`, `chunk_size=config.rag.chunk_size`, `auto_reindex_on_chunk_size_change=config.rag.auto_reindex_on_chunk_size_change`
    - CLI `rag_cmd.py`: same — read from loaded config
    - MCP `archon_toolkit_rag.py`: same
  - These are pass-through changes — the callers already have access to config
- **Releasable**: after this task, all sync entry points pass config to enable change detection
- **Tests (TDD)** — `tests/rag/test_sync.py`, `tests/cli/test_rag_cmd.py`, `tests/ai/test_archon_toolkit_rag.py`:
  - Integration: `test_server_sync_passes_config_params` — mock `RagCollectionSync`; verify constructor receives `embedding_model`, `chunk_size`, `auto_reindex_on_chunk_size_change`
  - Unit: `test_cli_sync_passes_config_params` — same for CLI caller
  - Unit: `test_mcp_sync_passes_config_params` — same for MCP caller
  - Checkpoint: `uv run pytest tests/rag/test_sync.py tests/cli/test_rag_cmd.py tests/ai/test_archon_toolkit_rag.py -v --no-cov -k "passes_config_params"`

#### Task 4.8 — CLI sync output for `updated` collections
- [ ] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 4.5 (SyncResult.updated field)
- **Description**:
  - In `_run_sync` (or wherever sync result is logged to stdout): add output for `result.updated` alongside existing `result.added`/`result.removed`
  - Format: `"  ↻ {name}"` for each updated collection
  - Summary line: include `updated` count
- **Releasable**: after this task, CLI users see which collections had file changes applied
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_run_sync_output_includes_updated` — mock sync returning `SyncResult(updated=["sessions"])`; verify stdout contains "sessions" and the update indicator
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -v --no-cov -k "sync_output_includes_updated"`

#### Task 4.9 — MCP `rag_sync` response includes `updated` field
- [ ] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 4.5 (SyncResult.updated field)
- **Description**:
  - In `_handle_rag_sync`: add `"updated": result.updated` to the JSON response dict
- **Releasable**: after this task, Telegram users (via Claude) see which collections were updated
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_handle_rag_sync_response_includes_updated` — mock sync returning `SyncResult(updated=["docs"])`; verify JSON response has `"updated": ["docs"]`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -v --no-cov -k "sync_response_includes_updated"`

#### Task 4.10 — `archon doctor` chunk size mismatch warning
- [ ] **File**: `archon/cli/doctor.py`
- **Depends on**: Task 4.1 (indexed_chunk_size field in state)
- **Description**:
  - In `_check_rag_health()`: read `IndexingStateStore` from `cfg.rag.db_path`; for each collection, compare `cp.indexed_chunk_size` against `cfg.rag.chunk_size`
  - If mismatch and `indexed_chunk_size != 0`: add warning `⚠️ {name} — chunk size mismatch (indexed: {indexed}, config: {configured})`
  - If `auto_reindex_on_chunk_size_change` is True: suppress the warning (auto-reindex will handle it)
  - Non-blocking: state read failure is silently ignored (existing fallback behaviour)
- **Releasable**: after this task, `archon doctor` surfaces chunk size mismatches for manual resolution
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_doctor_chunk_size_mismatch_warning` — state has `indexed_chunk_size=512`, config has `chunk_size=256`, `auto_reindex=false` → warning displayed
  - Unit: `test_doctor_chunk_size_mismatch_auto_reindex_suppressed` — same scenario but `auto_reindex=true` → no warning
  - Unit: `test_doctor_chunk_size_match_no_warning` — sizes match → no warning
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -v --no-cov -k "chunk_size"`
