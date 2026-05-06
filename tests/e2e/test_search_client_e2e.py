"""Suite 1 Happy Paths + Error Paths: SearchClient e2e tests via ASGI transport (FEAT-038 Task 1.1, 1.2)."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest
import pytest_asyncio
from unittest.mock import patch

from archon_search.config import SearchConfig
from archon_search.jobs.store import JobStore
from archon_search.server.app import create_app
from archon_search.sync import path_to_collection_name
from archon_search.types import IngestJob, JobStatus

from archon.ai.search_client import SearchClient


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_health_returns_running_status(patched_search_client):
    """H1.1: GET /health returns {"status": "running", "version": ...}."""
    result = await patched_search_client.health()
    assert result is not None
    assert result["status"] == "running"
    assert "version" in result


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_status_returns_empty_collections_on_fresh_server(patched_search_client):
    """H1.2: GET /status returns collections=[] and running=True on a fresh server."""
    result = await patched_search_client.status()
    assert result is not None
    assert result["collections"] == []
    assert result["running"] is True


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_add_collection_returns_pending_job(patched_search_client, tmp_path):
    """H1.3: POST /collections → 202 + IngestJob with status PENDING."""
    coll_path = str(tmp_path / "docs")
    result = await patched_search_client.add_collection(coll_path)
    assert result is not None
    assert "job_id" in result
    assert result["status"] == JobStatus.PENDING


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_list_collections_includes_added_collection(patched_search_client, tmp_path):
    """H1.4: After add_collection, list_collections includes the newly added collection."""
    coll_path = str(tmp_path / "my_docs")
    await patched_search_client.add_collection(coll_path)

    collections = await patched_search_client.list_collections()
    paths = [c["path"] for c in collections]
    assert coll_path in paths


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_get_collection_info_returns_metadata(patched_search_client, tmp_path):
    """H1.5: collection_info returns correct name, path, and status."""
    coll_path = str(tmp_path / "info_docs")
    result = await patched_search_client.add_collection(coll_path)
    assert result is not None
    # add_collection returns an IngestJob dict; derive the name from the path
    coll_name = path_to_collection_name(coll_path)

    info = await patched_search_client.collection_info(coll_name)
    assert info is not None
    assert info["name"] == coll_name
    assert info["path"] == coll_path
    assert "status" in info


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_remove_collection_deletes_it(patched_search_client, tmp_path):
    """H1.6: remove_collection returns {"deleted": true}; collection absent from subsequent list."""
    coll_path = str(tmp_path / "to_delete")
    result = await patched_search_client.add_collection(coll_path)
    assert result is not None
    # add_collection returns an IngestJob dict; derive the name from the path
    coll_name = path_to_collection_name(coll_path)

    deleted = await patched_search_client.remove_collection(coll_name)
    assert deleted is not None
    assert deleted.get("deleted") is True

    collections = await patched_search_client.list_collections()
    names = [c["name"] for c in collections]
    assert coll_name not in names


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_ingest_with_path_returns_pending_job(patched_search_client, tmp_path):
    """H1.7: SearchClient.ingest(collection, path) → IngestJob with status PENDING."""
    coll_path = str(tmp_path / "ingest_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None
    # add_collection returns an IngestJob dict; derive the name from the path
    coll_name = path_to_collection_name(coll_path)

    job = await patched_search_client.ingest(coll_name, path=coll_path)
    assert job is not None
    assert isinstance(job, IngestJob)
    assert job.status == JobStatus.PENDING
    assert job.job_id


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_get_job_returns_job_state(patched_search_client, tmp_path):
    """H1.8: get_job(job_id) returns IngestJob with status in {PENDING, RUNNING, DONE}."""
    coll_path = str(tmp_path / "job_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None
    job_id = add_result["job_id"]

    job = await patched_search_client.get_job(job_id)
    assert job is not None
    assert isinstance(job, IngestJob)
    assert job.job_id == job_id
    assert job.status in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.DONE}


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_cancel_job_in_pending_state_returns_202(patched_search_client, tmp_path):
    """H1.9: cancel_job on a PENDING job returns 202 and job transitions to CANCELLING."""
    coll_path = str(tmp_path / "cancel_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None
    job_id = add_result["job_id"]

    status_code = await patched_search_client.cancel_job(job_id)
    assert status_code == 202

    job = await patched_search_client.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.CANCELLING


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_cancel_terminal_job_is_idempotent(patched_search_client, tmp_path):
    """H1.10: cancel_job idempotency — cancelling an already-CANCELLING job returns 202.

    DONE state is not testable in CI (no background worker runs). Instead, we verify
    the idempotency contract by: PENDING → cancel → 202 (CANCELLING), then cancel again
    → 202 (already CANCELLING, idempotent response).
    """
    coll_path = str(tmp_path / "terminal_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None
    job_id = add_result["job_id"]

    # First cancel: PENDING job transitions to CANCELLING → 202
    first_cancel = await patched_search_client.cancel_job(job_id)
    assert first_cancel == 202

    job = await patched_search_client.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.CANCELLING

    # Second cancel: CANCELLING job → idempotent 202
    second_cancel = await patched_search_client.cancel_job(job_id)
    assert second_cancel == 202


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_indexing_state_returns_empty_on_fresh_server(patched_search_client):
    """H1.11: GET /indexing-state returns {} on a fresh server (no active indexing)."""
    result = await patched_search_client.indexing_state()
    assert result is not None
    assert result == {}


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_route_with_no_collections_returns_empty_routable(patched_search_client):
    """H1.12: route() with no collections returns RouteResponse with routable_names=[]."""
    result = await patched_search_client.route("what is archon?")
    assert result is not None
    assert result.routable_names == []


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_reindex_collection_returns_new_job(patched_search_client, tmp_path):
    """H1.13: POST /collections/{name}/reindex → 202 + new IngestJob with status PENDING."""
    coll_path = str(tmp_path / "reindex_docs")
    add_result = await patched_search_client.add_collection(coll_path)
    assert add_result is not None
    # add_collection returns an IngestJob dict; derive the name from the path
    coll_name = path_to_collection_name(coll_path)

    job = await patched_search_client.reindex_collection(coll_name)
    assert job is not None
    assert isinstance(job, IngestJob)
    assert job.status == JobStatus.PENDING
    assert job.job_id


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_search_client_close_smoke(search_app):
    """Smoke: construct SearchClient, close() is idempotent (second close does not raise)."""
    transport = httpx.ASGITransport(app=search_app)
    client = SearchClient("http://test", transport=transport)

    await client.close()
    # Second close must not raise
    await client.close()


# ---------------------------------------------------------------------------
# Suite E1 — Error paths (FEAT-038 Task 1.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_route_empty_query_returns_400(patched_search_client, caplog):
    """E1.1: route("") → server returns 400; client returns None, logs WARNING."""
    with caplog.at_level(logging.WARNING, logger="archon"):
        result = await patched_search_client.route("")
    assert result is None
    assert any("400" in r.message for r in caplog.records)


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_route_whitespace_query_returns_400(patched_search_client):
    """E1.2: route("   ") → server returns 400; client returns None."""
    result = await patched_search_client.route("   ")
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_route_invalid_slots_returns_400(patched_search_client):
    """E1.3: route("query", slots=0) → server returns 400; client returns None."""
    result = await patched_search_client.route("some query", slots=0)
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_add_collection_duplicate_returns_409(patched_search_client, tmp_path):
    """E1.4: Adding the same path twice → server returns 409 on second call; client returns None."""
    coll_path = str(tmp_path / "dup_docs")
    first = await patched_search_client.add_collection(coll_path)
    assert first is not None

    second = await patched_search_client.add_collection(coll_path)
    assert second is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_remove_nonexistent_collection_returns_404(patched_search_client):
    """E1.5: remove_collection("nonexistent") → server returns 404; client returns None."""
    result = await patched_search_client.remove_collection("nonexistent")
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_get_nonexistent_collection_returns_404(patched_search_client):
    """E1.6: collection_info("nonexistent") → server returns 404; client returns None."""
    result = await patched_search_client.collection_info("nonexistent")
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_get_nonexistent_job_returns_404(patched_search_client):
    """E1.7: get_job with a nil UUID → server returns 404; client returns None."""
    result = await patched_search_client.get_job("00000000-0000-0000-0000-000000000000")
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_cancel_nonexistent_job_returns_404(patched_search_client):
    """E1.8: cancel_job with a nil UUID → server returns 404; client returns 404."""
    status_code = await patched_search_client.cancel_job("00000000-0000-0000-0000-000000000000")
    assert status_code == 404


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_reindex_nonexistent_collection_returns_404(patched_search_client):
    """E1.9: reindex_collection("nonexistent") → server returns 404; client returns None."""
    result = await patched_search_client.reindex_collection("nonexistent")
    assert result is None


@pytest_asyncio.fixture(scope="function")
async def search_app_pinned(tmp_path):
    """FastAPI app with a pinned-only collection (in pinned_collections, not in collections)."""
    pinned_path = str(tmp_path / "pinned_docs")
    config = SearchConfig(
        db_path=str(tmp_path / "search_db"),
        pinned_collections=[pinned_path],
        collections=[],
    )
    job_store = JobStore(tmp_path / "jobs.json")
    app = create_app(config, job_store, config_path=tmp_path / "config.toml")
    async with app.router.lifespan_context(app):
        yield app, pinned_path
    tasks = list(getattr(app.state, "_background_tasks", None) or [])
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest_asyncio.fixture(scope="function")
async def patched_search_client_pinned(search_app_pinned):
    """SearchClient connected to a pinned-collection app via ASGI transport."""
    app, pinned_path = search_app_pinned
    transport = httpx.ASGITransport(app=app)
    client = SearchClient("http://test", transport=transport)
    client._http.follow_redirects = True
    try:
        yield client, pinned_path
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_remove_pinned_only_collection_returns_409(patched_search_client_pinned):
    """E1.10: remove_collection on a pinned-only collection → server returns 409; client returns None."""
    client, pinned_path = patched_search_client_pinned
    pinned_name = path_to_collection_name(pinned_path)
    result = await client.remove_collection(pinned_name)
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_ingest_empty_collection_name_returns_422(patched_search_client):
    """E1.11: ingest("", path="/tmp") → server returns 422; client returns None."""
    result = await patched_search_client.ingest("", path="/tmp")
    assert result is None


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_route_504_timeout_returns_none(patched_search_client, caplog):
    """E1.12: Patch server-side asyncio.wait_for to raise TimeoutError → 504; client returns None, logs WARNING."""
    with caplog.at_level(logging.WARNING, logger="archon"):
        with patch(
            "archon_search.server.routes_route.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await patched_search_client.route("anything")
    assert result is None
    assert any("504" in r.message for r in caplog.records)
