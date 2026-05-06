"""Shared ASGI transport fixtures for archon-search e2e tests."""
from __future__ import annotations

import asyncio

import httpx
import pytest_asyncio
from archon_search.server.app import create_app
from archon_search.config import SearchConfig
from archon_search.jobs.store import JobStore

from archon.ai.search_client import SearchClient


@pytest_asyncio.fixture(scope="function")
async def search_app(tmp_path):
    """FastAPI app instance wired to a temp directory, with lifespan started."""
    config = SearchConfig(db_path=str(tmp_path / "search_db"))
    job_store = JobStore(tmp_path / "jobs.json")
    app = create_app(config, job_store, config_path=tmp_path / "config.toml")
    async with app.router.lifespan_context(app):
        yield app
    tasks = list(getattr(app.state, "_background_tasks", None) or [])
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest_asyncio.fixture(scope="function")
async def patched_search_client(search_app):
    """SearchClient connected to the in-process FastAPI app via ASGI transport.

    FastAPI routes use trailing slashes (e.g. POST /collections/) and emit 307
    redirects for the slash-less variants.  Enabling follow_redirects on the
    existing httpx client (without replacing it) makes those redirects transparent,
    matching the behaviour of a real uvicorn server.
    """
    transport = httpx.ASGITransport(app=search_app)
    client = SearchClient("http://test", transport=transport)
    client._http.follow_redirects = True
    try:
        yield client
    finally:
        await client.close()
