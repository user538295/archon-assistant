"""RAG-related MCP tools for ArchonToolkit (FEAT-023)."""
from __future__ import annotations

import asyncio
import functools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger("archon")

if TYPE_CHECKING:
    from archon.ai.archon_toolkit import ArchonToolkit

try:
    from archon.platform import get_search_service
    from archon.search.store import SearchStore
    from archon.search.pipeline import create_pipeline
    from archon.search.progress import (
        CollectionProgress,
        IndexingStateStore,
        IndexingStatus,
        compute_eta_seconds,
    )
    from archon.search.sync import (
        path_to_collection_name,
        SearchCollectionSync,
        manifest_lookup_by_path,
        manifest_remove_entry,
    )
    _SEARCH_AVAILABLE = True
except ImportError:
    _SEARCH_AVAILABLE = False

from archon.config.loader import load_config
from archon.config.config_rw import config_collections_append, config_collections_remove

def _resolve_status(cp: "CollectionProgress") -> str:
    """Return display status string, promoting IN_PROGRESS+processed>0 to 'partial'."""
    if cp.status == IndexingStatus.IN_PROGRESS and cp.processed_files > 0:
        return "partial"
    return str(cp.status)


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
    """Return RAG service status as a JSON string."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    # Use module-level names so tests can patch them
    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415
    try:
        rag_service_factory = _self.get_search_service
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

    watch_mode = bool(getattr(cfg.search, "watch", False))

    store = _self.SearchStore(cfg.search.db_path)
    try:
        await store.connect()
        cols = await store.list_collections()

        # Read indexing state for progress fields
        state_store = _self.IndexingStateStore(Path(cfg.search.db_path))
        state = state_store.read()

        col_dicts: list[dict[str, Any]] = []
        indexed_names: set[str] = set()
        for c in cols:
            indexed_names.add(c.name)
            d: dict[str, Any] = {"name": c.name, "doc_count": c.doc_count, "chunk_count": c.chunk_count}
            if state and c.name in state.collections:
                cp = state.collections[c.name]
                d["status"] = _resolve_status(cp)
                d["processed_files"] = cp.processed_files
                d["total_files"] = cp.total_files
                d["error"] = cp.error
                d["error_count"] = cp.error_count
                eta = compute_eta_seconds(cp)
                if eta is not None:
                    d["eta_seconds"] = eta
            d["watching"] = watch_mode
            col_dicts.append(d)

        # Include state-only collections not yet in LanceDB
        if state:
            for name, cp in state.collections.items():
                if name not in indexed_names:
                    entry: dict[str, Any] = {
                        "name": name,
                        "doc_count": 0,
                        "chunk_count": 0,
                        "status": _resolve_status(cp),
                        "processed_files": cp.processed_files,
                        "total_files": cp.total_files,
                        "error": cp.error,
                        "error_count": cp.error_count,
                    }
                    eta = compute_eta_seconds(cp)
                    if eta is not None:
                        entry["eta_seconds"] = eta
                    entry["watching"] = watch_mode
                    col_dicts.append(entry)

        return json.dumps({
            "running": True,
            "pid": info.pid,
            "collections": col_dicts,
        })
    except Exception as exc:
        logger.warning("Failed to list RAG collections: %s", exc, exc_info=True)
        return json.dumps({"running": True, "pid": info.pid, "collections": []})
    finally:
        await store.disconnect()


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

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        rag_service_factory = _self.get_search_service
    except AttributeError:
        return "RAG not available"

    try:
        rc = await _self.asyncio.to_thread(rag_service_factory().start)
    except Exception as exc:
        logger.warning("Failed to start RAG service: %s", exc, exc_info=True)
        return f"RAG service start failed: {exc}"

    if rc == 0:
        return "RAG service started."
    return f"RAG service start failed (exit code {rc})."


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

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        rag_service_factory = _self.get_search_service
    except AttributeError:
        return "RAG not available"

    try:
        rc = await _self.asyncio.to_thread(rag_service_factory().stop)
    except Exception as exc:
        logger.warning("Failed to stop RAG service: %s", exc, exc_info=True)
        return f"RAG service stop failed: {exc}"

    if rc == 0:
        return "RAG service stopped."
    return f"RAG service stop failed (exit code {rc})."


_SEARCH_INGEST_SCHEMA: dict[str, Any] = {
    "name": "search_ingest",
    "description": (
        "Ingest a directory of documents into a RAG collection. "
        "The RAG service must be stopped first (use rag_stop). "
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
    """Ingest a directory into a RAG collection."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    if toolkit._config is None:
        return "Configuration not available."

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        rag_service_factory = _self.get_search_service
        create_pipeline = _self.create_pipeline
        path_to_collection_name = _self.path_to_collection_name
    except AttributeError:
        return "RAG not available"

    try:
        status = await _self.asyncio.to_thread(rag_service_factory().status)
    except Exception as exc:
        logger.warning("Failed to get RAG service status: %s", exc, exc_info=True)
        return f"Ingest failed: could not check service status: {exc}"

    if status.running:
        return "Error: RAG service is running. Stop it first (rag_stop) to avoid data races."

    if "path" in arguments:
        resolved_path = Path(arguments["path"]).expanduser()
    else:
        resolved_path = Path(toolkit._config.history.directory).expanduser() / "sessions"

    collection = arguments.get("collection") or path_to_collection_name(str(resolved_path))

    pipeline = create_pipeline(toolkit._config.search)
    try:
        await pipeline.store.connect()
        results = await pipeline.ingest_directory(resolved_path, collection)  # TODO: route through BackgroundAgentManager as a proper background task
        ok = sum(1 for r in results if r.status == "ok")
        errors = sum(1 for r in results if r.status != "ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ingest failed: %s", exc, exc_info=True)
        return f"Ingest failed: {exc}"
    finally:
        await pipeline.store.disconnect()

    return json.dumps({"ok": ok, "errors": errors, "collection": collection})


_SEARCH_SYNC_SCHEMA: dict[str, Any] = {
    "name": "search_sync",
    "description": (
        "Reconcile all configured RAG collections with LanceDB — "
        "adds new files, removes deleted ones."
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
    """Reconcile configured RAG collections with LanceDB."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    if toolkit._config is None:
        return "Configuration not available."

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        rag_service_factory = _self.get_search_service
        create_pipeline = _self.create_pipeline
        SearchCollectionSync = _self.SearchCollectionSync
    except AttributeError:
        return "RAG not available"

    warning: str | None = None
    try:
        status = await _self.asyncio.to_thread(rag_service_factory().status)
        if status.running:
            warning = "RAG service is running — write conflicts are possible"
    except Exception as exc:
        logger.warning("Failed to get RAG service status: %s", exc, exc_info=True)

    pipeline = create_pipeline(toolkit._config.search)
    try:
        await pipeline.store.connect()
        state_store = _self.IndexingStateStore(Path(toolkit._config.search.db_path))
        sync = SearchCollectionSync(
            pipeline,
            state_store=state_store,
            pinned_collections=toolkit._config.search.pinned_collections,
            embedding_model=toolkit._config.search.embedding_model,
            chunk_size=toolkit._config.search.chunk_size,
            auto_reindex_on_chunk_size_change=toolkit._config.search.auto_reindex_on_chunk_size_change,
        )
        try:
            state_store.set_trigger("manual")
        except Exception as exc:
            logger.warning("rag_sync: failed to write manual trigger (notification suppression may not apply): %s", exc)
        result = await sync.sync(  # TODO: route through BackgroundAgentManager as a proper background task
            toolkit._config.search.collections
        )
        payload: dict[str, Any] = {
            "added": list(result.added),
            "removed": list(result.removed),
            "unchanged": len(result.unchanged),
            "errors": list(result.errors),
            "updated": list(result.updated),
        }
        if warning:
            payload["warning"] = warning
        return json.dumps(payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sync failed: %s", exc, exc_info=True)
        return f"Sync failed: {exc}"
    finally:
        await pipeline.store.disconnect()


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
    """List all RAG collections with status, path, doc and chunk counts."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        cfg = _self.load_config(require_token=False)
    except Exception as exc:
        logger.warning("Failed to load config: %s", exc, exc_info=True)
        return f"Configuration error: {exc}"
    if cfg.search is None:
        return "Configuration not available."

    db_path = Path(cfg.search.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"

    # Load manifest: {collection_name: source_path}
    manifest: dict[str, str] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Build desired set from config: {collection_name: source_path}
    desired: dict[str, str] = {}
    for raw_path in cfg.search.collections:
        name = _self.path_to_collection_name(raw_path)
        desired[name] = raw_path

    store = _self.SearchStore(cfg.search.db_path)
    exc_to_return: Exception | None = None
    try:
        await store.connect()
        collections = await store.list_collections()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to list RAG collections: %s", exc, exc_info=True)
        exc_to_return = exc
        collections = []
    finally:
        await store.disconnect()

    if exc_to_return is not None:
        return f"Error: {exc_to_return}"

    result: list[dict[str, Any]] = []

    # Process LanceDB collections
    indexed_names = {c.name for c in collections}
    for col in collections:
        name = col.name
        if name in desired:
            status = "indexed"
            path_str = desired[name]
        elif name in manifest:
            status = "orphan (managed)"
            path_str = manifest[name]
        else:
            status = "unmanaged"
            path_str = "(unknown)"
        result.append({
            "name": name,
            "path": path_str,
            "doc_count": col.doc_count,
            "chunk_count": col.chunk_count,
            "status": status,
        })

    # Add config paths not yet indexed
    for name, raw_path in desired.items():
        if name not in indexed_names:
            result.append({
                "name": name,
                "path": raw_path,
                "doc_count": 0,
                "chunk_count": 0,
                "status": "not yet indexed",
            })

    return json.dumps(result)


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
    """Add a filesystem path as a RAG collection and ingest it."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        cfg = _self.load_config(require_token=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load config: %s", exc, exc_info=True)
        return f"Configuration error: {exc}"

    if cfg.search is None:
        return "Configuration not available."

    raw_path = arguments["path"]
    resolved = Path(raw_path).expanduser().resolve()

    # Check duplicate: compare resolved path against each configured path
    for existing in cfg.search.collections:
        if Path(existing).expanduser().resolve() == resolved:
            return f"Already registered: {resolved}"

    # Service-running guard (non-blocking warning)
    warning = ""
    try:
        status = await asyncio.to_thread(_self.get_search_service().status)
        if status.running:
            warning = "Warning: RAG service is running — write conflicts are possible."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not check RAG service status: %s", exc, exc_info=True)

    # Determine config file path
    config_file = (
        Path(toolkit._config_file) if toolkit._config_file else Path("~/.archon/config.toml").expanduser()
    )

    # Append to config
    _self.config_collections_append(config_file, raw_path)

    # Determine collection name: manifest lookup first, then fallback
    db_path = Path(cfg.search.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"
    col_name = (
        _self.manifest_lookup_by_path(manifest_path, str(resolved))
        or _self.path_to_collection_name(raw_path)
    )

    # Ingest
    pipeline = _self.create_pipeline(cfg.search)
    exc_to_return: Exception | None = None
    try:
        await pipeline.store.connect()
        await pipeline.ingest_directory(resolved, col_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ingest failed for %s: %s", raw_path, exc, exc_info=True)
        exc_to_return = exc
    finally:
        await pipeline.store.disconnect()

    if exc_to_return is not None:
        return f"Ingest error: {exc_to_return}"

    success = f"Collection added and indexed: {raw_path}"
    return f"{warning} {success}" if warning else success


_SEARCH_COLLECTION_REMOVE_SCHEMA: dict[str, Any] = {
    "name": "search_collection_remove",
    "description": "Remove a RAG collection: drops the LanceDB table, removes from config, and cleans up the manifest.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Filesystem path of the collection to remove.",
            },
            "force": {
                "type": "boolean",
                "description": "If true, remove even if the RAG service is running. Default false.",
            },
        },
        "required": ["path"],
    },
}


async def _handle_rag_collection_remove(
    toolkit: "ArchonToolkit",
    arguments: dict[str, Any],
    *,
    user_id: int | None = None,
) -> str:
    """Remove a RAG collection from the store, config, and manifest."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        cfg = _self.load_config(require_token=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load config: %s", exc, exc_info=True)
        return f"Configuration error: {exc}"

    if cfg.search is None:
        return "Configuration not available."

    raw_path = arguments["path"]
    force = arguments.get("force", False)
    resolved = Path(raw_path).expanduser().resolve()

    # Check path is in config
    registered = any(Path(p).expanduser().resolve() == resolved for p in cfg.search.collections)
    if not registered:
        return f"Error: not in collections: {raw_path}"

    # Determine collection name
    db_path = Path(cfg.search.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"
    col_name = (
        _self.manifest_lookup_by_path(manifest_path, str(resolved))
        or _self.path_to_collection_name(raw_path)
    )

    # Service-running guard (hard block unless force=True)
    try:
        status = await asyncio.to_thread(_self.get_search_service().status)
        if status.running and not force:
            return "Error: RAG service is running. Use force=true to remove anyway."
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not check RAG service status: %s", exc, exc_info=True)
        return f"Error: could not check RAG service status: {exc}"

    # Drop collection
    store = _self.SearchStore(cfg.search.db_path)
    exc_to_return: Exception | None = None
    try:
        await store.connect()
        try:
            await store.drop_collection(col_name)
        except KeyError:
            pass  # collection not in store — still clean up config/manifest
    except Exception as exc:  # noqa: BLE001
        logger.warning("Drop failed for %s: %s", col_name, exc, exc_info=True)
        exc_to_return = exc
    finally:
        await store.disconnect()

    if exc_to_return is not None:
        return f"Drop failed: {exc_to_return}"

    # Clean up config and manifest
    config_file = (
        Path(toolkit._config_file) if toolkit._config_file else Path("~/.archon/config.toml").expanduser()
    )
    _self.config_collections_remove(config_file, raw_path)
    _self.manifest_remove_entry(manifest_path, col_name)

    return f"Collection removed: {raw_path}"


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
    """Return metadata for a RAG collection as a JSON string."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        cfg = _self.load_config(require_token=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load config: %s", exc, exc_info=True)
        return f"Configuration error: {exc}"

    if cfg.search is None:
        return "Configuration not available."

    col_name = arguments["collection_name"]
    pipeline = _self.create_pipeline(cfg.search)
    exc_to_return: Exception | None = None
    meta = None
    try:
        await pipeline.store.connect()
        meta = await pipeline.get_collection_meta(col_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get collection meta for %s: %s", col_name, exc, exc_info=True)
        exc_to_return = exc
    finally:
        await pipeline.store.disconnect()

    if exc_to_return is not None:
        return f"Error: {exc_to_return}"

    if meta is None:
        return f"Error: collection {col_name!r} not found."

    return json.dumps({
        "name": meta.name,
        "description": meta.description,
        "doc_count": meta.doc_count,
        "chunk_count": meta.chunk_count,
        "embedding_model": meta.embedding_model,
        "centroid": meta.centroid is not None,
        "last_indexed": meta.last_indexed.isoformat() if meta.last_indexed else None,
    })


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
    """Force a full re-ingest of a named RAG collection."""
    if not _SEARCH_AVAILABLE:
        return "RAG not available"

    import archon.ai.archon_toolkit_search as _self  # noqa: PLC0415

    try:
        cfg = _self.load_config(require_token=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to load config: %s", exc, exc_info=True)
        return f"Configuration error: {exc}"

    if cfg.search is None:
        return "Configuration not available."

    col_name = arguments["collection_name"]

    try:
        status = await _self.asyncio.to_thread(_self.get_search_service().status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to get RAG service status: %s", exc, exc_info=True)
        return f"Error: could not check RAG service status: {exc}"

    if status.running:
        return "Error: RAG service is running. Stop it first (rag_stop)."

    # Find source path for the collection
    resolved: Path | None = None
    for raw in cfg.search.collections:
        if _self.path_to_collection_name(raw) == col_name:
            resolved = Path(raw).expanduser().resolve()
            break

    if resolved is None:
        return f"Error: collection {col_name!r} not found in config."

    # Clear prior state (including processed_paths) to force a full re-index
    try:
        _self.IndexingStateStore(Path(cfg.search.db_path)).remove_collection(col_name)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to clear indexing state for %r before reindex", col_name)

    pipeline = _self.create_pipeline(cfg.search)
    exc_to_return: Exception | None = None
    ok = 0
    errors = 0
    try:
        await pipeline.store.connect()
        results = await pipeline.ingest_directory(resolved, col_name, force_regenerate_description=True)
        ok = sum(1 for r in results if r.status == "ok")
        errors = sum(1 for r in results if r.status != "ok")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reindex failed for %s: %s", col_name, exc, exc_info=True)
        exc_to_return = exc
    finally:
        await pipeline.store.disconnect()

    if exc_to_return is not None:
        return f"Error: {exc_to_return}"

    return json.dumps({"ok": ok, "errors": errors})


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
