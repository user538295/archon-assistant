# FIX-033 — SearchStore tilde in db_path not expanded, creates spurious `~` directory
**Purpose**: Fix `SearchStore.__init__` to call `.expanduser()` on `db_path` so that `~/.archon/search` is never resolved relative to the process CWD.
**Audience**: Internal — Archon maintainers
**Status**: To Do

---

## Background

Running `uv run https://…/install.py` from `~/Documents/development/` creates a spurious
`~/Documents/development/~/.archon/search/` directory.

**Root cause**: `SearchStore.__init__` stores `self._db_path = Path(db_path)` without calling
`.expanduser()`. `SearchStore.connect()` then calls `self._db_path.mkdir(parents=True, exist_ok=True)`
on the unexpanded path. When the process CWD is any directory other than `$HOME`, Python treats `~`
as a literal directory component and `mkdir(parents=True)` creates
`<CWD>/~/.archon/search/`.

**Trigger**: After a fresh install the installer prints *"run 'archon search status' to track
progress."* Running `archon search status` from `~/Documents/development/` causes
`_run_status()` in `search_cmd.py` to construct `SearchStore(cfg.search.db_path)` and call
`store.connect()` — which creates the spurious directory tree.

**Evidence**:
- `~/Documents/development/~/.archon/search/` is **completely empty** — only `SearchStore.connect()`
  performs a bare `mkdir`. `IndexingStateStore.write()` and manifest writes would leave files.
- A prior partial fix (commit `34a6227`) applied `.expanduser()` to `IndexingStateStore` call sites
  but missed `SearchStore` in `store.py` and `create_pipeline()` in `pipeline.py`.
- `working_directory` and `attachments_dir` are already expanded at load time in `loader.py`
  (lines 479 and 498 respectively) using `str(Path(...).expanduser())`. The `db_path` field
  was missed, making the config-layer fix the correct primary fix — consistent with the
  existing pattern.

---

## Goal

After this fix: constructing `SearchStore` with any tilde path (e.g. `"~/.archon/search"`) always
resolves the home directory correctly, regardless of the process CWD. No spurious directories are
ever created.

---

## Scope

### In Scope
- Expand `db_path` in `config/loader.py` at load time — primary fix, covers all consumers
- Add `.expanduser()` in `SearchStore.__init__` (`store.py:38`) — defense-in-depth, covers all call sites transitively
- Add `.expanduser()` in `IndexingStateStore.__init__` (`progress.py:85`) — defense-in-depth
- Unit test: constructing `SearchStore` with a tilde path produces an expanded `_db_path`
- Unit test: `create_pipeline()` produces a correctly expanded path (regression guard)

### Out of Scope
- Call sites in `gateway.py:623`, `doctor.py:95`, `archon_toolkit_search.py:101`, `archon_toolkit_search.py:807`, and `search_cmd.py:522` that pass `cfg.search.db_path` to `IndexingStateStore` without `.expanduser()` — these are covered by Task 1.1 (once `cfg.search.db_path` is pre-expanded at load time, all downstream consumers are safe without per-site changes)
- Cleaning up the already-created spurious `~/Documents/development/~` directory — manual user action
- Changing the default `db_path` value in `config/loader.py` — the tilde default is correct; the fix
  is in how it is consumed

### What does NOT change
- `SearchStore` public API — no signature changes
- `create_pipeline()` public API — no signature changes
- Config schema — `db_path` default `"~/.archon/search"` stays as-is
- `archon/search/pipeline.py` — no change required; `SearchStore.__init__` handles expansion
- `archon/search/install.py` — already calls `.expanduser()` at each `db_path` call site; additionally covered by Task 1.1's config-layer fix
- Any test that constructs `SearchStore(tmp_path / "db")` — `tmp_path` is already absolute; `.expanduser()` is a no-op on absolute paths

---

## Acceptance criteria
- [ ] `SearchStore(path="~/.archon/search")._db_path` equals `Path.home() / ".archon/search"` (not a tilde-prefixed relative path)
- [ ] Running `archon search status` from any directory never creates a `~` subdirectory in CWD
- [ ] `create_pipeline()` passes an expanded path to `SearchStore`
- [ ] All existing `tests/search/test_store.py` tests pass unchanged
- [ ] New tests cover: tilde expansion in `SearchStore.__init__`, `IndexingStateStore.__init__`, `config/loader.py`, and a behavioral `connect()` test verifying no spurious `~` dir is created in CWD
- [ ] Overall test suite passes with ≥85% coverage

---

## Known limitations / accepted trade-offs
- Other direct `SearchStore(cfg.search.db_path)` call sites in `search_cmd.py` (lines 106, 347, 603)
  remain unfixed at the call-site level, but the fix in `__init__` makes them safe. Defensive
  per-call-site fixes are omitted to keep the change minimal.
- **`pinned_collections` and `collections` in `SearchConfig` remain unexpanded at load time** — the `all_indexed_collections` property documents this with "Returns raw config strings (may contain ~); callers must expanduser/resolve." This is a pre-existing design decision, not introduced by this fix. Callers in `server.py`, `sync.py`, and CLI commands already handle per-entry expansion. Addressing this is out of scope for FIX-033.
- **~12 call sites retain redundant `.expanduser()` calls** after Tasks 1.1 and 1.5 — e.g., `server.py:219`, `install.py:214`, `search_cmd.py:266`, `archon_toolkit_search.py:352`. These are harmless (`.expanduser()` on an already-absolute path is a no-op) but may mislead future maintainers into thinking `cfg.search.db_path` still contains tildes. Cleanup is deferred to a separate commit to keep this fix's diff reviewable.

---

## Architecture

### Affected files
| File | Change |
|------|--------|
| `archon/config/loader.py` | Expand `db_path` at load time (same pattern as `working_directory`) |
| `archon/search/store.py` | Line 38: `Path(db_path)` → `Path(db_path).expanduser()` |
| `archon/search/progress.py` | `IndexingStateStore.__init__`: add `.expanduser()` to the stored `state_dir` |
| `tests/config/test_loader.py` | New unit test for `db_path` expansion at load time |
| `tests/search/test_store.py` | New unit tests for tilde expansion in `SearchStore.__init__` |
| `tests/search/test_progress.py` | New unit tests for tilde expansion in `IndexingStateStore.__init__` |

### Change detail

**`archon/config/loader.py` — `db_path` expansion at load time** (before, line 710):
```python
db_path=str(search_data.get("db_path", SearchConfig.db_path)),
```
**After**:
```python
db_path=str(Path(search_data.get("db_path", SearchConfig.db_path)).expanduser()),
```
This follows the exact same pattern as `working_directory` (line 479):
```python
working_directory=str(Path(session_data["working_directory"]).expanduser()),
```

**`store.py:38`** (before):
```python
self._db_path = Path(db_path)
```
**After**:
```python
self._db_path = Path(db_path).expanduser()
```

**`progress.py:85`** (before):
```python
self._state_dir = state_dir
```
**After**:
```python
self._state_dir = Path(state_dir).expanduser()
self._state_file = self._state_dir / ".indexing_state.json"
```
Note: `Path(state_dir)` wraps the argument defensively before calling `.expanduser()`, matching `SearchStore`'s `str | Path` approach. This avoids `AttributeError` if a caller passes a plain `str` despite the `Path` type hint.

---

## Tests

- **test_search_db_path_is_expanded_at_load_time** (unit, `tests/config/test_loader.py`): load a config with `db_path = "~/.archon/search"` and assert `cfg.search.db_path` equals `str(Path("~/.archon/search").expanduser())`
- **test_search_store_init_expands_tilde** (unit): `SearchStore("~/.archon/search")._db_path` equals `Path.home() / ".archon/search"`
- **test_search_store_init_absolute_path_unchanged** (unit): `SearchStore("/tmp/db")._db_path` equals `Path("/tmp/db")` — no regression for absolute paths
- **test_search_store_init_expands_tilde_path_object** (unit): `SearchStore(Path("~/.archon/search"))._db_path` equals `Path.home() / ".archon/search"` — Path object input
- **test_search_store_connect_does_not_create_tilde_dir_in_cwd** (unit): use `monkeypatch.chdir(tmp_path)` to set CWD to a fresh temp dir, also `monkeypatch.setenv("HOME", str(tmp_path))` so the expanded path resolves to `tmp_path / '.archon/search'` (POSIX; Search is macOS/Linux-only in practice), create `SearchStore('~/.archon/search')`, mock lancedb via `monkeypatch.setitem(sys.modules, 'lancedb', MagicMock(connect_async=AsyncMock()))` (`AsyncMock` required because `connect()` awaits `lancedb.connect_async()`; plain `MagicMock` is not awaitable and would raise `TypeError`), call `await store.connect()`, assert `(tmp_path / '~').exists() is False` (no spurious literal-tilde dir) and `(tmp_path / '.archon/search').exists() is True` (correct expanded dir was created)
- **test_indexing_state_store_init_expands_tilde** (unit, `tests/search/test_progress.py`): construct `IndexingStateStore(Path("~/.archon/search"))` and assert `store._state_dir == Path.home() / ".archon/search"`
- **test_indexing_state_store_init_absolute_path_unchanged** (unit, `tests/search/test_progress.py`): construct `IndexingStateStore(Path("/tmp/state"))` and assert `store._state_dir == Path("/tmp/state")`
- **test_create_pipeline_uses_expanded_db_path** (unit, `tests/search/test_pipeline.py`): construct a minimal `SearchConfig` with `db_path="~/.archon/search"`, call `create_pipeline(cfg, embedder_backend=MagicMock(), reranker_backend=MagicMock())`, and assert `pipeline.store._db_path == Path.home() / ".archon/search"`

---

## Documentation update
- N/A — internal bug fix, no user-visible behaviour change

---

## Task breakdown

### Phase 1 — Fix and tests
> **Releasable**: after this phase — the bug is fixed and covered by automated tests.

#### Task 1.0 — Tests: `db_path` expansion at load time (TDD, will fail until Task 1.1)
- [ ] **File**: `tests/config/test_loader.py`
- **Depends on**: nothing
- **Description**:
  - Add `test_search_db_path_is_expanded_at_load_time` — load a config with `[search]\ndb_path = "~/.archon/search"` (or use the default) and assert `cfg.search.db_path == str(Path("~/.archon/search").expanduser())`. Follow the existing test patterns in that file (`_env_file`, `_config_file`, `monkeypatch.delenv`). This test will **fail** until Task 1.1 is complete (TDD).
- **Checkpoint**: `uv run pytest tests/config/test_loader.py -k "test_search_db_path_is_expanded_at_load_time" --no-cov -q --tb=short`
- **Releasable**: after Task 1.1.

#### Task 1.1 — Fix: expand `db_path` in `config/loader.py`
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: Task 1.0 (tests written; now make them green)
- **Description**:
  - At line 710 inside `load_config()`, change:
    ```python
    db_path=str(search_data.get("db_path", SearchConfig.db_path)),
    ```
    to:
    ```python
    db_path=str(Path(search_data.get("db_path", SearchConfig.db_path)).expanduser()),
    ```
  - This follows the exact pattern already used for `working_directory` at line 479:
    ```python
    working_directory=str(Path(session_data["working_directory"]).expanduser()),
    ```
    and `attachments_dir` at line 498:
    ```python
    session.attachments_dir = str(Path(session.attachments_dir).expanduser())
    ```
  - No other changes to `loader.py`.
- **Checkpoint**: `uv run pytest tests/config/test_loader.py --no-cov -q --tb=short`
- **Releasable**: after this task, `cfg.search.db_path` is always an absolute expanded string.

#### Task 1.2 — Tests: tilde expansion in `SearchStore.__init__`
- [ ] **File**: `tests/search/test_store.py`
- **Depends on**: nothing
- **Description**:
  - Add `test_search_store_init_expands_tilde`: construct `SearchStore("~/.archon/search")` and
    assert `store._db_path == Path.home() / ".archon/search"`. No filesystem access — pure unit test.
  - Add `test_search_store_init_absolute_path_unchanged`: construct `SearchStore("/tmp/test_db")` and
    assert `store._db_path == Path("/tmp/test_db")` — ensures the fix is a no-op for absolute paths.
  - Add `test_search_store_init_expands_tilde_path_object`: construct `SearchStore(Path("~/.archon/search"))` and assert `store._db_path == Path.home() / ".archon/search"` — Path object input works correctly.
  - Add `test_search_store_connect_does_not_create_tilde_dir_in_cwd`: use `monkeypatch.chdir(tmp_path)` to set CWD to a fresh temp dir, also `monkeypatch.setenv("HOME", str(tmp_path))` so the expanded path resolves to `tmp_path / '.archon/search'` (POSIX; Search is macOS/Linux-only in practice), create `SearchStore('~/.archon/search')`, mock lancedb via `monkeypatch.setitem(sys.modules, 'lancedb', MagicMock(connect_async=AsyncMock()))` (`AsyncMock` required because `connect()` awaits `lancedb.connect_async()`; plain `MagicMock` raises `TypeError`), call `await store.connect()`, assert `(tmp_path / '~').exists() is False` (no spurious literal-tilde dir) and `(tmp_path / '.archon/search').exists() is True` (correct expanded dir created). This directly exercises the original bug scenario.
  - All tests will **fail** until Task 1.3 is complete (TDD).
- **Releasable**: after Task 1.3, these tests pass and the constructor is correct.
- **Tests (TDD)** — `tests/search/test_store.py`:
  - Unit: `test_search_store_init_expands_tilde`
  - Unit: `test_search_store_init_absolute_path_unchanged`
  - Unit: `test_search_store_init_expands_tilde_path_object`
  - Unit: `test_search_store_connect_does_not_create_tilde_dir_in_cwd`
  - Checkpoint: `uv run pytest tests/search/test_store.py -k "test_search_store_init_expands_tilde or test_search_store_init_absolute_path_unchanged or test_search_store_init_expands_tilde_path_object or test_search_store_connect_does_not_create_tilde_dir_in_cwd" --no-cov -q --tb=short`

#### Task 1.3 — Fix: add `.expanduser()` in `SearchStore.__init__`
- [ ] **File**: `archon/search/store.py`
- **Depends on**: Task 1.2 (tests written; now make them green)
- **Description**:
  - Change line 38 from `self._db_path = Path(db_path)` to `self._db_path = Path(db_path).expanduser()`
  - No other changes to `store.py`.
- **Releasable**: after this task, Task 1.2 tests pass and `SearchStore` is safe for any CWD.
- **Tests (TDD)** — `tests/search/test_store.py`:
  - Run tests from Task 1.2 to verify they pass
  - Run full `test_store.py` to verify no regressions
  - Checkpoint: `uv run pytest tests/search/test_store.py --no-cov -q --tb=short`

#### Task 1.4 — Tests: tilde expansion in `IndexingStateStore.__init__` (TDD, will fail until Task 1.5)
- [ ] **File**: `tests/search/test_progress.py`
- **Depends on**: nothing (independent of Task 1.1)
- **Description**:
  - Add two tests. They will **FAIL** until Task 1.5 applies the fix.
  - Unit: `test_indexing_state_store_init_expands_tilde` — construct `IndexingStateStore(Path("~/.archon/search"))` and assert `store._state_dir == Path.home() / ".archon/search"`. Follow the same pattern as the SearchStore tests.
  - Unit: `test_indexing_state_store_init_absolute_path_unchanged` — construct `IndexingStateStore(Path("/tmp/state"))` and assert `store._state_dir == Path("/tmp/state")`.
- **Checkpoint**: `uv run pytest tests/search/test_progress.py -k "test_indexing_state_store_init" --no-cov -q --tb=short`

#### Task 1.5 — Fix: add `.expanduser()` to `IndexingStateStore.__init__`
- [ ] **File**: `archon/search/progress.py`
- **Depends on**: Task 1.4 (tests written; now make them green)
- **Description**:
  - In `IndexingStateStore.__init__` (line 84-85 in `progress.py`), the constructor currently stores:
    ```python
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._state_file = state_dir / ".indexing_state.json"
    ```
  - Change to apply `.expanduser()` when storing, using `Path()` wrapping for defensive `str | Path` handling:
    ```python
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir).expanduser()
        self._state_file = self._state_dir / ".indexing_state.json"
    ```
  - After the config-layer fix (Task 1.1), `cfg.search.db_path` is already expanded, so this is defense-in-depth for any direct callers that pass a tilde path.
  - Run Task 1.4 tests to verify they pass; run full `test_progress.py` for no regressions.
- **Checkpoint**: `uv run pytest tests/search/test_progress.py --no-cov -q --tb=short`

#### Task 1.6 — Test: `create_pipeline()` produces expanded path (regression guard)
- [ ] **File**: `tests/search/test_pipeline.py`
- **Depends on**: Task 1.3
- **Description**:
  - Add `test_create_pipeline_uses_expanded_db_path` in `tests/search/test_pipeline.py`:
    construct a minimal `SearchConfig` with `db_path="~/.archon/search"`, call
    `create_pipeline(cfg, embedder_backend=MagicMock(), reranker_backend=MagicMock())`, and assert
    `pipeline.store._db_path == Path.home() / ".archon/search"`.
    Use the existing `SearchConfig` factory/fixture pattern already in that file. Passing mock
    backends avoids instantiating real `ModelEmbedder`/`ModelReranker` objects with heavy dependencies.
  - No changes to `pipeline.py` — `SearchStore.__init__` handles expansion. This test is a
    regression guard to verify `create_pipeline()` produces a correctly expanded path end-to-end.
- **Releasable**: after this task, all call sites are safe and the full fix is verified end-to-end.
- **Tests (TDD)** — `tests/search/test_pipeline.py`:
  - Unit: `test_create_pipeline_uses_expanded_db_path`
  - Checkpoint: `uv run pytest tests/search/test_store.py tests/search/test_pipeline.py --no-cov -q --tb=short`
