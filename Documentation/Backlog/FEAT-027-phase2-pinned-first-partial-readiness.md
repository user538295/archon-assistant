# FEAT-027-P2 — Pinned Collections First + Partial Readiness
**Purpose**: Ensure the most critical collections are searchable earliest; health checks stop false-alarming on in-progress collections
**Audience**: Any user with Search enabled, checking status during background indexing
**Status**: Done ✅

---

## Background
Phase 1 (Done) added per-collection progress visibility via `.indexing_state.json`, CLI `archon search status`, and the `search_status` MCP tool. Collections sync in arbitrary order, and `archon doctor` treats in-progress collections as potentially unhealthy — producing false alarms during background indexing.

Phase 2 addresses two gaps:
1. **Pinned-first ordering** — `pinned_collections` (from `config.toml`) should be ingested before regular ones so the most-used collections are searchable first.
2. **Partial readiness** — in-progress collections should display as `partial (N/M files)` rather than triggering health warnings.

Full spec: `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`, Phase 2 section.

## Goal
After this phase: `SearchCollectionSync` ingests pinned collections first. `archon search status`, `search_status` MCP tool, and `archon doctor` show `partial` status for in-progress collections instead of treating them as unhealthy. A collection is queryable as soon as it has any documents (vector search; FTS after completion).

> **Note — partially superseded by Phase 6 Task 6.1:** In `archon doctor`, the `partial` label for `IN_PROGRESS` collections was renamed to `in_progress` in Phase 6. Phase 6 also reassigned the `partial` label in `archon doctor` to mean a `PENDING` collection with prior progress (an interrupted sync). The `archon search status` CLI and `search_status` MCP tool were not changed by Phase 6 and continue to use the Phase 2 `partial` definition for `IN_PROGRESS` collections.

---

## Scope

### In Scope
- `SearchCollectionSync.__init__()` — accept `pinned_collections` parameter; `sync()` sorts ingestion order accordingly
- `archon search status` CLI — show `partial (N/M files)` for in-progress collections
- `search_status` MCP tool — reflect `partial` status in JSON response
- `_check_search_health()` in `doctor.py` — suppress false alarms on `in_progress`/`pending` collections by reading state file

### Out of Scope
- Changing search routing logic — pinned collections already bypass routing; no change needed
- Priority ordering within pinned collections — declaration order in config is sufficient
- Per-collection priority weights — not planned
- `archon doctor` full real-time integration with live status display overhaul (Phase 6 — this phase only suppresses false alarms and shows basic partial status)
- Resumable indexing (Phase 3)
- Watch mode (Phase 8)

---

## Acceptance criteria
- [x] `sync()` ingests collections matching `pinned_collections` before non-pinned ones
- [x] Pinned ordering preserves declaration order from `config.toml`
- [x] Non-pinned collections retain stable ordering (sorted alphabetically by collection name)
- [x] `archon search status` shows `partial` with file progress for `IN_PROGRESS` collections with `processed_files > 0`
- [x] `archon search status` shows `in_progress` for `IN_PROGRESS` collections with `processed_files == 0`
- [x] `search_status` MCP tool returns `"partial"` status string for `IN_PROGRESS` collections with `processed_files > 0`
- [x] `archon doctor` does not warn on `IN_PROGRESS` or `PENDING` collections — shows informational status instead
- [x] `archon doctor` still warns on `FAILED` collections
- [x] `archon doctor` reads indexing state from state file alongside `CollectionMeta`
- [x] `archon doctor` shows state-only collections (in state file but not yet in LanceDB, e.g. PENDING) — not invisible
- [x] Existing test `test_search_status_includes_progress_fields` updated to expect `"partial"` (processed_files=15 > 0)
- [x] All existing tests pass; new tests cover all new code paths

---

## What does NOT change
- `IndexingStatus` enum — `PARTIAL` is **not** a stored status; it is derived at display time from `IN_PROGRESS` + `processed_files > 0`
- `IndexingStateStore` read/write — no schema changes
- `CollectionProgress` dataclass fields — no new fields
- State file schema (`collections` dict, `last_updated`)
- Search routing logic — pinned collections already bypass routing
- `install.py` behaviour
- Per-collection locking in `SearchCollectionSync`

---

## Known limitations / accepted trade-offs
- **FTS during partial indexing**: `ingest_directory()` calls `store.rebuild_fts_index(collection)` only once at the end. During partial indexing, vector search works immediately but FTS/hybrid search returns incomplete results. Acceptable for Phase 2.
- **`partial` is a display-only concept**: The state file stores `IN_PROGRESS`. The `partial` label is derived at display time when a collection is `IN_PROGRESS` and has `processed_files > 0`. No new enum value or state file field needed.
- **Pinned ordering only affects new collections (`to_add`)**: On subsequent syncs, previously indexed collections are in `unchanged` and skip ingestion entirely — pinned ordering is irrelevant for them. This is correct behaviour: the feature matters on first install or when adding new collections.
- **Doctor cannot distinguish "actively indexing" from "crashed mid-index"**: If the service crashes mid-index and the user runs `archon doctor` before restarting, the state file still shows `IN_PROGRESS`. Doctor will display `partial (N/M files)` — technically correct but implies active work. Crash recovery (`_reset_stale_in_progress`) only runs when `sync()` starts. Phase 6 can add stale `started_at` detection to distinguish these cases.
- **Phase 2 vs Phase 6 boundary**: Phase 2 adds state file reading to doctor and suppresses false alarms. Phase 6 will add the full display overhaul (multi-state per-collection output replacing the staleness check entirely, `--watch` mode consideration, etc.). Phase 2 is scoped to the minimum change: read state, suppress false positive, print informational line.

---

## Architecture
- **`IndexingStatus` enum** (`archon/search/progress.py`): No change. `partial` is derived at display time, not stored.
- **`SearchCollectionSync.__init__()`** (`archon/search/sync.py`): Add `pinned_collections: list[str] | None = None` parameter to the constructor. Store as `self._pinned_collections`. In `sync()`, after building `desired` dict and computing `to_add`, build a sorted list of `(name, path)` pairs for ingestion: pinned first (in declaration order), then non-pinned (alphabetically by name). Replace the `for name, path_str in desired.items()` loop with iteration over this sorted list.
  - **Pinned matching**: Resolve each `pinned_collections` path via `Path(p).expanduser().resolve()` and build a reverse lookup `{str(resolved_path): index}` from `desired` values. Match pinned paths against this reverse lookup to determine which collection names are pinned. Pinned paths not in `desired` are silently skipped.
  - **Ordering guarantee**: The sorted list (not the dict) carries the ordering guarantee. Tests verify order via `result.added` list, which reflects the sequential ingestion loop.
- **`archon search status` CLI** (`archon/cli/search_cmd.py`): In `_print_progress_table()`, when `status == IN_PROGRESS` and `processed_files > 0`, display status as `partial`. When `processed_files == 0`, display as `in_progress`.
- **`search_status` MCP tool** (`archon/ai/archon_toolkit_search.py`): In `_handle_search_status()`, when `d["status"] == "in_progress"` and `cp.processed_files > 0`, set `d["status"] = "partial"`.
- **`_check_search_health()` in `doctor.py`** (`archon/cli/doctor.py`): Read state file via lazy import. For each collection from JSON-RPC, check state before staleness warnings. Also iterate state-only entries (in state file but not in JSON-RPC response, e.g. PENDING collections not yet in LanceDB) to avoid invisible collections.
- **Config**: No new config keys. Uses existing `cfg.search.pinned_collections: list[str]`.
- **Call sites for `SearchCollectionSync` constructor** (all 4 must pass `pinned_collections`; sites 2-4 currently lack `state_store` — add it so progress tracking works at all entry points):
  1. `archon/search/server.py:195` — already has `state_store`; add `pinned_collections`
  2. `archon/search/install.py:210` — add `state_store` and `pinned_collections`
  3. `archon/cli/search_cmd.py:245` — add `state_store` and `pinned_collections`
  4. `archon/ai/archon_toolkit_search.py:326` — add `state_store` and `pinned_collections`

---

## Tests
Complete list of ALL test cases across the plan:

**Sync ordering (Task 2.1):**
- **test_sync_pinned_first_ordering** (unit): pinned collections are ingested before non-pinned; verified via `result.added` order
- **test_sync_pinned_preserves_declaration_order** (unit): pinned follow config order, not alphabetical
- **test_sync_non_pinned_alphabetical** (unit): non-pinned collections sorted alphabetically by collection name
- **test_sync_pinned_not_in_desired_ignored** (unit): pinned path not in `collections` list is silently skipped; other collections still sorted correctly
- **test_sync_all_pinned** (unit): all collections are pinned — order matches config
- **test_sync_no_pinned** (unit): empty `pinned_collections` — alphabetical order (regression guard for default behaviour)
- **test_sync_pinned_tilde_expansion** (unit): pinned path with `~` correctly matches resolved desired path

**Call site wiring (Task 2.2):**
- **test_server_sync_passes_pinned_collections** (integration): server passes `pinned_collections` from config to `SearchCollectionSync` constructor

**CLI display (Task 2.3):**
- **test_cli_status_shows_partial** (unit): IN_PROGRESS with processed_files=50, total_files=100 → output contains `partial` and `50 / 100`
- **test_cli_status_in_progress_zero** (unit): IN_PROGRESS with processed_files=0 → output contains `in_progress` and `0 /`
- **test_cli_status_pending_shows_dash** (unit): PENDING → output contains `—` (regression guard)
- **test_cli_status_done_shows_done** (unit): DONE → output contains `done` (regression guard)

**MCP display (Task 2.4):**
- **test_mcp_status_partial** (unit): IN_PROGRESS with processed_files>0 returns `"partial"` in JSON
- **test_mcp_status_in_progress_zero** (unit): IN_PROGRESS with processed_files=0 returns `"in_progress"` in JSON
- **test_search_status_includes_progress_fields** (existing, updated): update to expect `"partial"` since processed_files=15 > 0

**Doctor (Task 2.5):**
- **test_doctor_partial_no_warning** (unit): IN_PROGRESS + processed_files=50 → output contains `⏳` and `in_progress`, no `⚠` *(Phase 6 note: test name retained; output label changed from `partial` to `in_progress` by Phase 6 Task 6.1)*
- **test_doctor_in_progress_zero_no_warning** (unit): IN_PROGRESS + processed_files=0 → output contains `⏳` and `indexing starting`, no `⚠`
- **test_doctor_pending_no_warning** (unit): PENDING → output contains `⏳` and `pending`, no `⚠`
- **test_doctor_failed_still_warns** (unit): FAILED → output contains `❌` and `failed`
- **test_doctor_done_staleness_still_checked** (unit): DONE → staleness check still executes; stale DONE collection still triggers `⚠`
- **test_doctor_state_only_collection_visible** (unit): collection in state file but not in JSON-RPC response → still printed (not invisible)
- **test_doctor_missing_state_file_fallback** (unit): state file returns None → existing staleness checks run unchanged
- **test_doctor_reads_state_file** (integration): verify doctor reads state file from `cfg.search.db_path`, merges with JSON-RPC response, and outputs correctly for both data sources

---

## Documentation update
- [x] `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`, section: Phase 2 — mark tasks complete as they ship
- [x] `CLAUDE.md`, section: `archon/search/progress.py` entry — update if any public API changes
- [x] `Documentation/UserManual/search_guide.md`, section: Status output — update `archon search status` example to show `partial`

---

## Task breakdown

### Phase 2 — Pinned collections first + partial readiness
> **Releasable**: After all tasks in this phase are complete and all tests pass.

#### Task 2.1 — Sort collections: pinned first in sync()
- [x] **File**: `archon/search/sync.py`
- **Depends on**: nothing (Phase 1 complete)
- **Description**:
  - Add `pinned_collections: list[str] | None = None` parameter to `SearchCollectionSync.__init__()`. Store as `self._pinned_collections = pinned_collections or []`.
  - In `sync()`, after building the `desired` dict and computing `to_add`:
    1. Build reverse lookup: `{resolved_path: name for name, resolved_path in desired.items()}`
    2. Resolve each `self._pinned_collections` path: `str(Path(p).expanduser().resolve())`
    3. Match resolved pinned paths against the reverse lookup to get pinned collection names (preserving declaration order). Skip unmatched paths silently.
    4. Build sorted ingestion list: pinned names first (in declaration order), then remaining `to_add` names sorted alphabetically.
    5. Replace the `for name, path_str in desired.items()` / `if name not in to_add: continue` loop with iteration over the sorted list, looking up paths from `desired`.
  - The sort only affects ingestion order of `to_add` — `unchanged` and `to_remove` are unaffected. On subsequent syncs where all collections are `unchanged`, pinned ordering is a no-op.
- **Releasable**: After this task, `SearchCollectionSync` accepts `pinned_collections` and ingests them first
- **Tests (TDD)** — `tests/search/test_sync.py`:
  - Unit: `test_sync_pinned_first_ordering` — given 3 collections where 1 is pinned, verify pinned is ingested first by checking `result.added` order
  - Unit: `test_sync_pinned_preserves_declaration_order` — given 2 pinned collections, verify they appear in config declaration order
  - Unit: `test_sync_non_pinned_alphabetical` — non-pinned collections are sorted alphabetically
  - Unit: `test_sync_pinned_not_in_desired_ignored` — pinned path not in collections list does not cause error; other collections still sorted correctly
  - Unit: `test_sync_all_pinned` — all collections pinned, order matches config
  - Unit: `test_sync_no_pinned` — empty pinned list, alphabetical fallback
  - Unit: `test_sync_pinned_tilde_expansion` — pinned path `~/somedir` matches desired entry with fully resolved path
  - Checkpoint: `uv run pytest tests/search/test_sync.py -k "pinned" --no-cov -v`

#### Task 2.2 — Pass pinned_collections to SearchCollectionSync at all call sites
- [x] **Files**: `archon/search/server.py`, `archon/search/install.py`, `archon/cli/search_cmd.py`, `archon/ai/archon_toolkit_search.py`
- **Depends on**: Task 2.1
- **Description**:
  - Update ALL 4 production call sites that construct `SearchCollectionSync` to pass `pinned_collections` and `state_store` (sites 2-4 currently lack `state_store`, which means progress tracking is broken at those entry points — fix this opportunistically):
    1. `archon/search/server.py:195` — already has `state_store`; add `pinned_collections=cfg.search.pinned_collections`
    2. `archon/search/install.py:210` — add `state_store=IndexingStateStore(Path(cfg.search.db_path))` and `pinned_collections=self._full_cfg.search.pinned_collections`
    3. `archon/cli/search_cmd.py:245` — add `state_store=IndexingStateStore(Path(cfg.search.db_path))` and `pinned_collections=cfg.search.pinned_collections`
    4. `archon/ai/archon_toolkit_search.py:326` — add `state_store=IndexingStateStore(Path(cfg.search.db_path))` and `pinned_collections=toolkit._config.search.pinned_collections`
  - Default `None` in the constructor for both `state_store` and `pinned_collections` ensures existing test call sites (`SearchCollectionSync(pipeline)`) remain valid without changes.
- **Releasable**: After this task, pinned-first ordering is active at all sync entry points
- **Tests (TDD)** — `tests/search/test_server.py` (or existing server test file):
  - Integration: `test_server_sync_passes_pinned_collections` — verify server constructs `SearchCollectionSync` with `pinned_collections` from config
  - Checkpoint: `uv run pytest tests/search/test_server.py -k "pinned" --no-cov -v`

#### Task 2.3 — CLI status: show `partial` for in-progress collections
- [x] **File**: `archon/cli/search_cmd.py`
- **Depends on**: nothing (Phase 1 complete)
- **Description**:
  - In `_print_progress_table()`, when `progress.status == IndexingStatus.IN_PROGRESS`:
    - If `progress.processed_files > 0`: display status as `partial` and progress as `N / M files`
    - If `progress.processed_files == 0`: display status as `in_progress` and progress as `0 / M files`
  - No change to exit code logic — `partial`/`in_progress` still returns exit 0
- **Releasable**: After this task, `archon search status` shows `partial` for collections mid-index
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Unit: `test_cli_status_shows_partial` — IN_PROGRESS with processed_files=50, total_files=100 → output contains `partial` and `50 / 100`
  - Unit: `test_cli_status_in_progress_zero` — IN_PROGRESS with processed_files=0 → output contains `in_progress` and `0 /`
  - Unit: `test_cli_status_pending_shows_dash` — PENDING → output contains `—` (regression guard)
  - Unit: `test_cli_status_done_shows_done` — DONE → output contains `done` (regression guard)
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -k "partial or pending_shows_dash or done_shows_done or in_progress_zero" --no-cov -v`

#### Task 2.4 — MCP search_status: return `partial` status in JSON
- [x] **File**: `archon/ai/archon_toolkit_search.py`
- **Depends on**: nothing (Phase 1 complete)
- **Description**:
  - In `_handle_search_status()`, after reading state and populating `d["status"]`:
    - If `d["status"] == "in_progress"` and `cp.processed_files > 0`: set `d["status"] = "partial"`
  - Apply the same logic in both the `for c in cols` loop (LanceDB collections with state) and the state-only loop
  - This affects only the JSON response — the state file still stores `in_progress`
  - **Breaking change to existing test**: `test_search_status_includes_progress_fields` currently asserts `col["status"] == "in_progress"` with `processed_files=15`. After this change it must assert `"partial"`. Update this test.
- **Releasable**: After this task, the MCP tool returns `"partial"` for in-progress collections with progress
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_search.py`:
  - Unit: `test_mcp_status_partial` — mock state with IN_PROGRESS + processed_files=50 → JSON response has `"status": "partial"`
  - Unit: `test_mcp_status_in_progress_zero` — mock state with IN_PROGRESS + processed_files=0 → JSON response has `"status": "in_progress"`
  - Update existing: `test_search_status_includes_progress_fields` — change expected status from `"in_progress"` to `"partial"`
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_search.py -k "partial or in_progress_zero or includes_progress" --no-cov -v`

#### Task 2.5 — Doctor: read state file + suppress false alarms
- [x] **File**: `archon/cli/doctor.py`
- **Depends on**: nothing (Phase 1 complete)
- **Description**:
  - In `_check_search_health()`, after fetching collection metadata from the Search server:
    - Read indexing state via lazy import: `from archon.search.progress import IndexingStateStore, IndexingStatus; state = IndexingStateStore(Path(cfg.search.db_path)).read()` (sync I/O inside async function is acceptable — doctor is a CLI tool)
    - For each collection from JSON-RPC response, check state before applying staleness/health warnings:
      - `IN_PROGRESS` with `processed_files > 0` → print `⏳ Collection '<name>' — partial (<N>/<M> files)` — skip staleness warning
      - `IN_PROGRESS` with `processed_files == 0` → print `⏳ Collection '<name>' — indexing starting` — skip staleness warning
      - `PENDING` → print `⏳ Collection '<name>' — pending` — skip staleness warning
      - `FAILED` → print `❌ Collection '<name>' — failed: <error>` — this IS a warning
      - `DONE` → proceed with normal staleness/model mismatch checks (no change)
      - No state entry → proceed with existing staleness-only checks (no change)
    - After the JSON-RPC collection loop, iterate state-only entries (names in state file but not in JSON-RPC response, e.g. PENDING collections not yet in LanceDB) and print their status — prevents invisible collections
  - **Scope note**: This is the minimum change to suppress false alarms. Phase 6 will add the full display overhaul.
- **Releasable**: After this task, `archon doctor` no longer false-alarms on in-progress collections
- **Tests (TDD)** — `tests/cli/test_doctor.py`:
  - Unit: `test_doctor_partial_no_warning` — mock state IN_PROGRESS + processed_files=50 → output contains `⏳` and `in_progress`, no `⚠` *(Phase 6 note: test name retained; output label changed from `partial` to `in_progress` by Phase 6 Task 6.1)*
  - Unit: `test_doctor_in_progress_zero_no_warning` — mock state IN_PROGRESS + processed_files=0 → output contains `⏳` and `indexing starting`, no `⚠`
  - Unit: `test_doctor_pending_no_warning` — mock state PENDING → output contains `⏳` and `pending`, no `⚠`
  - Unit: `test_doctor_failed_still_warns` — mock state FAILED → output contains `❌` and `failed`
  - Unit: `test_doctor_done_staleness_still_checked` — mock state DONE with stale `last_indexed` → staleness `⚠` still appears
  - Unit: `test_doctor_state_only_collection_visible` — collection in state file but not in JSON-RPC → still printed
  - Unit: `test_doctor_missing_state_file_fallback` — state file returns None → existing staleness checks run unchanged
  - Integration: `test_doctor_reads_state_file` — verify doctor reads state file from `cfg.search.db_path`, merges with JSON-RPC response
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -k "partial or failed_still or done_staleness or pending_no or missing_state or reads_state or state_only or in_progress_zero" --no-cov -v`

#### Task 2.6 — Documentation update
- [x] **File**: `Documentation/Backlog/FEAT-027-search-background-indexing-progress.md`
- **Depends on**: Tasks 2.1–2.5
- **Description**:
  - Mark Phase 2 tasks as complete in the feature brief
  - Update `Documentation/UserManual/search_guide.md` status output example to show `partial`
  - Update `CLAUDE.md` if any public API signatures changed
- **Releasable**: After this task, documentation reflects Phase 2 completion
- **Tests (TDD)**: N/A — documentation only
- Checkpoint: N/A
