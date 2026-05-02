"""RAG-related MCP tools for ArchonToolkit — HTTP client edition (FEAT-038 Task 7.3).

Most tools communicate with the archon-search service via SearchClient HTTP calls.
search_start and search_stop use the local platform service directly (since HTTP cannot
start a stopped service).
No direct imports from archon.search.* or archon_search.* (except via search_client).
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import TYPE_CHECKING, Any

from archon.ai.search_client import get_search_client

logger = logging.getLogger("archon")

if TYPE_CHECKING:
    from archon.ai.archon_toolkit import ArchonToolkit

try:
    from archon.platform import get_search_service
    _SEARCH_AVAILABLE = True
except ImportError:
    _SEARCH_AVAILABLE = False


# ---------------------------------------------------------------------------
# search_status
# ---------------------------------------------------------------------------

_SEARCH_STATUS_SCHEMA: dict[str, Any] = {
    "name": "search_status",
    "description": (
        "Check RAG service status — whether it is running, its PID, "
        "and the list of indexed collections with document and chunk counts; "
        "includes optional eta_seconds (integer, estimated seconds remaining) for in-progress collections, "
        "and watching (bool) indicating whether file-system watch mode is enabled globally in config (same value for all collections) — "
        "use this tool to check if watch mode is enabled."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_rag_status(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Return RAG service status as a JSON string via HTTP client."""
    client = get_search_client()
    result = await client.status()
    if result is None:
        return json.dumps({"running": False, "pid": None, "collections": [], "error": "service unavailable"})
    return json.dumps(result)


# ---------------------------------------------------------------------------
# search_start
# ---------------------------------------------------------------------------

_SEARCH_START_SCHEMA: dict[str, Any] = {
    "name": "search_start",
    "description": "Start the RAG search service.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_rag_start(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Start the RAG service and return a status string."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    try:
        rc = await asyncio.to_thread(get_search_service().start)
    except Exception as exc:
        logger.warning("Failed to start RAG service: %s", exc, exc_info=True)
        return f"RAG service start failed: {exc}"

    if rc == 0:
        return "RAG service started."
    return f"RAG service start failed (exit code {rc})."


# ---------------------------------------------------------------------------
# search_stop
# ---------------------------------------------------------------------------

_SEARCH_STOP_SCHEMA: dict[str, Any] = {
    "name": "search_stop",
    "description": "Stop the RAG search service.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_rag_stop(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Stop the RAG service and return a status string."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    try:
        rc = await asyncio.to_thread(get_search_service().stop)
    except Exception as exc:
        logger.warning("Failed to stop RAG service: %s", exc, exc_info=True)
        return f"RAG service stop failed: {exc}"

    if rc == 0:
        return "RAG service stopped."
    return f"RAG service stop failed (exit code {rc})."


# ---------------------------------------------------------------------------
# search_ingest
# ---------------------------------------------------------------------------

_SEARCH_INGEST_SCHEMA: dict[str, Any] = {
    "name": "search_ingest",
    "description": (
        "Ingest a directory of documents into a RAG collection. "
        "Returns a job_id immediately — ingestion runs asynchronously. "
        "Defaults to the history sessions directory if no path is given."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the directory to ingest. Defaults to history sessions directory.",
            },
            "collection": {
                "type": "string",
                "description": "Collection name to ingest into. Derived from path if omitted.",
            },
        },
    },
}


async def _handle_rag_ingest(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Submit an ingest job via HTTP; returns job info string with job_id."""
    if toolkit._config is None:
        return "Configuration not available."

    path = arguments.get("path")
    collection = arguments.get("collection")

    if path is None and toolkit._config is not None:
        import os  # noqa: PLC0415
        path = str(os.path.join(toolkit._config.history.directory, "sessions"))

    if collection is None and path is not None:
        # Derive collection name from path (last non-empty path component)
        import os  # noqa: PLC0415
        collection = os.path.basename(path.rstrip("/\\")) or "default"

    client = get_search_client()
    job = await client.ingest(collection=collection or "default", path=path)
    if job is None:
        return "Ingest failed: service unavailable or error"
    return json.dumps({"job_id": job.job_id, "status": str(job.status), "collection": collection})


# ---------------------------------------------------------------------------
# search_sync
# ---------------------------------------------------------------------------

_SEARCH_SYNC_SCHEMA: dict[str, Any] = {
    "name": "search_sync",
    "description": (
        "Not currently supported via HTTP. "
        "Use the archon-search CLI directly for sync operations."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_rag_sync(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Sync is not supported via HTTP in this version."""
    return "search_sync is not supported via the HTTP API in this version. Use the search service CLI directly."


# ---------------------------------------------------------------------------
# search_collection_list
# ---------------------------------------------------------------------------

_SEARCH_COLLECTION_LIST_SCHEMA: dict[str, Any] = {
    "name": "search_collection_list",
    "description": "List all RAG collections: their source path, doc/chunk counts, and sync status.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}


async def _handle_rag_collection_list(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """List all RAG collections via HTTP client."""
    client = get_search_client()
    collections = await client.list_collections()
    return json.dumps(collections)


# ---------------------------------------------------------------------------
# search_collection_add
# ---------------------------------------------------------------------------

_SEARCH_COLLECTION_ADD_SCHEMA: dict[str, Any] = {
    "name": "search_collection_add",
    "description": "Add a filesystem path as a RAG collection and immediately ingest it.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Filesystem path to add as a collection.",
            },
        },
        "required": ["path"],
    },
}


async def _handle_rag_collection_add(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Add a filesystem path as a RAG collection via HTTP client."""
    raw_path = arguments["path"]
    client = get_search_client()
    result = await client.add_collection(raw_path)
    if result is None:
        return f"Failed to add collection: service unavailable or error for path {raw_path!r}"
    return json.dumps(result)


# ---------------------------------------------------------------------------
# search_collection_remove
# ---------------------------------------------------------------------------

_SEARCH_COLLECTION_REMOVE_SCHEMA: dict[str, Any] = {
    "name": "search_collection_remove",
    "description": "Remove a RAG collection: drops the LanceDB table, removes from config, and cleans up the manifest.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the collection to remove.",
            },
        },
        "required": ["name"],
    },
}


async def _handle_rag_collection_remove(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Remove a RAG collection via HTTP client."""
    name = arguments["name"]
    client = get_search_client()
    result = await client.remove_collection(name)
    if result is None:
        return f"Failed to remove collection {name!r}: service unavailable or error"
    return json.dumps(result)


# ---------------------------------------------------------------------------
# search_collection_info
# ---------------------------------------------------------------------------

_SEARCH_COLLECTION_INFO_SCHEMA: dict[str, Any] = {
    "name": "search_collection_info",
    "description": "Get detailed metadata for a specific RAG collection.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "collection_name": {
                "type": "string",
                "description": "Name of the RAG collection to inspect.",
            },
        },
        "required": ["collection_name"],
    },
}


async def _handle_rag_collection_info(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Return metadata for a RAG collection via HTTP client."""
    col_name = arguments["collection_name"]
    client = get_search_client()
    result = await client.collection_info(col_name)
    if result is None:
        return f"Error: collection {col_name!r} not found or service unavailable"
    return json.dumps(result)


# ---------------------------------------------------------------------------
# search_collection_reindex
# ---------------------------------------------------------------------------

_SEARCH_COLLECTION_REINDEX_SCHEMA: dict[str, Any] = {
    "name": "search_collection_reindex",
    "description": "Force full re-ingest of a collection, bypassing change thresholds.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "collection_name": {
                "type": "string",
                "description": "Name of the collection to reindex.",
            },
        },
        "required": ["collection_name"],
    },
}


async def _handle_rag_collection_reindex(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Submit a reindex job via HTTP; returns job info string with job_id."""
    col_name = arguments["collection_name"]
    client = get_search_client()
    job = await client.reindex_collection(col_name)
    if job is None:
        return f"Error: failed to reindex collection {col_name!r}: service unavailable or error"
    return json.dumps({"job_id": job.job_id, "status": str(job.status), "collection": col_name})


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _register_search_tools(toolkit: "ArchonToolkit") -> None:
    """Register RAG-related tools into the given toolkit instance."""
    toolkit.register_tool(
        "search_status",
        _SEARCH_STATUS_SCHEMA,
        functools.partial(_handle_rag_status, toolkit),
    )
    toolkit.register_tool(
        "search_start",
        _SEARCH_START_SCHEMA,
        functools.partial(_handle_rag_start, toolkit),
    )
    toolkit.register_tool(
        "search_stop",
        _SEARCH_STOP_SCHEMA,
        functools.partial(_handle_rag_stop, toolkit),
    )
    toolkit.register_tool(
        "search_ingest",
        _SEARCH_INGEST_SCHEMA,
        functools.partial(_handle_rag_ingest, toolkit),
    )
    toolkit.register_tool(
        "search_sync",
        _SEARCH_SYNC_SCHEMA,
        functools.partial(_handle_rag_sync, toolkit),
    )
    toolkit.register_tool(
        "search_collection_list",
        _SEARCH_COLLECTION_LIST_SCHEMA,
        functools.partial(_handle_rag_collection_list, toolkit),
    )
    toolkit.register_tool(
        "search_collection_add",
        _SEARCH_COLLECTION_ADD_SCHEMA,
        functools.partial(_handle_rag_collection_add, toolkit),
    )
    toolkit.register_tool(
        "search_collection_remove",
        _SEARCH_COLLECTION_REMOVE_SCHEMA,
        functools.partial(_handle_rag_collection_remove, toolkit),
    )
    toolkit.register_tool(
        "search_collection_info",
        _SEARCH_COLLECTION_INFO_SCHEMA,
        functools.partial(_handle_rag_collection_info, toolkit),
    )
    toolkit.register_tool(
        "search_collection_reindex",
        _SEARCH_COLLECTION_REINDEX_SCHEMA,
        functools.partial(_handle_rag_collection_reindex, toolkit),
    )
