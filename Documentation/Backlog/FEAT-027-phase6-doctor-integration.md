# FEAT-027-P6 — `archon doctor` Real-time Indexing Status Integration
**Purpose**: Replace the binary staleness check in `archon doctor` with per-collection multi-state status output
**Audience**: Users running `archon doctor` to diagnose Search collection health after install, update, or crash
**Status**: To Do

---

## Background
Phases 1–5 added background indexing, progress visibility, resumability, change detection, and Telegram notifications. `archon doctor` received partial integration in Phase 2 (false-alarm suppression for `IN_PROGRESS` collections), but still has two gaps: (1) it prints nothing for fully healthy `DONE` collections — the user must infer "no output = healthy", and (2) it labels `IN_PROGRESS` as "partial" where the spec says "in_progress", and has no "partial" label for the distinct case of a `PENDING` collection that has prior progress from an interrupted sync.

Full feature spec: `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`, Phase 6 section.

## Goal
`archon doctor` shows a clear per-collection status line for every collection: `✅ done (N docs)` for healthy indexed collections, `⏳ in_progress (N/M files)` for active syncs, `⚠️ partial (N/M files)` for syncs that were interrupted and are queued to resume, `❌ failed: <error>` for failures, and existing `⚠️` lines for stale or mismatched collections. Positive health is always confirmed explicitly — the user no longer needs to infer that "nothing printed = healthy."

---

## Scope

### In Scope
- `_check_search_health()` in `archon/cli/doctor.py`:
  - Rename `IN_PROGRESS` label from `"partial"` → `"in_progress"` (`⏳ {name} — in_progress (N/M files)`)
  - Add `⚠️ {name} — partial (N/M files)` for `PENDING` collections with `processed_files > 0` (was interrupted; queued to resume)
  - Add `✅ {name} — done ({doc_count} docs)` for `DONE` collections with no staleness, model, empty, or centroid issues
  - Apply the `in_progress`/`partial` label changes to the state-only entries block at the bottom of the function
- Tests in `tests/cli/test_doctor.py`

### Out of Scope
- `archon doctor --json` structured output — not planned in this phase; doctor is CLI-only
- Doctor watching state file for live updates (`--watch` mode) — not planned
- Doctor auto-fixing issues (e.g. triggering re-index on stale) — doctor is read-only
- Changes to `archon search status` output — separate command, unchanged here
- Staleness threshold (`_SEARCH_STALE_DAYS`) or warning text changes

---

## Acceptance criteria
- [ ] `DONE` collections with no staleness, model, empty, or centroid issues print `✅ Collection '{name}' — done ({doc_count} docs)`
- [ ] `DONE` + stale prints the existing `⚠ Collection '{name}' last indexed {N} days ago` warning; no `✅` line
- [ ] `DONE` + model mismatch prints the existing mismatch warning; no `✅` line
- [ ] `DONE` + empty collection prints the existing empty warning; no `✅` line
- [ ] `IN_PROGRESS` + `processed_files > 0` prints `⏳ Collection '{name}' — in_progress ({processed}/{total} files)` (label was "partial")
- [ ] `IN_PROGRESS` + `processed_files == 0` prints `⏳ Collection '{name}' — indexing starting` (unchanged)
- [ ] `PENDING` + `processed_files > 0` prints `⚠️ Collection '{name}' — partial ({processed}/{total} files)` (NEW)
- [ ] `PENDING` + `processed_files == 0` prints `⏳ Collection '{name}' — pending` (unchanged)
- [ ] `FAILED` prints `❌ Collection '{name}' — failed: {error}` (unchanged)
- [ ] State file absent → falls back to `CollectionMeta` staleness check only; no `✅` lines printed
- [ ] Collection in state file but absent from LanceDB (`PENDING` + `processed_files > 0`) → prints `⚠️ partial (N/M files)`
- [ ] Collection in state file but absent from LanceDB (`PENDING` + `processed_files == 0`) → prints `⏳ pending`
- [ ] Collection present in LanceDB but not in state file → no `✅` line (state must confirm `DONE`)
- [ ] All existing doctor tests continue to pass

---

## What does NOT change
- `IndexingStateStore`, `IndexingState`, `CollectionProgress` dataclasses — no data model changes
- `archon search status` CLI output format
- `search_status` MCP tool response
- `_check_search_server()` function
- `_SEARCH_STALE_DAYS = 7` threshold and staleness warning text
- Model mismatch warning text
- Empty collection warning
- Missing centroid warning
- State-only entries block structure (only the `PENDING`/`IN_PROGRESS` label strings change)

---

## Known limitations / accepted trade-offs
- `archon doctor` cannot distinguish "currently syncing" from "sync task completed but state not yet flushed to `DONE`" — both show as `in_progress`. The 50-file batch write cadence means up to 50 files of lag is possible. Accepted.
- `DONE` collections with multiple issues (stale AND model mismatch) print multiple `⚠` lines without a single unified status. Existing behaviour, not changed here.
- `✅ done` is printed only when the state file confirms `DONE`. A collection that was indexed before Phase 1 was deployed (no state file entry) shows no positive confirmation. Accepted: the state file is the authoritative source of indexing status.
- "Partial" (⚠️) is only shown for `PENDING` + prior progress. If the process was killed while `IN_PROGRESS` (before the restart-reset logic could run), the collection would show as `in_progress` rather than `partial`. In practice, the server resets stale `IN_PROGRESS` entries to `PENDING` on every startup — so this edge case is expected to be rare.
- A collection that is `DONE` in the state file but absent from LanceDB (inconsistent state) is silently skipped — no output is produced. This edge case (e.g., LanceDB collection dropped manually while state file was not cleared) is considered an out-of-band administration error; doctor intentionally does not guess whether it was deliberately removed or is a bug. The user would need to run `search_collection_list` or `archon search status` to diagnose. Accepted.
- The `doc_count` shown in `✅ done (N docs)` comes from the LanceDB JSON-RPC metadata response, not the state file. In the rare window where indexing finishes but the LanceDB count hasn't refreshed, the count may lag. Accepted; the `doc_count == 0` check fires first and suppresses the checkmark when LanceDB reports zero documents.
- `PENDING` collections with prior progress that appear only in the state file (not in LanceDB) are indistinguishable from newly-added collections mid-first-sync. Both show `⚠️ partial (N/M files)`. This is acceptable — the output is still actionable (a sync is pending) regardless of the cause.

---

## Architecture

### Changes in `archon/cli/doctor.py` — `_check_search_health()`

**Rename IN_PROGRESS label** (two locations — collections-in-LanceDB block and state-only block):
```python
# Before:
print(f"⏳ Collection '{name}' — partial ({cp.processed_files}/{cp.total_files} files)")
# After:
print(f"⏳ Collection '{name}' — in_progress ({cp.processed_files}/{cp.total_files} files)")
```

**Add PENDING partial detection** (both blocks):
```python
# Before:
elif cp.status == IndexingStatus.PENDING:
    print(f"⏳ Collection '{name}' — pending")
    continue

# After:
elif cp.status == IndexingStatus.PENDING:
    if cp.processed_files > 0:
        print(f"⚠️ Collection '{name}' — partial ({cp.processed_files}/{cp.total_files} files)")
    else:
        print(f"⏳ Collection '{name}' — pending")
    continue
```

**Add positive DONE confirmation** — add `has_warning` tracking to the DONE fall-through path:
```python
# Initialise before the per-collection checks:
has_warning = False

# Inside each existing warning block (staleness, model mismatch, chunk size mismatch,
# empty collection, missing centroid) — add has_warning = True:
has_warning = True

# After all checks for this collection (staleness, model, chunk, empty, centroid):
if cp is not None and cp.status == IndexingStatus.DONE and not has_warning:
    print(f"✅ Collection '{name}' — done ({col.get('doc_count', 0)} docs)")
```

The `has_warning` flag is local to each iteration of the `for col in raw_collections:` loop. It is only relevant for the `DONE` fall-through path — all other statuses (`IN_PROGRESS`, `PENDING`, `FAILED`) hit a `continue` before reaching the checkmark check and do not need the flag.

Note: the chunk size mismatch check (`if cp is not None and cp.indexed_chunk_size != 0 and cp.indexed_chunk_size != search.chunk_size`) is guarded by `auto_reindex_on_chunk_size_change`; when `auto_reindex_on_chunk_size_change = True` the warning is suppressed and `has_warning` stays `False`, so a `DONE` + auto-reindex-eligible collection correctly gets the `✅` line.

**Rename IN_PROGRESS label in the state-only block** (second location):
```python
# Before (state-only entries block):
elif cp.status == IndexingStatus.IN_PROGRESS:
    print(f"⏳ Collection '{name}' — partial ({cp.processed_files}/{cp.total_files} files)")

# After:
elif cp.status == IndexingStatus.IN_PROGRESS:
    print(f"⏳ Collection '{name}' — in_progress ({cp.processed_files}/{cp.total_files} files)")
```

**Add PENDING partial detection in the state-only block** (second location):
```python
# Before (state-only entries block):
elif cp.status == IndexingStatus.PENDING:
    print(f"⏳ Collection '{name}' — pending")

# After:
elif cp.status == IndexingStatus.PENDING:
    if cp.processed_files > 0:
        print(f"⚠️ Collection '{name}' — partial ({cp.processed_files}/{cp.total_files} files)")
    else:
        print(f"⏳ Collection '{name}' — pending")
```

Note: `DONE` collections in the state-only block (in state file but absent from LanceDB) remain silently skipped — this is intentional and unchanged by this phase (see Known Limitations).

---

## Tests

### Existing tests to update
The `IN_PROGRESS` label rename (Task 6.1) breaks two existing tests that assert the old `"partial"` label. These must be updated as part of Task 6.1:
- **`test_doctor_partial_no_warning`** — change `assert "partial" in out` to `assert "in_progress" in out` (and `assert "partial" not in out`)
- **`test_doctor_reads_state_file`** — change the `"partial" in out` assertion to `assert "in_progress" in out`

### New tests in `tests/cli/test_doctor.py`

- **test_in_progress_label_is_in_progress** (unit): `IN_PROGRESS` + `processed_files > 0` → output contains `"in_progress"` and does NOT contain `"partial"`
- **test_in_progress_no_files_label** (unit): `IN_PROGRESS` + `processed_files == 0` → output contains `"indexing starting"`
- **test_pending_with_prior_progress_shows_partial** (unit): `PENDING` + `processed_files > 0` → output contains `"partial"` with `⚠️` and does NOT contain `"— pending"` (the standalone pending label)
- **test_pending_fresh_shows_pending** (unit): `PENDING` + `processed_files == 0` → output contains `"pending"` with `⏳` and does NOT contain `"partial"`
- **test_state_only_in_progress_label** (unit): `IN_PROGRESS` + `processed_files > 0` in state but NOT in LanceDB → contains `"in_progress"`, not `"partial"`
- **test_state_only_pending_partial** (unit): `PENDING` + `processed_files > 0` in state but NOT in LanceDB → contains `"partial"` with `⚠️`
- **test_state_only_pending_fresh** (unit): `PENDING` + `processed_files == 0` in state but NOT in LanceDB → contains `"pending"` with `⏳`
- **test_state_only_done_silently_skipped** (unit): `DONE` in state file but NOT in LanceDB → collection name does NOT appear in output (silent skip)
- **test_done_no_issues_prints_checkmark** (unit): `DONE`, recent `last_indexed`, matching model, `doc_count > 0`, has centroid, `indexed_chunk_size` matching config → output contains `"✅"` and `"done"` and the doc count
- **test_done_stale_no_checkmark** (unit): `DONE` + stale (> 7 days) → staleness `⚠` line printed, no `✅` line
- **test_done_model_mismatch_no_checkmark** (unit): `DONE` + model mismatch → mismatch `⚠` line printed, no `✅` line
- **test_done_empty_no_checkmark** (unit): `DONE` + `doc_count == 0` → empty `⚠` line printed, no `✅` line
- **test_done_chunk_mismatch_no_checkmark** (unit): `DONE` + `indexed_chunk_size` ≠ config `chunk_size` + `auto_reindex_on_chunk_size_change = False` → chunk mismatch warning printed, no `✅` line
- **test_done_missing_centroid_no_checkmark** (unit): `DONE` + centroid absent (`col.get("centroid") is None`) → centroid warning printed, no `✅` line
- **test_done_multiple_issues_no_checkmark** (unit): `DONE` + stale + model mismatch → both `⚠` lines printed, no `✅` line (verifies `has_warning` is not accidentally reset between checks)
- **test_done_no_state_no_checkmark** (unit): collection in LanceDB but no state file entry (`cp is None`) → no `✅` line printed
- **test_failed_no_checkmark** (unit): `FAILED` → `❌` line printed, no `✅` line

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`, Phase 6 section: mark ✅ Done when complete

---

## Task breakdown

### Phase 6 — `archon doctor` indexing status integration
> **Releasable**: after Task 6.2 — all status labels are correct and positive confirmation is shown for healthy collections

#### Task 6.1 — Rename IN_PROGRESS label and add PENDING partial detection
- [x] **File**: `archon/cli/doctor.py`
- **Depends on**: nothing (all required state fields exist from Phases 1–3)
- **Description**:
  - In `_check_search_health()`, rename the `IN_PROGRESS` display label from `"partial"` to `"in_progress"`:
    - Line inside the `for col in raw_collections:` block: `f"⏳ Collection '{name}' — in_progress ({cp.processed_files}/{cp.total_files} files)"`
    - Same rename in the state-only entries block at the bottom (`for name, cp in state.collections.items():`)
  - Add `PENDING` + prior progress → `partial` case in both blocks:
    ```python
    elif cp.status == IndexingStatus.PENDING:
        if cp.processed_files > 0:
            print(f"⚠️ Collection '{name}' — partial ({cp.processed_files}/{cp.total_files} files)")
        else:
            print(f"⏳ Collection '{name}' — pending")
        continue
    ```
  - All `continue` statements preserved — no fall-through behaviour changed for any other status
  - No new imports required
- **Releasable**: `IN_PROGRESS` is labeled "in_progress"; interrupted-and-queued collections show as "partial" with `⚠️`
- **Existing tests to update** — before writing new tests, update these to reflect the rename:
  - `test_doctor_partial_no_warning`: change `assert "partial" in out` → `assert "in_progress" in out` and add `assert "partial" not in out`
  - `test_doctor_reads_state_file`: change the `"partial" in out` assertion → `assert "in_progress" in out`
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_in_progress_label_is_in_progress` — `IN_PROGRESS` + `processed_files > 0` → output contains `"in_progress"` and NOT `"partial"`
  - Unit: `test_in_progress_no_files_label` — `IN_PROGRESS` + `processed_files == 0` → output contains `"indexing starting"`
  - Unit: `test_pending_with_prior_progress_shows_partial` — `PENDING` + `processed_files > 0` → output contains `"partial"` with `⚠️` and NOT `"— pending"`
  - Unit: `test_pending_fresh_shows_pending` — `PENDING` + `processed_files == 0` → output contains `"pending"` with `⏳` and NOT `"partial"`
  - Unit: `test_state_only_in_progress_label` — `IN_PROGRESS` + `processed_files > 0` in state but NOT in LanceDB → output contains `"in_progress"`
  - Unit: `test_state_only_pending_partial` — `PENDING` + `processed_files > 0` in state but NOT in LanceDB → output contains `"partial"` with `⚠️`
  - Unit: `test_state_only_pending_fresh` — `PENDING` + `processed_files == 0` in state but NOT in LanceDB → output contains `"pending"` with `⏳`
  - Unit: `test_state_only_done_silently_skipped` — `DONE` in state file, collection NOT in LanceDB → collection name does NOT appear in output
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -v --no-cov -k "in_progress or pending or partial or state_only"`

#### Task 6.2 — Add `✅ done` positive confirmation for healthy DONE collections
- [x] **File**: `archon/cli/doctor.py`
- **Depends on**: Task 6.1
- **Description**:
  - In `_check_search_health()`, inside the `for col in raw_collections:` loop, add `has_warning = False` at the start of each iteration (before the staleness/model/chunk/empty/centroid checks)
  - Inside each existing `print(f"⚠ ...")` warning statement for staleness, model mismatch, chunk size mismatch, empty collection, and missing centroid: add `has_warning = True` immediately before or after the `print()` call
  - After all per-collection checks: `if cp is not None and cp.status == IndexingStatus.DONE and not has_warning: print(f"✅ Collection '{name}' — done ({col.get('doc_count', 0)} docs)")`
  - The `has_warning` flag is local to each loop iteration — it only covers the current collection
  - Collections without a state entry (`cp is None`) do NOT get a `✅` line; the state file must confirm `DONE`
  - No changes to the state-only entries block (state-only `DONE` collections remain silently skipped, per existing behaviour)
  - No new imports required
- **Releasable**: `archon doctor` explicitly confirms `✅ {name} — done (N docs)` for all fully-indexed, healthy collections
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_done_no_issues_prints_checkmark` — `DONE`, recent `last_indexed`, matching model, `doc_count > 0`, has centroid, `indexed_chunk_size` matches config → output contains `"✅"` and `"done"` and the doc count
  - Unit: `test_done_stale_no_checkmark` — `DONE` + `last_indexed` > 7 days ago → staleness `⚠` line printed; no `✅` line in output
  - Unit: `test_done_model_mismatch_no_checkmark` — `DONE` + model mismatch → mismatch `⚠` line printed; no `✅` line in output
  - Unit: `test_done_empty_no_checkmark` — `DONE` + `doc_count == 0` → empty `⚠` line printed; no `✅` line in output
  - Unit: `test_done_chunk_mismatch_no_checkmark` — `DONE` + `indexed_chunk_size != config chunk_size` + `auto_reindex_on_chunk_size_change = False` → chunk mismatch `⚠` line printed; no `✅` line in output
  - Unit: `test_done_missing_centroid_no_checkmark` — `DONE` + `centroid` is absent → centroid `⚠` line printed; no `✅` line in output
  - Unit: `test_done_multiple_issues_no_checkmark` — `DONE` + stale + model mismatch → both `⚠` lines printed; no `✅` line in output (verifies `has_warning` is not reset between checks)
  - Unit: `test_done_no_state_no_checkmark` — collection in LanceDB raw data with recent `last_indexed` and matching model, but `state` is `None` (no state file) → no `✅` line printed; existing staleness check still runs
  - Unit: `test_failed_no_checkmark` — `FAILED` collection → `❌` line printed via the `continue` path; no `✅` line
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -v --no-cov -k "done or checkmark or failed"`
