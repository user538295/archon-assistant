"""archon search — CLI subcommand for managing the search service."""
from __future__ import annotations

import argparse
import asyncio
import logging
import math
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from archon.config.config_rw import config_collections_append, config_collections_remove
from archon.config.loader import load_config

logger = logging.getLogger("archon")

_CONFIG_PATH = Path.home() / ".archon" / "config.toml"


def _path_to_collection_name(path: str) -> str:
    """Derive a sanitized LanceDB collection name from a filesystem path."""
    resolved = Path(path).expanduser().resolve()
    name = re.sub(r"[^a-z0-9]+", "_", resolved.name.lower()).strip("_")
    return name or "collection"


def _base_url(cfg: object) -> str:
    """Build the SearchClient base URL from config."""
    return str(cfg.search.url)  # type: ignore[union-attr]


def _run_archon_search(*args: str) -> int:
    """Delegate to the standalone archon-search CLI. Returns exit code."""
    try:
        result = subprocess.run(["archon-search", *args])
        return result.returncode
    except FileNotFoundError:
        print("Error: 'archon-search' not found in PATH. Install it first: uv tool install archon-search")
        return 1


def run_search(
    args: argparse.Namespace,
    search_parser: argparse.ArgumentParser | None = None,
    collection_parser: argparse.ArgumentParser | None = None,
) -> int:
    """Dispatch to the appropriate search sub-action."""
    if args.search_command is None or args.search_command == "help":
        if search_parser is not None:
            search_parser.print_help()
        else:
            print("Usage: archon search <install|uninstall|start|stop|status|ingest|sync|collection>")
        return 0

    if args.search_command == "collection":
        return _run_collection(args, collection_parser=collection_parser)

    dispatch = {
        "install": _run_install,
        "uninstall": _run_uninstall,
        "start": _run_start,
        "stop": _run_stop,
        "status": _run_status,
        "ingest": _run_ingest,
        "sync": _run_sync,
    }
    action = dispatch.get(args.search_command)
    if action is None:
        print("Usage: archon search <install|uninstall|start|stop|status|ingest|sync|collection>")
        return 1
    return action(args)


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

def _run_install(args: argparse.Namespace) -> int:
    cmd = ["install"]
    if args.dry_run:
        cmd.append("--dry-run")
    if args.non_interactive:
        cmd.append("--non-interactive")
    return _run_archon_search(*cmd)


def _run_uninstall(args: argparse.Namespace) -> int:
    cmd = ["uninstall"]
    if args.delete_db:
        cmd.append("--delete-db")
    return _run_archon_search(*cmd)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def _run_start(args: argparse.Namespace) -> int:
    rc = _run_archon_search("start")
    if rc == 0:
        print("Search service started.")
    else:
        print(f"Search service start failed (exit code {rc}).")
    return rc


def _run_stop(args: argparse.Namespace) -> int:
    rc = _run_archon_search("stop")
    if rc == 0:
        print("Search service stopped.")
    else:
        print(f"Search service stop failed (exit code {rc}).")
    return rc


# ---------------------------------------------------------------------------
# Progress display helpers (duck-typed — work with CollectionProgress-like objects)
# ---------------------------------------------------------------------------


def compute_eta_seconds(cp: object, now: datetime | None = None) -> int | None:
    """Compute estimated seconds remaining for an in-progress collection.

    Works via duck-typing on CollectionProgress-like objects. Returns None when
    ETA cannot be computed (too early, missing data, not in-progress, etc.).
    """
    status = getattr(cp, "status", None)
    if status is None:
        return None
    # Accept both string "in_progress" and StrEnum with value "in_progress"
    status_val = status.value if hasattr(status, "value") else str(status)
    if status_val != "in_progress":
        return None
    processed = getattr(cp, "processed_files", 0)
    total = getattr(cp, "total_files", 0)
    started_at = getattr(cp, "started_at", None)
    if processed < 10:
        return None
    if started_at is None:
        return None
    if processed >= total:
        return None
    try:
        started = datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        return None
    if now is None:
        now = datetime.now(UTC)
    if getattr(started, "tzinfo", None) is None:
        started = started.replace(tzinfo=UTC)
    if getattr(now, "tzinfo", None) is None:
        now = now.replace(tzinfo=UTC)
    elapsed = (now - started).total_seconds()
    if elapsed <= 0:
        return None
    fps = processed / elapsed
    remaining = total - processed
    return max(0, int(remaining / fps))


def _print_progress_table(
    state: object,
    collections: list,  # type: ignore[type-arg]
    watching: bool = False,
) -> bool:
    """Print a progress table for all collections in state. Returns True if any FAILED.

    Works via duck-typing: accepts IndexingState-like objects or plain dicts.
    """
    # Gather state collections (supports dict and dataclass)
    if hasattr(state, "collections"):
        state_cols = state.collections  # type: ignore[union-attr]
    else:
        state_cols = {}

    # Build name→info mapping from LanceDB collections
    lancedb_by_name: dict[str, object] = {}
    for col in collections:
        if hasattr(col, "name"):
            lancedb_by_name[col.name] = col
        elif isinstance(col, dict):
            lancedb_by_name[col.get("name", "")] = col

    all_names = set(state_cols.keys()) | set(lancedb_by_name.keys())
    has_failed = False

    for name in sorted(all_names):
        cp = state_cols.get(name)
        if cp is not None:
            status = getattr(cp, "status", None)
            status_val = status.value if hasattr(status, "value") else str(status)
            processed = getattr(cp, "processed_files", 0)
            total = getattr(cp, "total_files", 0)
            error = getattr(cp, "error", None)

            if status_val == "failed":
                has_failed = True

            if status_val == "pending":
                progress_str = "—"
            elif status_val in ("in_progress", "done"):
                if processed > 0:
                    progress_str = f"{'partial' if status_val == 'in_progress' else 'done'}  {processed} / {total} files"
                else:
                    progress_str = f"in_progress  0 / {total} files"
                    status_val = "in_progress"  # normalize
                if status_val == "done":
                    progress_str = f"done  {processed} / {total} files"
            else:
                progress_str = status_val

            suffix = ""
            if error:
                suffix += f"  [{error}]"

            if status_val == "in_progress":
                eta = compute_eta_seconds(cp)
                if eta is not None:
                    if eta < 60:
                        suffix += "  < 1 min remaining"
                    else:
                        mins = math.ceil(eta / 60)
                        suffix += f"  ~{mins} min remaining"

            # Watch indicator: shown for DONE and IN_PROGRESS (not FAILED/PENDING)
            watch_suffix = ""
            if watching and status_val in ("done", "in_progress"):
                watch_suffix = "  (watch)"

            print(f"  {name}  {progress_str}{suffix}{watch_suffix}")
        else:
            # Collection in LanceDB but not in state
            info = lancedb_by_name.get(name, {})
            if isinstance(info, dict):
                doc_count = info.get("doc_count", 0)
                chunk_count = info.get("chunk_count", 0)
            else:
                doc_count = getattr(info, "doc_count", 0)
                chunk_count = getattr(info, "chunk_count", 0)
            print(f"  collection={name}  docs={doc_count}  chunks={chunk_count}")

    return has_failed


# ---------------------------------------------------------------------------
# Dict-backed duck-typed helpers for HTTP-sourced state data
# ---------------------------------------------------------------------------


class _DictCollectionProgress:
    """Wraps a plain dict (from HTTP /indexing-state) to look like CollectionProgress."""

    def __init__(self, data: dict) -> None:  # type: ignore[type-arg]
        self.status = _StrStatus(data.get("status", "pending"))
        self.total_files: int = int(data.get("total_files", 0))
        self.processed_files: int = int(data.get("processed_files", 0))
        self.started_at: str | None = data.get("started_at")
        self.error: str | None = data.get("error")


class _StrStatus:
    """Minimal status wrapper so duck-typed code can call .value."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _StrStatus):
            return self.value == other.value
        return self.value == str(other)


class _DictIndexingState:
    """Wraps an indexing-state dict (from HTTP) to look like IndexingState."""

    def __init__(self, data: dict) -> None:  # type: ignore[type-arg]
        raw_cols = data.get("collections", {})
        self.collections: dict[str, _DictCollectionProgress] = {
            name: _DictCollectionProgress(col_data)
            for name, col_data in (raw_cols.items() if isinstance(raw_cols, dict) else {}.items())
        }


# ---------------------------------------------------------------------------
# status — uses SearchClient HTTP
# ---------------------------------------------------------------------------

def _run_status(args: argparse.Namespace) -> int:
    from archon.ai.search_client import SearchClient

    cfg = load_config(require_token=False)
    base_url = _base_url(cfg)
    client = SearchClient(base_url=base_url)

    async def _get_data() -> tuple[dict | None, dict | None]:
        async with client:
            status = await client.status()
            state = await client.indexing_state() if status is not None else None
            return status, state

    try:
        status_data, state_data = asyncio.run(_get_data())
    except Exception as exc:
        status_data = None
        state_data = None
        logger.debug("search status failed: %s", exc)

    if status_data is None:
        print("Search service: stopped (unreachable)")
        return 1

    running = status_data.get("running", False)
    pid = status_data.get("pid")
    if running:
        print(f"Search service: running (pid={pid})")
    else:
        print("Search service: stopped (unreachable)")
        return 1

    collections = status_data.get("collections", [])

    # Show progress table if we have indexing state data
    if state_data and state_data.get("collections"):
        # Build a duck-typed state object from the dict
        _state = _DictIndexingState(state_data)
        has_failed = _print_progress_table(_state, collections)
        return 1 if has_failed else 0

    # Fallback: simple collection list
    if collections:
        for col in collections:
            name = col.get("name", "?")
            doc_count = col.get("doc_count", 0)
            chunk_count = col.get("chunk_count", 0)
            print(f"  collection={name}  docs={doc_count}  chunks={chunk_count}")
    else:
        print("  No collections found.")
    return 0


# ---------------------------------------------------------------------------
# ingest — delegates to standalone CLI
# ---------------------------------------------------------------------------

def _run_ingest(args: argparse.Namespace) -> int:
    cmd = ["ingest"]
    if args.path:
        cmd.extend(["--path", args.path])
    if args.collection:
        cmd.extend(["--collection", args.collection])
    return _run_archon_search(*cmd)


# ---------------------------------------------------------------------------
# sync — delegates to standalone CLI
# ---------------------------------------------------------------------------


def _run_sync(args: argparse.Namespace) -> int:
    """Reconcile all configured collections via standalone CLI."""
    return _run_archon_search("sync")


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------


def _run_collection(
    args: argparse.Namespace,
    collection_parser: argparse.ArgumentParser | None = None,
) -> int:
    """Dispatch to the appropriate collection sub-action."""
    collection_command = getattr(args, "collection_command", None)

    if collection_command is None or collection_command == "help":
        if collection_parser is not None:
            collection_parser.print_help()
        else:
            print("Usage: archon search collection <list|add|remove|info|reindex>")
        return 0

    dispatch = {
        "list": _run_collection_list,
        "add": _run_collection_add,
        "remove": _run_collection_remove,
        "info": _run_collection_info,
        "reindex": _run_collection_reindex,
    }
    action = dispatch.get(collection_command)
    if action is None:
        print("Usage: archon search collection <list|add|remove|info|reindex>")
        return 1
    return action(args)


def _run_collection_list(args: argparse.Namespace) -> int:
    """List all collections via SearchClient HTTP."""
    from archon.ai.search_client import SearchClient

    cfg = load_config(require_token=False)
    base_url = _base_url(cfg)
    client = SearchClient(base_url=base_url)

    async def _list() -> list[dict]:  # type: ignore[type-arg]
        async with client:
            return await client.list_collections()

    try:
        collections = asyncio.run(_list())
    except Exception as exc:
        print(f"Error listing collections: {exc}")
        return 1

    if not collections:
        print("No collections found.")
        return 0

    for col in collections:
        name = col.get("name", "?")
        path_str = col.get("path", "(unknown)")
        doc_count = col.get("doc_count", 0)
        chunk_count = col.get("chunk_count", 0)
        status = col.get("status", "indexed")
        print(f"{name}  path={path_str}  docs={doc_count}  chunks={chunk_count}  status={status}")

    return 0


def _run_collection_add(args: argparse.Namespace) -> int:
    """Register and ingest a new collection via SearchClient HTTP."""
    from archon.ai.search_client import SearchClient

    cfg = load_config(require_token=False)
    base_url = _base_url(cfg)
    client = SearchClient(base_url=base_url)

    async def _add() -> dict | None:
        async with client:
            return await client.add_collection(args.path)

    try:
        result = asyncio.run(_add())
    except Exception as exc:
        print(f"Error adding collection: {exc}")
        return 1

    if result is None:
        print(f"Error: failed to add collection {args.path!r} — is the search service running?")
        return 1

    print(f"Collection added: {args.path}")
    return 0


def _run_collection_info(args: argparse.Namespace) -> int:
    """Print collection info via SearchClient HTTP."""
    from archon.ai.search_client import SearchClient

    cfg = load_config(require_token=False)
    base_url = _base_url(cfg)
    client = SearchClient(base_url=base_url)

    async def _fetch() -> dict | None:
        async with client:
            return await client.collection_info(args.collection_name)

    try:
        meta = asyncio.run(_fetch())
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    if meta is None:
        print(f"Error: collection {args.collection_name!r} not found.")
        return 1

    print(f"name:            {meta.get('name', '?')}")
    print(f"description:     {meta.get('description') or '(none)'}")
    print(f"doc_count:       {meta.get('doc_count', 0)}")
    print(f"chunk_count:     {meta.get('chunk_count', 0)}")
    print(f"embedding_model: {meta.get('embedding_model', '?')}")
    centroid_status = "present" if meta.get("centroid") is not None else "absent"
    print(f"centroid:        {centroid_status}")
    if meta.get("last_indexed"):
        print(f"last_indexed:    {meta['last_indexed']}")
    return 0


def _run_collection_reindex(args: argparse.Namespace) -> int:
    """Force full re-ingest of a collection via SearchClient HTTP."""
    from archon.ai.search_client import SearchClient

    cfg = load_config(require_token=False)
    base_url = _base_url(cfg)
    client = SearchClient(base_url=base_url)

    async def _reindex() -> object:
        async with client:
            return await client.reindex_collection(args.collection_name)

    try:
        job = asyncio.run(_reindex())
    except Exception as exc:
        print(f"Reindex failed: {exc}")
        return 1

    if job is None:
        print(f"Error: failed to reindex {args.collection_name!r} — is the search service running?")
        return 1

    print(f"Reindex job submitted: {args.collection_name!r}")
    return 0


def _run_collection_remove(args: argparse.Namespace) -> int:
    """Remove a registered filesystem path from RAG collections via SearchClient HTTP."""
    dry_run: bool = args.dry_run
    force: bool = args.force

    # Mutual exclusivity guard
    if dry_run and force:
        print("Error: --dry-run and --force are mutually exclusive.")
        return 1

    resolved = Path(args.path).expanduser().resolve()

    cfg = load_config(require_token=False)

    # Check if path is registered; use server-sourced collection list when available.
    # Fallback: check config-side collections/pinned_collections if attributes exist (mocks/legacy).
    all_indexed = getattr(cfg.search, "all_indexed_collections", None)
    if all_indexed is not None:
        in_all = any(
            Path(stored).expanduser().resolve() == resolved for stored in all_indexed
        )
        if not in_all:
            print(f"Error: not in collections: {args.path}")
            return 1
        in_collections = any(
            Path(stored).expanduser().resolve() == resolved
            for stored in getattr(cfg.search, "collections", [])
        )
        if not in_collections:
            print(
                f"Error: '{args.path}' is a pinned collection and cannot be removed. "
                "Edit pinned_collections in config.toml to change it."
            )
            return 1

    # Determine collection name: sanitized last path component.
    col_name = _path_to_collection_name(args.path)

    # Dry-run: print what would be removed and exit without executing
    if dry_run:
        print(f"Would remove config entry: {args.path}")
        print(f"Would drop LanceDB table: {col_name}")
        return 0

    # Delegate removal to SearchClient HTTP
    from archon.ai.search_client import SearchClient

    base_url = _base_url(cfg)
    client = SearchClient(base_url=base_url)

    async def _remove() -> dict | None:
        async with client:
            return await client.remove_collection(col_name)

    try:
        result = asyncio.run(_remove())
    except Exception as exc:
        if not force:
            print(f"Drop failed: {exc}")
            return 1
        result = None  # service unreachable — force falls through to config removal

    if result is None and not force:
        print("Error: failed to remove collection — is the search service running? Use --force to remove from config only.")
        return 1

    # Remove from config
    config_collections_remove(_CONFIG_PATH, args.path)

    in_pinned = any(
        Path(p).expanduser().resolve() == resolved
        for p in getattr(cfg.search, "pinned_collections", [])
    )
    if in_pinned:
        print(f"Collection removed: {args.path} (note: still indexed as a pinned collection)")
    else:
        print(f"Collection removed: {args.path}")
    return 0
