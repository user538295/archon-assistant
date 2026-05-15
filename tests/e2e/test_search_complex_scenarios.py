"""Suite 7 Complex Multi-Collection Scenarios (FEAT-038 Task 12.2).

X7.1–X7.3:  concurrent route/ingest, routable cap
X7.4–X7.7:  score normalization edge cases
X7.8–X7.10: job state machine (crash recovery, eviction, cancel race)
X7.11–X7.13: config persistence (add/remove writes TOML, None path skips I/O)
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from archon.ai.search_context_provider import (
    SearchContextProvider,
    SearchResult,
    _normalize_and_merge,
)

try:
    from archon_search.config import SearchConfig
    from archon_search.jobs.store import JobStore
    from archon_search.server.app import create_app
    from archon_search.sync import path_to_collection_name
    from archon_search.types import JobStatus

    _HAS_ARCHON_SEARCH = True
except ImportError:
    _HAS_ARCHON_SEARCH = False

pytestmark = pytest.mark.skipif(
    not _HAS_ARCHON_SEARCH, reason="archon-search package not available"
)


# ---------------------------------------------------------------------------
# Helpers shared across suites
# ---------------------------------------------------------------------------


def _make_cfg(
    *,
    enabled: bool = True,
    max_parallel: int = 3,
    top_k_return: int = 5,
) -> MagicMock:
    cfg = MagicMock()
    cfg.enabled = enabled
    cfg.max_parallel_collections = max_parallel
    cfg.top_k_return = top_k_return
    return cfg


def _make_route_response(
    *,
    pre_context: str | None = None,
    pinned_names: list[str] | None = None,
    routable_names: list[str] | None = None,
    decomposer_invoked: bool = False,
) -> Any:
    rr = MagicMock()
    rr.pre_context = pre_context
    rr.pinned_names = pinned_names or []
    rr.routable_names = routable_names or []
    rr.decomposer_invoked = decomposer_invoked
    return rr


def _make_mock_client(route_response: Any = None) -> MagicMock:
    client = MagicMock()
    client.route = AsyncMock(return_value=route_response)
    return client


def _search_result(
    *,
    doc_id: str = "doc1",
    chunk_id: str = "chunk1",
    text: str = "chunk text",
    score: float = 0.9,
    source_path: str = "/docs/file.md",
) -> SearchResult:
    return SearchResult(
        doc_id=doc_id,
        chunk_id=chunk_id,
        text=text,
        score=score,
        source_path=source_path,
    )


def _jsonrpc_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(results)}]},
    }


def _search_result_dict(**kwargs: Any) -> dict[str, Any]:
    defaults = {
        "doc_id": "doc1",
        "chunk_id": "chunk1",
        "text": "chunk text",
        "score": 0.9,
        "source_path": "/docs/file.md",
    }
    defaults.update(kwargs)
    return defaults


def _make_stub_search_app(
    results_by_collection: dict[str, list[dict[str, Any]]] | None = None,
) -> FastAPI:
    app = FastAPI()
    _results = results_by_collection or {}

    @app.post("/mcp")
    async def handle_mcp(request: Request) -> JSONResponse:
        body = await request.json()
        collection = body.get("params", {}).get("arguments", {}).get("collection", "")
        results = _results.get(collection, [])
        return JSONResponse(content=_jsonrpc_result(results))

    return app


def _stub_http(stub_app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=stub_app)
    return httpx.AsyncClient(base_url="http://stub", transport=transport, timeout=10.0)


# ---------------------------------------------------------------------------
# X7.1: Concurrent route + ingest don't interfere
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_1_concurrent_route_and_ingest_dont_interfere(
    patched_search_client, tmp_path
):
    """X7.1: Starting an ingest job and routing a query concurrently both succeed."""
    coll_path = str(tmp_path / "docs")

    route_task = asyncio.create_task(patched_search_client.route("what is archon?"))
    ingest_task = asyncio.create_task(patched_search_client.add_collection(coll_path))

    route_result, ingest_result = await asyncio.gather(route_task, ingest_task)

    # route() on empty server returns a RouteResponse (not None) with empty routable_names
    assert route_result is not None
    assert hasattr(route_result, "routable_names")

    # ingest creates a PENDING job
    assert ingest_result is not None
    assert ingest_result["status"] == JobStatus.PENDING


# ---------------------------------------------------------------------------
# X7.2: Routable cap respected with multiple collections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_2_routable_cap_excludes_unindexed_collections(
    patched_search_client, tmp_path
):
    """X7.2: route() only returns routable collections (those already indexed).

    Adding a collection via add_collection creates a PENDING job, but the
    collection is not yet indexed (no background worker runs in tests).
    The route endpoint returns only collections whose indexing state is DONE.
    On a fresh server with PENDING jobs, routable_names should be empty.
    """
    # Add multiple collections — none are indexed yet (PENDING state)
    paths = [str(tmp_path / f"col_{i}") for i in range(3)]
    for p in paths:
        result = await patched_search_client.add_collection(p)
        assert result is not None

    route_result = await patched_search_client.route("some query")
    assert route_result is not None
    # No collection is indexed → routable_names is empty
    assert route_result.routable_names == []


# ---------------------------------------------------------------------------
# X7.3: Concurrent ingest of same collection returns same job or 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_3_concurrent_ingest_same_collection_no_crash(
    patched_search_client, tmp_path
):
    """X7.3: POST /collections twice for the same path → second returns None (409), no crash."""
    coll_path = str(tmp_path / "shared_docs")

    first_task = asyncio.create_task(patched_search_client.add_collection(coll_path))
    second_task = asyncio.create_task(patched_search_client.add_collection(coll_path))

    first, second = await asyncio.gather(first_task, second_task)

    # Exactly one must succeed (PENDING job) and the other must be rejected (409 → None)
    results = [first, second]
    success_count = sum(1 for r in results if r is not None)
    none_count = sum(1 for r in results if r is None)

    # Server dedup check is effectively atomic in asyncio — exactly one must succeed
    assert success_count == 1, f"Expected exactly one to succeed, got {success_count}"
    assert none_count == 1, f"Expected exactly one to be rejected (409), got {none_count}"


# ---------------------------------------------------------------------------
# X7.4: Score normalization — single result per collection normalized to 0.5
# ---------------------------------------------------------------------------


def test_X7_4_single_result_per_collection_normalized_to_0_5():
    """X7.4: _normalize_and_merge with one result per collection → score == 0.5."""
    per_collection = {
        "col_a": [_search_result(doc_id="a1", chunk_id="a1", text="only a", score=0.99)],
        "col_b": [_search_result(doc_id="b1", chunk_id="b1", text="only b", score=0.11)],
    }
    merged = _normalize_and_merge(per_collection, top_k=10)
    assert len(merged) == 2
    for r in merged:
        assert r.score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# X7.5: Score normalization — multiple results normalized to [0, 1] range
# ---------------------------------------------------------------------------


def test_X7_5_multiple_results_normalized_to_0_1_range():
    """X7.5: _normalize_and_merge with multiple results per collection → min=0.0, max=1.0."""
    per_collection = {
        "col_a": [
            _search_result(doc_id="a1", chunk_id="a1", text="high", score=0.95),
            _search_result(doc_id="a2", chunk_id="a2", text="mid", score=0.50),
            _search_result(doc_id="a3", chunk_id="a3", text="low", score=0.05),
        ],
    }
    merged = _normalize_and_merge(per_collection, top_k=10)
    assert len(merged) == 3
    scores = [r.score for r in merged]
    assert max(scores) == pytest.approx(1.0)
    assert min(scores) == pytest.approx(0.0)
    # Results sorted descending by score
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# X7.6: Score normalization — all same scores → 0.5 each (no division by zero)
# ---------------------------------------------------------------------------


def test_X7_6_all_same_scores_normalized_to_0_5():
    """X7.6: _normalize_and_merge with all identical scores → normalized to 0.5 (avoids ZeroDivisionError)."""
    per_collection = {
        "col_a": [
            _search_result(doc_id="a1", chunk_id="a1", text="first", score=0.7),
            _search_result(doc_id="a2", chunk_id="a2", text="second", score=0.7),
            _search_result(doc_id="a3", chunk_id="a3", text="third", score=0.7),
        ],
    }
    # Must not raise ZeroDivisionError
    merged = _normalize_and_merge(per_collection, top_k=10)
    assert len(merged) == 3
    for r in merged:
        assert r.score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# X7.7: Empty search result from collection doesn't crash SearchContextProvider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_7_empty_collection_result_skipped_no_crash():
    """X7.7: One collection returns [] from search — SearchContextProvider skips it without crashing."""
    results_by_collection = {
        "col_empty": [],
        "col_ok": [_search_result_dict(doc_id="ok1", chunk_id="ok1", text="good result", score=0.8)],
    }
    mock_client = _make_mock_client(
        route_response=_make_route_response(
            pinned_names=[],
            routable_names=["col_empty", "col_ok"],
            decomposer_invoked=False,
        )
    )

    async def _mock_search(collection: str, query: str, top_k: int) -> list[dict]:
        return results_by_collection.get(collection, [])

    mock_client.search = AsyncMock(side_effect=_mock_search)

    cfg = _make_cfg(max_parallel=3, top_k_return=5)
    provider = SearchContextProvider(cfg=cfg, search_client=mock_client)

    await provider.get_pre_context("query")
    from archon.ai.decomposer import TaskOutput
    task_output = TaskOutput(scope="small", prompt="query")
    task_output.selected_collections = None
    result = await provider.search_and_prepare(task_output, "query")

    # col_empty returns nothing; col_ok has one result → result is not None
    assert result is not None
    rag_text, chunk_count, actual_searched = result
    assert "good result" in rag_text
    assert chunk_count >= 1
    assert "col_ok" in actual_searched, f"Expected col_ok in searched collections, got {actual_searched}"


# ---------------------------------------------------------------------------
# X7.8: Job crash recovery — RUNNING job on reload is marked as process_restart
# ---------------------------------------------------------------------------


def test_X7_8_running_job_marked_process_restart_on_reload(tmp_path):
    """X7.8: A RUNNING job in the JSON file is marked FAILED with error='process_restart' on JobStore reload."""
    jobs_file = tmp_path / "jobs.json"

    # Write a RUNNING job directly to the file (simulating a mid-run crash)
    now_iso = datetime.now(timezone.utc).isoformat()
    running_job = {
        "job_id": "aaaaaaaa-0000-0000-0000-000000000001",
        "status": "RUNNING",
        "created_at": now_iso,
        "updated_at": now_iso,
        "result": None,
        "error": None,
    }
    jobs_file.write_text(json.dumps([running_job]))

    # Reload JobStore — crash recovery should fire
    store = JobStore(path=jobs_file)

    job = store.get("aaaaaaaa-0000-0000-0000-000000000001")
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error == "process_restart"


# ---------------------------------------------------------------------------
# X7.9: Job eviction — job older than 7 days is evicted on reload
# ---------------------------------------------------------------------------


def test_X7_9_old_job_evicted_on_reload(tmp_path):
    """X7.9: A job with updated_at > 7 days ago is evicted when the JobStore reloads."""
    jobs_file = tmp_path / "jobs.json"

    old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    recent_ts = datetime.now(timezone.utc).isoformat()

    old_job = {
        "job_id": "bbbbbbbb-0000-0000-0000-000000000002",
        "status": "DONE",
        "created_at": old_ts,
        "updated_at": old_ts,
        "result": None,
        "error": None,
    }
    recent_job = {
        "job_id": "cccccccc-0000-0000-0000-000000000003",
        "status": "DONE",
        "created_at": recent_ts,
        "updated_at": recent_ts,
        "result": None,
        "error": None,
    }
    jobs_file.write_text(json.dumps([old_job, recent_job]))

    store = JobStore(path=jobs_file)

    # Old job should be evicted
    assert store.get("bbbbbbbb-0000-0000-0000-000000000002") is None
    # Recent job should still be present
    assert store.get("cccccccc-0000-0000-0000-000000000003") is not None


# ---------------------------------------------------------------------------
# X7.10: Cancel race — cancel a DONE job is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_10_cancel_done_job_is_idempotent(patched_search_client, tmp_path):
    """X7.10: cancel_job idempotency — second cancel returns same status, job doesn't regress.
    Accepts both 202 (CANCELLING) and 200 (DONE) from first cancel due to asyncio scheduling."""
    coll_path = str(tmp_path / "cancel_race_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None
    job_id = add_result["job_id"]

    # Background task uses asyncio.sleep(0) — timing is non-deterministic.
    # Accept both outcomes: 202 (CANCELLING) or 200 (already DONE)
    first_cancel = await patched_search_client.cancel_job(job_id)
    assert first_cancel in (200, 202)

    job = await patched_search_client.get_job(job_id)
    assert job is not None
    expected_status = JobStatus.CANCELLING if first_cancel == 202 else JobStatus.DONE
    assert job.status == expected_status

    # Second cancel must be idempotent — same status, same code
    second_cancel = await patched_search_client.cancel_job(job_id)
    assert second_cancel in (200, 202)

    job_after = await patched_search_client.get_job(job_id)
    assert job_after is not None
    assert job_after.status == expected_status


# ---------------------------------------------------------------------------
# X7.11: Config persistence — add_collection writes to TOML
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_11_add_collection_writes_to_toml(patched_search_client, tmp_path):
    """X7.11: POST /collections writes the collection path to the TOML file on disk."""
    # patched_search_client uses the search_app fixture which sets config_path to tmp_path/config.toml
    coll_path = str(tmp_path / "persisted_docs")
    result = await patched_search_client.add_collection(coll_path)
    assert result is not None

    # The config.toml must have been written
    config_path = tmp_path / "config.toml"
    assert config_path.exists(), "config.toml should have been created by add_collection"

    import tomlkit
    doc = tomlkit.parse(config_path.read_text())
    written_collections = list(doc.get("collections", {}).get("collections", []))
    # The resolved path of coll_path should appear in collections
    resolved = str(Path(coll_path).expanduser().resolve())
    assert resolved in written_collections, (
        f"Expected {resolved} in TOML collections, got {written_collections}"
    )


# ---------------------------------------------------------------------------
# X7.12: Config persistence — remove_collection updates TOML
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_12_remove_collection_updates_toml(patched_search_client, tmp_path):
    """X7.12: DELETE /collections/{name} removes the collection path from the TOML file."""
    coll_path = str(tmp_path / "to_remove_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None

    coll_name = path_to_collection_name(coll_path)
    del_result = await patched_search_client.remove_collection(coll_name)
    assert del_result is not None
    assert del_result.get("deleted") is True

    config_path = tmp_path / "config.toml"
    assert config_path.exists(), "config.toml should exist after add_collection"
    import tomlkit
    doc = tomlkit.parse(config_path.read_text())
    written_collections = list(doc.get("collections", {}).get("collections", []))
    resolved = str(Path(coll_path).expanduser().resolve())
    assert resolved not in written_collections, (
        f"Removed collection {resolved} should not appear in TOML, got {written_collections}"
    )


# ---------------------------------------------------------------------------
# X7.13: Config persistence — None config_path skips TOML write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_X7_13_none_config_path_skips_toml_write(tmp_path):
    """X7.13: App created with config_path=None — add/remove collections do not create a TOML file."""
    config = SearchConfig(db_path=str(tmp_path / "search_db"))
    job_store = JobStore(tmp_path / "jobs_no_cfg.json")
    # Explicitly pass config_path=None
    app = create_app(config, job_store, config_path=None)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
            follow_redirects=True,
        ) as client:
            coll_path = str(tmp_path / "no_cfg_docs")

            # add_collection
            resp = await client.post("/collections/", json={"path": coll_path})
            assert resp.status_code in (200, 201, 202)

            # No TOML file should have been created anywhere in tmp_path
            toml_files = list(tmp_path.glob("*.toml"))
            assert toml_files == [], (
                f"No TOML file should have been created, found: {toml_files}"
            )

            # remove_collection — also must not crash
            from archon_search.sync import path_to_collection_name
            coll_name = path_to_collection_name(coll_path)
            del_resp = await client.delete(f"/collections/{coll_name}")
            assert del_resp.status_code in (200, 404)  # 404 if not yet committed

            toml_files_after = list(tmp_path.glob("*.toml"))
            assert toml_files_after == [], (
                f"No TOML file should have been created after delete, found: {toml_files_after}"
            )

    tasks = list(getattr(app.state, "_background_tasks", None) or [])
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
