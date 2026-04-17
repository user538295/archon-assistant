# FIX-031 — fastembed TextCrossEncoder import path
**Purpose**: Fix broken import of `TextCrossEncoder` from top-level `fastembed` module, which does not export it in fastembed 0.8.0+
**Audience**: Archon developers; users relying on search reranking
**Status**: Complete

---

## Background

fastembed 0.8.0 restructured its public API — `TextCrossEncoder` was moved to the `rerank` submodule and is no longer exported from `fastembed.__init__`. The `archon/search/reranker.py` import (`from fastembed import TextCrossEncoder`) therefore raised an `ImportError` at runtime on first use, silently breaking all search calls that involved reranking. The error occurred at import time (lazy import on first use), so startup was unaffected but the first search request failed.

## Goal

Replace the broken import with the canonical submodule path (`from fastembed.rerank.cross_encoder import TextCrossEncoder`), add a regression test that will catch any future reversion, and correct the architecture doc that referenced the old import path.

---

## Scope

### In Scope
- Fix import in `archon/search/reranker.py`
- Fix test mock target and conftest sys.modules injection in `tests/search/`
- Add regression test that fails if the import is reverted
- Update `Documentation/Architecture/180_search_architecture.md`

### Out of Scope
- Changes to `embedder.py` or `install.py` (`TextEmbedding` is correctly exported from the top-level module and is unaffected)
- Pinning or downgrading fastembed
- Any fastembed API changes beyond the cross-encoder submodule path

---

## Acceptance criteria
- [x] `from fastembed import TextCrossEncoder` is gone from the codebase
- [x] `from fastembed.rerank.cross_encoder import TextCrossEncoder` is the only import of `TextCrossEncoder`
- [x] All 11 reranker tests pass
- [x] Regression test `test_model_reranker_uses_submodule_import_path` exists and fails if the old import path is restored
- [x] `tests/search/conftest.py` injects `fastembed.rerank.cross_encoder` into `sys.modules`
- [x] Architecture doc updated to reflect correct import path

---

## What does NOT change
- `archon/search/embedder.py` — `TextEmbedding` import from top-level `fastembed` is correct
- `install.py` — same as above
- `pyproject.toml` — `fastembed>=0.8.0` constraint is correct and stays
- Search ranking logic, model loading, or any other behaviour

---

## Known limitations / accepted trade-offs
- No multi-version compatibility shim — the old import path (`from fastembed import TextCrossEncoder`) will not return; a shim would be over-engineering

---

## Architecture

**Affected file**: `archon/search/reranker.py` line 31  
**Change**: one-line import path fix  

**Test infrastructure**: `tests/search/conftest.py` must inject the submodule into `sys.modules` so that mock patches targeting `fastembed.rerank.cross_encoder.TextCrossEncoder` resolve correctly in the test environment.

**Regression guard**: `test_model_reranker_uses_submodule_import_path` temporarily removes `TextCrossEncoder` from `fastembed.__init__` (via `sys.modules` manipulation) and asserts that `ModelReranker.predict()` still succeeds. This test will fail immediately if the import in `reranker.py` is reverted to the broken path.

No new modules, config keys, or environment variables introduced.

---

## Tests

- **test_model_reranker_uses_submodule_import_path** (unit): asserts reranker works even when `TextCrossEncoder` is absent from the top-level `fastembed` module — regression guard
- **test_conftest_patches_submodule_path** (unit): verifies that `tests/search/conftest.py` correctly injects `fastembed.rerank.cross_encoder` into `sys.modules`
- *(pre-existing 11 reranker tests)* (unit): full suite continues to pass

---

## Documentation update
- [x] `Documentation/Architecture/180_search_architecture.md`, line 206: updated import path reference from `from fastembed import TextCrossEncoder` to `from fastembed.rerank.cross_encoder import TextCrossEncoder`

---

## Task breakdown

### Phase 1 — Fix import and update tests
> **Releasable**: when Task 1.1 is complete — search reranking works again

#### Task 1.1 — Fix import in reranker.py
- [x] **File**: `archon/search/reranker.py`
- **Depends on**: nothing
- **Description**:
  - Line 31: replace `from fastembed import TextCrossEncoder` with `from fastembed.rerank.cross_encoder import TextCrossEncoder`
  - No logic changes; import path only
- **Releasable**: after this task, `ModelReranker` can be instantiated without `ImportError`
- **Tests (TDD)** — `tests/search/test_reranker.py`:
  - Unit: all 11 pre-existing reranker tests pass
  - Checkpoint: `uv run pytest tests/search/test_reranker.py --no-cov`

#### Task 1.2 — Fix conftest sys.modules injection
- [x] **File**: `tests/search/conftest.py`
- **Depends on**: Task 1.1
- **Description**:
  - Inject `fastembed.rerank.cross_encoder` submodule into `sys.modules` in the conftest fixture so mock patches targeting the submodule path resolve correctly
  - Patch target in `test_model_reranker_init_called_once_under_concurrent_predict` updated to match new import path
- **Releasable**: after this task, all test mocks resolve correctly against the new import path
- **Tests (TDD)** — `tests/search/test_conftest.py`:
  - Unit: `test_conftest_patches_submodule_path` — verifies submodule is present in `sys.modules` after conftest runs
  - Checkpoint: `uv run pytest tests/search/test_conftest.py --no-cov`

#### Task 1.3 — Add regression test
- [x] **File**: `tests/search/test_reranker.py`
- **Depends on**: Task 1.2
- **Description**:
  - `test_model_reranker_uses_submodule_import_path`: temporarily removes `TextCrossEncoder` from `fastembed` top-level module via `sys.modules` manipulation, then asserts `ModelReranker.predict()` still succeeds via the submodule import; restores original state in teardown
  - This test will fail immediately if `reranker.py` is reverted to `from fastembed import TextCrossEncoder`
- **Releasable**: after this task, any future reversion of the import is caught automatically
- **Tests (TDD)** — `tests/search/test_reranker.py`:
  - Unit: `test_model_reranker_uses_submodule_import_path`
  - Checkpoint: `uv run pytest tests/search/test_reranker.py tests/search/test_conftest.py --no-cov`

### Phase 2 — Documentation update
> **Releasable**: after this phase

#### Task 2.1 — Update architecture doc
- [x] **File**: `Documentation/Architecture/180_search_architecture.md`
- **Depends on**: nothing
- **Description**:
  - Line 206: update import path reference to `from fastembed.rerank.cross_encoder import TextCrossEncoder`
- **Releasable**: architecture doc accurately reflects the code
- **Tests (TDD)**: N/A — documentation change
  - Checkpoint: manual review
