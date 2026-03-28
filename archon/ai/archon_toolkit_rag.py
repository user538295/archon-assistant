"""RAG-related MCP tools for ArchonToolkit (FEAT-023)."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("archon")

if TYPE_CHECKING:
    from archon.ai.archon_toolkit import ArchonToolkit

try:
    from archon.platform import get_rag_service
    from archon.rag.store import RagStore
    _RAG_AVAILABLE = True
except ImportError:
    _RAG_AVAILABLE = False

_RAG_STATUS_SCHEMA: dict[str, Any] = {
    "name": "rag_status",
    "description": (
        "Check RAG service status — whether it is running, its PID, "
        "and the list of indexed collections with document and chunk counts."
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
    """Return RAG service status as a JSON string."""
    if not _RAG_AVAILABLE:
        return "RAG not available"

    # Use module-level names so tests can patch them
    import archon.ai.archon_toolkit_rag as _self  # noqa: PLC0415
    try:
        rag_service_factory = _self.get_rag_service
    except AttributeError:
        return "RAG not available"

    try:
        info = await _self.asyncio.to_thread(rag_service_factory().status)
    except Exception as exc:
        logger.warning("Failed to get RAG service status: %s", exc, exc_info=True)
        return json.dumps({"running": False, "pid": None, "collections": [], "error": "service unavailable"})

    if not info.running:
        return json.dumps({"running": False, "pid": None, "collections": []})

    # Service is running — fetch collections
    cfg = toolkit._config
    if cfg is None:
        return json.dumps({"running": True, "pid": info.pid, "collections": []})

    store = _self.RagStore(cfg.rag.db_path)
    try:
        await store.connect()
        cols = await store.list_collections()
        return json.dumps({
            "running": True,
            "pid": info.pid,
            "collections": [
                {"name": c.name, "doc_count": c.doc_count, "chunk_count": c.chunk_count}
                for c in cols
            ],
        })
    except Exception as exc:
        logger.warning("Failed to list RAG collections: %s", exc, exc_info=True)
        return json.dumps({"running": True, "pid": info.pid, "collections": []})
    finally:
        await store.disconnect()


def _register_rag_tools(toolkit: "ArchonToolkit") -> None:
    """Register RAG-related tools into the given toolkit instance."""
    toolkit.register_tool(
        "rag_status",
        _RAG_STATUS_SCHEMA,
        functools.partial(_handle_rag_status, toolkit),
    )
