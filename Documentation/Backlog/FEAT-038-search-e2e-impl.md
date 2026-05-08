# FEAT-038 — Search E2E & Integration Test Implementation
**Purpose**: Implement all test suites from the FEAT-038 search e2e test plan, covering the Archon ↔ archon-search HTTP boundary with in-process FastAPI dispatch, pipeline integration, CLI gap coverage, and store/sync fault tolerance.
**Audience**: Development team; CI pipeline.
**Status**: To Do

---

## Background

The archon-search feature crosses an HTTP boundary (`SearchClient` → httpx → FastAPI → LanceDB + fastembed). Tasks 1–12 of FEAT-038 delivered the production code and existing mocked unit tests. What remains is a comprehensive test layer that exercises the real boundary without mocking at the wrong level. The test plan (`FEAT-038-search-e2e-test-plan.md`) defines 15 suites and ~200 test cases, prioritized P0–P3. This plan implements them in priority order.

## Goal

All P0 and P1 tests from the test plan are implemented, green, and integrated into CI. P2 tests are implemented as a stretch goal. After completion: the archon-search HTTP boundary, pipeline RAG injection path, CLI progress display, store crash recovery, and config validation all have production-grade test coverage with ≥85% line coverage.

---

## Scope

### In Scope
- Suites 1–15 from `FEAT-038-search-e2e-test-plan.md`
- pytest marker registration (`e2e`, `stress`) in `pyproject.toml`
- Shared `tests/e2e/conftest.py` with ML model stubs and ASGI transport fixtures
- All P0 tests (must have before release)
- All P1 tests (should have)
- P2 tests as stretch (Suites 6, 7, 8, 12, 13 watcher, 14 core pipeline, 15 full)

### Out of Scope
- Production code changes (tests only, except `SearchClient.__init__` transport param — Task 0.3)
- P3 stress tests (X7.14–X7.16, benchmark) — manual run only
- New test infrastructure beyond `conftest.py`, `tests/_search_stubs.py`, and marker registration (these three are explicitly in scope)

---

## Acceptance criteria
- [x] `tests/_search_stubs.py` `install_stubs()` is idempotent (calling twice does not raise or overwrite already-captured references)
- [x] `tests/e2e/test_conftest_smoke.py` has been deleted (Phase 0 completion gate)
- [x] pytest markers `e2e` and `stress` registered in `pyproject.toml`
- [x] `tests/e2e/conftest.py` provides ML model stubs and ASGI transport fixtures
- [x] All Suite 1 tests pass (H1.1–H1.13, E1.1–E1.12, N1.1–N1.7)
- [x] All Suite 4 tests pass (H4.1–H4.5, C4.1–C4.4, W4.1–W4.4)
- [x] Suite 9 status progress table tests pass (S9.1–S9.11)
- [ ] Suite 9 helper function unit tests pass (S9.12–S9.29)
- [x] Suite 14 pipeline fault tolerance tests pass (P14.17–P14.20)
- [x] All Suite 2 tests pass (H2.1–H2.13, E2.1–E2.7, C2.1–C2.4)
- [x] All Suite 3 tests pass (H3.1–H3.20, E3.1–E3.10)
- [x] All Suite 5 tests pass (H5.1–H5.8, E5.1–E5.5, E5.6 [partial-send-failure ordering], E5.7 [cancellation mid-poll] — both promoted from M12 P2 to P1)
- [ ] Suite 9 remaining edge cases pass (S9.30–S9.56)
- [ ] Suite 10 SearchClient/SearchContextProvider gap coverage passes (A10.1–A10.42)
- [ ] Suite 11 archon-search config tests pass (C11.13–C11.22)
- [x] Suite 11 Archon-side config tests pass (C11.4, C11.6) — promoted from P2 to P1 (implemented in Phase 11 Task 11.2)
- [ ] Suite 12 `_needs_install_trigger()` tests pass (M12.13–M12.16)
- [ ] Suite 13 JobStore and IndexingStateStore gap tests pass (J13.1–J13.19) — note: J13.20–J13.24 (watcher) remain P2 stretch (Task 12.5), not part of P1 gate
- [x] Suite 14 SQL injection regression tests pass (P14.23, P14.24)
- [ ] Suite 9 S9.44–S9.56 each have either a new test OR an explicit cross-reference to an existing `tests/cli/test_doctor.py` test
- [x] Suite 15 crash recovery tests pass (S15.7–S15.9)
- [ ] All existing tests continue to pass
- [ ] Overall test coverage ≥ 85%
- [x] `packages/archon-search/pyproject.toml` has `--cov-fail-under=85` configured

---

## What does NOT change
- Production code in `archon/`, `packages/archon-search/archon_search/` (except optional `transport` param in `SearchClient.__init__` — Task 0.3)
- Existing test files — only additions, never modifications to passing tests
- `archon/ai/search_client.py` behavioral contract — the `transport` keyword param (Task 0.3) is additive-only; all existing call sites using positional or keyword args are unaffected. No methods removed or renamed. (Note: the `transport` param IS a public API addition — it is listed as a known exception to "nothing changes" in the Out of Scope note above.)
- `get_search_client()` singleton — no enabled-gate added
- FastAPI route signatures and response models in `packages/archon-search/`

---

## Known limitations / accepted trade-offs
- N1.5 (real-TCP timeout) requires real sockets; marked `pytest.mark.integration`, excluded from unit CI. N1.7 (transient outage recovery) is mock-based and runs in CI.
- H3.8 (cancel while running) requires `httpx.AsyncClient` with `ASGITransport` instead of `TestClient`; implemented with its own async fixture.
- H2.13 (real FastMCP Phase B) depends on FastMCP's ASGI method — verified against installed version before implementation.
- E1.12 (504 timeout) patches `archon_search.server.routes_route.asyncio.wait_for` instead of running a real 31-second sleep.
- X7.14–X7.16 (stress) are excluded from CI; marked `pytest.mark.stress`.
- Tests in Suite 4 mock HTTP at the httpx level (not in `tests/integration/`) — safe for regular CI.
- Suite 11 Archon-side config (C11.1–C11.3, C11.5, C11.7–C11.12) references server-side fields not in `SearchConfig`; only C11.4 and C11.6 are implemented against the Archon-side config class. Mapping to new IDs C11.13–C11.22: C11.13 ≈ default-construction smoke (no Archon-side analogue — defaults already covered by `tests/config/`); C11.14–C11.18 (TOML I/O), C11.19 (port), C11.20 (chunk_size), C11.21 (shortlist), C11.22 (confidence) are server-only fields by design (no archon-side counterpart) — they live in `~/.archon/archon-search.toml` and emit a deprecation warning if present in `config.toml`. No further migration is intended.
- `get_search_client()` singleton has no `config.search.enabled = False` short-circuit; it always returns a `SearchClient` instance once `config.search.url` is set. Tests assert behavior as-is; A10.43 was dropped because adding a gate would be a production change outside this plan's scope.
- JobStore and IndexingStateStore concurrent-write safety is not tested at P0–P1. `JobStore._write_atomic()` uses `Path.rename()` (no cleanup on failure); `IndexingStateStore.write()` uses `os.replace()` with try/finally cleanup. Concurrent updates from different collections may result in last-writer-wins behavior. Concurrency stress is P3 manual.
- S9.35 (`collection remove` dry-run pinned note) requires a production code change to add the dry-run note. It is **deferred to P3/exploratory** and not included in Task 3.5; the plan forbids in-scope production changes beyond the `transport` parameter.

---

## Architecture

### New test files introduced
- `tests/_search_stubs.py` — **shared** ML stub installer module (`install_stubs()` function) imported by both `tests/conftest.py` and `packages/archon-search/tests/conftest.py`. Must be invoked at conftest top-level (before any test module imports `fastembed`) so `sys.modules` is patched in time. Conftest load order: the topmost `tests/conftest.py` (and `packages/archon-search/tests/conftest.py`) call `install_stubs()` at import time — pytest ensures top-level conftests load before subdirectory conftests.
- `tests/e2e/conftest.py` — shared ASGI transport fixtures (no stub duplication; defers to `tests/_search_stubs.py`)
- `tests/e2e/test_search_client_e2e.py` — Suite 1
- `tests/e2e/test_search_context_provider_e2e.py` — Suite 2
- `tests/e2e/test_search_complex_scenarios.py` — Suite 7
- `tests/ai/test_pipeline_search_integration.py` — Suite 4
- `tests/ai/test_search_client.py` — Suite 10 additions (appended to existing file)
- `tests/ai/test_search_context_provider.py` — Suite 10 additions (appended to existing file)
- `tests/ai/test_archon_toolkit_search_e2e.py` — Suite 6
- `tests/gateway/test_notification_monitor_e2e.py` — Suite 5
- `tests/cli/test_doctor_search.py` — Suite 8
- `tests/cli/test_search_cmd.py` — Suite 9 additions (appended to existing file)
- `tests/config/test_config_search.py` — Suite 11 Archon-side
- `packages/archon-search/tests/test_routes_e2e.py` — Suite 3
- `packages/archon-search/tests/test_config.py` — Suite 11 archon-search side
- `packages/archon-search/tests/test_mcp.py` — Suite 12 _needs_install_trigger()
- `packages/archon-search/tests/test_sync_e2e.py` — Suite 15
- `packages/archon-search/tests/test_job_store.py` — Suite 13 JobStore
- `packages/archon-search/tests/test_progress.py` — Suite 13 IndexingStateStore additions
- `packages/archon-search/tests/test_watcher.py` — Suite 13 watcher additions
- `packages/archon-search/tests/test_pipeline.py` — Suites 14.1–14.4 additions

### Key pattern: ASGI transport wiring
`httpx.ASGITransport` does **not** automatically run FastAPI lifespan, so the fixture must explicitly enter the lifespan context (so `app.state._background_tasks` is initialised and any startup tasks fire). The `patched_search_client` fixture uses the new `transport=` constructor param from Task 0.3 — never replaces `_http` for the canonical wiring. (Test-level mocks of individual methods, e.g. `patch.object(client._http, "get", ...)`, are still fine.)
```python
# tests/e2e/conftest.py
# IMPORTANT: Both fixtures MUST remain function-scoped. `search_app` creates a fresh
# `JobStore` and `IndexingStateStore` per test. Changing to session scope would cause
# state leakage across tests. asyncio.Task.cancel() on an already-cancelled task is a
# no-op — the post-lifespan cleanup below is defensive for tasks spawned during shutdown.
@pytest_asyncio.fixture(scope="function")
async def search_app(tmp_path):
    config = SearchConfig(...)
    job_store = JobStore(tmp_path / "jobs.json")
    app = create_app(config, job_store, config_path=tmp_path / "config.toml")
    async with app.router.lifespan_context(app):
        yield app
    # Post-lifespan safety net: lifespan exit already cancels tasks,
    # but tasks spawned DURING teardown (in other tasks' exception handlers)
    # may not be caught. The double-cancel is harmless for already-cancelled tasks.
    tasks = list(getattr(app.state, "_background_tasks", []) or [])
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

@pytest_asyncio.fixture(scope="function")
async def patched_search_client(search_app):
    transport = httpx.ASGITransport(app=search_app)
    client = SearchClient("http://test", transport=transport)
    try:
        yield client
    finally:
        await client.close()
```

**Note on ASGI lifespan teardown**: `asyncio.Task.cancel()` on an already-cancelled task is a no-op. The post-lifespan cleanup in `search_app` is defensive for tasks spawned during shutdown (e.g. in other tasks' exception handlers), not a double-cancel bug. The lifespan context exit cancels all tasks it knows about; the explicit loop catches any that were spawned after that point.

### Optional transport param (Task 0.3 — cleaner alternative)
```python
# archon/ai/search_client.py SearchClient.__init__
def __init__(self, base_url: str, timeout: float = 10.0,
             transport: httpx.AsyncBaseTransport | None = None) -> None:
    self._http = httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        transport=transport,
    )
```

---

## Tests

All tests are listed by suite in the test plan (`FEAT-038-search-e2e-test-plan.md`). Summary by file:

- **`tests/e2e/test_search_client_e2e.py`**: H1.1–H1.13, E1.1–E1.12, N1.1–N1.7 (e2e)
- **`tests/e2e/test_search_context_provider_e2e.py`**: H2.1–H2.13, E2.1–E2.7, C2.1–C2.4 (e2e)
- **`tests/e2e/test_search_complex_scenarios.py`**: X7.1–X7.13 (e2e)
- **`tests/ai/test_pipeline_search_integration.py`**: H4.1–H4.5, C4.1–C4.4, W4.1–W4.4 (unit/integration)
- **`tests/ai/test_search_client.py`**: A10.1–A10.42 (unit; A10.43 dropped — see Task 8.3 / C1-I-02)
- **`tests/ai/test_archon_toolkit_search_e2e.py`**: H6.1–H6.13, E6.1–E6.4 (unit)
- **`tests/gateway/test_notification_monitor_e2e.py`**: H5.1–H5.8, E5.1–E5.5 (unit)
- **`tests/cli/test_doctor_search.py`**: H8.1–H8.6 (unit)
- **`tests/cli/test_search_cmd.py`**: S9.1–S9.56 (unit)
- **`tests/config/test_config_search.py`**: C11.4, C11.6 (unit)
- **`packages/archon-search/tests/test_routes_e2e.py`**: H3.1–H3.20, E3.1–E3.10 (integration)
- **`packages/archon-search/tests/test_config.py`**: C11.13–C11.22 (unit)
- **`packages/archon-search/tests/test_mcp.py`**: M12.13–M12.16 (unit)
- **`packages/archon-search/tests/test_sync_e2e.py`**: S15.1–S15.10 (integration)
- **`packages/archon-search/tests/test_job_store.py`**: J13.1–J13.11 (unit)
- **`packages/archon-search/tests/test_progress.py`**: J13.12–J13.19 (unit)
- **`packages/archon-search/tests/test_watcher.py`**: J13.20–J13.24 (unit)
- **`packages/archon-search/tests/test_pipeline.py`**: P14.1–P14.22 (unit/integration) + P14.23, P14.24 (SQL injection regression — Task 10.4; renamed from `P14.17b`/`P14.18b` to eliminate collision)

---

## Documentation update
- N/A — no documentation changes required; test plan doc (`FEAT-038-search-e2e-test-plan.md`) is the authoritative reference.

---

## Task breakdown

### Phase 0 — Test Infrastructure
> **Releasable**: after Task 0.2; subsequent phases depend on these fixtures.

#### Task 0.1 — Register pytest markers and CI invocation scope
- [x] **File**: `pyproject.toml`
- **Depends on**: nothing
- **Description**:
  - Add `e2e` and `stress` to `[tool.pytest.ini_options] markers` list
  - `e2e = "in-process e2e tests using ASGITransport (included in CI)"`
  - `stress = "long-running load tests, excluded from CI by default"`
  - Both markers already referenced in test plan; without registration they cause `PytestUnknownMarkWarning` or fail under `--strict-markers`
  - **Update `addopts`**: change current `addopts = "--cov=archon --cov-report=term-missing --cov-fail-under=85 -m 'not live'"` to `addopts = "--cov=archon --cov-report=term-missing --cov-fail-under=85 -m 'not live and not stress and not integration'"`. This excludes the new `stress` marker and the existing `integration` marker from default runs (existing `live` exclusion preserved).
  - **Canonical CI invocation** (canonical decision: option (b) — separate trees, separate coverage, because `packages/archon-search` has its own `pyproject.toml`, its own coverage config, and adding it to root `testpaths` mixes coverage scopes):
    1. `uv run pytest` — runs everything under root `tests/` (including new `tests/e2e/`) with `--cov=archon`
    2. `cd packages/archon-search && uv run pytest` — runs `packages/archon-search/tests/` with its own coverage config (coverage config added by Task 0.4)
  - Document the two-step invocation in a comment above `[tool.pytest.ini_options]`.
  - Update CI configuration (`.github/workflows/` or equivalent): add a second `pytest` step for `cd packages/archon-search && uv run pytest`. If no CI config exists yet, document the two-step invocation in a comment in `pyproject.toml` above `[tool.pytest.ini_options]`.
- **Releasable**: markers registered; tests using them no longer warn; default invocation excludes stress + integration
- **Tests (TDD)** — `pyproject.toml`:
  - Checkpoint: `uv run pytest --collect-only -q 2>&1 | grep -i "unknown mark"` (expect no output)
  - Checkpoint: `uv run pytest --collect-only -q -m "stress" 2>&1` (expect 0 selected by default after addopts change is honoured if user does NOT pass an explicit `-m`)

#### Task 0.2 — Shared ML stub module + e2e conftest with ASGI transport fixtures
- [x] **Files**: `tests/_search_stubs.py` (new), `tests/conftest.py` (modify), `tests/e2e/conftest.py` (new), `packages/archon-search/tests/conftest.py` (modify to import shared module)
- **Depends on**: Task 0.1, Task 0.3 (transport param must exist before fixture can be tested — implement Task 0.3 first since the `patched_search_client` fixture verifies the transport param)
- **Prerequisites**:
  - Run `uv sync --dev` so that `archon-search` is installed as an editable path dependency.
  - Verify importability: `python -c 'from archon_search.server.app import create_app'` must succeed before implementing fixtures.
- **Description**:
  - **Create `tests/_search_stubs.py`** exposing `install_stubs() -> None` that patches `sys.modules` for `fastembed` (and any other ML deps currently stubbed in `packages/archon-search/tests/conftest.py`). Must be idempotent (re-callable) and must run before any test module imports `fastembed`.
  - **Update `tests/conftest.py`** (root) to call `from tests._search_stubs import install_stubs; install_stubs()` at import time (top of file, before any other imports that may transitively load `fastembed`). If the root `tests/conftest.py` doesn't yet exist, create it with just this content.
  - **Update `packages/archon-search/tests/conftest.py`** to import the same shared module using option (b): create `packages/archon-search/tests/_search_stubs_shim.py` as a thin shim that adds the repo root to `sys.path` and re-exports `install_stubs` from `tests._search_stubs`. Then in `conftest.py`: `from _search_stubs_shim import install_stubs; install_stubs()`. This keeps `packages/archon-search` runnable in isolation without requiring sys.path surgery in the conftest itself.
  - **Conftest load order**: pytest loads top-level conftests before subdirectory conftests, so the root `tests/conftest.py` `install_stubs()` call is guaranteed to run before any test module under `tests/`. For `packages/archon-search/tests/`, that conftest is the topmost one in its tree, so it is also safe.
  - Create `tests/e2e/` directory and `conftest.py`. **No stub duplication** — defers to root conftest.
  - `search_app(tmp_path)` async fixture: constructs `create_app(config, job_store, config_path=tmp_path/"config.toml")` with minimal config (no collections, default ports), then `async with app.router.lifespan_context(app):` yields the app. On teardown, cancels `app.state._background_tasks` and `await asyncio.gather(*tasks, return_exceptions=True)` (this is mandatory because `httpx.ASGITransport` does not fire lifespan; without it, background tasks may leak between tests).
  - `patched_search_client(search_app)` async fixture: `SearchClient("http://test", transport=httpx.ASGITransport(app=search_app))` (uses Task 0.3's new `transport=` param — does NOT replace `_http`). Yields client, calls `await client.close()` in teardown.
  - Import: `from archon_search.server.app import create_app` — verified against installed package per prerequisites.
- **Releasable**: fixtures importable by Suite 1+ tests; ML stubs available repo-wide
- **Tests (TDD)** — `tests/e2e/test_conftest_smoke.py` (delete after Task 0.2 confirms all green):
  - Unit: `test_search_app_fixture_creates_fastapi_instance` — `search_app` is not None, has `/health` route
  - Unit: `test_install_stubs_idempotent` — calling `install_stubs()` twice does not raise
  - Checkpoint: `uv run pytest tests/e2e/test_conftest_smoke.py -v`
  - **Lifecycle**: delete `test_conftest_smoke.py` after Task 0.2 passes — do NOT keep as canary.
  - **GATE**: `ls tests/e2e/test_conftest_smoke.py` must fail before Task 0.2 is marked complete. Deletion is part of Task 0.2 completion, not a separate step.

#### Task 0.3 — Optional transport parameter in SearchClient
- [x] **File**: `archon/ai/search_client.py`
- **Depends on**: nothing
- **Description**:
  - Add `transport: httpx.AsyncBaseTransport | None = None` parameter to `SearchClient.__init__`
  - Forward to `httpx.AsyncClient(base_url=..., timeout=..., transport=transport)`
  - This is the cleaner alternative to `patch.object`; both approaches are valid per test plan
  - No other public API changes
- **Releasable**: `SearchClient` accepts an injected transport; all existing uses unaffected (default `None`)
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Unit: `test_transport_param_forwarded_to_http_client` — construct `SearchClient` with a `MockTransport`; verify `_http.transport` matches
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py::test_transport_param_forwarded_to_http_client -v`

#### Task 0.4 — Add archon-search coverage configuration
- [x] **File**: `packages/archon-search/pyproject.toml`
- **Depends on**: Task 0.1
- **Description**:
  - Add `addopts = "--cov=archon_search --cov-report=term-missing --cov-fail-under=85 -m 'not benchmark and not integration'"` to `[tool.pytest.ini_options]` in `packages/archon-search/pyproject.toml`
  - This ensures `cd packages/archon-search && uv run pytest` enforces ≥85% coverage on the `archon_search` package (currently there is no `--cov` or `--cov-fail-under` in that file)
- **Releasable**: archon-search CI step enforces coverage threshold
- **Tests (TDD)**:
  - Checkpoint: `cd packages/archon-search && uv run pytest --co -q` (expect no coverage errors on collection)
  - Checkpoint: `cd packages/archon-search && uv run pytest` (expect `--cov-fail-under=85` enforced)

---

### Phase 1 — Suite 1: SearchClient ↔ Real In-Process Server (P0)
> **Releasable**: after Task 1.3; the full Suite 1 passes as a CI gate.

#### Task 1.1 — Suite 1 Happy Paths (H1.1–H1.13)
- [x] **File**: `tests/e2e/test_search_client_e2e.py`
- **Depends on**: Task 0.2, Task 0.3
- **Description**:
  - `@pytest.mark.asyncio` + `@pytest.mark.e2e` on all tests
  - Use `patched_search_client` fixture throughout
  - H1.1: `test_health_returns_running_status` — `GET /health` returns `{"status": "running", "version": ...}`
  - H1.2: `test_status_returns_empty_collections_on_fresh_server` — `collections=[]`, `running=True`
  - H1.3: `test_add_collection_returns_pending_job` — `POST /collections` → 202 + `IngestJob(status=PENDING)`
  - H1.4: `test_list_collections_includes_added_collection` — after add, appears in list
  - H1.5: `test_get_collection_info_returns_metadata` — correct name, path, status
  - H1.6: `test_remove_collection_deletes_it` — 200 + `{"deleted": true}`; not in subsequent list
  - H1.7: `test_ingest_with_path_returns_pending_job` — `SearchClient.ingest(collection, path)` → 202
  - H1.8: `test_get_job_returns_job_state` — job_id and status in `{PENDING, RUNNING, DONE}`
  - H1.9: `test_cancel_job_in_pending_state_returns_202` — DELETE → 202 + CANCELLING
  - H1.10: `test_cancel_terminal_job_is_idempotent` — cancel DONE → 200, remains DONE
  - H1.11: `test_indexing_state_returns_empty_on_fresh_server` — `GET /indexing-state` → `{}`
  - H1.12: `test_route_with_no_collections_returns_empty_routable` — `routable_names=[]`
  - H1.13: `test_reindex_collection_returns_new_job` — `POST /collections/{name}/reindex` → 202
  - **Smoke for client lifecycle (promoted from A10.23–A10.26)**: add minimal `test_search_client_close_smoke` — construct, `await close()`, verify second `close()` is idempotent. Full A10.23–A10.26 coverage still happens in Task 8.3 — this smoke prevents unclosed-httpx hangs during Phase 1 dev iterations.
- **Releasable**: happy path coverage for all 13 SearchClient methods via real in-process FastAPI
- **Tests (TDD)** — `tests/e2e/test_search_client_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_client_e2e.py -k "H1" -v`

#### Task 1.2 — Suite 1 Error Paths (E1.1–E1.12)
- [x] **File**: `tests/e2e/test_search_client_e2e.py`
- **Depends on**: Task 1.1
- **Description**:
  - E1.1: `test_route_empty_query_returns_400` — `""` → 400; `route()` returns None, logs WARNING
  - E1.2: `test_route_whitespace_query_returns_400` — `"   "` → 400
  - E1.3: `test_route_invalid_slots_returns_400` — `slots=0` → 400
  - E1.4: `test_add_collection_duplicate_returns_409` — add same path twice → 409; second call returns None
  - E1.5: `test_remove_nonexistent_collection_returns_404` — `remove_collection()` returns None
  - E1.6: `test_get_nonexistent_collection_returns_404` — `collection_info()` returns None
  - E1.7: `test_get_nonexistent_job_returns_404` — `get_job()` returns None
  - E1.8: `test_cancel_nonexistent_job_returns_404` — `cancel_job()` returns 404
  - E1.9: `test_reindex_nonexistent_collection_returns_404` — returns None
  - E1.10: `test_remove_pinned_only_collection_returns_409` — returns None
  - E1.11: `test_ingest_empty_collection_name_returns_422` — `ingest()` returns None
  - E1.12: `test_route_504_timeout_returns_none` — patch the exact server-side import: `patch("archon_search.server.routes_route.asyncio.wait_for", side_effect=asyncio.TimeoutError)` (verified against `packages/archon-search/archon_search/server/routes_route.py:73`). The handler raises `HTTPException(504, "routing timed out")`; `SearchClient.route()` returns None and logs WARNING. Note: the patch must be active when the ASGI handler runs — the test must `patch.start()`/`patch.stop()` around the awaited request (or use `with patch(...):`). A test-module-scope monkeypatch will not affect an already-imported handler module.
- **Releasable**: error path coverage for all 4xx/5xx boundary conditions
- **Tests (TDD)** — `tests/e2e/test_search_client_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_client_e2e.py -k "E1" -v`

#### Task 1.3 — Suite 1 Network-Level Errors (N1.1–N1.7)
- [x] **File**: `tests/e2e/test_search_client_e2e.py`
- **Depends on**: Task 1.2
- **Description**:
  - N1.1–N1.4: use `SearchClient` pointed at unused port (e.g. `http://127.0.0.1:19999`); no server listening
    - N1.1: `route()` returns None, logs DEBUG
    - N1.2: `health()` returns None
    - N1.3: `list_collections()` returns `[]`
    - N1.4: `cancel_job()` returns 503
  - N1.5: `test_route_timeout_returns_none` — `@pytest.mark.integration`; real `SearchClient` with `timeout=0.001`, real sleeping route → `TimeoutException` → returns None, logs WARNING
  - N1.6: `test_route_malformed_json_response_returns_none` — create a separate app fixture that wraps the search app with a malformed-response middleware (ASGI middleware that intercepts `/route` responses and replaces the body with `b'not-json'` while keeping `Content-Type: application/json`). Do NOT attempt to patch a route into the live router — Starlette's router is frozen after startup. Alternative: create a dedicated minimal FastAPI app for just this test that returns the malformed response. `route()` returns None, logs WARNING.
  - N1.7: `test_search_client_recovers_after_transient_server_outage` — mock-based (no real TCP needed): `patch.object(client._http, "get", side_effect=[ok_response, ConnectError(...), ok_response])`; third call returns valid result. **No `integration` marker** — this test is mock-only and runs in standard CI.
  - Note: only N1.5 is marked `pytest.mark.integration` (real TCP timeout) — excluded from default runs. N1.7 runs in CI.
- **Releasable**: network failure isolation complete; N1.1–N1.4 and N1.6 run in CI
- **Tests (TDD)** — `tests/e2e/test_search_client_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_client_e2e.py -m "not integration" -v`

---

### Phase 2 — Suite 4: Pipeline Integration with Mocked HTTP (P0)
> **Releasable**: after Task 2.3; the primary production RAG injection path has full test coverage.

#### Task 2.1 — Suite 4 Happy Paths (H4.1–H4.5)
- [x] **File**: `tests/ai/test_pipeline_search_integration.py`
- **Depends on**: Task 0.1
- **Description**:
  - Real `Pipeline` + real `SearchContextProvider`, `SearchClient` HTTP mocked via `patch.object(client._http, ...)`
  - H4.1: `test_pipeline_injects_rag_context_when_search_enabled` — `inject_context` called with `"search_retrieval"` type
  - H4.2: `test_pipeline_pre_context_passed_to_route_task` — `route()` returns `pre_context="hint"` → passed to `route_task`
  - H4.3: `test_pipeline_tier1_searches_all_routable_collections` — `decomposer_invoked=False` → all routable+pinned searched
  - H4.4: `test_pipeline_tier3_uses_decomposer_selected_collections` — decomposer output → only selected collection searched
  - H4.5: `test_pipeline_rag_detail_string_includes_collection_names` — detail string lists searched collection names
- **Releasable**: primary RAG injection flow tested end-to-end on the Archon side
- **Tests (TDD)** — `tests/ai/test_pipeline_search_integration.py`:
  - Checkpoint: `uv run pytest tests/ai/test_pipeline_search_integration.py -k "H4" -v`

#### Task 2.2 — Suite 4 Disabled/Missing Provider (C4.1–C4.4)
- [x] **File**: `tests/ai/test_pipeline_search_integration.py`
- **Depends on**: Task 2.1
- **Description**:
  - C4.1: `test_pipeline_no_rag_when_search_disabled` — `SearchConfig(enabled=False)` → `inject_context` never called
  - C4.2: `test_pipeline_no_rag_when_search_provider_is_none` — no `search_url` → `_search_provider=None` → no search
  - C4.3: `test_pipeline_completes_normally_when_route_fails` — `route()` returns None → Pipeline continues, response delivered
  - C4.4: `test_pipeline_completes_normally_when_all_searches_fail` — all collections fail → no context injected
- **Releasable**: disabled/unconfigured states fully covered
- **Tests (TDD)** — `tests/ai/test_pipeline_search_integration.py`:
  - Checkpoint: `uv run pytest tests/ai/test_pipeline_search_integration.py -k "C4" -v`

#### Task 2.3 — Suite 4 Warning Paths (W4.1–W4.4)
- [x] **File**: `tests/ai/test_pipeline_search_integration.py`
- **Depends on**: Task 2.2
- **Description**:
  - W4.1: `test_pipeline_logs_warning_on_phase_a_exception` — exception in `get_pre_context()` → WARNING "RAG get_pre_context failed"
  - W4.2: `test_pipeline_logs_warning_on_phase_b_exception` — exception in `search_and_prepare()` → WARNING "RAG search_and_prepare failed"
  - W4.3: `test_pipeline_logs_debug_on_search_client_connect_error` — `ConnectError` in `route()` → DEBUG (not WARNING)
  - W4.4: `test_pipeline_logs_warning_on_route_500_response` — `/route` returns 500 → WARNING with status code
  - Use `caplog` with `propagate=True` to assert log level and message content
- **Releasable**: warning/error escalation policy is regression-guarded
- **Tests (TDD)** — `tests/ai/test_pipeline_search_integration.py`:
  - Checkpoint: `uv run pytest tests/ai/test_pipeline_search_integration.py -v`

---

### Phase 3 — Suite 9: archon search CLI Gap Coverage (P0)
> **Releasable**: after Task 3.2; status progress table and helper functions have full unit test coverage.

#### Task 3.1 — Suite 9: `archon search status` Progress Table (S9.1–S9.11)
- [x] **File**: `tests/cli/test_search_cmd.py`
- **Depends on**: Task 0.1
- **Description**:
  - Append to existing `test_search_cmd.py` — do NOT create a new file
  - Mock `SearchClient.status()` and `SearchClient.indexing_state()` via `patch.object`
  - S9.1: `test_status_shows_all_done_collection` — row shows `done` label
  - S9.2: `test_status_shows_in_progress_with_fraction` — `processed=3, total=10` → `partial/done 3/10`
  - S9.3: `test_status_shows_failed_collection_with_error_message` — `status=FAILED, error="oom"` → error text, exit 1
  - S9.4: `test_status_shows_pending_collection` — `status=PENDING` → `—`, no crash
  - S9.5: `test_status_shows_eta_when_available` — in-progress with computable ETA → `eta Xm` shown
  - S9.6: `test_status_multiple_collections_all_shown` — 3 collections → all 3 rows in output
  - S9.7: `test_status_shows_pid_from_service` — `{"pid": 9999}` → `pid=9999` in output
  - S9.8: `test_status_shows_stopped_when_status_returns_none` — `status()` None → "stopped (unreachable)", exit 1
  - S9.9: `test_status_shows_stopped_when_indexing_state_returns_none` — graceful degradation
  - S9.10: `test_status_exit_code_0_when_all_healthy` — all DONE → exit 0
  - S9.11: `test_status_exit_code_1_when_any_failed` — any FAILED → exit 1
  - Use `capsys` for output assertion
- **Releasable**: status command rendering fully tested
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -k "S9_1 or status_shows or status_exit" -v`

#### Task 3.2 — Suite 9: `compute_eta_seconds()` Unit Tests (S9.12–S9.18)
- [x] **File**: `tests/cli/test_search_cmd.py`
- **Depends on**: Task 3.1
- **Description**:
  - Import `compute_eta_seconds` from `archon.cli.search_cmd`
  - S9.12: not IN_PROGRESS → None
  - S9.13: `total_files=0` → None (divide-by-zero guard)
  - S9.14: `processed_files=5` → None (fewer than 10 processed)
  - S9.15: no `started_at` → None
  - S9.16: 100 files, 50 processed, started 60s ago → ~60s remaining
  - S9.17: `processed == total` → None (already complete)
  - S9.18: `started_at` as ISO 8601 string → parsed correctly
- **Releasable**: ETA computation fully unit-tested
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -k "compute_eta" -v`

#### Task 3.3 — Suite 9: `_path_to_collection_name()` Unit Tests (S9.19–S9.24)
- [x] **File**: `tests/cli/test_search_cmd.py`
- **Depends on**: Task 3.2
- **Description**:
  - Import `_path_to_collection_name` from `archon.cli.search_cmd`
  - S9.19: `/home/user/my-docs` → `my_docs`
  - S9.20: `/data/history` → `history`
  - S9.21: `/data/docs/` → `docs` (trailing slash stripped)
  - S9.22: `/data/my project (2024)` → `my_project_2024`
  - S9.23: `!!!` → `collection` (fallback for all-special names)
  - S9.24: `/data/MyDocs` → `mydocs` (lowercased)
- **Releasable**: path-to-name normalization tested
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -k "path_to_collection_name" -v`

#### Task 3.4 — Suite 9: `_print_progress_table()` Unit Tests (S9.25–S9.29)
- [x] **File**: `tests/cli/test_search_cmd.py`
- **Depends on**: Task 3.3
- **Description**:
  - Import `_print_progress_table` from `archon.cli.search_cmd`
  - S9.25: empty state → no output
  - S9.26: all DONE → returns `False`
  - S9.27: one FAILED → returns `True`
  - S9.28: 3 configured collections, 2 have state → all 3 shown
  - S9.29: collection in config but no state entry → `—` or `not_yet_indexed`
  - Use `capsys` for output assertion
- **Releasable**: progress table rendering fully unit-tested
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -k "print_progress_table" -v`

#### Task 3.5 — Suite 9: Edge Cases and Remaining CLI Tests (S9.30–S9.56)
- [x] **File**: `tests/cli/test_search_cmd.py`
- **Depends on**: Task 3.4
- **Description**:
  - S9.30–S9.32: status edge cases (zero total_files, null error field, long collection name)
  - S9.33–S9.35: `collection remove` edge cases (trailing slash, symlink, dry-run pinned note)
  - S9.36–S9.37: `collection add` edge cases (absolute path not double-resolved, 409 output)
  - S9.38–S9.40: `install`/`uninstall` error paths (FileNotFoundError, non-zero exit, --delete-db flag)
  - S9.41–S9.43: `start`/`stop` error paths (non-zero exit, FileNotFoundError)
  - S9.44–S9.47: `_check_search_server()` — cross-reference `tests/cli/test_doctor.py` first; skip if already covered
  - S9.48–S9.56: `_check_search_health()` — cross-reference first; skip if already covered
  - Before implementing S9.44–S9.56, read `tests/cli/test_doctor.py` and only add scenarios not already there
- **Releasable**: all remaining CLI edge cases covered
- **Tests (TDD)** — `tests/cli/test_search_cmd.py`:
  - Checkpoint: `uv run pytest tests/cli/test_search_cmd.py -v`

---

### Phase 4 — Suite 14: Pipeline Fault Tolerance (P0)
> **Releasable**: after Task 4.1; ingest/search exception propagation is regression-guarded.

#### Task 4.1 — Suite 14 Pipeline Fault Tolerance (P14.17–P14.20)
- [x] **File**: `packages/archon-search/tests/test_pipeline.py`
- **Depends on**: Task 0.1
- **Description**:
  - Append to existing `test_pipeline.py` — do NOT create a new file
  - Use fastembed stub infrastructure from `packages/archon-search/tests/conftest.py`
  - P14.17: `test_pipeline_ingest_file_embedder_exception_propagates` — embedder raises during `ingest_file()` → propagates to caller
  - P14.18: `test_pipeline_ingest_directory_partial_file_failure_continues` — one file fails → others indexed, `progress_cb` called
  - P14.19: `test_pipeline_search_embedder_exception_propagates` — embedder raises during `search()` → propagates
  - P14.20: `test_pipeline_search_with_context_fetch_fails_gracefully` — `fetch_adjacent_chunks()` raises → logs, continues, result returned
- **Releasable**: pipeline exception propagation paths covered; ingest/search boundary is regression-guarded
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_pipeline.py -k "P14_1" -v`

---

### Phase 5 — Suite 2: SearchContextProvider ↔ Real In-Process Server (P1)
> **Releasable**: after Task 5.3; SearchContextProvider routing and search phases fully covered.

#### Task 5.1 — Suite 2 Happy Paths (H2.1–H2.12)
- [x] **File**: `tests/e2e/test_search_context_provider_e2e.py`
- **Depends on**: Task 0.2
- **Description**:
  - Wire `provider._search_client` with `patched_search_client` fixture
  - Wire `provider._http` with stub ASGI app for Phase B JSON-RPC calls (option b — stub, not real FastMCP)
  - H2.1: no collections → `get_pre_context()` returns None
  - H2.2: after `get_pre_context()`, `_route_response` has `pinned_names`, `routable_names`
  - H2.3: `search_and_prepare()` without `get_pre_context()` → returns None
  - H2.4: tier1 (`decomposer_invoked=False`) → searches all routable + pinned
  - H2.5: tier2 + `selected_collections=[]` + pinned → only pinned searched
  - H2.6: tier2 + `selected_collections=[]` + no pinned → returns None
  - H2.7: tier3 + hallucinated collection → only real collection searched
  - H2.8: 10 routable + **`pinned_collections=[]`** (mandatory — pinned collections bypass the cap), `max_parallel=3` → at most 3 collections searched. Resolves C1-I-05.
  - H2.9: merged results ranked by score
  - H2.10: score normalization per-collection
  - H2.11: returned text starts with `[RAG context` and ends with `[End RAG context]`
  - H2.12: second element of tuple == number of merged chunks
- **Releasable**: happy path coverage for all 3 search tiers
- **Tests (TDD)** — `tests/e2e/test_search_context_provider_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_context_provider_e2e.py -k "H2" -v`

#### Task 5.2 — Suite 2 Error/Warning Paths (E2.1–E2.7)
- [x] **File**: `tests/e2e/test_search_context_provider_e2e.py`
- **Depends on**: Task 5.1
- **Description**:
  - E2.1: `route()` returns None → `get_pre_context()` returns None, no exception
  - E2.2: one collection returns 500, others return results → skipped; remaining returned; failure logged DEBUG
  - E2.3: all collections error → returns None
  - E2.4: single result per collection → score normalized to 0.5
  - E2.5: all collections return empty lists → returns None
  - E2.6: server returns valid JSON but wrong shape → collection returns `[]`, no crash
- **Releasable**: error isolation in parallel search paths covered
- **Tests (TDD)** — `tests/e2e/test_search_context_provider_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_context_provider_e2e.py -k "E2" -v`

#### Task 5.3 — Suite 2 Config Behaviour (C2.1–C2.4)
- [x] **File**: `tests/e2e/test_search_context_provider_e2e.py`
- **Depends on**: Task 5.2
- **Description**:
  - C2.1: `SearchConfig(enabled=False)` → `get_pre_context()` returns None without HTTP call
  - C2.2: `enabled=False` → `search_and_prepare()` returns None at its own guard
  - C2.3: 20 results, `top_k=3` → exactly 3 returned
  - C2.4: `max_parallel=1` → sequential search; verified via call-order recorder (no timing assertions)
- **Releasable**: config-driven behavior fully tested
- **Tests (TDD)** — `tests/e2e/test_search_context_provider_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_context_provider_e2e.py -v`

#### Task 5.4 — Suite 2 Phase B Real FastMCP Integration (H2.13, E2.7)
- [x] **File**: `tests/e2e/test_search_context_provider_e2e.py`
- **Depends on**: Task 5.3
- **Description**:
  - Before implementing, verify FastMCP ASGI method name: `from archon_search.server.mcp import create_app as create_mcp_app; mcp = create_mcp_app(...); dir(mcp)` — find `.get_asgi_app()` or equivalent
  - H2.13: wire `provider._http` to `httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app.get_asgi_app()))`. Call `_search_collection("col", "test query")` → response parsed into `list[SearchResult]`
  - E2.7: FastMCP returns `{"error": {"code": -32600, ...}}` → `_search_collection` returns `[]`, no exception
- **Releasable**: real MCP tool dispatch regression-guarded
- **Tests (TDD)** — `tests/e2e/test_search_context_provider_e2e.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_context_provider_e2e.py -k "H2_13 or E2_7" -v`

---

### Phase 6 — Suite 3: archon-search Routes Extended (P1)
> **Releasable**: after Task 6.4; all extended route edge cases covered in-package.

#### Task 6.1 — Suite 3: /route Endpoint (H3.1–H3.5, E3.1–E3.5b, H3.6b)
- [x] **File**: `packages/archon-search/tests/test_routes_e2e.py`
- **Depends on**: Task 0.1
- **Description**:
  - Use `TestClient(create_app(config, job_store))` with `tmp_path` isolation
  - H3.1: `/route` returns `pre_context` with collection metadata
  - H3.2: `pinned_collections=["A"]` → `pinned_names=["A"]` regardless of query
  - H3.3: `{"query": "x", "slots": 2}` → `shortlist_size=2` passed to router
  - H3.4: Unicode query → 200, valid response
  - H3.5: 10k character query → 200
  - E3.1: `POST /route {}` → 422
  - E3.2: `{"query": null}` → 422
  - E3.3: `{"query": "x", "slots": -1}` → 400
  - E3.4: `{"query": "x", "slots": 0}` → 400
  - E3.5b: inject router that sleeps > 30s → mock `asyncio.wait_for` to raise `TimeoutError` → 504 with "routing timed out"
  - H3.6b: collections configured but none pass confidence threshold → 200, `pre_context=None`, `routable_names=[]`
- **Releasable**: /route edge cases covered
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_e2e.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_routes_e2e.py -k "route" -v`

#### Task 6.2 — Suite 3: /ingest + /jobs Lifecycle (H3.6–H3.11, E3.5–E3.7)
- [x] **File**: `packages/archon-search/tests/test_routes_e2e.py`
- **Depends on**: Task 6.1
- **Description**:
  - H3.6: `test_ingest_job_transitions_pending_to_done` — stub pipeline, poll until DONE
  - H3.7: `test_ingest_job_failure_sets_failed_status` — failing pipeline → FAILED, error non-empty
  - H3.8: `test_ingest_cancel_while_running_transitions_to_cancelling` — `@pytest.mark.asyncio`; own async fixture with `httpx.AsyncClient(transport=httpx.ASGITransport(app=...))`. Pipeline stub uses `asyncio.Event` to hold execution; DELETE while RUNNING → CANCELLING. **Synchronization protocol** (resolves C1-I-28): (1) `POST /ingest`, (2) `await asyncio.sleep(0)` to yield to the spawned background task, (3) poll `GET /jobs/{id}` until `status==RUNNING` (with a small bounded retry loop, e.g. ≤20 iterations × 10 ms), (4) `DELETE /jobs/{id}`, (5) assert `status==CANCELLING`. Without step 3 the cancel may arrive before the task transitions out of PENDING. **Preferred deterministic alternative**: have the pipeline stub set a `started_event = asyncio.Event()` at the start of execution, and `await started_event.wait()` in the test before issuing DELETE. This avoids all timing assumptions. The polling loop is acceptable if the pipeline stub guarantees it calls `await asyncio.sleep(0)` before setting RUNNING, but event-based synchronization is preferred.
  - H3.9: cancel DONE job → 200, unchanged
  - H3.10: two concurrent `POST /ingest` → two distinct job IDs
  - H3.11: server-side handler **unconditionally replaces** body `ingested_by` with the `X-Ingested-By` header value (the body field is overwritten regardless of its prior value). Test must seed body with a distinct value (e.g. `"client-side"`), set header `X-Ingested-By: header-side`, then assert the resulting `IngestJob.ingested_by == "header-side"`. Resolves C1-I-07.
  - E3.5: `POST /ingest {"path": "/tmp"}` (no collection) → 422
  - E3.6: GET unknown UUID → 404
  - E3.7: cancel CANCELLING twice → both 202
- **Releasable**: full ingest/job lifecycle tested
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_e2e.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_routes_e2e.py -k "ingest or job" -v`

#### Task 6.3 — Suite 3: /collections Lifecycle (H3.12–H3.15, E3.8–E3.10)
- [x] **File**: `packages/archon-search/tests/test_routes_e2e.py`
- **Depends on**: Task 6.2
- **Description**:
  - Fixtures must pass `config_path=tmp_path / "config.toml"` to `create_app()` for H3.12, H3.13
  - H3.12: `config_path` set → TOML file updated after add
  - H3.13: TOML file updated after remove
  - H3.14: path `/home/user/my-docs` → name `my_docs`
  - H3.15: one regular + one pinned collection → both in `GET /collections`
  - E3.8: `path="~/docs"` → resolved to absolute
  - E3.9: pinned but not in `collections` → 409
  - E3.10: delete then delete again → 404 on second
- **Releasable**: collection lifecycle fully tested
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_e2e.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_routes_e2e.py -k "collection" -v`

#### Task 6.4 — Suite 3: /status and /indexing-state (H3.16–H3.20)
- [x] **File**: `packages/archon-search/tests/test_routes_e2e.py`
- **Depends on**: Task 6.3
- **Description**:
  - H3.16: collections returned alphabetically
  - H3.17: fresh collection → `status="not_yet_indexed"`
  - H3.18: indexing-state fields filtered to expected set
  - H3.19: no prior ingest → `{}`
  - H3.20: `status.pid == os.getpid()`
- **Releasable**: Suite 3 fully implemented
- **Tests (TDD)** — `packages/archon-search/tests/test_routes_e2e.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_routes_e2e.py -v`

---

### Phase 7 — Suite 5: IndexingNotificationMonitor (P1)
> **Releasable**: after Task 7.2; notification monitor behavior fully regression-guarded.

#### Task 7.1 — Suite 5 Happy Paths (H5.1–H5.8)
- [x] **File**: `tests/gateway/test_notification_monitor_e2e.py`
- **Depends on**: Task 0.1
- **Description**:
  - Real `IndexingNotificationMonitor` with mocked Telegram bot. `SearchClient` HTTP via `patch.object`
  - All fixtures must include `trigger="install"` or `"update"` in state where specified
  - H5.1: all DONE → Telegram notification sent once
  - H5.2: mix DONE + FAILED → notification with FAILED summary
  - H5.3: quiet mode → notification not sent
  - H5.4: `trigger="manual"` → exits without notification
  - H5.5: one DONE, one RUNNING → keeps polling
  - H5.6: all DONE → sends and stops
  - H5.7: `trigger="install"` → fires after terminal state
  - H5.8: `trigger="update"` → fires after terminal state
- **Releasable**: monitor happy paths covered
- **Tests (TDD)** — `tests/gateway/test_notification_monitor_e2e.py`:
  - Checkpoint: `uv run pytest tests/gateway/test_notification_monitor_e2e.py -k "H5" -v`

#### Task 7.2 — Suite 5 Error Paths (E5.1–E5.5)
- [x] **File**: `tests/gateway/test_notification_monitor_e2e.py`
- **Depends on**: Task 7.1
- **Description**:
  - E5.1: `indexing_state()` returns None → logs and retries, no crash
  - E5.2: state = `{}` → keeps polling
  - E5.3: first call succeeds, subsequent return None → continues polling
  - E5.4: state dict has no `"trigger"` key → no notification, no crash
  - E5.5: `trigger="sync"` → no notification
  - **E5.6 (promoted from M12.4 P2 → P1, resolves C1-I-43)**: `_send_to_all` raises mid-notification → `_notified` was set to `True` *before* the await (verify against `archon/gateway/notification_monitor.py:~77`), so the monitor must NOT silently swallow the failure and stay permanently silenced. Test asserts: (a) the exception is logged at WARNING/ERROR (not swallowed), (b) on the next poll cycle the monitor either retries the send (preferred) or surfaces the stuck `_notified=True` state for follow-up. If the production code does not currently retry, this test pins the existing behavior + a `# TODO C1-I-43` note for follow-up. Either way it must regression-guard the ordering.
  - **E5.7 (promoted from M12.2 P2 → P1)**: cancellation mid-poll — task receives `CancelledError` while awaiting `indexing_state()` → propagates cleanly, no exception leaked, no partial state written.
- **Releasable**: Suite 5 fully implemented
- **Tests (TDD)** — `tests/gateway/test_notification_monitor_e2e.py`:
  - Checkpoint: `uv run pytest tests/gateway/test_notification_monitor_e2e.py -v`

---

### Phase 8 — Suite 10: SearchClient & SearchContextProvider Gap Coverage (P1)
> **Releasable**: after Task 8.3; all untested error branches have regression guards.

#### Task 8.1 — Suite 10 SearchClient Error Branches (A10.1–A10.26)
- [x] **File**: `tests/ai/test_search_client.py`
- **Depends on**: Task 0.3
- **Description**:
  - Append to existing file; use `patch.object(client._http, ...)` pattern
  - A10.1–A10.3: `health()` timeout/ConnectError/5xx
  - A10.4–A10.7: `status()` and `indexing_state()` timeout/ConnectError
  - A10.8–A10.11: `ingest()` timeout/ConnectError + payload omission tests
  - A10.12–A10.14: `get_job()` timeout; `cancel_job()` ConnectError → 503; 4xx → no WARNING
  - A10.15–A10.16: `list_collections()` timeout/ConnectError → `[]`
  - A10.17–A10.20: timeout for `add_collection`, `remove_collection`, `collection_info`, `reindex_collection`
  - A10.21: `route(query, slots=None)` → no `"slots"` key in payload
  - A10.21b: real in-process FastAPI via ASGITransport; all 4 `RouteResponse` fields correctly populated
  - A10.22: trailing slash in base_url normalized
  - A10.23–A10.24: `close()` and context manager
  - A10.25–A10.26: `reset_search_client()` closes and clears; noop when None
  - A10.27b: path-prefix limitation regression guard
- **Releasable**: all SearchClient error branches covered
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py -k "A10" -v`

#### Task 8.2 — Suite 10 SearchContextProvider Branches (A10.27–A10.39)
- [x] **File**: `tests/ai/test_search_context_provider.py`
- **Depends on**: Task 8.1
- **Description**:
  - Append to existing file
  - A10.27: `search_client=None` → calls `get_search_client()` internally
  - A10.28: context manager → `_http.aclose()` called on exit
  - A10.29: successful route → DEBUG log with elapsed ms
  - A10.30: `decomposer_invoked=True` + `selected_collections=None` → treated as `[]`, searches pinned only
  - A10.31: 5 pinned, `max_parallel=3` → all 5 pinned searched (semaphore limits concurrency, not count)
  - A10.32: non-text content block → returns `[]`
  - A10.33–A10.34: `top_k` truncation and over-fetch
  - A10.35–A10.36: normalize and merge edge cases (empty input)
  - A10.37–A10.38: format_results output shape
  - A10.39: all tasks raise `ValueError` → returns None, each logged DEBUG
- **Releasable**: SearchContextProvider untested branches covered
- **Tests (TDD)** — `tests/ai/test_search_context_provider.py`:
  - Checkpoint: `uv run pytest tests/ai/test_search_context_provider.py -k "A10" -v`

#### Task 8.3 — Suite 10 `get_search_client()` Singleton (A10.40–A10.42)
- [x] **File**: `tests/ai/test_search_client.py`
- **Depends on**: Task 8.2
- **Description**:
  - A10.40: config with `search.url` + singleton is None → `get_search_client()` returns `SearchClient` with correct `base_url`
  - A10.41: call twice without reset → same object (`is` check)
  - A10.42: call then `reset_search_client()` then call → new object (different `id()`)
  - A10.43 **dropped**: production `get_search_client()` has no `enabled` gate (verified in `archon/ai/search_client.py:356–361`); the singleton always returns a `SearchClient`. Callers gate on `config.search.enabled` themselves. Resolves C1-I-02.
  - Each test must reset singleton after: `await reset_search_client()` in teardown
- **Releasable**: singleton lifecycle fully tested
- **Tests (TDD)** — `tests/ai/test_search_client.py`:
  - Checkpoint: `uv run pytest tests/ai/test_search_client.py -k "get_search_client" -v`

---

### Phase 9 — Suite 11: archon-search Configuration (P1)
> **Releasable**: after Task 9.1; archon-search config layer has test coverage for the first time.

#### Task 9.1 — Suite 11 archon-search Config (C11.13–C11.22)
- [x] **File**: `packages/archon-search/tests/test_config.py`
- **Depends on**: Task 0.1
- **Description**:
  - New file — currently zero tests for `archon_search/config.py`
  - C11.13: `SearchConfig()` with no args → all defaults valid
  - C11.14: non-existent path → returns `SearchConfig()` defaults
  - C11.15: invalid TOML → raises `ConfigError` (with `__cause__`)
  - C11.16: TOML with all 4 sections → all fields populated
  - C11.17: load → modify → save → reload → values preserved
  - C11.18: save to nonexistent path → file created
  - C11.19: `port=0` or `port=65536` → `ConfigError`
  - C11.20: `chunk_size=0` → `ConfigError`
  - C11.21: `routing_shortlist_size=0` → `ConfigError`
  - C11.22: `routing_confidence_threshold` boundary (`0.0` and `1.0` valid; `-0.1` and `1.1` raise)
  - Use `tmp_path` for all file I/O
- **Releasable**: archon-search config module has first test coverage
- **Tests (TDD)** — `packages/archon-search/tests/test_config.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_config.py -v`

---

### Phase 10 — Suites 12 & 13: Store and Monitor Gaps (P1)
> **Releasable**: after Task 10.4; crash recovery, atomicity, and SQL injection paths covered.

#### Task 10.1 — Suite 12: `_needs_install_trigger()` (M12.13–M12.16)
- [x] **File**: `packages/archon-search/tests/test_mcp.py`
- **Depends on**: Task 0.1
- **Description**:
  - New file; import `_needs_install_trigger` from `archon_search.server.mcp`
  - Use `tmp_path` + real `IndexingStateStore` for all tests
  - M12.13: no state file → returns `True` (fresh install)
  - M12.14: state exists but new collection absent → returns `True`
  - M12.15: all configured collections at DONE → returns `False`
  - M12.16: a collection is IN_PROGRESS → returns `True` (per `_needs_install_trigger` docstring: "any status other than DONE" triggers; on restart, IN_PROGRESS implies a prior crash and warrants re-trigger). Resolves C1-I-01.
- **Releasable**: install trigger logic regression-guarded
- **Tests (TDD)** — `packages/archon-search/tests/test_mcp.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_mcp.py -v`

#### Task 10.2 — Suite 13: JobStore Gap Coverage (J13.1–J13.11)
- [x] **File**: `packages/archon-search/tests/test_job_store.py`
- **Depends on**: Task 0.1
- **Description**:
  - New file; import `JobStore` from `archon_search.store.job_store` (verify import path)
  - J13.1: new store, no file → `_load()` returns False, `_jobs` empty
  - J13.2: corrupt JSON → empty store, logged warning
  - J13.3: valid JSON, missing required key → empty store
  - J13.4: valid JSON, wrong type (list not dict) → empty store
  - J13.5: RUNNING job with `error="prior"` → after reload, error = `"process_restart"`
  - J13.6: `updated_at` 7 days + 1s ago → evicted
  - J13.7: `updated_at` exactly 7 days ago → NOT evicted
  - J13.8: `updated_at="not-a-date"` → no crash, handled gracefully
  - J13.9: job is DONE, `transition(from_statuses={RUNNING})` → returns None, unchanged
  - J13.10: sequential double-transition rejected — second `transition(PENDING→RUNNING)` returns None
  - J13.11: `test_job_store_write_atomic_rename_failure_leaves_tmp_on_disk` — simulate `Path.rename()` failure without class-level patching (which would affect pytest's own `tmp_path` cleanup). Preferred approach: make the destination path unwritable by removing write permission on its parent directory with `os.chmod(tmp_path, 0o555)` before triggering `_write_atomic()`; restore permissions in teardown. This causes a genuine `OSError` on `rename()` without mocking. Verify `.tmp` file IS left on disk after the exception propagates (no cleanup in `_write_atomic()` on rename failure — known limitation: no atomic cleanup in JobStore on rename failure). Restore permissions before `tmp_path` teardown: `os.chmod(tmp_path, 0o755)` in a `finally` block or `addfinalizer`.
  - Use `tmp_path` for all disk I/O
- **Releasable**: JobStore crash recovery and atomicity covered
- **Tests (TDD)** — `packages/archon-search/tests/test_job_store.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_job_store.py -v`

#### Task 10.3 — Suite 13: IndexingStateStore Gap Coverage (J13.12–J13.19)
- [x] **File**: `packages/archon-search/tests/test_progress.py`
- **Depends on**: Task 10.2
- **Description**:
  - Append to existing `test_progress.py`
  - J13.12: `PermissionError` reading state file → returns None, logs warning
  - J13.13: state path is directory → returns None, no crash
  - J13.14: `os.replace()` raises → `.tmp` unlinked; original exception re-raised
  - J13.15: state absent → `remove_collection()` doesn't crash, doesn't write
  - J13.16: update collection A → collection B unchanged
  - J13.17: `processed_files > 0`, `elapsed=0` → returns None, no `ZeroDivisionError`
  - J13.18: `started_at` with UTC+05:00 → ETA computed without crash
  - J13.19: `file_mtimes: {"file.md": true}` → boolean fails isinstance check; `file_mtimes == {}`
- **Releasable**: IndexingStateStore edge cases covered
- **Tests (TDD)** — `packages/archon-search/tests/test_progress.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_progress.py -v`

#### Task 10.4 — Suite 14 SQL Injection Regression Tests (P14.23, P14.24)
- [x] **File**: `packages/archon-search/tests/test_pipeline.py`
- **Depends on**: Task 0.1 (markers only). Note: functionally independent of Task 10.3 — the dependency is only for consistent file-append ordering; there is no functional relationship between IndexingStateStore gap coverage and SQL injection regression guards.
- **Description**:
  - Append to existing `test_pipeline.py`
  - **Renamed from `P14.17b`/`P14.18b` → `P14.23`/`P14.24`** to eliminate the ID collision with the P0 fault-tolerance tests `P14.17`/`P14.18` (Task 4.1) and remove the ad-hoc `b`-suffix convention. Resolves C1-I-06 / C1-I-42.
  - P14.23: `add_collection("col'name")` → raises `ValueError` from `_validate_collection`; no SQL injection possible
  - P14.24: `delete_document(doc_id="' OR '1'='1")` → rejected by `_DOC_ID_RE` before SQL construction
  - These are security regression guards — must never regress
- **Releasable**: SQL injection paths regression-guarded
- **Tests (TDD)** — `packages/archon-search/tests/test_pipeline.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_pipeline.py -k "injection" -v`

---

### Phase 11 — Suite 15: SearchCollectionSync Crash Recovery (P1)
> **Releasable**: after Task 11.1 and Task 11.2; stale IN_PROGRESS reset, partial resume, and Archon-side config edge cases are all regression-guarded.

#### Task 11.1 — Suite 15 Crash Recovery (S15.7–S15.9)
- [x] **File**: `packages/archon-search/tests/test_sync_e2e.py`
- **Depends on**: Task 0.1
- **Description**:
  - New file; real `SearchCollectionSync` + real `SearchPipeline` (fastembed stub) + real `IndexingStateStore` + `tmp_path`
  - S15.7: `IndexingStateStore` has collection at `IN_PROGRESS` from prior crash → `_reset_stale_in_progress()` resets to `PENDING` before sync
  - S15.8: `processed_paths` set in state → `sync()` skips already-indexed files, resumes from remaining
  - S15.9: registered collection path doesn't exist on disk → `sync()` logs warning, doesn't crash, marks as not-yet-indexed
  - No HTTP involved — this is sync engine from the outside
- **Releasable**: SearchCollectionSync crash recovery tested; stale state reset is regression-guarded
- **Tests (TDD)** — `packages/archon-search/tests/test_sync_e2e.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_sync_e2e.py -v`

#### Task 11.2 — Suite 11 Archon-side Config Edge Cases (C11.4, C11.6)
- [x] **File**: `tests/config/test_config_search.py`
- **Depends on**: Task 9.1
- **Description**:
  - Only C11.4 and C11.6 are valid for Archon-side `SearchConfig` (see test plan note)
  - C11.4: `max_parallel_collections="many"` → `ConfigError`
  - C11.6: `top_k_return="few"` → `ConfigError`
  - C11.1–C11.3, C11.5, C11.7–C11.12 reference server-side fields not in Archon's `SearchConfig` — skip
  - **Promoted from P2 to P1** — these are part of the P1 Acceptance Criteria gate
- **Releasable**: Archon-side config validation edge cases regression-guarded
- **Tests (TDD)** — `tests/config/test_config_search.py`:
  - Checkpoint: `uv run pytest tests/config/test_config_search.py -v`

---

### Phase 12 — P2 Stretch: Suites 6, 7, 8, 12 Gaps, 13 Watcher, 14 Core Pipeline, 15 Full (P2)
> **Releasable**: after each task independently; these are stretch goals and do not block release.

#### Task 12.1 — Suite 6: ArchonToolkitSearch E2E (H6.1–H6.13, E6.1–E6.4)
- [x] **File**: `tests/ai/test_archon_toolkit_search_e2e.py`
- **Depends on**: Task 0.1
- **Description**:
  - Real `ArchonToolkit` + `_register_search_tools`; `SearchClient` HTTP mocked
  - H6.1–H6.3: `search_status` tool (enabled+running, enabled+down, disabled)
  - H6.4, E6.1: `search_ingest` tool (success + server down)
  - H6.5–H6.9, E6.2–E6.3: collection management tools
  - H6.10–H6.12: `search_start`, `search_stop`, `search_sync` CLI redirect messages
  - H6.13, E6.4: `search_collection_reindex` (success + server down)
- **Tests (TDD)** — `tests/ai/test_archon_toolkit_search_e2e.py`:
  - Checkpoint: `uv run pytest tests/ai/test_archon_toolkit_search_e2e.py -v`

#### Task 12.2 — Suite 7: Complex Multi-Collection Scenarios (X7.1–X7.13)
- [x] **File**: `tests/e2e/test_search_complex_scenarios.py`
- **Depends on**: Task 0.2, Task 5.3
- **Description**:
  - X7.1–X7.3: concurrent route/ingest, routable cap
  - X7.4–X7.7: score normalization edge cases
  - X7.8–X7.10: job state machine (crash recovery, eviction, cancel race)
  - X7.11–X7.13: config persistence (add/remove writes TOML, None path skips I/O)
- **Tests (TDD)** — `tests/e2e/test_search_complex_scenarios.py`:
  - Checkpoint: `uv run pytest tests/e2e/test_search_complex_scenarios.py -v`

#### Task 12.3 — Suite 8: archon doctor Search Checks (H8.1–H8.6)
- [x] **File**: `tests/cli/test_doctor_search.py`
- **Depends on**: Task 0.1
- **Description**:
  - Real `run_checks()` with mocked `SearchClient` HTTP
  - Cross-reference `tests/cli/test_doctor.py` before implementing — only add uncovered scenarios
  - H8.1: disabled → "disabled", no network
  - H8.2: healthy → check passes
  - H8.3: unreachable → check fails with connection message
  - H8.4: IN_PROGRESS → "⏳ partial (N/M files)"
  - H8.5: FAILED → "❌" shown
  - H8.6: PENDING → informational only
- **Tests (TDD)** — `tests/cli/test_doctor_search.py`:
  - Checkpoint: `uv run pytest tests/cli/test_doctor_search.py -v`

#### Task 12.4 — Suite 12 Notification Monitor & Toolkit Gaps (M12.1–M12.12)
- [x] **File**: `tests/gateway/test_notification_monitor.py`, `tests/ai/test_archon_toolkit_search.py`
- **Depends on**: Task 7.2, Task 12.1
- **Description**:
  - Append to existing files
  - M12.1–M12.7: `IndexingNotificationMonitor` loop, cancellation, exception logging, `send_to_all` partial failure
  - M12.8–M12.12: `ArchonToolkitSearch` error paths (default path, config unavailable, reindex error string, empty list JSON, collection info JSON)
- **Tests (TDD)** — as listed above:
  - Checkpoint: `uv run pytest tests/gateway/test_notification_monitor.py tests/ai/test_archon_toolkit_search.py -v`

#### Task 12.5 — Suite 13 Watcher Gap Coverage (J13.20–J13.24)
- [x] **File**: `packages/archon-search/tests/test_watcher.py`
- **Depends on**: Task 10.3
- **Description**:
  - Append to existing `test_watcher.py`
  - J13.20: `observer.schedule()` raises `OSError` → logged WARNING, no crash
  - J13.21: `observer.join()` raises `OSError` → logged WARNING, no propagation
  - J13.22: new `WatcherManager()` → `watching_names()` empty
  - J13.23: add same path with two different names → two watchers
  - J13.24: `observer.schedule(handler, path, recursive=True)` → `recursive=True` verified
- **Tests (TDD)** — `packages/archon-search/tests/test_watcher.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_watcher.py -v`

#### Task 12.6 — Suite 14 Core Pipeline Gaps (P14.1–P14.16, P14.21–P14.22)
- [ ] **File**: `packages/archon-search/tests/test_pipeline.py`, `test_embedder.py`, `test_reranker.py`, `test_store.py`
- **Depends on**: Task 4.1, Task 10.4
- **Description**:
  - P14.1–P14.4: Embedder error paths (wrong count, empty result, whitespace)
  - P14.5–P14.7: Reranker edge cases (top_k > candidates, stable order, score count mismatch)
  - P14.8–P14.16: Store error paths (name validation, empty rebuild, hybrid search top_k=0, list_documents limit=0, fetch nonexistent, list exception on one table, malformed centroid JSON, invalid hex doc_id)
  - P14.21–P14.22: Pipeline zero-files ingest, chunk_size=1
- **Tests (TDD)** — as listed above:
  - Checkpoint: `uv run pytest packages/archon-search/tests/ -k "P14" -v`

#### Task 12.7 — Suite 15 Full SearchCollectionSync Coverage (S15.1–S15.6, S15.10)
- [ ] **File**: `packages/archon-search/tests/test_sync_e2e.py`
- **Depends on**: Task 11.1
- **Description**:
  - Append to existing `test_sync_e2e.py`
  - S15.1: new directory → sync starts ingest, state → IN_PROGRESS
  - S15.2: DONE + no changes → sync skips
  - S15.3: file modified → incremental update
  - S15.4: embedding model changed → full reindex
  - S15.5: chunk_size changed → full reindex
  - S15.6: collection removed from config → drops LanceDB table, cleans state
  - S15.10: path points to `~/.archon/history` (legacy) → remapped to `.../sessions`
- **Tests (TDD)** — `packages/archon-search/tests/test_sync_e2e.py`:
  - Checkpoint: `uv run pytest packages/archon-search/tests/test_sync_e2e.py -v`

<!-- Task 12.8 (C11.4, C11.6) moved to Task 11.2 in Phase 11 — promoted from P2 to P1 -->
