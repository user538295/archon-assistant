# FEAT-027 Audit Review — Gaps, Deviations, and Missing Tests
**Scope**: All phases (1–8) of `FEAT-027-rag-background-indexing-progress.md`
**Date**: 2026-04-05
**Status**: Open — all items require resolution

This document lists every deviation, missing test, wrong test, and implementation bug found by
cross-referencing every line of the phase specs against the actual code. Items are organized by
severity: **CRITICAL → MAJOR → MINOR**. Each item is independently checkable.

---

## How to use this document

- Each item has a unique ID (e.g. `P1-001`), a type, and a precise fix description.
- Check off `[x]` when resolved.
- "Affected files" always lists the exact files to change.

---

## Summary by severity

| Severity | Count |
|---|---|
| CRITICAL | 2 |
| MAJOR | 9 |
| MINOR | 31 |
| **Total** | **42** |

---

## CRITICAL

---

### P1-001 — BUG — Install is blocking, not non-blocking
- [x] **Type**: BUG
- **Phase**: 1
- **Spec reference** (`FEAT-027-rag-background-indexing-progress.md`, Phase 1 Key Decision):
  > "Fire-and-forget install: A progress bar at install time is a prettier version of the same blocking problem. Non-blocking means non-blocking."
  >
  > Core flow step 2: "Install configures Search, starts the service, fires background sync, and **exits immediately**"
  >
  > Scenario: "Install completes, indexing starts → **Install exits; service running; state file shows `in_progress`**"

- **What was found**: The call chain is fully blocking:
  1. `install.py:_offer_search_setup()` line 493 calls `subprocess.run(["archon", "search", "install", "--non-interactive"])` — **synchronous, blocks until subprocess exits**.
  2. That subprocess dispatches to `archon/search/install.py:SearchInstaller.run()`.
  3. `SearchInstaller.run()` at lines 303–306 calls `asyncio.run(self._bootstrap_collections())` at step **[4/5] — before the service is even started**.
  4. `_bootstrap_collections()` ingests all collections to completion before returning.
  5. Only at line 310 (`[5/5] Starting search service ...`) does the service start.

  Effect: `_offer_search_setup` blocks until every collection is fully indexed. The state file
  **never** shows `in_progress` during an install via this path because indexing is complete
  before the service starts. The "Indexing in background" success message at line 511 is a lie.

- **What should exist**: `SearchInstaller.run()` must start the service first (step [4/5] and
  [5/5] swapped), then let the running server handle sync asynchronously. The explicit
  `asyncio.run(self._bootstrap_collections())` before service start must be removed. The
  server already syncs on startup via `asyncio.create_task` when `sync_timeout_seconds=0`.

- **Affected files**:
  - `archon/search/install.py` (lines 303–314 — the [4/5] bootstrap block before [5/5] service start)
  - `install.py` (line 493 — `subprocess.run` blocking on `archon search install`)

---

### P1-002 — MISSING_TEST — No test verifies the install path is non-blocking
- [x] **Type**: MISSING_TEST
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Phase 1 Use Cases):
  > "Non-interactive / scripted install: `install.py --non-interactive` exits immediately with zero once the service is running; indexing runs asynchronously."

- **What was found**: Zero tests exist that verify `_offer_search_setup` or
  `SearchInstaller.run()` returns **before** indexing completes. The test
  `test_offer_search_setup_prints_status_hint` (line 2710, `tests/test_installer_py.py`)
  mocks `subprocess.run` entirely — it never verifies timing, call ordering, or that
  `asyncio.run(_bootstrap_collections())` does not block the install return. No test in
  `tests/search/test_install.py` checks that service start precedes bootstrap.

- **What should exist**: A test for `SearchInstaller.run()` that verifies the call order is:
  deps → configure → data dir → **service start** → (background sync), not:
  deps → configure → data dir → **blocking ingest** → service start.

- **Affected files**:
  - `tests/search/test_install.py`
  - `tests/test_installer_py.py`

---

## MAJOR

---

### P1-004 — WRONG_TEST — PENDING write test does not assert `total_files=0`
- [x] **Type**: WRONG_TEST
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Task 1.4 test list):
  > `test_sync_writes_pending_before_ingest` — state has `PENDING` with `total_files=0`

- **What was found**: The test is renamed `test_sync_writes_pending_then_in_progress_before_ingest`
  (line 784, `tests/search/test_sync.py`). It asserts the status sequence `[PENDING, IN_PROGRESS]`
  but does **not** assert `total_files=0` on the PENDING write — which was the specific
  postcondition in the spec.

- **What should exist**: The test must add: `assert pending_progress.total_files == 0` when
  the PENDING status is first written.

- **Affected files**:
  - `tests/search/test_sync.py` (line 784)

---

### P1-005 — BUG — Success message says "RAG enabled." instead of "Search enabled."
- [x] **Type**: BUG
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Task 1.8 and Acceptance Criteria):
  > `install.py` prints **"Search enabled. Indexing in background — run `archon search status` to track progress."**

- **What was found**: `install.py` line 511:
  ```python
  console.success("RAG enabled. Indexing in background — run 'archon search status' to track progress.")
  ```
  The prefix is `"RAG enabled."` not `"Search enabled."` as the spec requires.

- **What should exist**: Change `"RAG enabled."` → `"Search enabled."`.

- **Affected files**:
  - `install.py` (line 511)

---

### P1-006 — WRONG_TEST — Hint message test uses substring match, does not catch P1-005
- [x] **Type**: WRONG_TEST
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Task 1.8 test):
  > `test_offer_search_setup_prints_status_hint` — success path prints the new hint message

- **What was found**: The test (line 2710, `tests/test_installer_py.py`) only asserts:
  ```python
  assert "archon search status" in captured
  assert "Indexing in background" in captured
  ```
  It does not assert `"Search enabled."` — so P1-005 passes undetected.

- **What should exist**: Add `assert "Search enabled." in captured` to catch the wrong prefix.

- **Affected files**:
  - `tests/test_installer_py.py` (line 2710–2724)

---

### P4-012 — MISSING_TEST — `test_sync_apply_changes_failed_midway` missing the deletion scenario
- [x] **Type**: MISSING_TEST
- **Phase**: 4
- **Spec reference** (`FEAT-027-phase4-file-level-change-detection.md`):
  > "3 files to process (1 delete, 2 new); second new file's `ingest_file` raises; FAILED state has: deleted path removed from `file_mtimes`, first new file added to `file_mtimes`, second new file NOT in `file_mtimes`"

- **What was found**: The test at line 3119 uses only 2 new files and no deleted file.
  The spec explicitly requires a deletion in the scenario. The FAILED state after a
  mid-new-file raise must include the deletion already applied — this is not verified.

- **What should exist**: Add a deleted file to the initial state in `test_sync_apply_changes_failed_midway`.
  Assert that in the FAILED state: the deleted path is absent from `file_mtimes`, the first
  new file's mtime is present, and the second (failing) new file is absent.

- **Affected files**:
  - `tests/search/test_sync.py` (line 3119)

---

### P5-002 — SPEC_DEVIATION — Gateway starts monitor on `search_url is not None`, not `search_state == RUNNING`
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 5
- **Spec reference** (`FEAT-027-phase5-telegram-notification.md`, Task 5.4 architecture):
  > "if cfg.search.enabled and `search_state == SearchState.RUNNING`"

- **What was found**: `archon/gateway/gateway.py` line 616 uses:
  ```python
  if cfg.search.enabled and search_url is not None:
  ```
  `search_url` is not `None` in two cases: `SearchState.RUNNING` AND
  `SearchState.NOT_RUNNING + auto_started=True`. So the monitor also starts when the service
  was auto-started at gateway boot. No test covers the `NOT_RUNNING + auto_started=True`
  path (test `test_monitor_not_started_when_rag_not_running` only sets `auto_start=False`).

- **What should exist**: Either update the spec to document the broader condition as intentional,
  or restrict the condition to `search_state == SearchState.RUNNING`. Add a test for the
  `NOT_RUNNING + auto_started=True` path.

- **Affected files**:
  - `archon/gateway/gateway.py` (line 616)
  - `tests/gateway/test_gateway.py`

---

### P4-006 — BUG — `processed_paths` updated unconditionally on soft-ingest failure
- [x] **Type**: BUG
- **Phase**: 4
- **Spec reference**: The Phase 3 spec (`FEAT-027-phase3-resumable-indexing.md`) explicitly states: "Errored files are retried, not skipped: files that fail ingest (status=error) are never added to processed_paths and will be retried on the next sync." The implementation violates this directly — there is no guard on `ingest_result.status` before appending to `processed_paths`.

- **What was found**: In `_apply_collection_changes`, `processed_paths.append(resolved_str)`
  (lines 659–660 for changed files, 681–683 for new files) runs unconditionally — even when
  `ingest_result.status != "ok"`. A soft-failed file gets added to `processed_paths` and
  would be skipped on the next sync, leaving its stale chunks in LanceDB permanently.

- **What should exist**: Gate `processed_paths.append(...)` on `ingest_result.status == "ok"`
  for both changed and new files in `_apply_collection_changes`.

- **Affected files**:
  - `archon/search/sync.py` (lines 658–660, 680–683)

---

### P1-009 — BUG — 49-file boundary test fires `progress_cb` not `on_file_complete`
- [x] **Type**: BUG
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Task 1.4):
  > Every 50 files (when `done_count % 50 == 0`): calls `state_store.update_collection()` with current progress

- **What was found**: `test_sync_batched_writes_boundary_49_files` (line 1018,
  `tests/search/test_sync.py`) fires `progress_cb` 49 times. The actual batching in
  `_ingest_collection` uses the `on_file_complete` callback — `progress_cb` does not trigger
  batching. The test could pass even if the 50-file batching via `on_file_complete` is broken.

  The 50-file batching is a crash-recovery guarantor — at most 50 files of progress are lost on crash. A test that cannot detect broken batch boundary logic is a test that provides false safety confidence.

- **What should exist**: The test must fire `on_file_complete` 49 times (not `progress_cb`)
  to exercise the real batching code path.

- **Affected files**:
  - `tests/search/test_sync.py` (line 1018–1060)

---

### P6-005 — WRONG_TEST — `auto_reindex=True` + chunk mismatch: spec says ✅ should show, test says it shouldn't
- [x] **Type**: WRONG_TEST
- **Phase**: 6
- **Spec reference** (`FEAT-027-phase6-doctor-integration.md`, architecture note):
  > "When `auto_reindex_on_chunk_size_change = True` the warning is suppressed and `has_warning` stays `False`, so a `DONE + auto-reindex-eligible` collection correctly gets the `✅` line."

- **What was found**: `doctor.py` lines 159–167 set `has_warning = True` before the
  `if not search.auto_reindex_on_chunk_size_change:` branch — meaning `has_warning=True`
  regardless of the config flag. The test `test_done_chunk_mismatch_auto_reindex_no_checkmark`
  asserts `"✅" not in out`, consistent with the implementation but contradicting the spec.

- **What should exist**: Either: (a) fix the implementation so `has_warning` is NOT set when
  `auto_reindex_on_chunk_size_change=True`, update the test to assert `"✅" in out`; or
  (b) update the spec architecture note to reflect the intentional conservative behavior
  (always suppress ✅ on any chunk mismatch). Decide which is correct and align all three.

- **Affected files**:
  - `archon/cli/doctor.py` (lines 159–167)
  - `tests/cli/test_doctor.py` (`test_done_chunk_mismatch_auto_reindex_no_checkmark`)
  - `Documentation/Backlog/FEAT-027-phase6-doctor-integration.md`

---

### P2-006 — MISSING_TEST — Call-site wiring tests don't assert `pinned_collections` was passed
- [x] **Type**: MISSING_TEST
- **Phase**: 2
- **Spec reference** (`FEAT-027-phase2-pinned-first-partial-readiness.md`, Task 2.2):
  > Update ALL 4 production call sites that construct `SearchCollectionSync` to pass `pinned_collections`

- **What was found**: `test_cli_sync_passes_config_params` (`tests/cli/test_search_cmd.py`
  line 2380) and `test_mcp_sync_passes_config_params` (`tests/ai/test_archon_toolkit_search.py`
  line 2154) both assert `embedding_model`, `chunk_size`, `auto_reindex_on_chunk_size_change`
  — but neither asserts `pinned_collections`. The implementations are correct but the
  wiring is not verified by tests.

  Since pinned-first ordering is the entire purpose of Phase 2, the absence of a wiring assertion for the key parameter means the core Phase 2 deliverable is not verified by tests.

- **What should exist**: Add `assert call_kwargs["pinned_collections"] == expected_pinned` to
  both tests.

- **Affected files**:
  - `tests/cli/test_search_cmd.py` (line ~2410)
  - `tests/ai/test_archon_toolkit_search.py` (line ~2182)

---

## MINOR

---

### P1-003 — WRONG_TEST — `total_files` test verifies enumeration but spec said callback
- [x] **Type**: WRONG_TEST
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Task 1.4 test list):
  > `test_sync_total_files_set_from_first_callback` — `total_files` populated from first `progress_cb(1, total)` call

- **What was found**: The test is renamed `test_sync_total_files_set_from_file_enumeration`
  (line 849, `tests/search/test_sync.py`). The implementation computes `total_new` from
  `self._iter_eligible_files(p)` before calling `ingest_directory`, not from the first
  callback. The test accurately tests the current implementation but the spec test name and
  intent were never updated to document this design change.

- **What should exist**: Either a spec update to document the enumeration approach as the
  canonical design (with the test renamed accordingly), OR a test matching the original spec
  name if the callback-based approach was intentional. The deviation must be explicitly
  acknowledged in the spec.

- **NOTE**: Downgraded from MAJOR to MINOR: the test accurately reflects the actual implementation; only the spec name was not updated when the design changed. This is spec-name maintenance, not a test correctness issue.

- **Affected files**:
  - `tests/search/test_sync.py` (line 849)
  - `Documentation/Backlog/FEAT-027-phase1-progress-visibility.md` (Task 1.4 test list)

---

### P2-001 — SPEC_DEVIATION — Phase 2 spec `partial` label superseded by Phase 6 (false positive)
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 2

- Phase 2 spec wording `partial` was superseded by Phase 6 Task 6.1, which explicitly renames the IN_PROGRESS label to `in_progress`. The implementation and test are correct per Phase 6. The Phase 2 spec document (`FEAT-027-phase2-pinned-first-partial-readiness.md`) should be annotated to note it was superseded by Phase 6. No code change needed.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-phase2-pinned-first-partial-readiness.md`

---

### P2-002 — SPEC_DEVIATION — Phase 2 spec test name misleading after Phase 6 change (false positive)
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 2

- The test was correctly updated to match Phase 6's design (which changed IN_PROGRESS from 'partial' to 'in_progress'). The assertion `assert 'partial' not in out` is correct per Phase 6 spec. The Phase 2 spec's test name `test_doctor_partial_no_warning` is now misleading but the test logic is correct. Update the spec to note the Phase 6 change.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-phase2-pinned-first-partial-readiness.md`

---

### P4-002 — SPEC_DEVIATION — MCP sync `updated` test: wrong name
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 4
- **Spec reference** (`FEAT-027-phase4-file-level-change-detection.md`, Task 4.6 test list):
  > `test_handle_search_sync_response_includes_updated`

- **What was found**: The test is named `test_handle_rag_sync_response_includes_updated`
  (line 728, `tests/ai/test_archon_toolkit_search.py`).

- **What should exist**: Rename to `test_handle_search_sync_response_includes_updated` per spec.
  NOTE: the missing `@pytest.mark.asyncio` decorator is NOT a bug — `pyproject.toml` sets `asyncio_mode = 'auto'`, which automatically runs all `async def` test functions. Adding the decorator is optional cosmetic improvement.

- **Affected files**:
  - `tests/ai/test_archon_toolkit_search.py` (line 728)

---

### P5-001 — SPEC_DEVIATION — `run()` CancelledError semantics contradict spec wording
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 5
- **Spec reference** (`FEAT-027-phase5-telegram-notification.md`, acceptance criteria):
  > "Monitor task is started in gateway when Search is enabled; cancelled gracefully on shutdown" / "exits cleanly without exception"

- **What was found**: `test_run_exits_cleanly_on_cancelled_error` (line 380,
  `tests/search/test_notification_monitor.py`) asserts `pytest.raises(asyncio.CancelledError)`.
  The spec says "exits cleanly without exception" but the test asserts the opposite. The
  implementation re-raises `CancelledError` which is standard asyncio — but the spec wording
  "exits cleanly" is contradicted by the test's assertion.

- **What should exist**: Clarify the spec: "exits cleanly" means "no unexpected exception
  other than `CancelledError`" — `CancelledError` propagation is correct Python asyncio
  behavior and is the intended design. The test is correct; the spec wording needs updating.
  Update `FEAT-027-phase5-telegram-notification.md` to say: "cancelled gracefully —
  `CancelledError` propagates normally to the asyncio task runner; no other exception leaks."
  NOTE: The test method at the relevant line has a comment `# Should not raise CancelledError or any other exception` immediately followed by `pytest.raises(asyncio.CancelledError)` — the comment directly contradicts the assertion and should also be updated or removed.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-phase5-telegram-notification.md`

---

### P1-007 — SPEC_DEVIATION — MCP test class and test names use `rag` prefix instead of `search`
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`):
  > `tests/ai/test_archon_toolkit_search.py::TestSearchStatusProgress`
  > `test_search_status_includes_progress_fields`, `test_search_status_without_state_file`,
  > `test_search_status_merges_new_collections`, `test_search_status_error_fields`

- **What was found**: Class is `TestRagStatusProgress`; all 4 test methods use `rag_status`
  prefix (e.g. `test_rag_status_includes_progress_fields`).

- **What should exist**: Rename class to `TestSearchStatusProgress`; rename methods to
  use `search_status` prefix per spec.

- **Affected files**:
  - `tests/ai/test_archon_toolkit_search.py`

---

### P1-008 — SPEC_DEVIATION — `test_sync_failed_preserves_total_files_from_callback` renamed; spec not updated
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 1
- **Spec reference** (`FEAT-027-phase1-progress-visibility.md`, Task 1.4 test list):
  > `test_sync_failed_preserves_total_files_from_callback` — exception at file 30/100: `total_files=100` (from callback)

- **What was found**: Renamed to `test_sync_failed_preserves_total_files_from_enumeration`
  (line 1388, `tests/search/test_sync.py`). The test accurately reflects the implementation
  (enumeration, not callback) but the spec was never updated.

- **What should exist**: Update the spec test name and description to document the enumeration
  approach.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-phase1-progress-visibility.md` (Task 1.4 test list)

---

### P1-012 — SPEC_DEVIATION — Phase 3/4/5 dataclass fields pre-built in Phase 1
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 1
- **Spec reference** (State Schema Evolution table):
  > `processed_paths` — Phase 3 | `file_mtimes`, `file_hashes` — Phase 4 | `trigger` — Phase 5

- **What was found**: `CollectionProgress` and `IndexingState` in `archon/search/progress.py`
  already contain all Phase 3, 4, and 5 fields despite being committed as a Phase 1 task.

- **What should exist**: The phase task checklists for Tasks 3.1, 4.1, and 5.1 should note
  "fields pre-implemented in Phase 1" rather than "add field X" so future readers understand
  the state is already present.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-phase3-resumable-indexing.md`
  - `Documentation/Backlog/FEAT-027-phase4-file-level-change-detection.md`
  - `Documentation/Backlog/FEAT-027-phase5-telegram-notification.md`

---

### P2-003 — SPEC_DEVIATION — Misleading dead-code comment on `PENDING + processed_files > 0` branch in state-only loop
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 2
- **Spec reference** (`FEAT-027-phase2-pinned-first-partial-readiness.md`, Task 2.5):
  > Same `partial` label for `IN_PROGRESS + processed_files > 0` in state-only loop

- **What was found**: The `in_progress` label in the state-only loop at lines 187–188 is CORRECT per Phase 6 (same as P2-001 retraction rationale). The label part of this finding is a false positive. However, lines 191–192 of the state-only loop handle `PENDING + processed_files > 0` — this state occurs when the server restarts and resets stale `IN_PROGRESS` entries to `PENDING` while retaining `processed_files`. The Phase 6 spec (line 72) documents this scenario. The branch is NOT dead code — it handles restart-recovery.

- **What should exist**: Remove the 'dead code' comment at lines 191–192 and replace with a comment explaining the restart-recovery scenario.

- **Affected files**:
  - `archon/cli/doctor.py` (lines 191–192)

---

### P4-003 — WRONG_TEST — Ingest failure test covers only soft-error; hard-raise on changed file untested
- [x] **Type**: WRONG_TEST
- **Phase**: 4
- **Spec reference** (`FEAT-027-phase4-file-level-change-detection.md`, test list):
  > `test_sync_apply_changes_ingest_failure_preserves_old_mtime` — "`ingest_file` fails for a changed file: `file_mtimes` retains the OLD mtime (ensures retry on next sync)"

- **What was found**: The test at line 3478 (`tests/search/test_sync.py`) tests a soft-error
  (`ingest_file` returns `status="error"`). A hard raise (`ingest_file` raises an exception)
  on a changed file produces a FAILED state with different semantics — the old mtime must also
  be preserved in that state. No test covers the hard-raise path for changed files specifically.

- **What should exist**: A test where `ingest_file` **raises** (not returns `status="error"`)
  on a changed file, and verifies the FAILED state's `file_mtimes` still contains the old
  mtime for that file (not cleared).

- **NOTE**: Downgraded from MAJOR to MINOR. The hard-raise path (exception propagating from `ingest_file`) is partially covered by `test_sync_apply_changes_failed_midway` for new files. The specific gap is that no test verifies the old mtime is preserved for a *changed* file when ingest raises rather than returns `status='error'` — but since nothing removes the old mtime from `file_mtimes` on exception (the update only happens inside `if ingest_result.status == 'ok':`), this is a narrow edge case with low risk. Downgraded from MAJOR to MINOR.

- **Affected files**:
  - `tests/search/test_sync.py`

---

### P3-001 — SPEC_DEVIATION — Phase 3 MCP test names use `rag` prefix
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 3
- **Spec reference** (`FEAT-027-phase3-resumable-indexing.md`, Task 3.5 test list):
  > `test_handle_search_collection_reindex_clears_state`
  > `test_handle_search_collection_reindex_state_clear_failure_non_fatal`

- **What was found**: Tests named `test_handle_rag_collection_reindex_clears_state` and
  `test_handle_rag_collection_reindex_state_clear_failure_non_fatal` (lines 1792, 1835,
  `tests/ai/test_archon_toolkit_search.py`).

- **What should exist**: Rename to match spec names (`search` not `rag` prefix).

- **Affected files**:
  - `tests/ai/test_archon_toolkit_search.py` (lines 1792, 1835)

---

### P3-002 — MISSING_TEST — No end-to-end test: new files ingested, existing files skipped
- [x] **Type**: MISSING_TEST
- **Phase**: 3
- **Spec reference** (`FEAT-027-rag-background-indexing-progress.md`, Phase 3 Scenarios):
  > "New files added to collection directory → New paths not in `processed_paths` are ingested; existing paths skipped"

- **What was found**: `test_sync_resumes_from_processed_paths` verifies `exclude_paths` is
  non-empty but uses a mock `ingest_directory` that always returns a result regardless of
  what's excluded. The actual skip behavior is never exercised end-to-end.

- **What should exist**: A test that: (1) pre-seeds `processed_paths`, (2) uses real or
  semi-real `ingest_directory` that filters by `exclude_paths`, and (3) asserts pre-seeded
  paths do NOT appear in new ingest results while new paths DO.

- **Affected files**:
  - `tests/search/test_sync.py`

---

### P3-003 — WRONG_IMPL — DONE state uses `len(results)` for `total_files` instead of `total_new`
- [x] **Type**: WRONG_IMPL
- **Phase**: 3
- **Spec reference** (`FEAT-027-phase3-resumable-indexing.md`, Task 3.3):
  > `total_files = resume_offset + total_new`

- **What was found**: `sync.py` line 561 uses `resume_offset + len(results)` in the DONE
  state. The FAILED path (line ~585) correctly uses `total_new`. While `len(results) ==
  total_new` in practice (one result per file), the variable is semantically wrong per spec
  and diverges from the FAILED path.

- **What should exist**: Change line 561 to use `resume_offset + total_new` to match the
  spec and the FAILED path.

- **Affected files**:
  - `archon/search/sync.py` (line 561)

---

### P3-004 — MISSING_TEST — No test asserting Phase 3 does NOT track deletions
- [x] **Type**: MISSING_TEST
- **Phase**: 3
- **Spec reference** (`FEAT-027-rag-background-indexing-progress.md`, Phase 3 Scenarios):
  > "File in `processed_paths` is deleted → Deletion not tracked in this phase — chunks remain until Phase 4"

- **What was found**: No test asserts that when a path in `processed_paths` is deleted from
  disk, Phase 3 sync does NOT remove it or error.

- **What should exist**: A test that: seeds `processed_paths` with a path, removes the file,
  runs `sync()`, and asserts the path remains in `processed_paths` and no deletion/error occurs.

- **Affected files**:
  - `tests/search/test_sync.py`

---

### P3-006 — WRONG_TEST — Batch flush test assertion is too loose
- [x] **Type**: WRONG_TEST
- **Phase**: 3
- **Spec reference** (`FEAT-027-phase3-resumable-indexing.md`, Task 3.3):
  > `test_sync_batched_path_flush_every_50_files` — "100 files: state write at file 50 with 50 paths; final write with 100 paths"

- **What was found**: `test_sync_batched_path_flush_every_50_files` (line 1891,
  `tests/search/test_sync.py`) asserts `50 in write_path_counts` and `100 in write_path_counts`
  — does not verify ordering or that exactly one write at 50 and one at 100 occurred. The
  `write_path_counts` list also includes 0-count entries from the early PENDING/IN_PROGRESS
  writes.

- **What should exist**: Assert the write sequence is exactly `[..., 50, 100]` in order,
  confirming one mid-ingest batch at 50 and one final write at 100.

- **Affected files**:
  - `tests/search/test_sync.py` (line 1891)

---

### P3-007 — MISSING_TEST — No test for initial IN_PROGRESS write containing `processed_paths` before any file
- [x] **Type**: MISSING_TEST
- **Phase**: 3
- **Spec reference** (`FEAT-027-phase3-resumable-indexing.md`, Task 3.3, acceptance criterion):
  > "Initial `IN_PROGRESS` write includes `processed_paths = resume_paths` (not default `[]`)"

- **What was found**: No test verifies that immediately after `_ingest_collection` transitions
  to IN_PROGRESS (before any `on_file_complete` fires), the state file already contains
  `processed_paths = resume_paths`. This is the crash-before-first-batch scenario.

- **What should exist**: A test that captures the first IN_PROGRESS state write and asserts
  `processed_paths == resume_paths` (not `[]`).

- **Affected files**:
  - `tests/search/test_sync.py`

---

### P4-001 — MISSING_TEST — 2 of 4 spec-named `delete_by_source_path` tests absent by name
- [x] **Type**: MISSING_TEST
- **Phase**: 4
- **Spec reference** (`FEAT-027-phase4-file-level-change-detection.md`, Task 4.1 test list):
  > `test_delete_by_source_path_delegates_to_delete_document`
  > `test_delete_by_source_path_returns_count`

- **What was found**: Only `test_delete_by_source_path_computes_doc_id` (compound test) and
  `test_delete_by_source_path_collection_not_found` exist in `tests/search/test_store.py`.
  The two intermediate tests are covered in one compound test but not as separately named cases.

- **What should exist**: Add `test_delete_by_source_path_delegates_to_delete_document` and
  `test_delete_by_source_path_returns_count` as separate named tests.

- **Affected files**:
  - `tests/search/test_store.py`

---

### P4-004 — SPEC_DEVIATION — Step 9 manifest includes failed-apply collections
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 4
- **Spec reference** (`FEAT-027-phase4-file-level-change-detection.md`, Step 9 pseudocode):
  > Manifest retention: `successfully_added | successfully_updated | unchanged` — only successful applies

- **What was found**: `sync.py` line 252 uses `to_update` (all change-detected collections,
  including those where `_apply_collection_changes` returned an error) instead of a
  `successfully_updated` subset. A collection where change detection triggered but apply
  failed is incorrectly retained in the manifest.

- **What should exist**: Track a separate `successfully_updated: set[str]` of collections
  where `_apply_collection_changes` returned `None`. Use this set (not `to_update`) in the
  Step 9 manifest write.

- **Affected files**:
  - `archon/search/sync.py` (line 252)

---

### P4-008 — MISSING_TEST — No test for `OSError` during `file.stat()` on a changed file's mtime write
- [x] **Type**: MISSING_TEST
- **Phase**: 4
- **Spec reference** (`FEAT-027-phase4-file-level-change-detection.md`, acceptance criteria):
  > "In `_apply_collection_changes`, `file.stat().st_mtime` is wrapped in `try/except OSError`; on error, old mtime is retained for changed files"

- **What was found**: The `file.stat()` OSError path for **new** files (no mtime added) is
  tested by `test_sync_file_vanishes_between_check_and_apply`. The path for **changed** files
  (old mtime retained) is NOT tested — the `except OSError: pass  # keep old mtime` branch
  at line 656 of `sync.py` has no dedicated test.

- **What should exist**: A test where `ingest_file` succeeds for a changed file but
  `file.stat()` raises `OSError` during the mtime write — verifying the OLD mtime is
  preserved (not cleared) in the resulting DONE state.

- **Affected files**:
  - `tests/search/test_sync.py`

---

### P4-013 — MISSING_TEST — No test for `state_store=None` existing collection in Step 7
- [x] **Type**: MISSING_TEST
- **Phase**: 4
- **Spec reference**: Step 7 is skipped when `state_store is None` — existing collections
  fall through to `result.unchanged`.

- **What was found**: No test verifies that a sync with `state_store=None` on an existing
  DONE collection correctly places it in `result.unchanged` rather than crashing or entering
  the `to_check` path.

- **What should exist**: `test_sync_no_state_store_existing_collection_goes_to_unchanged` —
  existing collection, `state_store=None` → `result.unchanged` contains the collection;
  no crash; `_check_collection_changes` not called.

- **Affected files**:
  - `tests/search/test_sync.py`

---

### P5-003 — MISSING_TEST — No gateway test for `search_enabled=True` but `NOT_INSTALLED`
- [x] **Type**: MISSING_TEST
- **Phase**: 5
- **Spec reference** (`FEAT-027-phase5-telegram-notification.md`, Task 5.4 test list):
  > `test_monitor_task_none_on_shutdown_when_search_disabled`

- **What was found**: The test only covers `search_enabled=False`. When `search_enabled=True`
  but `search_state=NOT_INSTALLED` or `NOT_REGISTERED`, `_monitor_task` is also `None`
  and shutdown must not raise — but this is untested.

- **What should exist**: Add a test for `cfg.search.enabled=True`,
  `search_state=SearchState.NOT_INSTALLED` → `_monitor_task` remains `None` → shutdown
  does not raise.

- **Affected files**:
  - `tests/gateway/test_gateway.py`

---

### P5-005 — MISSING_TEST — Manual trigger async test methods missing `@pytest.mark.asyncio`
- [x] **Type**: MISSING_TEST
- **Phase**: 5
- **Spec reference**: Task 5.3 tests require `@pytest.mark.asyncio`.

- **What was found**: `TestRagSyncManualTrigger` (lines 2194, 2226,
  `tests/ai/test_archon_toolkit_search.py`) contains `async def` test methods with **no
  `@pytest.mark.asyncio` decorator**.

- **What should exist**: Add `@pytest.mark.asyncio` to both async test methods for explicit clarity.
  NOTE: `asyncio_mode = 'auto'` is already set in `pyproject.toml` — these tests DO run. Adding `@pytest.mark.asyncio` decorators is cosmetic only.

- **Affected files**:
  - `tests/ai/test_archon_toolkit_search.py` (lines 2194, 2226)

---

### P5-008 — WRONG_TEST — `test_clears_trigger_before_send` uses fragile `call_order[0]` assertion
- [x] **Type**: WRONG_TEST
- **Phase**: 5
- **Spec reference** (`FEAT-027-phase5-telegram-notification.md`, Task 5.2):
  > "`set_trigger(None)` called **before** `_send_to_all`"

- **What was found**: `tests/search/test_notification_monitor.py` line 157 asserts
  `call_order[0] == "set_trigger"` — fragile because it depends on no other call having
  been appended before it. `call_order.index("set_trigger") < call_order.index("send_message")`
  is a stronger assertion.

- **What should exist**: Replace `assert call_order[0] == "set_trigger"` with:
  ```python
  assert call_order.index("set_trigger") < call_order.index("send_message")
  ```

- **Affected files**:
  - `tests/search/test_notification_monitor.py` (line 157)

---

### P5-009 — MISSING_TEST — No test for "bot not yet connected" notification scenario
- [x] **Type**: MISSING_TEST
- **Phase**: 5
- **Spec reference** (`FEAT-027-rag-background-indexing-progress.md`, Phase 5 Scenarios):
  > "Daemon not connected to Telegram → notification silently skipped; no error"

- **What was found**: `test_send_failure_is_caught` covers `bot.send_message` raising
  `RuntimeError` but does not cover the case where the bot session is uninitialized
  (e.g. bot exists but has no active polling session yet). The acceptance criterion
  explicitly names this scenario and it has no dedicated test.

- **What should exist**: A test where `bot.send_message` raises `RuntimeError("Bot is not
  running")` (or equivalent) — verifying the exception is caught, logged, and does not
  propagate from the notification monitor.

- **Affected files**:
  - `tests/search/test_notification_monitor.py`

---

### P6-001 — SPEC_DEVIATION — `test_in_progress_label_is_in_progress` absent by spec name
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 6
- **Spec reference** (`FEAT-027-phase6-doctor-integration.md`, Task 6.1 test list):
  > `test_in_progress_label_is_in_progress` — `IN_PROGRESS + processed_files > 0` → output contains `"in_progress"` and NOT `"partial"`

- **What was found**: No test with this exact name exists in `tests/cli/test_doctor.py`. The behavior IS covered by `test_doctor_partial_no_warning` (line 907) which verifies the same assertions (`"in_progress" in out` and `"partial" not in out` for IN_PROGRESS + processed_files=50). This is a naming mismatch only.

- **What should exist**: Rename the existing test `test_doctor_partial_no_warning` to `test_in_progress_label_is_in_progress` to match the spec name.

- **Affected files**:
  - `tests/cli/test_doctor.py`

---

### P6-002 — SPEC_DEVIATION — `test_in_progress_no_files_label` absent by spec name
- [x] **Type**: SPEC_DEVIATION
- **Phase**: 6
- **Spec reference** (`FEAT-027-phase6-doctor-integration.md`, Task 6.1 test list):
  > `test_in_progress_no_files_label` — `IN_PROGRESS + processed_files == 0` → output contains `"indexing starting"`

- **What was found**: No test with this exact name in `tests/cli/test_doctor.py`. The behavior IS covered by `test_doctor_in_progress_zero_no_warning` (line 931). This is a naming mismatch only.

- **What should exist**: Rename the existing test `test_doctor_in_progress_zero_no_warning` to `test_in_progress_no_files_label` to match the spec name.

- **Affected files**:
  - `tests/cli/test_doctor.py`

---

### P6-004 — SPEC_DEVIATION — Phase 6 not marked Done in main spec and phase file
- [ ] **Type**: SPEC_DEVIATION
- **Phase**: 6
- **Spec reference**: Main spec Phase 6 heading has no ✅. Phase file header says `Status: To Do`.

- **What was found**: All other completed phases (1–5, 7, 8) are marked ✅ Done in the
  main spec. Phase 6 tasks 6.1 and 6.2 are checked `[x]` in the phase file, but the
  phase file header still says `Status: To Do` and the main spec has no ✅.

- **What should exist**: Update `FEAT-027-rag-background-indexing-progress.md` Phase 6
  heading to `✅ Done`. Update `FEAT-027-phase6-doctor-integration.md` header to
  `Status: Complete`.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`
  - `Documentation/Backlog/FEAT-027-phase6-doctor-integration.md`

---

### P7-002 — SPEC_DEVIATION — Main spec says ETA "< 10 seconds" but implementation uses < 60 seconds
- [ ] **Type**: SPEC_DEVIATION
- **Phase**: 7
- **Spec reference** (`FEAT-027-rag-background-indexing-progress.md`, Phase 7 Scenarios):
  > "ETA < 10 seconds → Shows `< 1 min remaining`"

- **What was found**: The phase spec (`FEAT-027-phase7-eta-in-status.md`) and implementation
  both use `eta < 60` (< 60 seconds → `< 1 min remaining`). The main spec's "< 10 seconds"
  is internally inconsistent with the phase spec and the boundary test
  `(59, "< 1 min remaining")`.

- **What should exist**: Update the main spec Phase 7 scenarios table: change
  "ETA < 10 seconds" to "ETA < 60 seconds".

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md` (Phase 7 scenarios table)

---

### P8-001 — SPEC_DEVIATION — Phase 8 phase file still says `Status: To Do`
- [ ] **Type**: SPEC_DEVIATION
- **Phase**: 8
- **Spec reference**: Main spec marks Phase 8 as ✅ Done; task checkboxes in phase file are `[x]`.

- **What was found**: `FEAT-027-phase8-watch-mode.md` line 4 still says `Status: To Do`.

- **What should exist**: Update header to `Status: Complete`.

- **Affected files**:
  - `Documentation/Backlog/FEAT-027-phase8-watch-mode.md`

---

### P8-002 — WRONG_IMPL — `_fire()` clears `_timer` before `try` block instead of in `finally`
- [ ] **Type**: WRONG_IMPL
- **Phase**: 8
- **Spec reference** (`FEAT-027-phase8-watch-mode.md`, Task 8.2 architecture):
  > "In a `finally` block, under lock, set `self._timer = None` to release the timer reference and prevent memory leak."

- **What was found**: `archon/search/watcher.py` lines 99–112: `self._timer = None` is set
  BEFORE the `try` block (not in a `finally`). If `asyncio.run_coroutine_threadsafe` raises
  an unexpected exception (other than `RuntimeError`), `_timer` is already cleared — the
  clearing happens before the potential raise. This deviates from the spec's `finally` intent.

- **What should exist**: Move `self._timer = None` into a `finally` block after the
  `try/except RuntimeError` block, as the spec requires.

- **Affected files**:
  - `archon/search/watcher.py` (lines 99–112)

---

### P8-004 — MISSING_TEST — Rapid-events test does not verify single callback firing
- [ ] **Type**: MISSING_TEST
- **Phase**: 8
- **Spec reference** (`FEAT-027-phase8-watch-mode.md`, Task 8.2 test):
  > `test_debounce_handler_resets_timer_on_rapid_events` — "single second timer active; **callback fires once**"

- **What was found**: The test at line 72 (`tests/search/test_watcher.py`) verifies timer
  replacement (`second_timer is not first_timer`) but does NOT verify that the callback
  fires only once. The test does not run the asyncio event loop or verify call count.

- **What should exist**: Add an assertion that the async sync callback is invoked exactly
  once when two rapid events arrive (one debounced timer fires).

- **Affected files**:
  - `tests/search/test_watcher.py` (line 72)

---

## Fix priority order

Resolve in this order — each group unblocks or validates the next:

| Priority | ID | Why first |
|---|---|---|
| 1 | P1-001 | Core architectural bug — blocks the fundamental feature promise |
| 2 | P1-002 | Test that would have caught P1-001 |
| 3 | P1-005 | Wrong string shipped to users |
| 4 | P1-006 | Test that would have caught P1-005 |
| 5 | P4-006 | Soft-failed files permanently skipped from re-index (silent data corruption) |
| 6 | P1-009 | Batching test fires wrong callback — crash-recovery batching unverified |
| 7 | P6-005 | Spec/impl/test triangle on auto_reindex checkmark — must pick one |
| 8 | P2-006 | Core Phase 2 deliverable (pinned_collections wiring) unverified |
| 9 | P4-012 | Spec scenario not tested — missing deletion case |
| 10 | P1-004 | PENDING state total_files=0 postcondition not asserted in test |
| 11 | P5-002 | Gateway monitor start condition broader than spec |
| 12+ | All MINOR | P5-001, P4-004, P1-003, P4-003, P5-005, P5-008, P5-009, test naming, spec wording, coverage gaps |
