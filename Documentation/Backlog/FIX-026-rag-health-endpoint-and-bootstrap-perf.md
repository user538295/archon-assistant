# FIX-026 — RAG health endpoint missing and bootstrap performance
**Purpose**: Fix `_is_service_running()` which always returns False because the FastMCP server has no `/health` endpoint, and improve bootstrap performance for large datasets.
**Audience**: End users who run `archon rag install`.
**Status**: To Do

---

## Background

Three linked problems discovered during a live `archon rag install --non-interactive` on a dataset of 253 session files (~35 MB):

**Problem 1 — Health endpoint doesn't exist.** `_is_service_running()` in `install.py` probes `http://{host}:{port}/health`, but the RAG server is a FastMCP app that only serves MCP protocol endpoints (`/mcp` for streamable HTTP). There is no `/health` route. The probe gets HTTP 404, catches it as an exception, and returns `False`. This means `_wait_for_service()` **always times out** — even when the server is running fine. This is the root cause of the "RAG service did not become ready within 60 seconds" error.

**Problem 2 — No bootstrap progress feedback.** `_bootstrap_collections()` calls `ingest_directory()` which processes files sequentially with no per-file progress output. For 253 files producing ~71K chunks, embedding takes ~7 minutes and LanceDB writes add more. The user sees only `[4/5] Bootstrapping collections ...` with no indication of progress.

**Problem 3 — Embedding batch size is unbounded.** Large session files (up to 5 MB) produce thousands of chunks in a single `embed()` call. While CoreML/GPU is correctly used (`CoreMLExecutionProvider` is active), a single unbounded batch consumes excessive memory (observed: 8.6 GB RSS, 629% CPU). Embedding in fixed-size batches would cap peak memory and allow progress reporting.

---

## Goal

After this fix: `archon rag install` correctly detects the running FastMCP server and reports success; bootstrap shows per-file progress for large datasets; embedding memory usage is bounded by a configurable batch size.

---

## Scope

### In Scope
- Add a `/health` endpoint to the FastMCP app in `server.py` via `app.custom_route()`
- Change `_is_service_running()` to probe `/health` (now a real endpoint) — OR probe `/mcp` for 406 as a fallback
- Add `progress_cb` usage in `_bootstrap_collections()` to print per-file progress
- Add batch-size control to `Embedder.embed()` to cap per-call chunk count
- Update tests for all changes

### Out of Scope
- Parallelizing file ingestion across multiple threads (future optimization)
- Adding rich/tqdm progress bars
- Changing the MCP protocol or transport
- Modifying `ingest_directory()` signatures beyond what's needed for progress

---

## Acceptance criteria
- [ ] `GET /health` on the RAG server returns HTTP 200 with `{"status": "ok"}`
- [ ] `_is_service_running()` returns `True` when the FastMCP server is running
- [ ] `archon rag install` completes with "RAG service installed and running successfully." on a working setup
- [ ] Bootstrap of ≥100 files shows per-file progress (e.g. `[4/5] Bootstrapping collections ... 50/253 files`)
- [ ] `Embedder.embed()` processes chunks in batches of ≤512 (`_EMBED_BATCH_SIZE` constant), capping peak memory
- [ ] All existing tests pass

---

## What does NOT change
- `ingest_file()` and `ingest_directory()` public signatures (only internal batching changes)
- `RagCollectionSync.sync()` — no changes
- MCP tool definitions in `server.py` — no changes
- `_wait_for_service()` — already has progress dots from FIX-025, no changes needed
- `run()` step labels — already done in FIX-025

---

## Known limitations / accepted trade-offs
- The `/health` endpoint is a simple custom route returning 200; it does not verify that LanceDB is connected or that collections exist — it only proves the HTTP server is listening. A deeper readiness check could be added later.
- Batch size of 512 chunks is a developer-tunable module-level constant (`_EMBED_BATCH_SIZE`), not exposed in `config.toml`. Optimal batch size depends on model and hardware.
- Per-file progress prints `\r`-based overwrite lines; this may not render cleanly when stdout is piped to a file (e.g. CI/scripted `--non-interactive` runs). Acceptable for now; a TTY check (`sys.stdout.isatty()`) could be added later to fall back to newlines.
- Progress only fires for **new** collections (first install). On re-install where collections already exist, sync skips to "unchanged" and `progress_cb` is never called — this is fast and requires no progress.
- When multiple collections are configured, the file counter resets per collection (e.g. `50/253` then `1/10`). This is inherent to the per-`ingest_directory()` callback design.

---

## Architecture

**`archon/rag/server.py`**
- New: `/health` custom route via `app.custom_route("/health", methods=["GET"])` returning `JSONResponse({"status": "ok"})` using Starlette `JSONResponse` (FastMCP's underlying framework)

**`archon/rag/install.py`**
- `_is_service_running()` — probes `GET /health`; expects HTTP 200. No change to the URL path (it now actually exists).
- `_bootstrap_collections()` — passes a `progress_cb` to `RagCollectionSync.sync()` → `ingest_directory()` that prints `\r[4/5] Bootstrapping collections ... {done}/{total} files` with `flush=True`

**`archon/rag/embedder.py`**
- `_EMBED_BATCH_SIZE = 512` — module-level constant
- `Embedder.embed()` — splits input into batches of `_EMBED_BATCH_SIZE`, calls `backend.encode()` per batch, concatenates results. `embed_one()` unchanged.

---

## Tests
- **`test_health_endpoint_returns_200`** (unit): FastMCP app `/health` returns 200 + `{"status":"ok"}`
- **`test_is_service_running_returns_true_on_200`** (unit): mock urlopen to return 200; assert True
- **`test_is_service_running_returns_false_on_error`** (unit): mock urlopen to raise; assert False
- **`test_bootstrap_collections_passes_progress_cb`** (unit): mock sync, verify progress_cb is passed
- **`test_bootstrap_progress_prints_file_count`** (unit): simulate progress_cb calls, verify output
- **`test_embedder_batches_large_input`** (unit): 1000 texts with batch=256; verify encode called 4 times
- **`test_embedder_small_input_single_batch`** (unit): 100 texts with batch=512; verify encode called once
- **`test_embedder_batch_preserves_order`** (unit): verify output order matches input order across batches
- **`test_embedder_empty_input`** (unit): empty list returns empty list, encode not called

---

## Documentation update
- [ ] `Documentation/Architecture/180_rag_architecture.md`, section "RAG Server": add `/health` endpoint description

---

## Task breakdown

### Phase 1 — Health endpoint (fixes the install failure)
> **Releasable**: after Task 1.2 — `archon rag install` correctly detects a running server

#### Task 1.1 — Add `/health` endpoint to FastMCP server
- [x] **File**: `archon/rag/server.py`
- **Depends on**: nothing
- **Description**:
  - In `create_app()`, after all `@app.tool()` definitions, add a custom health route:
    ```python
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    @app.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})
    ```
  - Import `Request` from `starlette.requests` and `JSONResponse` from `starlette.responses` at top of file (starlette is a transitive dependency of FastMCP — no new package needed)
- **Releasable**: after this task, `GET /health` returns 200 on the RAG server
- **Tests (TDD)** — `tests/rag/test_server.py`:
  - Unit: `test_health_endpoint_returns_200` — create the app via `create_app()`, use `httpx.ASGITransport(app=app.http_app())` with `httpx.AsyncClient` to `GET /health`; assert status 200 and body `{"status": "ok"}`. Note: `FastMCP.http_app()` returns the Starlette ASGI app; if this method doesn't exist, fall back to starting the app with `run_http_async` in a background task and hitting it with a real HTTP client
  - Checkpoint: `uv run pytest tests/rag/test_server.py --no-cov -v -k "test_health"`

#### Task 1.2 — Verify `_is_service_running()` works with new endpoint
- [x] **File**: `tests/rag/test_install.py`
- **Depends on**: Task 1.1
- **Description**:
  - No code changes to `_is_service_running()` — it already probes `/health`. The fix is that the endpoint now exists.
  - Add/update tests to verify the integration works:
    - Mock `urllib.request.urlopen` to return a 200 response → `_is_service_running()` returns `True`
    - Mock `urllib.request.urlopen` to raise `urllib.error.HTTPError(404)` → returns `False`
    - Mock `urllib.request.urlopen` to raise `ConnectionRefusedError` → returns `False`
- **Releasable**: after this task, install correctly detects a running FastMCP server
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_is_service_running_returns_true_on_200` — mock urlopen success; assert True
  - Unit: `test_is_service_running_returns_false_on_http_error` — mock urlopen raising HTTPError; assert False
  - Unit: `test_is_service_running_returns_false_on_connection_refused` — mock urlopen raising ConnectionRefusedError; assert False
  - Checkpoint: `uv run pytest tests/rag/test_install.py --no-cov -v -k "test_is_service_running"`

---

### Phase 2 — Embedding batch size control
> **Releasable**: after Task 2.1 — embedding memory usage is bounded

#### Task 2.1 — Batch `Embedder.embed()` calls
- [ ] **File**: `archon/rag/embedder.py`
- **Depends on**: nothing
- **Description**:
  - Add module-level constant: `_EMBED_BATCH_SIZE = 512`
  - Modify `Embedder.embed(self, texts: list[str]) -> list[list[float]]`:
    ```python
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        all_results: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_SIZE):
            batch = texts[i : i + _EMBED_BATCH_SIZE]
            result = await asyncio.to_thread(self._backend.encode, batch)
            all_results.extend(result)
        if self._embedding_dim is None and all_results:
            self._embedding_dim = len(all_results[0])
        return all_results
    ```
  - `embed_one()` unchanged (delegates to `embed()`)
  - Effect: a 5000-chunk file now makes 10 encode calls (9 × 512 + 1 × 488) instead of one massive call
- **Releasable**: after this task, embedding peak memory is capped at ~512 chunks per encode call
- **Tests (TDD)** — `tests/rag/test_embedder.py`:
  - Unit: `test_embedder_batches_large_input` — backend with 1000 texts, mock `_EMBED_BATCH_SIZE=256`; verify `encode()` called 4 times (3 × 256 + 1 × 232)
  - Unit: `test_embedder_small_input_single_batch` — 100 texts; verify `encode()` called once
  - Unit: `test_embedder_batch_preserves_order` — texts=["a","b","c",...]; verify output vectors correspond 1:1 in order
  - Unit: `test_embedder_empty_input` — `embed([])` returns `[]`; `encode()` not called
  - Checkpoint: `uv run pytest tests/rag/test_embedder.py --no-cov -v -k "test_embedder_batch or test_embedder_small or test_embedder_empty"`

---

### Phase 3 — Bootstrap progress feedback
> **Releasable**: after Task 3.1 — user sees per-file progress during bootstrap

#### Task 3.1 — Add progress output to `_bootstrap_collections()`
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: nothing
- **Description**:
  - Modify `_bootstrap_collections()` to pass a `progress_cb` to `sync()`:
    ```python
    async def _bootstrap_collections(self) -> None:
        from archon.rag.sync import RagCollectionSync

        pipeline = create_pipeline(self.cfg)
        try:
            await pipeline.store.connect()

            def _progress(done: int, total: int) -> None:
                print(f"\r[4/5] Bootstrapping collections ... {done}/{total} files", end="", flush=True)

            await RagCollectionSync(pipeline).sync(self._full_cfg.rag.collections, progress_cb=_progress)
        finally:
            await pipeline.store.disconnect()
    ```
  - In `run()`, after `_bootstrap_collections()` returns, print a newline before the "Collections ready" message to terminate the `\r` line:
    ```python
    asyncio.run(self._bootstrap_collections())
    print()  # terminate the \r progress line
    print("[4/5] Collections ready.")
    ```
  - Note: `RagCollectionSync.sync()` already accepts `progress_cb: Callable[[int, int], None | Awaitable[None]] | None` and passes it through to `ingest_directory()` which calls it after each file. No changes to sync or pipeline needed.
- **Releasable**: after this task, bootstrap shows `[4/5] Bootstrapping collections ... 50/253 files` during ingest
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_bootstrap_collections_passes_progress_cb` — mock `RagCollectionSync.sync`; verify it was called with a non-None `progress_cb` argument
  - Unit: `test_bootstrap_progress_prints_file_count` — capture stdout; simulate the progress_cb being called with (1, 10), (5, 10), (10, 10); assert `1/10`, `5/10`, `10/10` appear in output
  - Unit: `test_run_prints_newline_after_bootstrap` — mock all methods; capture stdout; assert `\n[4/5] Collections ready.` pattern (newline before the ready message)
  - Checkpoint: `uv run pytest tests/rag/test_install.py --no-cov -v -k "test_bootstrap"`

---

### Phase 4 — Documentation
> **Releasable**: after Task 4.1

#### Task 4.1 — Update architecture doc with `/health` endpoint
- [ ] **File**: `Documentation/Architecture/180_rag_architecture.md`
- **Depends on**: Task 1.1
- **Description**:
  - Add `/health` endpoint to the RAG server section: `GET /health → 200 {"status":"ok"}` — used by `archon rag install` to verify service readiness
  - Mention embedding batch size constant `_EMBED_BATCH_SIZE = 512` in the performance section if one exists
- **Releasable**: after this task, docs match implementation
- **Tests (TDD)**: N/A — documentation only
  - Checkpoint: N/A
