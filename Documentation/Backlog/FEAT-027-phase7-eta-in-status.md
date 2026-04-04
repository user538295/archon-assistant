# FEAT-027-P7 — ETA in `archon rag status` Output
**Purpose**: Show estimated time remaining for in-progress collections in `archon rag status` and the `rag_status` MCP tool
**Audience**: Users waiting for background indexing after install or update; Claude answering "how long until RAG is ready?" in Telegram
**Status**: In Progress

---

## Background
Phases 1–6 added background indexing, progress visibility, resumability, change detection, Telegram notifications, and `archon doctor` integration. The `archon rag status` output shows `N / M files` for in-progress collections but gives no indication of how long remains. The `rag_status` MCP tool similarly exposes `processed_files` and `total_files` but no ETA — Claude cannot give a time estimate when asked.

Full feature spec: `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 7 section.

## Goal
`archon rag status` appends `~N min remaining` (or `< 1 min remaining`) to the progress column for any collection that is `IN_PROGRESS` with at least 10 files processed. The `rag_status` MCP JSON response adds an `eta_seconds` integer field for the same collections. ETA is suppressed when fewer than 10 files have been processed (too noisy), when the collection is not `IN_PROGRESS`, or when `started_at` is missing.

---

## Scope

### In Scope
- `compute_eta_seconds(cp: CollectionProgress, now: datetime | None = None) -> int | None` — pure function in `archon/rag/progress.py`
- ETA appended to progress column in `_print_progress_table` in `archon/cli/rag_cmd.py`
- `eta_seconds` integer key conditionally added to each collection dict in `_handle_rag_status` in `archon/ai/archon_toolkit_rag.py` (key absent when ETA is not applicable; never set to `None`)

### Out of Scope
- ETA in `archon doctor` — doctor is a snapshot check, not a progress monitor
- Confidence intervals or uncertainty ranges
- Per-file timing (file-level duration not tracked)
- Historical ETA accuracy across runs
- ETA push notifications to Telegram mid-index

---

## Acceptance criteria
- [ ] `archon rag status` shows `~N min remaining` for `IN_PROGRESS` collections with ≥10 processed files
- [ ] `archon rag status` shows `< 1 min remaining` when eta_seconds < 60
- [ ] `archon rag status` shows no ETA for `IN_PROGRESS` with fewer than 10 processed files
- [ ] `archon rag status` shows no ETA for non-`IN_PROGRESS` collections (DONE, PENDING, FAILED)
- [ ] `rag_status` MCP JSON includes `eta_seconds` (integer) for qualifying `IN_PROGRESS` collections
- [ ] `rag_status` MCP JSON omits `eta_seconds` (key absent) when ETA is not applicable
- [ ] `compute_eta_seconds` returns `None` when `processed_files < 10`
- [ ] `compute_eta_seconds` returns `None` when `status != IN_PROGRESS`
- [ ] `compute_eta_seconds` returns `None` when `started_at` is `None` or unparseable
- [ ] `compute_eta_seconds` returns `None` when `elapsed_seconds <= 0`
- [ ] `compute_eta_seconds` returns `None` when `processed_files >= total_files` (nothing remaining)
- [ ] All existing `tests/cli/test_rag_cmd.py` assertions for `_print_progress_table` continue to pass after ETA addition
- [ ] All existing tests continue to pass
- [ ] `_RAG_STATUS_SCHEMA["description"]` mentions `eta_seconds` so Claude selects the tool for ETA queries

---

## What does NOT change
- `CollectionProgress` and `IndexingState` dataclasses — no new fields added to either
- State file schema — no new persisted fields; ETA is computed on-the-fly from `started_at` and `processed_files`
- `_print_progress_table` table structure — only the `progress_str` column value changes for `IN_PROGRESS` rows
- `_print_progress_table` status column label for `IN_PROGRESS` collections — remains `"partial"` (pre-existing inconsistency with `archon doctor`'s `"in_progress"` label; fixing is deferred to a future cleanup phase)
- `archon doctor` output
- `archon rag status` exit code logic (non-zero on `FAILED`)
- Any other fields in the `rag_status` MCP JSON response

---

## Known limitations / accepted trade-offs
- ETA is based on a uniform files/second rate. Files that vary wildly in parse time (PDF vs plain text) will produce noisy estimates. No confidence interval is shown — "best-effort" is the accepted standard.
- ETA is suppressed when fewer than 10 files have been processed; the first few files are most likely to skew the rate. This means large slow collections will show no ETA early on. Accepted.
- The ETA is recomputed fresh from `started_at` at read time — it is not stored. If the sync rate changes (e.g. heavier files later in the batch), the ETA will self-correct on the next `archon rag status` poll.
- `eta_seconds` is omitted (key absent) in the MCP response for non-qualifying collections. Consumers must treat the key as optional.
- The status column in `archon rag status` displays `"partial"` for `IN_PROGRESS` collections (see `_print_progress_table`), while `archon doctor` displays `"in_progress"` for the same state (Phase 6). This inconsistency pre-dates Phase 7 and is out of scope here. ETA is added alongside whichever label appears.

---

## Architecture

### New function in `archon/rag/progress.py`

```python
def compute_eta_seconds(
    cp: CollectionProgress,
    now: datetime | None = None,
) -> int | None:
    """Compute estimated seconds remaining for an in-progress collection.

    Returns None when ETA cannot be computed reliably:
      - status is not IN_PROGRESS
      - fewer than 10 files processed (too early to be reliable)
      - started_at is missing or unparseable
      - elapsed seconds is zero or negative
      - no files remaining (processed_files >= total_files)
    """
    if cp.status != IndexingStatus.IN_PROGRESS:
        return None
    if cp.processed_files < 10:
        return None
    if cp.started_at is None:
        return None
    if cp.processed_files >= cp.total_files:
        return None
    try:
        started = datetime.fromisoformat(cp.started_at)
    except (ValueError, TypeError):
        return None
    if now is None:
        now = datetime.now(UTC)
    # Ensure both datetimes are comparable: convert naive to UTC-aware
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    elapsed = (now - started).total_seconds()
    if elapsed <= 0:
        return None
    fps = cp.processed_files / elapsed
    remaining = cp.total_files - cp.processed_files
    return max(0, int(remaining / fps))
```

### Changes in `archon/cli/rag_cmd.py` — `_print_progress_table`

Add ETA suffix to `progress_str` for `IN_PROGRESS` collections:

```python
import math
from archon.rag.progress import compute_eta_seconds  # add to imports

# IMPORTANT: This block must be INSIDE the `if progress is not None:` branch,
# not at the same indentation level as the outer if/else (which would cause
# AttributeError when progress is None for LanceDB-only entries).
# Place it after the error suffix line (if progress.error: ...), before print().

# Inside the for-loop, AFTER the else-block that builds progress_str
# (after the error suffix is appended, before the print() call):
if progress.status == IndexingStatus.IN_PROGRESS:
    eta = compute_eta_seconds(progress)
    if eta is not None:
        if eta < 60:
            progress_str += "  < 1 min remaining"
        else:
            mins = math.ceil(eta / 60)
            progress_str += f"  ~{mins} min remaining"
```

ETA coexists with the error suffix when both are present: `progress_str` may contain `(parse error)` from the error suffix block before the ETA suffix is appended. Both are shown — error shows as `(parse error)` suffix, ETA shows as `~N min remaining` suffix.

### Changes in `archon/ai/archon_toolkit_rag.py` — `_handle_rag_status`

Add `eta_seconds` to each collection dict where ETA is available.

The `compute_eta_seconds` name must be added to the EXISTING import tuple inside the `try` block,
NOT as a new standalone import. Add it to the line that already imports `CollectionProgress`,
`IndexingStateStore`, etc. (approximately line 20 in `archon_toolkit_rag.py`):

```python
# Inside the try: ... except ImportError: block — append compute_eta_seconds here:
from archon.rag.progress import (
    CollectionProgress,
    IndexingState,
    IndexingStateStore,
    IndexingStatus,
    compute_eta_seconds,   # NEW
)
```

A standalone `from archon.rag.progress import compute_eta_seconds` outside the try block would
raise an uncaught ImportError in environments without RAG packages installed.

```python
# In the for-loop building col_dicts:
if state and c.name in state.collections:
    cp = state.collections[c.name]
    d["status"] = _resolve_status(cp)
    d["processed_files"] = cp.processed_files
    d["total_files"] = cp.total_files
    d["error"] = cp.error
    d["error_count"] = cp.error_count
    eta = compute_eta_seconds(cp)      # NEW
    if eta is not None:                # NEW
        d["eta_seconds"] = eta         # NEW

# In the state-only block (collections in state file but NOT in LanceDB):
if state:
    for name, cp in state.collections.items():
        if name not in indexed_names:
            entry: dict[str, Any] = {
                "name": name,
                "doc_count": 0,
                "chunk_count": 0,
                "status": _resolve_status(cp),
                "processed_files": cp.processed_files,
                "total_files": cp.total_files,
                "error": cp.error,
                "error_count": cp.error_count,
            }
            eta = compute_eta_seconds(cp)      # NEW
            if eta is not None:                # NEW
                entry["eta_seconds"] = eta     # NEW
            col_dicts.append(entry)
```

Also update the `description` field of `_RAG_STATUS_SCHEMA` to mention the optional ETA. Change the description string to end with: `...and the list of indexed collections with document and chunk counts; includes optional eta_seconds (integer, seconds remaining) for in-progress collections.` This ensures Claude knows to use this tool when answering ETA questions.

---

## Tests

### Task 7.1 tests — `tests/rag/test_progress.py`
- **test_compute_eta_returns_none_when_not_in_progress** (unit): Parametrized over `[IndexingStatus.DONE, IndexingStatus.PENDING, IndexingStatus.FAILED]` — each yields a separate test case; each status → `None`. Use `@pytest.mark.parametrize` so that failure of one status does not mask others.
- **test_compute_eta_returns_none_when_too_few_files** (unit): `IN_PROGRESS`, `processed_files=9` → `None`
- **test_compute_eta_returns_none_when_started_at_missing** (unit): `IN_PROGRESS`, `processed_files=50`, `started_at=None` → `None`
- **test_compute_eta_returns_none_when_elapsed_zero** (unit): `now` equals `started_at` exactly → `None`. Use `now=` kwarg for determinism.
- **test_compute_eta_returns_none_when_nothing_remaining** (unit): `IN_PROGRESS`, `processed_files=100`, `total_files=100` → `None` (guard `processed >= total` fires; use values ≥10 so the `< 10` guard doesn't fire first)
- **test_compute_eta_basic_calculation** (unit): 50 files in 50s, 50 remaining → `50`: inject `started_at = (fixed_now - timedelta(seconds=50)).isoformat()` and `now=fixed_now` with `processed_files=50`, `total_files=100`. Use `now=` kwarg for determinism.
- **test_compute_eta_accepts_custom_now** (unit): fixed `now` kwarg → deterministic result. Inject `now=` 100s after `started_at`, `processed_files=20`, `total_files=100` → `400` (fps=0.2, remaining=80, int(80/0.2)=400). Use `now=` kwarg for determinism.
- **test_compute_eta_returns_none_for_invalid_started_at** (unit): `started_at="not-a-date"` → `None`
- **test_compute_eta_returns_value_at_exact_threshold** (unit): `processed_files=10`, `total_files=100`, `now=` 10s after `started_at` → returns `90` (int(90/1.0) = 90); verifies the threshold is `< 10` not `<= 10`, and result is the expected numeric value. Use `now=` kwarg for determinism.
- **test_compute_eta_naive_started_at_treated_as_utc** (unit): `started_at` is a naive ISO string (no timezone suffix, e.g. `"2026-04-04T10:00:00"`), inject UTC-aware `now=` 100s after `started_at`, `processed_files=20`, `total_files=100` → returns `400` (fps=0.2, remaining=80, int(80/0.2)=400); verifies naive `started_at` is treated as UTC and arithmetic is correct. Use `now=` kwarg for determinism.
- **test_compute_eta_returns_none_when_elapsed_negative** (unit): inject `now` that is 5 seconds BEFORE `started_at` (clock skew) → `None` (hits `elapsed <= 0` guard). Use `now=` kwarg for determinism.
- **test_compute_eta_returns_none_when_total_files_zero** (unit): `IN_PROGRESS`, `processed_files=10`, `total_files=0` → `None` (guard `processed >= total`: 10 >= 0 is True, nothing remaining)

### Task 7.2 tests — `tests/cli/test_rag_cmd.py`
- **test_status_shows_eta_for_in_progress** (unit): Mock `compute_eta_seconds` at import site (patch `archon.cli.rag_cmd.compute_eta_seconds`) to return `300` (exact-division case); assert output contains `'~5 min remaining'`
- **test_status_shows_ceil_rounding_for_eta** (unit): Mock `compute_eta_seconds` to return `150` (2.5 min); assert output contains `'~3 min remaining'` (verifies `math.ceil(150/60) = 3`, not `round(2.5) = 2` which is Python banker's rounding)
- **test_status_shows_exactly_1_min_at_boundary** (unit): Mock `compute_eta_seconds` to return `60`; assert output contains `'~1 min remaining'` (not `'< 1 min'`)
- **test_status_shows_less_than_1_min_at_boundary** (unit): Mock `compute_eta_seconds` to return `59`; assert output contains `'< 1 min remaining'`

  (The two boundary tests above can be written as a single parametrized test with `[(59, "< 1 min remaining"), (60, "~1 min remaining")]` if preferred.)

- **test_status_suppresses_eta_when_too_few_files** (unit): Mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) to return `None`; assert output does NOT contain `'remaining'` (verifies CLI renders no ETA when function returns None)
- **test_status_suppresses_eta_for_non_in_progress** (unit): Parametrized over `[IndexingStatus.DONE, IndexingStatus.FAILED, IndexingStatus.PENDING]`; each status → output does NOT contain `'remaining'`. The ETA block is gated by `progress.status == IndexingStatus.IN_PROGRESS`, so no mocking needed for these cases.
- **test_status_no_eta_when_processed_zero** (unit): Mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) to return `None`; assert output does NOT contain `'remaining'`

Note: All `_print_progress_table` tests for IN_PROGRESS collections must mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) — positive return for 'shows ETA' tests, `None` for 'suppresses ETA' tests. Non-IN_PROGRESS tests (DONE, FAILED, PENDING) do NOT need mocking since the ETA block is gated by `progress.status == IndexingStatus.IN_PROGRESS`.

Note: Review existing `tests/cli/test_rag_cmd.py` tests that call `_print_progress_table` with `IN_PROGRESS` collections. Tests that do NOT set `started_at` are unaffected (ETA returns `None`, no suffix). Tests that set a valid `started_at` AND `processed_files >= 10` will now have an ETA suffix in output — those tests need `compute_eta_seconds` mocked to return `None` or have their assertions updated to accept the ETA suffix.

### Task 7.3 tests — `tests/ai/test_archon_toolkit_rag.py`
- **test_rag_status_mcp_includes_eta_seconds** (unit): Mock `compute_eta_seconds` at call site (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `300`; parsed JSON collection dict contains `'eta_seconds': 300` (verify exact value, not just key presence)
- **test_rag_status_mcp_omits_eta_seconds_when_too_few** (unit): Mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `None`; assert JSON collection dict does NOT contain `'eta_seconds'` key
- **test_rag_status_mcp_omits_eta_seconds_for_non_in_progress** (unit): Parametrized over DONE and FAILED; mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `None`; JSON collection dict does NOT contain `'eta_seconds'` key
- **test_rag_status_mcp_includes_eta_seconds_state_only** (unit): Mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `300`; `IN_PROGRESS` + ≥10 files in state-only block (not in LanceDB) → parsed JSON collection dict contains `'eta_seconds': 300` (verify exact value, not just key presence)

Note: All MCP tests must mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) — return a positive integer for 'includes' tests, `None` for 'omits' tests. This isolates the MCP rendering logic from the ETA computation logic.

---

## Documentation update
- [ ] `Documentation/Backlog/FEAT-027-rag-background-indexing-progress.md`, Phase 7 section: mark ✅ Done when complete

---

## Task breakdown

### Phase 7 — ETA in status output
> **Releasable**: after Task 7.3 — ETA appears in both CLI and MCP tool for all qualifying in-progress collections

#### Task 7.1 — `compute_eta_seconds` pure function in `progress.py`
- [x] **File**: `archon/rag/progress.py`
- **Depends on**: nothing (all required fields — `status`, `processed_files`, `total_files`, `started_at` — exist from Phase 1)
- **Description**:
  - Add `compute_eta_seconds(cp: CollectionProgress, now: datetime | None = None) -> int | None`
  - Returns `None` when:
    - `cp.status != IndexingStatus.IN_PROGRESS`
    - `cp.processed_files < 10`
    - `cp.started_at is None`
    - `cp.processed_files >= cp.total_files`
    - `started_at` fails `datetime.fromisoformat()` (invalid string)
    - elapsed seconds ≤ 0 (clock skew / same-tick)
  - Formula: `fps = cp.processed_files / elapsed_seconds`; `eta = int((cp.total_files - cp.processed_files) / fps)`; clamp to `max(0, eta)`
  - Timezone normalization: convert both `started` and `now` to UTC-aware if either is naive (`replace(tzinfo=UTC)`). Since `started_at` is always written as `datetime.now(UTC).isoformat()`, a naive `started_at` means legacy/edited state and UTC is assumed. Naive `now` via the injectable parameter is also treated as UTC.
  - `now` defaults to `datetime.now(UTC)` — injectable for deterministic tests
  - No new imports needed beyond existing `datetime`, `UTC` already in the module
- **Releasable**: `compute_eta_seconds` is importable from `archon.rag.progress` and returns correct values
- **Tests (TDD)** — `tests/rag/test_progress.py`:
  - Unit: `test_compute_eta_returns_none_when_not_in_progress` — Parametrized over `[IndexingStatus.DONE, IndexingStatus.PENDING, IndexingStatus.FAILED]` — each yields a separate test case; each status → `None`. Use `@pytest.mark.parametrize` so that failure of one status does not mask others.
  - Unit: `test_compute_eta_returns_none_when_too_few_files` — `IN_PROGRESS`, `processed_files=9`, valid `started_at` → `None`
  - Unit: `test_compute_eta_returns_none_when_started_at_missing` — `IN_PROGRESS`, `processed_files=50`, `started_at=None` → `None`
  - Unit: `test_compute_eta_returns_none_when_elapsed_zero` — `now` equals `started_at` exactly → `None`. Use `now=` kwarg for determinism.
  - Unit: `test_compute_eta_returns_none_when_nothing_remaining` — `IN_PROGRESS`, `processed_files=100`, `total_files=100` → `None` (use values ≥10 so `< 10` guard doesn't fire first)
  - Unit: `test_compute_eta_basic_calculation` — inject `started_at = (fixed_now - timedelta(seconds=50)).isoformat()` and `now=fixed_now` with `processed_files=50`, `total_files=100` → `50`. Use `now=` kwarg for determinism.
  - Unit: `test_compute_eta_accepts_custom_now` — inject `now=` kwarg 100s after `started_at`, `processed_files=20`, `total_files=100` → `400` (fps=0.2, remaining=80, int(80/0.2)=400). Use `now=` kwarg for determinism.
  - Unit: `test_compute_eta_returns_none_for_invalid_started_at` — `started_at="not-a-date"` → `None`
  - Unit: `test_compute_eta_returns_value_at_exact_threshold` — `processed_files=10`, `total_files=100`, `now=` 10s after `started_at` → returns `90` (int(90/1.0) = 90); verifies the threshold is `< 10` not `<= 10`, and result is the expected numeric value. Use `now=` kwarg for determinism.
  - Unit: `test_compute_eta_naive_started_at_treated_as_utc` — `started_at` is a naive ISO string (no timezone suffix, e.g. `"2026-04-04T10:00:00"`), inject UTC-aware `now=` 100s after `started_at`, `processed_files=20`, `total_files=100` → returns `400` (fps=0.2, remaining=80, int(80/0.2)=400); verifies naive `started_at` is treated as UTC and arithmetic is correct. Use `now=` kwarg for determinism.
  - Unit: `test_compute_eta_returns_none_when_elapsed_negative` — inject `now=` 5 seconds BEFORE `started_at` (clock skew) → `None` (hits `elapsed <= 0` guard). Use `now=` kwarg for determinism.
  - Unit: `test_compute_eta_returns_none_when_total_files_zero` — `IN_PROGRESS`, `processed_files=10`, `total_files=0` → `None` (guard `processed >= total`: 10 >= 0 is True, nothing remaining)
  - Checkpoint: `uv run pytest tests/rag/test_progress.py -v --no-cov -k "eta"`

#### Task 7.2 — ETA display in `_print_progress_table` (`rag_cmd.py`)
- [x] **File**: `archon/cli/rag_cmd.py`
- **Depends on**: Task 7.1
- **Description**:
  - Add `compute_eta_seconds` to existing `archon.rag.progress` import; add `import math` to `rag_cmd.py` imports
  - INSIDE the `if progress is not None:` block, after the inner `else` clause that builds `progress_str` (specifically after the `if progress.error: progress_str += ...` line), before the outer `print()` call. Placing the ETA block outside the `if progress is not None:` block would cause `AttributeError` for LanceDB-only entries where `progress is None`. Only when `progress.status == IndexingStatus.IN_PROGRESS`. ETA coexists with error suffix when both are present.
    - Call `eta = compute_eta_seconds(progress)`
    - If `eta is not None` and `eta < 60`: append `"  < 1 min remaining"` to `progress_str`
    - If `eta is not None` and `eta >= 60`: append `f"  ~{math.ceil(eta / 60)} min remaining"` to `progress_str`
  - No ETA appended for `DONE`, `FAILED`, `PENDING`, or `IN_PROGRESS` with `None` ETA
  - Table column widths unchanged — ETA is part of the existing `Progress` column value, not a new column
- **Releasable**: `archon rag status` shows ETA for qualifying in-progress collections
- **Tests (TDD)** — `tests/cli/test_rag_cmd.py`:
  - Unit: `test_status_shows_eta_for_in_progress` — mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) to return `300` (exact-division case); assert output contains `'~5 min remaining'`
  - Unit: `test_status_shows_ceil_rounding_for_eta` — mock `compute_eta_seconds` to return `150` (2.5 min); assert output contains `'~3 min remaining'` (verifies `math.ceil(150/60) = 3`, not `round(2.5) = 2` which is Python banker's rounding)
  - Unit: `test_status_shows_exactly_1_min_at_boundary` — mock `compute_eta_seconds` to return `60`; assert output contains `'~1 min remaining'` (not `'< 1 min'`)
  - Unit: `test_status_shows_less_than_1_min_at_boundary` — mock `compute_eta_seconds` to return `59`; assert output contains `'< 1 min remaining'`

    (The two boundary tests above can be written as a single parametrized test with `[(59, "< 1 min remaining"), (60, "~1 min remaining")]` if preferred.)

  - Unit: `test_status_shows_less_than_1_min` — mock `compute_eta_seconds` to return `45`; assert output contains `'< 1 min remaining'`
  - Unit: `test_status_suppresses_eta_when_too_few_files` — Mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) to return `None`; assert output does NOT contain `'remaining'` (verifies CLI renders no ETA when function returns None)
  - Unit: `test_status_suppresses_eta_for_non_in_progress` — Parametrized over `[IndexingStatus.DONE, IndexingStatus.FAILED, IndexingStatus.PENDING]`; each status → output does NOT contain `'remaining'`. The ETA block is gated by `progress.status == IndexingStatus.IN_PROGRESS`, so no mocking needed for these cases.
  - Unit: `test_status_no_eta_when_processed_zero` — Mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) to return `None`; assert output does NOT contain `'remaining'`
  - Note: All `_print_progress_table` tests for IN_PROGRESS collections must mock `compute_eta_seconds` (patch `archon.cli.rag_cmd.compute_eta_seconds`) — positive return for 'shows ETA' tests, `None` for 'suppresses ETA' tests. Non-IN_PROGRESS tests (DONE, FAILED, PENDING) do NOT need mocking since the ETA block is gated by `progress.status == IndexingStatus.IN_PROGRESS`.
  - Note: Review existing `tests/cli/test_rag_cmd.py` tests that call `_print_progress_table` with `IN_PROGRESS` collections. Tests that do NOT set `started_at` are unaffected (ETA returns `None`, no suffix). Tests that set a valid `started_at` AND `processed_files >= 10` will now have an ETA suffix in output — those tests need `compute_eta_seconds` mocked to return `None` or have their assertions updated to accept the ETA suffix.
  - Checkpoint: `uv run pytest tests/cli/test_rag_cmd.py -v --no-cov -k "eta or remaining"`

#### Task 7.3 — `eta_seconds` field in `rag_status` MCP response (`archon_toolkit_rag.py`)
- [ ] **File**: `archon/ai/archon_toolkit_rag.py`
- **Depends on**: Task 7.1
- **Description**:
  - Append `compute_eta_seconds` to the existing `from archon.rag.progress import (...)` tuple INSIDE the `try: ... except ImportError:` guard block in `archon_toolkit_rag.py`. Do NOT add a standalone import at module level — that would break environments without RAG dependencies.
  - In `_handle_rag_status`, inside the loop building `col_dicts` for LanceDB-present collections: after setting `d["error_count"]`, call `eta = compute_eta_seconds(cp)`; if `eta is not None`, set `d["eta_seconds"] = eta`
  - In the state-only block (collections in state file but NOT in LanceDB): after setting `"error_count"`, add `eta_seconds` if `compute_eta_seconds` returns non-None
  - `eta_seconds` is omitted entirely (key absent) when ETA is `None` — do not set the key to `None`
  - Update the `description` field of `_RAG_STATUS_SCHEMA` to mention the optional ETA. Change the description string to end with: `...and the list of indexed collections with document and chunk counts; includes optional eta_seconds (integer, seconds remaining) for in-progress collections.` This ensures Claude knows to use this tool when answering ETA questions.
- **Releasable**: `rag_status` MCP JSON includes `eta_seconds` for qualifying in-progress collections; Claude can answer "how long until RAG is ready?"
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_rag.py`:
  - Unit: `test_rag_status_mcp_includes_eta_seconds` — mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `300`; parsed JSON collection dict contains `'eta_seconds': 300` (verify exact value, not just key presence)
  - Unit: `test_rag_status_mcp_omits_eta_seconds_when_too_few` — Mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `None`; assert JSON collection dict does NOT contain `'eta_seconds'` key
  - Unit: `test_rag_status_mcp_omits_eta_seconds_for_non_in_progress` — Parametrized over DONE and FAILED; mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `None`; JSON collection dict does NOT contain `'eta_seconds'` key
  - Unit: `test_rag_status_mcp_includes_eta_seconds_state_only` — mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) to return `300`; `IN_PROGRESS` + ≥10 files in state-only block (not in LanceDB) → parsed JSON collection dict contains `'eta_seconds': 300` (verify exact value, not just key presence)
  - Note: All MCP tests must mock `compute_eta_seconds` (patch `archon.ai.archon_toolkit_rag.compute_eta_seconds`) — return a positive integer for 'includes' tests, `None` for 'omits' tests. This isolates the MCP rendering logic from the ETA computation logic.
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_rag.py -v --no-cov -k "eta"`
