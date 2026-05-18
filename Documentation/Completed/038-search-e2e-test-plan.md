# FEAT-038 Search E2E Test Plan

**Created:** 2026-05-03  
**Status:** Draft  
**Scope:** End-to-end and integration tests for the archon ↔ archon-search HTTP boundary — covering happy paths, edge cases, error paths, warning paths, and complex multi-collection scenarios.

---

## Context

The search feature crosses an HTTP boundary:

```
Archon (archon/ai/search_client.py)
    → HTTP POST/GET → archon-search FastAPI server (packages/archon-search/)
        → LanceDB + fastembed + reranker
```

**What already exists:**
- `tests/ai/test_search_client.py` — unit tests with mocked `httpx` calls
- `tests/gateway/test_search_integration.py` — routing logic with mocked HTTP
- `packages/archon-search/tests/test_routes_*.py` — FastAPI TestClient tests (in-process, no real network)

**What is missing:** Real e2e tests that spin up the archon-search FastAPI app in-process and exercise the full boundary: `SearchClient` → `httpx` → real ASGI dispatch (no network socket) → FastAPI routes → response.

---

## Test Infrastructure Notes

All new tests follow existing patterns:
- `packages/archon-search/` tests: use `TestClient(create_app(config, job_store))` with `tmp_path` isolation. For tests that verify config persistence (e.g., H3.12, H3.13, X7.11–X7.13), fixtures must pass `config_path=tmp_path / "config.toml"` as a third argument to `create_app()`, otherwise `config_path` defaults to `None` and persistence tests will silently pass without writing any file.
- `tests/` (Archon-side) tests: use `httpx.AsyncClient` backed by `httpx.ASGITransport` to bypass the real TCP network while still exercising real HTTP routing, middleware, and Pydantic validation. **Note:** `SearchClient.__init__` hardcodes `self._http = httpx.AsyncClient(base_url=..., timeout=...)` with no transport parameter — you cannot pass `httpx.ASGITransport` to it externally. Tests must either construct a `SearchClient` subclass or use `patch.object` to replace `client._http` with an `httpx.AsyncClient(transport=httpx.ASGITransport(app=...))` instance. This wires real HTTP dispatch through FastAPI without opening a network port.
- **Alternative:** `SearchClient.__init__` should be updated to accept an optional `transport: httpx.BaseTransport | None = None` parameter, forwarded to `httpx.AsyncClient`. Either approach is valid; the transport parameter is cleaner.
- `@pytest.mark.asyncio` for all async tests
- ML model stubs from `packages/archon-search/tests/conftest.py` (fastembed → zero arrays, ONNX stub)
- `caplog` for asserting log levels and messages
- `pytest.mark.integration` for tests requiring a real server process (skipped in unit CI)
- **Cross-package conftest access:** `tests/e2e/` tests (Archon-side) that need ML model stubs from `packages/archon-search/tests/conftest.py` CANNOT import them automatically — pytest conftest scoping does not cross package boundaries. These tests must either: (a) define their own `tests/e2e/conftest.py` that duplicates the stub fixtures, or (b) import the stubs explicitly. The recommended approach is (a) with a `# shared with packages/archon-search/tests/conftest.py` comment to flag the duplication.

---

## Test Suites

### Suite 1 — SearchClient ↔ Real In-Process Server

**File:** `tests/e2e/test_search_client_e2e.py`  
**Strategy:** `httpx.ASGITransport(app=create_app(...))` — real HTTP dispatch through FastAPI, no network port needed. Tests construct a `SearchClient` subclass or use `patch.object` to replace `client._http` with an `httpx.AsyncClient(transport=httpx.ASGITransport(app=...))` instance. This wires real HTTP dispatch through FastAPI without opening a network port.

#### 1.1 Happy Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H1.1 | `test_health_returns_running_status` | `GET /health` → `{"status": "running", "version": ...}`; `SearchClient.health()` returns dict with those keys |
| H1.2 | `test_status_returns_empty_collections_on_fresh_server` | `GET /status` on a server with no collections → `{"running": true, "pid": <int>, "version": <str>, "collections": []}`. Assert `collections` is `[]` and `running` is `true`. |
| H1.3 | `test_add_collection_returns_pending_job` | `POST /collections` with a valid path → 202 + `IngestJob(status=PENDING)` |
| H1.4 | `test_list_collections_includes_added_collection` | After adding, `GET /collections` returns the new collection |
| H1.5 | `test_get_collection_info_returns_metadata` | `GET /collections/{name}` on registered collection → correct name, path, status |
| H1.6 | `test_remove_collection_deletes_it` | `DELETE /collections/{name}` → 200 + `{"deleted": true}`; subsequent list does not include it |
| H1.7 | `test_ingest_with_path_returns_pending_job` | `SearchClient.ingest(collection="docs", path="/tmp/test_dir")` → `POST /ingest {"collection": "docs", "path": "/tmp/test_dir"}` → 202 + `IngestJob(status=PENDING)`. Tests the path-based ingest flow via `SearchClient`; the server-side `IngestRequest` model also accepts inline `documents`, but `SearchClient.ingest()` takes `collection`, `path`, and `ingested_by` parameters. |
| H1.8 | `test_get_job_returns_job_state` | `POST /ingest` then `GET /jobs/{id}` → returns same job_id, status in {PENDING, RUNNING, DONE} |
| H1.9 | `test_cancel_job_in_pending_state_returns_202` | `POST /ingest` then immediately `DELETE /jobs/{id}` → 202 + status=CANCELLING |
| H1.10 | `test_cancel_terminal_job_is_idempotent` | Cancel a DONE job → 200, job remains DONE |
| H1.11 | `test_indexing_state_returns_empty_on_fresh_server` | `GET /indexing-state` → `{}` on a server with no prior indexing |
| H1.12 | `test_route_with_no_collections_returns_empty_routable` | `POST /route` with query on server with no collections → 200 + `routable_names=[]` |
| H1.13 | `test_reindex_collection_returns_new_job` | Register collection, then `POST /collections/{name}/reindex` → 202 + new job_id |

#### 1.2 Error Paths (Server Returns 4xx/5xx)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| E1.1 | `test_route_empty_query_returns_400` | `POST /route {"query": ""}` → 400; `SearchClient.route("")` returns None, logs WARNING |
| E1.2 | `test_route_whitespace_query_returns_400` | `POST /route {"query": "   "}` → 400; client returns None |
| E1.3 | `test_route_invalid_slots_returns_400` | `POST /route {"query": "x", "slots": 0}` → 400; client returns None |
| E1.4 | `test_add_collection_duplicate_returns_409` | Add same path twice → 409; second `add_collection()` returns None |
| E1.5 | `test_remove_nonexistent_collection_returns_404` | `DELETE /collections/does_not_exist` → 404; `remove_collection()` returns None |
| E1.6 | `test_get_nonexistent_collection_returns_404` | `GET /collections/ghost` → 404; `collection_info()` returns None |
| E1.7 | `test_get_nonexistent_job_returns_404` | `GET /jobs/fake-uuid` → 404; `get_job()` returns None |
| E1.8 | `test_cancel_nonexistent_job_returns_404` | `DELETE /jobs/fake-uuid` → 404; `cancel_job()` returns 404 |
| E1.9 | `test_reindex_nonexistent_collection_returns_404` | `POST /collections/ghost/reindex` → 404; `reindex_collection()` returns None |
| E1.10 | `test_remove_pinned_only_collection_returns_409` | Collection in `pinned_collections` only → 409; `remove_collection()` returns None |
| E1.11 | `test_ingest_empty_collection_name_returns_422` | `POST /ingest {"collection": ""}` → 422 (Pydantic validation); `ingest()` returns None |
| E1.12 | `test_route_504_timeout_returns_none` | Server-side routing times out (30s) → 504 response; `SearchClient.route()` returns None, logs WARNING with status 504. **Implementation note:** To observe a 504 (not a client-side `TimeoutException`), the test must set `SearchClient` timeout to >30s AND inject a router that sleeps exactly 31s. Alternatively, mock `asyncio.wait_for` to raise `TimeoutError` inside the route handler. The simpler approach is to mock the timeout — do not run a real 31-second sleep in CI. |

#### 1.3 SearchClient Network-Level Errors

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| N1.1 | `test_route_server_down_returns_none` | `SearchClient` pointed at port with nothing listening → `route()` returns None, logs at DEBUG |
| N1.2 | `test_health_server_down_returns_none` | Same → `health()` returns None |
| N1.3 | `test_list_collections_server_down_returns_empty_list` | `list_collections()` returns `[]` (not None) when server down |
| N1.4 | `test_cancel_job_server_down_returns_503` | `cancel_job()` returns 503 when server unreachable |
| N1.5 | `test_route_timeout_returns_none` | Set `timeout=0.001` on SearchClient; real FastAPI route that sleeps → TimeoutException → returns None, logs WARNING |
| N1.6 | `test_route_malformed_json_response_returns_none` | Inject a custom route that returns `Content-Type: application/json` but body `not-json` → `route()` returns None, logs WARNING |
| N1.7 | `test_search_client_recovers_after_transient_server_outage` | First call succeeds, second fails (ConnectError), third succeeds — httpx AsyncClient connection pool reconnects transparently; third call returns valid result |

> **Note on N1.5 and N1.7:** These tests require a real TCP connection, not `ASGITransport`. N1.5 (`timeout` with a sleeping route) does not trigger `httpx.TimeoutException` in-process. N1.7 (connection pool recovery after `ConnectError`) has no connection pool in ASGI transport. Both tests must use a real `SearchClient` pointed at an unused port (for N1.5) or a custom `httpx` mock (for N1.7), not `ASGITransport`. Mark both with `pytest.mark.integration` or move to a separate fixture.

---

### Suite 2 — SearchContextProvider ↔ Real In-Process Server

**File:** `tests/e2e/test_search_context_provider_e2e.py`  
**Strategy:** Same `httpx.ASGITransport` approach. `SearchContextProvider` gets a `SearchClient` pointed at the transport-backed server. For Phase B (`_search_collection` JSON-RPC calls), `SearchContextProvider` uses its own `httpx.AsyncClient` (`self._http`) pointed at the **FastMCP** server (`archon_search.server.mcp.create_app`), not the FastAPI REST app. There are two distinct `create_app()` functions: `archon_search.server.app.create_app(config, job_store)` (FastAPI REST, used in Suites 1 and 3) and `archon_search.server.mcp.create_app(pipeline, default_collection)` (FastMCP JSON-RPC, used here). Tests must either: (a) stand up both apps via separate `ASGITransport` instances, or (b) replace `provider._http` with an `httpx.AsyncClient` backed by a minimal stub ASGI app that responds to `POST /tools/call` with canned JSON-RPC results. Option (b) is recommended for unit-style tests; option (a) for full integration.

**Fixture note:** `SearchContextProvider` uses two separate HTTP clients internally — `self._search_client` (SearchClient) for Phase A routing, and `self._http` (bare `httpx.AsyncClient`) for Phase B JSON-RPC search calls. Test fixtures must wire both. Recommended approach: inject a pre-configured `SearchClient` with patched `_http`, and separately patch `provider._http` with an ASGI-transport-backed client for the MCP endpoint.

#### 2.1 Happy Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H2.1 | `test_get_pre_context_returns_none_when_no_collections` | No collections registered → `route()` returns `pre_context=None`; `get_pre_context()` returns None |
| H2.2 | `test_get_pre_context_stores_route_response` | After `get_pre_context()`, `_route_response` is set with `pinned_names`, `routable_names` |
| H2.3 | `test_search_and_prepare_returns_none_without_prior_route` | `search_and_prepare()` without calling `get_pre_context()` first → returns None |
| H2.4 | `test_search_and_prepare_tier1_searches_all_routable` | `decomposer_invoked=False` + `selected_collections=None` → searches all routable (up to max_parallel) + all pinned |
| H2.5 | `test_search_and_prepare_tier2_searches_only_pinned` | `decomposer_invoked=True` + `selected_collections=[]` + pinned present → searches only pinned |
| H2.6 | `test_search_and_prepare_tier2_no_pinned_returns_none` | `decomposer_invoked=True` + `selected_collections=[]` + no pinned → returns None |
| H2.7 | `test_search_and_prepare_tier3_filters_hallucinated_collections` | Decomposer selects `["real_col", "ghost_col"]`; `ghost_col` not in routable → only `real_col` searched |
| H2.8 | `test_search_and_prepare_caps_at_max_parallel` | 10 routable collections, `max_parallel=3` → at most 3 searched |
| H2.9 | `test_search_and_prepare_merges_and_ranks_results` | Two collections return results; merged result has highest-scored chunks at top |
| H2.10 | `test_search_and_prepare_normalizes_scores_per_collection` | If one collection returns [1.0, 0.0] and another [0.5], normalization happens per-collection before merge |
| H2.11 | `test_search_and_prepare_formats_rag_context_block` | Returned text starts with `[RAG context — retrieved document chunks:]` and ends with `[End RAG context]` |
| H2.12 | `test_search_and_prepare_returns_chunk_count` | Second element of returned tuple equals number of merged chunks |

#### 2.2 Error / Warning Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| E2.1 | `test_get_pre_context_route_fails_returns_none` | `route()` returns None (server down) → `get_pre_context()` returns None, no exception raised |
| E2.2 | `test_search_and_prepare_one_collection_fails_others_succeed` | One collection returns 500, others return results → skipped; remaining results returned; failure logged at DEBUG |
| E2.3 | `test_search_and_prepare_all_collections_fail_returns_none` | All collections error → returns None |
| E2.4 | `test_search_and_prepare_single_result_score_normalizes_to_half` | Single result per collection → score normalized to 0.5 (zero spread fallback) |
| E2.5 | `test_search_and_prepare_empty_results_returns_none` | All collections return empty lists → returns None (no context to inject) |
| E2.6 | `test_search_and_prepare_malformed_jsonrpc_response_returns_empty` | Server returns valid JSON but wrong shape (no `result.content`) → collection returns `[]`, no crash |

#### 2.3 Config Behaviour

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| C2.1 | `test_get_pre_context_disabled_returns_none_immediately` | `SearchConfig(enabled=False)` → `get_pre_context()` returns None without any HTTP call |
| C2.2 | `test_search_and_prepare_disabled_never_searches` | With `SearchConfig(enabled=False)`, `get_pre_context()` returns None immediately without setting `_route_response`. Since `_route_response` stays None, `search_and_prepare()` returns None at its own guard. The `enabled` flag is enforced indirectly through state, not checked in `search_and_prepare()` itself. |
| C2.3 | `test_top_k_limits_returned_results` | 20 results across 2 collections, `top_k=3` → exactly 3 returned |
| C2.4 | `test_max_parallel_collections_semaphore_limits_concurrency` | With `max_parallel=1`, collections searched sequentially — verify via call order: wrap `_search_collection` with a recorder appending `(collection, 'start'/'end')` timestamps; with `max_parallel=1`, sequence must be `[A-start, A-end, B-start, B-end]` — no interleaving. Timing-based verification is forbidden (CI flakiness). |

#### 2.4 Phase B — Real FastMCP Integration

**Strategy note:** The tests above (2.1–2.3) use a stub ASGI app for Phase B JSON-RPC calls (option b from the suite strategy). At least one test should wire `provider._http` to the *real* FastMCP server via `httpx.ASGITransport` to verify the actual MCP tool dispatch and response parsing. FastMCP exposes an ASGI app via `.get_asgi_app()` or similar — verify the exact method name against the installed FastMCP version before implementing.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H2.13 | `test_search_collection_dispatches_to_real_mcp_tool` | Wire `provider._http` to `httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app.get_asgi_app()))` where `mcp_app = archon_search.server.mcp.create_app(pipeline, default_collection)`. Call `_search_collection("col", "test query")` → real MCP tool dispatch → response parsed into `list[SearchResult]`. Regression guard against MCP response format changes. |
| E2.7 | `test_search_collection_mcp_jsonrpc_error_field_returns_empty` | FastMCP server returns `{"error": {"code": -32600, "message": "invalid"}}` in response → `_search_collection` returns `[]`, no exception raised |

---

### Suite 3 — archon-search Routes (Extended Edge Cases)

**File:** `packages/archon-search/tests/test_routes_e2e.py`  
**Strategy:** FastAPI `TestClient` as in existing route tests. These complement existing `test_routes_*.py` files with scenarios they don't cover.

#### 3.1 /route Endpoint (Complex Routing Scenarios)

> **ID note:** IDs E3.5b and H3.6b in this section use a "b" suffix to avoid collision with E3.5 and H3.6 in section 3.2, which were added later. Implementers should treat each ID as unique despite the similar names.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H3.1 | `test_route_query_returned_in_pre_context` | With configured collections, `/route` returns `pre_context` containing collection metadata |
| H3.2 | `test_route_pinned_collections_always_in_pinned_names` | `pinned_collections=["A"]` → `pinned_names=["A"]` regardless of query |
| H3.3 | `test_route_slots_parameter_respected` | `{"query": "x", "slots": 2}` → `shortlist_size=2` passed to router |
| H3.4 | `test_route_non_ascii_query_accepted` | Query with Unicode (emoji, CJK, RTL) → 200, valid response |
| H3.5 | `test_route_very_long_query_accepted` | 10k character query → 200 (no length limit enforced) |
| E3.1 | `test_route_missing_query_field_returns_422` | `POST /route {}` → 422 Pydantic validation error |
| E3.2 | `test_route_null_query_returns_422` | `POST /route {"query": null}` → 422 |
| E3.3 | `test_route_slots_negative_returns_400` | `{"query": "x", "slots": -1}` → 400 |
| E3.4 | `test_route_slots_zero_returns_400` | `{"query": "x", "slots": 0}` → 400 |
| E3.5b | `test_route_timeout_returns_504` | Inject router that sleeps > 30s → `POST /route` returns 504 with detail "routing timed out" |
| H3.6b | `test_route_confidence_gate_drops_all_returns_empty_routable` | Collections configured but none pass confidence threshold → 200, `pre_context=None`, `routable_names=[]` |

#### 3.2 /ingest + /jobs Lifecycle

> **Strategy note for H3.8:** `test_ingest_cancel_while_running_transitions_to_cancelling` requires an async HTTP client (`httpx.AsyncClient` with `ASGITransport`) because the test must issue a DELETE while a background task is mid-execution. This test cannot use the synchronous `TestClient` from the suite strategy. Implement it with its own async fixture that creates a separate `httpx.AsyncClient(transport=httpx.ASGITransport(app=...))`.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H3.6 | `test_ingest_job_transitions_pending_to_done` | Ingest with stub pipeline, poll job until DONE |
| H3.7 | `test_ingest_job_failure_sets_failed_status` | Ingest with failing pipeline → status=FAILED, error field non-empty |
| H3.8 | `test_ingest_cancel_while_running_transitions_to_cancelling` | Ingest with slow pipeline, cancel while RUNNING → CANCELLING (Requires `httpx.AsyncClient` with `ASGITransport`, not `TestClient` — must issue DELETE while background task is mid-execution. Use a pipeline stub with an `asyncio.Event` that holds execution until the test signals it to proceed.) |
| H3.9 | `test_ingest_cancel_after_done_is_idempotent` | Cancel a DONE job → 200, status unchanged |
| H3.10 | `test_ingest_two_concurrent_jobs_independent` | Two simultaneous `POST /ingest` calls → two distinct job IDs, both tracked independently |
| H3.11 | `test_ingest_ingested_by_header_overrides_body` | `X-Ingested-By: custom-tool` header + body `ingested_by="other"` → header wins |
| E3.5 | `test_ingest_missing_collection_field_returns_422` | `POST /ingest {"path": "/tmp"}` (no `collection`) → 422 |
| E3.6 | `test_get_job_unknown_id_returns_404` | Arbitrary UUID → 404 |
| E3.7 | `test_cancel_job_twice_in_flight_idempotent` | Cancel same CANCELLING job twice → both return 202 |

#### 3.3 /collections Lifecycle

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H3.12 | `test_add_collection_persists_to_config_when_config_path_set` | `config_path` provided → config file updated after add |
| H3.13 | `test_remove_collection_persists_to_config_when_config_path_set` | Config file updated after remove |
| H3.14 | `test_collection_name_derived_from_last_path_component` | Path `/home/user/my-docs` → name `my_docs` (hyphens and all non-alphanumeric chars replaced with `_`) |
| H3.15 | `test_list_collections_includes_both_regular_and_pinned` | One collection in `collections`, one in `pinned_collections` → both appear in `GET /collections` |
| E3.8 | `test_add_collection_path_tilde_expanded` | `path="~/docs"` → resolved to absolute, registered without `~` |
| E3.9 | `test_remove_pinned_only_collection_fails_with_409` | Pinned but not in `collections` → 409 |
| E3.10 | `test_remove_collection_twice_returns_404_second_time` | Delete then delete again → 404 |

#### 3.4 /status and /indexing-state

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H3.16 | `test_status_collections_sorted_alphabetically` | Collections returned in alphabetical order |
| H3.17 | `test_status_shows_not_yet_indexed_for_new_collection` | Freshly registered collection → `status="not_yet_indexed"` |
| H3.18 | `test_indexing_state_fields_filtered` | State only contains `{status, processed_files, total_files, error, error_count, started_at, completed_at}` |
| H3.19 | `test_indexing_state_empty_before_any_ingest` | No prior ingest → `{}` |
| H3.20 | `test_status_pid_is_current_process` | `status.pid == os.getpid()` |

---

### Suite 4 — Pipeline Integration (Archon-side, Mock HTTP)

**File:** `tests/ai/test_pipeline_search_integration.py` (NOT in `tests/integration/` — that directory is for tests requiring a real running server process. Suite 4 mocks HTTP at the httpx level and must be in regular CI.)  
**Strategy:** Real `Pipeline` + real `SearchContextProvider`, but `SearchClient` HTTP calls mocked at `httpx` level. Tests the full Archon-side call chain end-to-end without needing a real server.

#### 4.1 Happy Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H4.1 | `test_pipeline_injects_rag_context_when_search_enabled` | Pipeline.send() with search enabled → `inject_context` called on decomposer with `"search_retrieval"` type |
| H4.2 | `test_pipeline_pre_context_passed_to_route_task` | `route()` returns `pre_context="hint"` → `route_task(prompt, search_pre_context="hint")` called |
| H4.3 | `test_pipeline_tier1_searches_all_routable_collections` | `decomposer_invoked=False` → all routable+pinned collections searched |
| H4.4 | `test_pipeline_tier3_uses_decomposer_selected_collections` | Decomposer output `<search_selected_collections>col1</search_selected_collections>` → only `col1` searched |
| H4.5 | `test_pipeline_rag_detail_string_includes_collection_names` | Detail string in `inject_context` lists searched collection names |

#### 4.2 Disabled / Missing Provider

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| C4.1 | `test_pipeline_no_rag_when_search_disabled` | `SearchConfig(enabled=False)` → `inject_context` never called |
| C4.2 | `test_pipeline_no_rag_when_search_provider_is_none` | `Pipeline` created without `search_url` → `_search_provider=None` → no search |
| C4.3 | `test_pipeline_completes_normally_when_route_fails` | `route()` returns None → Pipeline continues, response delivered without RAG |
| C4.4 | `test_pipeline_completes_normally_when_all_searches_fail` | All per-collection searches fail → Pipeline continues, no context injected |

#### 4.3 Warning Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| W4.1 | `test_pipeline_logs_warning_on_phase_a_exception` | Exception in `get_pre_context()` → WARNING logged with "RAG get_pre_context failed" |
| W4.2 | `test_pipeline_logs_warning_on_phase_b_exception` | Exception in `search_and_prepare()` → WARNING logged with "RAG search_and_prepare failed" |
| W4.3 | `test_pipeline_logs_debug_on_search_client_connect_error` | `ConnectError` in `SearchClient.route()` → DEBUG log (expected when server down), not WARNING |
| W4.4 | `test_pipeline_logs_warning_on_route_500_response` | `/route` returns 500 → WARNING log with status code |

---

### Suite 5 — IndexingNotificationMonitor

**File:** `tests/gateway/test_notification_monitor_e2e.py`  
**Strategy:** Real `IndexingNotificationMonitor` with mocked Telegram bot. `SearchClient` HTTP calls mocked via `patch.object`.

#### 5.1 Happy Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H5.1 | `test_monitor_sends_notification_when_all_done` | All collections reach `DONE` → Telegram notification sent once (fixture must include `trigger="install"` in state) |
| H5.2 | `test_monitor_sends_notification_when_some_failed` | Mix of DONE and FAILED → notification sent with FAILED summary |
| H5.3 | `test_monitor_respects_quiet_mode_suppression` | Quiet mode → notification not sent |
| H5.4 | `test_monitor_ignores_manual_trigger` | `trigger="manual"` → monitor exits without sending notification |
| H5.5 | `test_monitor_waits_for_all_collections_to_finish` | One collection DONE, one still RUNNING → monitor keeps polling |
| H5.6 | `test_monitor_stops_polling_on_terminal_state` | All DONE → monitor sends notification and stops (fixture must include `trigger="install"` or `"update"` in state) |
| H5.7 | `test_monitor_fires_on_install_trigger` | `trigger="install"` in state → notification sent after terminal state |
| H5.8 | `test_monitor_fires_on_update_trigger` | `trigger="update"` in state → notification sent after terminal state |

#### 5.2 Error Paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| E5.1 | `test_monitor_handles_indexing_state_returning_none` | `SearchClient.indexing_state()` returns None → monitor logs and retries |
| E5.2 | `test_monitor_handles_indexing_state_returning_empty_dict` | State = `{}` → no collections terminal → monitor keeps polling |
| E5.3 | `test_monitor_handles_server_going_down_mid_poll` | First call succeeds, subsequent calls return None → monitor continues polling without crashing |
| E5.4 | `test_monitor_ignores_missing_trigger_field` | State dict has no `"trigger"` key → monitor does not send notification, no crash |
| E5.5 | `test_monitor_ignores_unknown_trigger_value` | `trigger="sync"` → monitor does not send notification |

---

### Suite 6 — archon-search CLI and MCP Tools (Archon-side)

**File:** `tests/ai/test_archon_toolkit_search_e2e.py`  
**Strategy:** Real `ArchonToolkit` + `_register_search_tools`, with `SearchClient` HTTP mocked.

#### 6.1 search_status Tool

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H6.1 | `test_search_status_enabled_server_running` | `enabled=True`, server returns valid status → formatted status string |
| H6.2 | `test_search_status_enabled_server_down` | Server unreachable → `client.status()` returns None → tool returns JSON string with `running=false` and `error="service unavailable"`. Assert the returned string deserializes to `{"running": false, "pid": null, "collections": [], "error": "service unavailable"}` (or similar). |
| H6.3 | `test_search_status_disabled` | **Note: requires code change first.** The current `_handle_rag_status` implementation does NOT check `cfg.enabled` — it always contacts the server. This test is only implementable if an `enabled` guard is added to `_handle_rag_status`. Until then, replace with: `enabled=False`, server running → tool still returns server status (no "disabled" short-circuit). |

#### 6.2 search_ingest Tool

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H6.4 | `test_search_ingest_returns_job_id` | `ingest()` succeeds → tool returns job_id in message |
| E6.1 | `test_search_ingest_server_down_returns_error` | `ingest()` returns None → tool returns error message, does not raise |

#### 6.3 search_collection_add / remove / list / info

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H6.5 | `test_search_collection_add_success` | `add_collection()` → success message with collection name |
| H6.6 | `test_search_collection_remove_success` | `remove_collection()` → success message |
| H6.7 | `test_search_collection_list_empty` | `list_collections()` returns `[]` → "no collections" message |
| H6.8 | `test_search_collection_list_with_entries` | Returns list → formatted table of collections |
| H6.9 | `test_search_collection_info_returns_details` | `collection_info()` → metadata fields present in output |
| E6.2 | `test_search_collection_add_failure_returns_error` | `add_collection()` returns None → error message |
| E6.3 | `test_search_collection_remove_failure_returns_error` | `remove_collection()` returns None → error message |

#### 6.4 search_start / search_stop / search_sync Tools

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H6.10 | `test_search_start_returns_cli_redirect_message` | `search_start` tool → returns message directing user to run `archon search start` CLI |
| H6.11 | `test_search_stop_returns_cli_redirect_message` | `search_stop` tool → returns message directing user to run `archon search stop` CLI |
| H6.12 | `test_search_sync_returns_not_supported_message` | `search_sync` tool → returns "not supported" or equivalent static message |

#### 6.5 search_collection_reindex Tool

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H6.13 | `test_search_collection_reindex_success` | `reindex_collection("col")` returns IngestJob → tool returns job_id and status in message |
| E6.4 | `test_search_collection_reindex_server_down_returns_error` | `reindex_collection("col")` returns None → error message, does not raise |

---

### Suite 7 — Cross-Cutting / Complex Scenarios

**File:** `tests/e2e/test_search_complex_scenarios.py`  
**Strategy:** Mix of real in-process FastAPI (via `httpx.ASGITransport`) and mocked ML models.

#### 7.1 Multi-Collection Concurrency

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| X7.1 | `test_routable_cap_limits_search_to_max_parallel` | With 10 registered routable collections, **zero pinned collections**, and `max_parallel=3` → exactly 3 collections searched. Fixture must explicitly set `pinned_names=[]`; tier 1 adds ALL pinned names before applying the routable cap, so any pinned collections would cause more than 3 to be searched. |
| X7.2 | `test_concurrent_route_requests_dont_interfere` | 5 simultaneous `POST /route` calls with different queries → each returns its own independent response |
| X7.3 | `test_concurrent_ingest_jobs_tracked_independently` | 5 simultaneous `POST /ingest` → 5 distinct job IDs, each retrievable independently |

#### 7.2 Score Normalization Edge Cases

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| X7.4 | `test_single_result_per_collection_normalizes_to_0_5` | One result from each collection → all scores become 0.5 (zero spread fallback) |
| X7.5 | `test_all_same_score_normalizes_to_0_5` | 5 results all with score=0.8 → all normalized to 0.5 |
| X7.6 | `test_top_k_merge_across_3_collections` | 3 collections × 10 results each = 30 total; `top_k=5` → exactly 5 returned, highest normalized scores |
| X7.7 | `test_pinned_collection_always_searched_tier1` | Verify that pinned collections are always in `to_search` at tier 1 regardless of routing score. Set up: 2 collections, 1 pinned + 1 routable; both have results; assert both are searched. **This test verifies search inclusion, not final output.** For final output inclusion, pinned results must win the `top_k` merge on their own normalized scores — there is no unconditional preservation. Set `top_k` large enough to include all results. |

#### 7.3 Job State Machine Edge Cases

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| X7.8 | `test_job_crash_recovery_transitions_running_to_failed` | Simulate crash by writing RUNNING job to job store then reloading store → job becomes FAILED with `error="process_restart"`. **Note:** This tests JobStore-level crash recovery only. `SearchCollectionSync._reset_stale_in_progress()` (which resets `IN_PROGRESS` → `PENDING` in `IndexingStateStore`) is a separate recovery path — see Suite 15 or `test_sync.py` for that coverage. |
| X7.9 | `test_job_eviction_removes_old_jobs` | Write a job with `updated_at` 8 days ago → evicted on next write, not returned by `GET /jobs/{id}` |
| X7.10 | `test_cancel_race_condition_is_idempotent` | Job transitions to DONE while cancel DELETE is in flight → both return 200 |

#### 7.4 Config Persistence

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| X7.11 | `test_add_collection_writes_config_file` | `config_path` set → TOML file updated with new collection |
| X7.12 | `test_remove_collection_removes_from_config_file` | After remove, TOML file no longer lists collection |
| X7.13 | `test_config_not_written_when_config_path_is_none` | `config_path=None` → no file I/O on add/remove |

#### 7.5 Large Payload / Stress

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| X7.14 | `test_route_100_collections_registered` | 100 collections configured; `/route` responds within 2 seconds (latency smoke test) |
| X7.15 | `test_ingest_1000_documents_in_one_request` | `POST /ingest {"documents": [{...} × 1000]}` → accepted, job created, no timeout |
| X7.16 | `test_indexing_state_with_50_collections` | State with 50 collections → `/indexing-state` returns all, response < 100ms |

---

### Suite 8 — archon doctor Search Checks

**Note:** `_check_search_server()` and `_check_search_health()` are tested in the existing `tests/cli/test_doctor.py`. Suite 8 and Suite 9 sections 9.10/9.11 should only add scenarios not already covered there. Before implementing, cross-reference `test_doctor.py` to avoid duplication.

**File:** `tests/cli/test_doctor_search.py`  
**Strategy:** Real `run_checks()` with mocked `SearchClient` HTTP calls.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| H8.1 | `test_doctor_shows_disabled_when_search_disabled` | `enabled=False` → "disabled" message, no network call |
| H8.2 | `test_doctor_shows_ok_when_server_healthy` | `enabled=True`, `/health` returns `{"status": "running"}` → check passes |
| H8.3 | `test_doctor_shows_warning_when_server_unreachable` | `/health` returns None (ConnectError) → check fails with connection message |
| H8.4 | `test_doctor_shows_partial_for_in_progress_collections` | Collection in `IN_PROGRESS` state → "⏳ partial (N/M files)", no false warning |
| H8.5 | `test_doctor_shows_error_for_failed_collections` | Collection in `FAILED` state → "❌" error shown |
| H8.6 | `test_doctor_suppresses_pending_state_as_informational` | `PENDING` collection → treated as in-progress, no alarm |

---

### Suite 9 — `archon search` CLI (Gap Coverage)

**File:** `tests/cli/test_search_cmd.py` (additions to the existing file)  
**Strategy:** Same pattern as existing tests — real `run_search()` / `_run_*()` functions with `SearchClient` HTTP calls mocked via `patch.object`, subprocess calls mocked via `patch("subprocess.run")`, output asserted via `capsys`.

**Context:** The existing ~60 tests cover most subcommands well. The gaps are:
- `status` — progress table rendering, ETA computation, failed/partial collection display
- `doctor` — `_check_search_server()` and `_check_search_health()` may already have tests in `tests/cli/test_doctor.py`; cross-reference before adding
- Helper functions — `compute_eta_seconds()`, `_print_progress_table()`, `_path_to_collection_name()` have no direct unit tests

#### 9.1 `archon search status` — Progress Table (currently missing)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.1 | `test_status_shows_all_done_collection` | Collection with `status=DONE` → row shows `done` label |
| S9.2 | `test_status_shows_in_progress_with_fraction` | Collection with `status=IN_PROGRESS`, `processed=3`, `total=10` → `partial/done 3/10` |
| S9.3 | `test_status_shows_failed_collection_with_error_message` | Collection with `status=FAILED`, `error="oom"` → error text in output, exit code 1 |
| S9.4 | `test_status_shows_pending_collection` | `status=PENDING` → `—` in output, no crash |
| S9.5 | `test_status_shows_eta_when_available` | Collection in-progress with ETA computable → `eta Xm` shown |
| S9.6 | `test_status_multiple_collections_all_shown` | 3 collections with different statuses → all 3 rows appear in output |
| S9.7 | `test_status_shows_pid_from_service` | `status()` returns `{"pid": 9999}` → `pid=9999` in output |
| S9.8 | `test_status_shows_stopped_when_status_returns_none` | `SearchClient.status()` returns None → "stopped (unreachable)", exit code 1 |
| S9.9 | `test_status_shows_stopped_when_indexing_state_returns_none` | `status()` succeeds but `indexing_state()` returns None → graceful degradation, no crash |
| S9.10 | `test_status_exit_code_0_when_all_healthy` | All collections DONE, server running → exit code 0 |
| S9.11 | `test_status_exit_code_1_when_any_failed` | Any collection FAILED → exit code 1 |

#### 9.2 `compute_eta_seconds()` — Unit Tests

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.12 | `test_compute_eta_returns_none_when_not_in_progress` | `status != IN_PROGRESS` → None |
| S9.13 | `test_compute_eta_returns_none_when_total_is_zero` | `total_files=0` → None (divide-by-zero guard) |
| S9.14 | `test_compute_eta_returns_none_when_fewer_than_10_processed` | `processed_files=5` → None (not enough data) |
| S9.15 | `test_compute_eta_returns_none_when_started_at_missing` | No `started_at` → None |
| S9.16 | `test_compute_eta_computes_correct_seconds` | 100 files total, 50 processed, started 60s ago → ~60s remaining |
| S9.17 | `test_compute_eta_returns_none_when_all_done` | `processed == total` → None (already complete) |
| S9.18 | `test_compute_eta_handles_started_at_as_isoformat_string` | `started_at` as ISO 8601 string → parsed and used correctly |

#### 9.3 `_path_to_collection_name()` — Unit Tests

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.19 | `test_path_to_collection_name_simple` | `/home/user/my-docs` → `my_docs` |
| S9.20 | `test_path_to_collection_name_already_clean` | `/data/history` → `history` |
| S9.21 | `test_path_to_collection_name_trailing_slash` | `/data/docs/` → `docs` |
| S9.22 | `test_path_to_collection_name_special_chars` | `/data/my project (2024)` → `my_project_2024` |
| S9.23 | `test_path_to_collection_name_all_special_falls_back` | `!!!` → `collection` (fallback) |
| S9.24 | `test_path_to_collection_name_uppercase_lowercased` | `/data/MyDocs` → `mydocs` |

#### 9.4 `_print_progress_table()` — Unit Tests

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.25 | `test_print_progress_table_empty_state_prints_nothing` | No collections in state → no output |
| S9.26 | `test_print_progress_table_returns_false_when_no_failures` | All DONE → returns False |
| S9.27 | `test_print_progress_table_returns_true_when_any_failed` | One FAILED → returns True |
| S9.28 | `test_print_progress_table_shows_all_configured_collections` | 3 configured collections, 2 have state → all 3 shown (not-yet-indexed for third) |
| S9.29 | `test_print_progress_table_not_yet_indexed_shows_dash` | Collection in config but no state entry → `—` or `not_yet_indexed` |

#### 9.5 `archon search status` — Edge Cases

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.30 | `test_status_collection_with_zero_total_files` | `total_files=0` in state → no division-by-zero crash |
| S9.31 | `test_status_collection_with_null_error_field` | `error=null` in state → no crash, no error text shown |
| S9.32 | `test_status_collection_name_truncated_if_very_long` | 80-char collection name → output fits terminal width (no wrapping assertion, just no crash) |

#### 9.6 `archon search collection remove` — Edge Cases (currently missing)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.33 | `test_collection_remove_path_with_trailing_slash_normalised` | `archon search collection remove /data/docs/` → same as `/data/docs` |
| S9.34 | `test_collection_remove_symlink_path_resolved` | Symlink to registered path → resolved correctly, remove proceeds |
| S9.35 | `test_collection_remove_dry_run_shows_pinned_note` | Path also in `pinned_collections` → dry-run output mentions pinned status. **Note: requires code change first.** The current dry-run path does NOT check `pinned_collections` — it only prints "Would remove config entry" and "Would drop LanceDB table". This test is only implementable after adding pinned detection to the dry-run path. Until then, demote to P3/exploratory. |

#### 9.7 `archon search collection add` — Edge Cases (currently missing)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.36 | `test_collection_add_path_already_absolute_not_double_resolved` | `/absolute/path` → used as-is, not double-expanded |
| S9.37 | `test_collection_add_server_returns_409_prints_already_registered` | `add_collection()` returns None (409 from server) → "already registered" in output |

#### 9.8 `archon search install` / `uninstall` — Error Paths (currently missing)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.38 | `test_install_subprocess_not_found_prints_error` | `archon-search` not in PATH → `FileNotFoundError` → error message, exit 1 |
| S9.39 | `test_install_subprocess_nonzero_exit_propagates` | subprocess returns exit 2 → `run_search()` returns 2 |
| S9.40 | `test_uninstall_delete_db_flag_passed_to_subprocess` | `--delete-db` → `["archon-search", "uninstall", "--delete-db"]` in subprocess call |

#### 9.9 `archon search start` / `stop` — Error Paths (currently missing)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.41 | `test_start_nonzero_exit_prints_error_message` | subprocess returns 1 → `"Search service start failed (exit code 1)."` |
| S9.42 | `test_stop_nonzero_exit_prints_error_message` | subprocess returns 1 → `"Search service stop failed (exit code 1)."` |
| S9.43 | `test_start_archon_search_not_found_prints_error` | `FileNotFoundError` → error message, exit 1 |

#### 9.10 `archon doctor` — `_check_search_server()` (cross-reference `tests/cli/test_doctor.py` before adding; only add scenarios not already covered there)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.44 | `test_check_search_server_disabled_returns_ok_disabled` | `enabled=False` → `CheckResult(ok=True, detail="disabled")` |
| S9.45 | `test_check_search_server_lancedb_not_installed_returns_fail` | `lancedb` not importable → `CheckResult(ok=False, detail contains "not installed")` |
| S9.46 | `test_check_search_server_socket_connects_returns_running` | Socket connects to `host:port` → `CheckResult(ok=True, detail="running")` |
| S9.47 | `test_check_search_server_socket_refused_returns_fail` | Connection refused → `CheckResult(ok=False, detail contains "not running")` |

#### 9.11 `archon doctor` — `_check_search_health()` (cross-reference `tests/cli/test_doctor.py` before adding; only add scenarios not already covered there)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S9.48 | `test_check_search_health_skipped_when_disabled` | `enabled=False` → function returns immediately, no HTTP call |
| S9.49 | `test_check_search_health_ok_when_all_done` | All collections DONE, non-stale → no warnings printed |
| S9.50 | `test_check_search_health_warns_stale_collection` | Collection `completed_at` > 7 days ago → staleness warning printed |
| S9.51 | `test_check_search_health_warns_empty_collection` | `doc_count=0` after indexing → empty warning printed |
| S9.52 | `test_check_search_health_shows_in_progress_without_warning` | Collection still `IN_PROGRESS` → progress shown, no alarm |
| S9.53 | `test_check_search_health_shows_pending_without_warning` | Collection `PENDING` → informational only |
| S9.54 | `test_check_search_health_shows_failed_as_error` | Collection `FAILED` → error shown |
| S9.55 | `test_check_search_health_health_endpoint_returns_none` | `health()` returns None → graceful, no crash |
| S9.56 | `test_check_search_health_indexing_state_returns_none` | `indexing_state()` returns None → graceful, no crash |

---

### Suite 10 — SearchClient & SearchContextProvider Gap Coverage

**File:** `tests/ai/test_search_client.py` and `tests/ai/test_search_context_provider.py`  
**Strategy:** Same `patch.object(client._http, ...)` pattern as existing tests.

#### 10.1 SearchClient — Untested error branches on non-route methods

The existing tests cover `route()` error paths exhaustively but leave `health()`, `status()`, `indexing_state()`, `ingest()`, `get_job()`, `cancel_job()`, `list_collections()`, `add_collection()`, `remove_collection()`, `collection_info()`, and `reindex_collection()` with zero error path coverage.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| A10.1 | `test_health_timeout_returns_none_logs_warning` | `TimeoutException` → None + WARNING |
| A10.2 | `test_health_connect_error_returns_none_logs_debug` | `ConnectError` → None + DEBUG (not WARNING) |
| A10.3 | `test_health_5xx_returns_none_logs_warning` | `HTTPStatusError` 500 → None + WARNING |
| A10.4 | `test_status_timeout_returns_none` | `TimeoutException` → None |
| A10.5 | `test_status_connect_error_returns_none_logs_debug` | `ConnectError` → None + DEBUG |
| A10.6 | `test_indexing_state_timeout_returns_none` | `TimeoutException` → None |
| A10.7 | `test_indexing_state_connect_error_returns_none_logs_debug` | `ConnectError` → None + DEBUG |
| A10.8 | `test_ingest_timeout_returns_none` | `TimeoutException` → None |
| A10.9 | `test_ingest_connect_error_returns_none_logs_debug` | `ConnectError` → None + DEBUG |
| A10.10 | `test_ingest_path_none_omitted_from_payload` | `path=None` → `"path"` key absent from POST body |
| A10.11 | `test_ingest_documents_none_omitted_from_payload` | `documents=None` → `"documents"` key absent from POST body |
| A10.12 | `test_get_job_timeout_returns_none` | `TimeoutException` → None |
| A10.13 | `test_cancel_job_connect_error_returns_503` | `ConnectError` → 503 + DEBUG |
| A10.14 | `test_cancel_job_4xx_returns_status_code_no_warning` | 404 → returns 404, no WARNING logged |
| A10.15 | `test_list_collections_timeout_returns_empty_list` | `TimeoutException` → `[]` + WARNING |
| A10.16 | `test_list_collections_connect_error_returns_empty_list_logs_debug` | `ConnectError` → `[]` + DEBUG |
| A10.17 | `test_add_collection_timeout_returns_none` | `TimeoutException` → None |
| A10.18 | `test_remove_collection_timeout_returns_none` | `TimeoutException` → None |
| A10.19 | `test_collection_info_timeout_returns_none` | `TimeoutException` → None |
| A10.20 | `test_reindex_collection_timeout_returns_none` | `TimeoutException` → None |
| A10.21 | `test_route_slots_none_omits_slots_from_payload` | `route(query, slots=None)` → payload has no `"slots"` key |
| A10.21b | `test_route_response_all_fields_mapped_from_server_json` | POST a RouteRequest to a real in-process FastAPI app (via ASGITransport) with configured collections. Verify `SearchClient.route()` returns a `RouteResponse` with all four fields correctly populated: `pre_context` (non-None string), `pinned_names` (list), `routable_names` (non-empty list), `decomposer_invoked` (bool). Regression guard against the manual `data.get(...)` field extraction silently returning defaults on schema change. |
| A10.22 | `test_base_url_trailing_slash_normalized` | `SearchClient("http://x:8765/")` → requests go to `http://x:8765/route` not `http://x:8765//route` |
| A10.23 | `test_close_calls_http_aclose` | `await client.close()` → `_http.aclose()` called |
| A10.24 | `test_context_manager_calls_close_on_exit` | `async with SearchClient(...) as c:` → `close()` called on exit |
| A10.25 | `test_reset_search_client_closes_and_clears_singleton` | After `reset_search_client()`, singleton is None; close was awaited |
| A10.26 | `test_reset_search_client_noop_when_none` | `reset_search_client()` with no singleton → no crash |
| A10.27b | `test_base_url_with_path_prefix_limitation` | **Note:** ID A10.27b avoids collision with A10.27 (SearchContextProvider section). These are different tests. `SearchClient("http://proxy/archon-search/")` — the `__init__` calls `rstrip("/")` which strips the required trailing slash, causing httpx to resolve `"/route"` against `"http://proxy/archon-search"` (no trailing slash) and produce `"http://proxy/route"` instead of `"http://proxy/archon-search/route"`. This is a known limitation for reverse-proxy deployments with path prefixes. Test verifies the actual (broken) behavior as a regression guard. |

#### 10.2 SearchContextProvider — Untested branches

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| A10.27 | `test_get_pre_context_uses_singleton_when_client_not_provided` | `SearchContextProvider(url, cfg, search_client=None)` → calls `get_search_client()` internally |
| A10.28 | `test_context_manager_closes_http_client` | `async with SearchContextProvider(...)` → `_http.aclose()` called on exit |
| A10.29 | `test_get_pre_context_logs_timing_at_debug` | Successful route → DEBUG log contains elapsed ms |
| A10.30 | `test_search_and_prepare_decomposer_invoked_none_remaps_to_empty` | `decomposer_invoked=True` + `selected_collections=None` → treated as `[]`, searches pinned only |
| A10.31 | `test_search_and_prepare_pinned_count_exceeds_max_parallel` | 5 pinned, `max_parallel=3` → `routable_cap = max(0, 3-5) = 0`; all 5 pinned collections are searched (none routable), with semaphore limiting concurrency to 3 at a time. Assert that all 5 pinned are searched (not just 3). |
| A10.32 | `test_search_collection_non_text_content_block_returns_empty` | Response has `{"type": "image", ...}` block → returns `[]` |
| A10.33 | `test_search_collection_top_k_less_than_results_truncates` | 10 results returned, `top_k=3` → 3 returned |
| A10.34 | `test_search_collection_top_k_more_than_results_returns_all` | 3 results, `top_k=10` → all 3 returned |
| A10.35 | `test_normalize_and_merge_empty_input_returns_empty` | `per_collection={}` → `[]` |
| A10.36 | `test_normalize_and_merge_all_empty_lists_returns_empty` | All collections have `[]` results → `[]` |
| A10.37 | `test_format_results_single_result` | One result → correct header, body, footer |
| A10.38 | `test_format_results_three_results_numbered_correctly` | Results numbered 1, 2, 3 in output |
| A10.39 | `test_search_and_prepare_all_collections_raise_returns_none` | All per-collection tasks raise `ValueError` → returns None, each failure logged DEBUG |

#### 10.3 `get_search_client()` Singleton — Untested Branches

**File:** `tests/ai/test_search_client.py`

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| A10.40 | `test_get_search_client_creates_instance_from_config_url` | `config.search.url = "http://127.0.0.1:8765"`, singleton is None → `get_search_client()` constructs and returns a `SearchClient` with `base_url` matching config |
| A10.41 | `test_get_search_client_returns_same_instance_on_repeat_call` | Call `get_search_client()` twice without reset → both calls return the exact same object (`is` check) |
| A10.42 | `test_get_search_client_returns_new_instance_after_reset` | `get_search_client()` then `reset_search_client()` then `get_search_client()` → third call returns a new object (different `id()`) |
| A10.43 | `test_get_search_client_returns_none_when_search_disabled` | `config.search.enabled = False` → `get_search_client()` returns None without constructing a client |

---

### Suite 11 — Search Configuration (Archon-side + archon-search side)

**Files:** `tests/config/test_config_search.py` (new), `packages/archon-search/tests/test_config.py` (new)

#### 11.1 Archon-side `[search]` config — type coercion and boundary errors

**Note:** The Archon-side `SearchConfig` has only 4 fields: `url`, `enabled`, `max_parallel_collections`, `top_k_return`. Tests C11.4 (`max_parallel_collections`) and C11.6 (`top_k_return`) are valid. Tests C11.1–C11.3 (`port`), C11.5 (`top_k_retrieve`), C11.7–C11.12 (`routing_confidence_threshold`, `routing_shortlist_size`, `sync_timeout_seconds`, `chunk_size`) reference server-side config fields that do NOT exist in Archon's `SearchConfig`. These tests must either be (a) moved to Suite 11.2 (archon-search config), or (b) removed. Do not implement C11.1–C11.3 and C11.5, C11.7–C11.12 against the Archon-side config class.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| C11.1 | `test_search_port_boundary_1_accepted` | `port=1` → valid, no ConfigError |
| C11.2 | `test_search_port_boundary_65535_accepted` | `port=65535` → valid |
| C11.3 | `test_search_port_non_integer_raises` | `port="abc"` → ConfigError |
| C11.4 | `test_search_max_parallel_collections_non_integer_raises` | `max_parallel_collections="many"` → ConfigError |
| C11.5 | `test_search_top_k_retrieve_non_integer_raises` | `top_k_retrieve="all"` → ConfigError |
| C11.6 | `test_search_top_k_return_non_integer_raises` | `top_k_return="few"` → ConfigError |
| C11.7 | `test_search_routing_confidence_threshold_boundary_0_accepted` | `routing_confidence_threshold=0.0` → valid |
| C11.8 | `test_search_routing_confidence_threshold_boundary_1_accepted` | `routing_confidence_threshold=1.0` → valid |
| C11.9 | `test_search_routing_confidence_threshold_non_float_raises` | `routing_confidence_threshold="high"` → ConfigError |
| C11.10 | `test_search_routing_shortlist_size_non_integer_raises` | `routing_shortlist_size="all"` → ConfigError |
| C11.11 | `test_search_sync_timeout_negative_raises` | `sync_timeout_seconds=-1` → ConfigError |
| C11.12 | `test_search_chunk_size_non_integer_raises` | `chunk_size="large"` → ConfigError |

#### 11.2 archon-search `config.py` — zero tests currently

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| C11.13 | `test_archon_search_config_defaults_are_valid` | `SearchConfig()` with no args → all defaults pass validation |
| C11.14 | `test_archon_search_load_config_missing_file_returns_defaults` | Non-existent path → returns `SearchConfig()` defaults |
| C11.15 | `test_archon_search_load_config_malformed_toml_raises` | Invalid TOML syntax → raises `ConfigError` (wrapping the underlying TOML parse error as `__cause__`) |
| C11.16 | `test_archon_search_load_config_all_sections_loaded` | TOML with `[server]`, `[database]`, `[routing]`, `[collections]` → all fields populated |
| C11.17 | `test_archon_search_save_config_round_trip` | `load → modify → save → reload` → values preserved |
| C11.18 | `test_archon_search_save_config_creates_file_when_absent` | Save to nonexistent path → file created |
| C11.19 | `test_archon_search_port_range_invalid_raises` | `port=0` or `port=65536` → ConfigError |
| C11.20 | `test_archon_search_chunk_size_must_be_positive` | `chunk_size=0` → ConfigError |
| C11.21 | `test_archon_search_routing_shortlist_size_must_be_positive` | `routing_shortlist_size=0` → ConfigError |
| C11.22 | `test_archon_search_routing_confidence_threshold_range` | `0.0` and `1.0` valid; `-0.1` and `1.1` raise ConfigError |

---

### Suite 12 — NotificationMonitor & ArchonToolkitSearch Gap Coverage

#### 12.1 IndexingNotificationMonitor — missing branches

**File:** `tests/gateway/test_notification_monitor.py` (additions)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| M12.1 | `test_run_loop_polls_at_configured_interval` | `run()` sleeps `poll_interval` between cycles; verified via `asyncio.sleep` mock call count |
| M12.2 | `test_run_loop_cancelled_error_propagates` | `asyncio.CancelledError` raised inside loop → propagates out of `run()`, not swallowed |
| M12.3 | `test_run_loop_unexpected_exception_logged_and_continues` | Non-CancelledError exception in `_check_and_notify()` → logged at ERROR, loop continues |
| M12.4 | `test_send_to_all_partial_failure_still_sends_to_others` | 3 user_ids, first raises → other 2 receive message; SUCCESS count = 2 |
| M12.5 | `test_send_to_all_uses_parse_mode_html` | `bot.send_message()` always called with `parse_mode="HTML"` |
| M12.6 | `test_check_and_notify_empty_collections_dict_does_not_notify` | `indexing_state()` returns `{"collections": {}}` → no notification sent |
| M12.7 | `test_check_and_notify_mixed_done_and_failed_sends_warning_message` | Some DONE, some FAILED → notification sent with failure count |

#### 12.2 ArchonToolkitSearch — missing error paths

**File:** `tests/ai/test_archon_toolkit_search.py` (additions)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| M12.8 | `test_search_ingest_default_path_derived_from_history_config` | `path=None` + config present → `client.ingest()` called with `<history.dir>/sessions` |
| M12.9 | `test_search_ingest_config_unavailable_returns_error_message` | Config accessor raises → returns "Configuration not available." message |
| M12.10 | `test_search_collection_reindex_error_includes_collection_name` | `reindex_collection("myc")` returns None → error string contains `"myc"` |
| M12.11 | `test_search_collection_list_returns_empty_list_returns_empty_json` | `list_collections()` returns `[]` (server down) → `_handle_rag_collection_list` calls `json.dumps([])` → tool returns valid empty JSON array string `"[]"`, no crash. **Note:** `list_collections()` never returns None — it always returns `[]` on any error. |
| M12.12 | `test_search_collection_info_result_is_valid_json` | Success path → returned string is valid JSON with expected keys |

#### 12.3 `_needs_install_trigger()` — MCP server startup logic

**File:** `packages/archon-search/tests/test_mcp.py` (new)  
**Strategy:** Unit tests for `_needs_install_trigger()` at `archon_search/server/mcp.py`. This function determines whether an `install` trigger fires when the MCP server starts — a bug here silently prevents `IndexingNotificationMonitor` from firing or causes spurious notifications.

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| M12.13 | `test_needs_install_trigger_true_when_no_prior_state` | `IndexingStateStore` has no state file → returns `True` (fresh install) |
| M12.14 | `test_needs_install_trigger_true_when_new_collection_added` | State exists but a new collection in config is absent from state → returns `True` |
| M12.15 | `test_needs_install_trigger_false_when_all_collections_done` | All configured collections at DONE in state → returns `False` |
| M12.16 | `test_needs_install_trigger_false_when_collection_in_progress` | A collection is IN_PROGRESS → returns `False` (ingest is already running) |

---

### Suite 13 — archon-search Job Store, State Store & Watcher Gap Coverage

#### 13.1 JobStore — missing branches

**File:** `packages/archon-search/tests/test_store.py` or new `test_job_store.py`

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| J13.1 | `test_load_returns_false_when_no_file` | New store with no JSON file → `_load()` returns False, `_jobs` empty |
| J13.2 | `test_load_recovers_on_json_decode_error` | Corrupt JSON file → store initializes empty, logs warning |
| J13.3 | `test_load_recovers_on_key_error` | Valid JSON but missing required key → store initializes empty |
| J13.4 | `test_load_recovers_on_type_error` | Valid JSON with wrong type (list instead of dict) → store initializes empty |
| J13.5 | `test_crash_recovery_overwrites_existing_error_field` | Job with `status=RUNNING` and `error="prior"` → after reload, error becomes `"process_restart"` |
| J13.6 | `test_eviction_exact_7_day_boundary` | Job `updated_at` 7 days + 1 second ago → evicted (boundary is exclusive: strictly older than 7 days) |
| J13.7 | `test_eviction_one_second_before_cutoff_not_evicted` | Job `updated_at` exactly 7 days ago → NOT evicted (boundary is exclusive: `updated_at < cutoff` uses strict less-than) |
| J13.8 | `test_eviction_malformed_timestamp_handled` | Job with `updated_at="not-a-date"` → no crash, job removed or retained gracefully |
| J13.9 | `test_transition_returns_none_when_status_not_in_from_statuses` | Job is DONE, `transition(from_statuses={RUNNING})` → returns None, job unchanged |
| J13.10 | `test_transition_atomicity_concurrent_update` | Sequential double-transition is rejected — call `transition(job_id, {PENDING}, RUNNING)` twice sequentially; second call returns None and job remains RUNNING (state machine rejects stale transitions). Note: concurrent interleaving is not testable in asyncio without threading. |
| J13.11 | `test_write_atomic_temp_file_cleaned_up_on_rename_failure` | Simulate `os.replace()` failure → `.tmp` file not left on disk |

#### 13.2 IndexingStateStore — missing branches

**File:** `packages/archon-search/tests/test_progress.py` (additions)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| J13.12 | `test_read_returns_none_on_permission_error` | `PermissionError` reading state file → returns None, logs warning |
| J13.13 | `test_read_returns_none_on_is_a_directory_error` | State path is a directory → returns None, no crash |
| J13.14 | `test_write_cleans_up_temp_file_on_rename_failure` | `os.replace()` raises → `.tmp` file unlinked; original exception re-raised |
| J13.15 | `test_remove_collection_noop_when_state_is_none` | State file absent → `remove_collection()` does not crash, does not write |
| J13.16 | `test_update_collection_does_not_lose_other_collections` | Update collection A → collection B state unchanged |
| J13.17 | `test_compute_eta_fps_zero_does_not_raise` | `processed_files > 0`, `elapsed=0` → returns None, no ZeroDivisionError |
| J13.18 | `test_compute_eta_timezone_offset_handled` | `started_at` with UTC+05:00 offset → ETA computed without crash |
| J13.19 | `test_from_dict_boolean_in_file_mtimes_silently_defaulted` | `file_mtimes: {"file.md": true}` → boolean values fail the `isinstance(v, (float, int)) and not isinstance(v, bool)` check; `file_mtimes` is silently set to `{}` (no error raised, no crash). Assert `result.file_mtimes == {}`. |

#### 13.3 archon-search Watcher — missing branches

**File:** `packages/archon-search/tests/test_watcher.py` (additions)

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| J13.20 | `test_collection_watcher_start_schedule_raises_os_error` | `observer.schedule()` raises `OSError` → logged at WARNING, no crash, observer not stored |
| J13.21 | `test_collection_watcher_stop_join_raises_os_error` | `observer.join()` raises `OSError` → logged at WARNING, no exception propagated |
| J13.22 | `test_watcher_manager_watching_names_empty_before_add` | New `WatcherManager()` → `watching_names()` returns empty set |
| J13.23 | `test_watcher_manager_add_same_path_different_names` | `add("col1", path)` + `add("col2", path)` → two watchers, both in `watching_names()` |
| J13.24 | `test_debounce_handler_observer_recursive_true` | `observer.schedule(handler, path, recursive=True)` → `recursive=True` verified |

---

### Suite 14 — archon-search Core Pipeline Gap Coverage

**Files:** `packages/archon-search/tests/test_pipeline.py`, `test_embedder.py`, `test_reranker.py`, `test_store.py` (additions)  
**Note:** ML model failures use the existing fastembed stub infrastructure from `conftest.py`.

#### 14.1 Embedder error paths

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| P14.1 | `test_embedder_backend_raises_returns_propagated_exception` | `backend.encode()` raises `RuntimeError` → exception propagates from `Embedder.embed()` |
| P14.2 | `test_embedder_backend_returns_wrong_count_raises` | `encode()` returns 2 vectors for 3 inputs → `ValueError` or similar raised |
| P14.3 | `test_embedder_embed_one_empty_result_raises` | `embed([text])` returns `[]` → `IndexError` raised, not silently None |
| P14.4 | `test_embedder_encode_whitespace_only_text` | `encode(["   "])` → returns a vector (fastembed handles it), no crash |

#### 14.2 Reranker edge cases

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| P14.5 | `test_reranker_top_k_greater_than_candidates_returns_all` | `top_k=10`, 3 candidates → all 3 returned |
| P14.6 | `test_reranker_all_same_score_stable_order` | All candidates get score=0.5 → original order preserved |
| P14.7 | `test_reranker_score_count_mismatch_raises` | `rerank()` returns 2 scores for 3 candidates → raises `ValueError` |

#### 14.3 Store error paths and edge cases

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| P14.8 | `test_store_collection_name_with_leading_underscore_rejected` | Collection name starting with `_` (underscore) is rejected because `_COLLECTION_RE` requires the first character to be alphanumeric. Use `_archon_foo` as the test input. **Note:** Rejection is due to the leading underscore, not a protected prefix check — `_ARCHON_PREFIX` is only used for filtering in `list_collections()`. |
| P14.9 | `test_store_collection_name_empty_string_rejected` | Empty string collection name → raises validation error |
| P14.10 | `test_store_rebuild_fts_index_on_empty_collection_no_crash` | `rebuild_fts_index()` on collection with zero rows → no crash |
| P14.11 | `test_store_hybrid_search_top_k_zero_returns_empty` | `top_k=0` → returns `[]`, no crash |
| P14.12 | `test_store_fetch_adjacent_nonexistent_doc_id_returns_empty` | `doc_id` not in collection → returns `[]` |
| P14.13 | `test_store_list_collections_exception_on_one_table_skipped` | One table raises on inspection → skipped, others returned |
| P14.14 | `test_store_centroid_json_malformed_logs_warning` | `centroid_json` is `"not-json"` → warning logged, returns None centroid |
| P14.15 | `test_store_delete_document_invalid_hex_raises` | `doc_id` with 63 hex chars (not 64) → `ValueError` |
| P14.16 | `test_store_list_documents_limit_zero_returns_empty` | `limit=0` → returns `[]`, no crash |
| P14.17b | `test_store_collection_name_with_single_quote_rejected` | `add_collection("col'name")` → raises `ValueError` from `_validate_collection`; no SQL injection possible |
| P14.18b | `test_store_delete_document_injection_attempt_rejected` | `delete_document(doc_id="' OR '1'='1")` → rejected by `_DOC_ID_RE` before SQL construction |

#### 14.4 Pipeline integration edge cases

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| P14.17 | `test_pipeline_ingest_file_embedder_exception_propagates` | Embedder raises during `ingest_file()` → exception propagates to caller |
| P14.18 | `test_pipeline_ingest_directory_partial_file_failure_continues` | One file fails to parse → other files still indexed, progress_cb called |
| P14.19 | `test_pipeline_search_embedder_exception_propagates` | Embedder raises during `search()` → exception propagates |
| P14.20 | `test_pipeline_search_with_context_fetch_fails_gracefully` | `fetch_adjacent_chunks()` raises → logs and continues, result still returned |
| P14.21 | `test_pipeline_ingest_directory_zero_files_no_crash` | Directory with no matching files → no crash, result is empty |
| P14.22 | `test_pipeline_chunk_size_1_produces_single_token_chunks` | `chunk_size=1` → chunker produces many tiny chunks, no crash |

---

### Suite 15 — SearchCollectionSync E2E Coverage

**File:** `packages/archon-search/tests/test_sync_e2e.py`  
**Strategy:** Real `SearchCollectionSync` + real `SearchPipeline` (with fastembed stub) + real `IndexingStateStore` + `tmp_path` isolated directories. No HTTP involved — this tests the sync engine as a unit from the outside.

#### 15.1 Collision Resolution

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S15.1 | `test_sync_adds_new_collection` | Directory registered but not yet indexed → `sync()` starts ingest job, state transitions to IN_PROGRESS |
| S15.2 | `test_sync_skips_already_done_collection` | Collection state = DONE, no file changes → `sync()` does not start a new job |
| S15.3 | `test_sync_reindexes_on_file_change` | One file modified since last index → `sync()` starts incremental update job |
| S15.4 | `test_sync_reindexes_on_embedding_model_change` | `indexed_embedding_model` in state differs from config → `sync()` forces full reindex |
| S15.5 | `test_sync_reindexes_on_chunk_size_change` | `indexed_chunk_size` in state differs from config → `sync()` forces full reindex |
| S15.6 | `test_sync_removes_deleted_collection` | Collection removed from config → `sync()` drops LanceDB table and cleans state |

#### 15.2 Crash Recovery

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S15.7 | `test_sync_resets_stale_in_progress_on_startup` | `IndexingStateStore` contains a collection stuck at `IN_PROGRESS` from a prior crash → `_reset_stale_in_progress()` resets it to `PENDING` before sync begins |
| S15.8 | `test_sync_resumes_partial_ingest_from_processed_paths` | `processed_paths` set in state → `sync()` skips already-indexed files and resumes from remaining |
| S15.9 | `test_sync_handles_missing_collection_directory_gracefully` | Registered collection path does not exist on disk → `sync()` logs warning, does not crash, marks collection as not-yet-indexed |

#### 15.3 Legacy Migration

| ID | Test name | What it verifies |
|----|-----------|-----------------|
| S15.10 | `test_sync_migrates_archon_history_to_sessions_subpath` | Collection path points to `~/.archon/history` (legacy) → `sync()` renames/remaps to `.../sessions` subpath |

---

## Test Prioritization

### P0 — Must have before any release

- All Suite 1 happy paths (H1.1–H1.13)
- All Suite 1 network-level errors (N1.1–N1.6)
- Suite 4 happy paths (H4.1–H4.5) — primary production code path for RAG injection
- Suite 4 disabled/missing paths (C4.1–C4.4)
- Suite 4 warning paths (W4.1–W4.4)
- Suite 9 `status` progress table (S9.1–S9.11) — currently broken in edge cases
- Suite 9 doctor crash-prevention paths (S9.44–S9.47, S9.55–S9.56) — graceful handling when server is down
- Suite 14 pipeline fault tolerance (P14.17–P14.20) — ingest/search exception propagation

### P1 — Should have

- Suite 2 complete (including H2.13, E2.7 for real FastMCP Phase B integration)
- Suite 3 complete
- Suite 5 complete
- Suite 9 helper function unit tests (S9.12–S9.29)
- Suite 9 edge cases (S9.30–S9.43)
- Suite 9 doctor display correctness (S9.48–S9.54)
- Suite 10 SearchClient/SearchContextProvider gaps (A10.1–A10.39, A10.21b)
- Suite 10 `get_search_client()` singleton (A10.40–A10.43)
- Suite 11 archon-search config (C11.13–C11.22) — currently zero coverage
- Suite 12 `_needs_install_trigger()` (M12.13–M12.16)
- Suite 13 job store and state store gaps (J13.1–J13.19) — crash recovery and atomicity
- Suite 14 SQL injection regression tests (P14.17b, P14.18b)
- Suite 15 SearchCollectionSync crash recovery (S15.7–S15.9) — stale IN_PROGRESS reset and resume

### P2 — Nice to have

- Suite 6, Suite 7, Suite 8
- Suite 11 Archon-side config edge cases (C11.1–C11.12)
- Suite 12 notification monitor + toolkit gaps (M12.1–M12.12)
- Suite 13 watcher gaps (J13.20–J13.24)
- Suite 14 core pipeline gaps (P14.1–P14.16, P14.21–P14.22)
- Suite 15 full SearchCollectionSync coverage (S15.1–S15.6, S15.10) — collision resolution and legacy migration

### P3 — Manual / exploratory

- X7.14–X7.16 (stress tests) — run manually with `pytest -m stress`
- Benchmark: existing `benchmark_routing_latency.py` (requires real server)
- Suite 14 concurrency / race conditions (require careful async harness)

---

## Markers

```python
# In pyproject.toml [tool.pytest.ini_options] markers:
# integration: requires a real running archon-search server process
# stress: long-running load tests, excluded from CI by default
# e2e: in-process e2e tests using ASGITransport (included in CI)
```

**Implementation note:** The `e2e` and `stress` markers must be added to `pyproject.toml` under `[tool.pytest.ini_options] markers` before any tests using these markers can run. Currently only `integration` (and project-specific markers like `live`, `requires_telegram`) are registered. Failing to register them causes `PytestUnknownMarkWarning` or failures under `--strict-markers`.

---

## Running the Tests

```bash
# All e2e tests (CI-safe, no real server needed)
uv run pytest tests/e2e/ -v

# Extended integration tests (requires running archon-search)
uv run pytest -m integration -v

# Full search-related test run
uv run pytest tests/e2e/ tests/integration/ tests/gateway/test_notification_monitor_e2e.py tests/ai/test_archon_toolkit_search_e2e.py -v

# Stress tests (manual)
uv run pytest -m stress -v
```
