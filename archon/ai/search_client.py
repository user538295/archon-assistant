"""SearchClient — HTTP client adapter for the archon-search service (FEAT-038 Task 7.1).

All HTTP failures are handled gracefully: return None / [] / status code.
Never raises; logs at WARNING (timeout / 5xx) or DEBUG (connection refused).
"""
from __future__ import annotations

import logging

import httpx

from archon.config import config

# Sole exemption: domain types live in archon_search.types
try:
    from archon_search.types import IngestJob, JobStatus, RouteResponse
except ImportError:
    IngestJob = None  # type: ignore[assignment,misc]
    JobStatus = None  # type: ignore[assignment,misc]
    RouteResponse = None  # type: ignore[assignment,misc]

logger = logging.getLogger("archon")


class SearchClient:
    """Async HTTP client for the archon-search REST API."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    # ------------------------------------------------------------------
    # /route
    # ------------------------------------------------------------------

    async def route(self, query: str, slots: int | None = None) -> RouteResponse | None:
        """POST /route; returns RouteResponse or None on any failure."""
        payload: dict[str, object] = {"query": query}
        if slots is not None:
            payload["slots"] = slots
        try:
            resp = await self._http.post("/route", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return RouteResponse(
                pre_context=data.get("pre_context"),
                pinned_names=data.get("pinned_names", []),
                routable_names=data.get("routable_names", []),
                decomposer_invoked=bool(data.get("decomposer_invoked", False)),
            )
        except httpx.TimeoutException:
            logger.warning("SearchClient.route: timed out for query %r", query)
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.route: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.route: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.route: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # /health
    # ------------------------------------------------------------------

    async def health(self) -> dict | None:  # type: ignore[type-arg]
        """GET /health; returns dict or None on failure."""
        try:
            resp = await self._http.get("/health")
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.health: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.health: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.health: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.health: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------

    async def status(self) -> dict | None:  # type: ignore[type-arg]
        """GET /status; returns rich service + collection state or None."""
        try:
            resp = await self._http.get("/status")
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.status: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.status: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.status: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.status: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # /indexing-state
    # ------------------------------------------------------------------

    async def indexing_state(self) -> dict | None:  # type: ignore[type-arg]
        """GET /indexing-state; returns dict or None on failure."""
        try:
            resp = await self._http.get("/indexing-state")
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.indexing_state: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.indexing_state: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.indexing_state: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.indexing_state: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # /ingest
    # ------------------------------------------------------------------

    async def ingest(
        self,
        collection: str,
        path: str | None = None,
        documents: list[dict] | None = None,  # type: ignore[type-arg]
    ) -> IngestJob | None:
        """POST /ingest; returns IngestJob or None on failure."""
        payload: dict[str, object] = {"collection": collection}
        if path is not None:
            payload["path"] = path
        if documents is not None:
            payload["documents"] = documents
        try:
            resp = await self._http.post("/ingest", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return IngestJob(
                job_id=data["job_id"],
                status=JobStatus(data["status"]),
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                result=data.get("result"),
                error=data.get("error"),
            )
        except httpx.TimeoutException:
            logger.warning("SearchClient.ingest: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.ingest: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.ingest: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.ingest: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # /jobs/<job_id>
    # ------------------------------------------------------------------

    async def get_job(self, job_id: str) -> IngestJob | None:
        """GET /jobs/<job_id>; returns IngestJob or None on failure."""
        try:
            resp = await self._http.get(f"/jobs/{job_id}")
            resp.raise_for_status()
            data = resp.json()
            return IngestJob(
                job_id=data["job_id"],
                status=JobStatus(data["status"]),
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                result=data.get("result"),
                error=data.get("error"),
            )
        except httpx.TimeoutException:
            logger.warning("SearchClient.get_job: timed out for %r", job_id)
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.get_job: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.get_job: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.get_job: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # DELETE /jobs/<job_id>  (cancel)
    # ------------------------------------------------------------------

    async def cancel_job(self, job_id: str) -> int:
        """DELETE /jobs/<job_id>; returns HTTP status code."""
        try:
            resp = await self._http.delete(f"/jobs/{job_id}")
            if resp.status_code >= 500:
                logger.warning("SearchClient.cancel_job: HTTP %s for %r", resp.status_code, job_id)
            return resp.status_code
        except httpx.TimeoutException:
            logger.warning("SearchClient.cancel_job: timed out for %r", job_id)
            return 408
        except httpx.ConnectError:
            logger.debug("SearchClient.cancel_job: connection refused (%s)", self._base_url)
            return 503
        except Exception as exc:
            logger.warning("SearchClient.cancel_job: unexpected error: %s", exc)
            return 500

    # ------------------------------------------------------------------
    # /collections
    # ------------------------------------------------------------------

    async def list_collections(self) -> list[dict]:  # type: ignore[type-arg]
        """GET /collections; returns list of collection summaries or [] on failure."""
        try:
            resp = await self._http.get("/collections")
            resp.raise_for_status()
            return list(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.list_collections: timed out")
            return []
        except httpx.ConnectError:
            logger.debug("SearchClient.list_collections: connection refused (%s)", self._base_url)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.list_collections: HTTP %s", exc.response.status_code)
            return []
        except Exception as exc:
            logger.warning("SearchClient.list_collections: unexpected error: %s", exc)
            return []

    async def add_collection(self, path: str) -> dict | None:  # type: ignore[type-arg]
        """POST /collections; returns response dict or None on failure."""
        payload: dict[str, object] = {"path": path}
        try:
            resp = await self._http.post("/collections", json=payload)
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.add_collection: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.add_collection: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.add_collection: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.add_collection: unexpected error: %s", exc)
            return None

    async def remove_collection(self, name: str) -> dict | None:  # type: ignore[type-arg]
        """DELETE /collections/<name>; returns response dict or None on failure."""
        try:
            resp = await self._http.delete(f"/collections/{name}")
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.remove_collection: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.remove_collection: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.remove_collection: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.remove_collection: unexpected error: %s", exc)
            return None

    async def collection_info(self, name: str) -> dict | None:  # type: ignore[type-arg]
        """GET /collections/<name>; returns collection info dict or None on failure."""
        try:
            resp = await self._http.get(f"/collections/{name}")
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.TimeoutException:
            logger.warning("SearchClient.collection_info: timed out")
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.collection_info: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.collection_info: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.collection_info: unexpected error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Context manager / lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> "SearchClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def reindex_collection(self, name: str) -> IngestJob | None:
        """POST /collections/<name>/reindex; returns IngestJob or None on failure."""
        try:
            resp = await self._http.post(f"/collections/{name}/reindex")
            resp.raise_for_status()
            data = resp.json()
            return IngestJob(
                job_id=data["job_id"],
                status=JobStatus(data["status"]),
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                result=data.get("result"),
                error=data.get("error"),
            )
        except httpx.TimeoutException:
            logger.warning("SearchClient.reindex_collection: timed out for %r", name)
            return None
        except httpx.ConnectError:
            logger.debug("SearchClient.reindex_collection: connection refused (%s)", self._base_url)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("SearchClient.reindex_collection: HTTP %s", exc.response.status_code)
            return None
        except Exception as exc:
            logger.warning("SearchClient.reindex_collection: unexpected error: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_search_client: SearchClient | None = None


def get_search_client() -> SearchClient:
    """Return (or create) the SearchClient singleton using Archon config."""
    global _search_client
    if _search_client is None:
        _search_client = SearchClient(base_url=config.search.url)
    return _search_client


async def reset_search_client() -> None:
    """Close and discard the SearchClient singleton (e.g. on gateway shutdown)."""
    global _search_client
    if _search_client is not None:
        await _search_client.close()
        _search_client = None
