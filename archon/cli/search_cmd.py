"""archon search — CLI subcommand for managing the search service."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
from pathlib import Path

from archon.config.config_rw import config_collections_append, config_collections_remove
from archon.config.loader import load_config
from archon.platform import get_search_service
from archon.search.install import SearchInstaller
from archon.search.pipeline import create_pipeline
from archon.search.progress import IndexingState, IndexingStateStore, IndexingStatus, compute_eta_seconds
from archon.search.store import SearchStore
from archon.search.sync import SearchCollectionSync, manifest_lookup_by_path, manifest_remove_entry, path_to_collection_name

logger = logging.getLogger("archon")

_CONFIG_PATH = Path.home() / ".archon" / "config.toml"


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
    installer = SearchInstaller(dry_run=args.dry_run)
    return installer.run(non_interactive=args.non_interactive)


def _run_uninstall(args: argparse.Namespace) -> int:
    installer = SearchInstaller()
    return installer.run_uninstall(delete_db=args.delete_db)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def _run_start(args: argparse.Namespace) -> int:
    rc = get_search_service().start()
    if rc == 0:
        print("Search service started.")
    else:
        print(f"Search service start failed (exit code {rc}).")
    return rc


def _run_stop(args: argparse.Namespace) -> int:
    rc = get_search_service().stop()
    if rc == 0:
        print("Search service stopped.")
    else:
        print(f"Search service stop failed (exit code {rc}).")
    return rc


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _run_status(args: argparse.Namespace) -> int:
    info = get_search_service().status()
    if not info.running:
        print(f"Search service: stopped (unreachable)")
        return 1

    print(f"Search service: running (pid={info.pid})")

    cfg = load_config(require_token=False)
    store = SearchStore(cfg.search.db_path)

    # Try reading indexing state for progress display
    state = _read_indexing_state(cfg.search.db_path)

    async def _get_stats() -> list:
        try:
            await store.connect()
            return await store.list_collections()
        except Exception as exc:
            print(f"  Stats unavailable — server may be writing ({exc})")
            return []
        finally:
            await store.disconnect()

    try:
        collections = asyncio.run(_get_stats())
    except Exception as exc:
        print(f"  Stats unavailable: {exc}")
        collections = []

    if state is not None:
        watching = info.running and cfg.search.watch
        return _print_progress_table(state, collections, watching=watching)

    # Fallback: no state file — use old format
    if collections:
        for col in collections:
            print(f"  collection={col.name}  docs={col.doc_count}  chunks={col.chunk_count}")
    else:
        print("  No collections found.")
    return 0


def _read_indexing_state(db_path: str) -> IndexingState | None:
    """Read indexing state from the state store. Returns None if missing/corrupt."""
    from archon.search.progress import IndexingStateStore  # noqa: PLC0415
    return IndexingStateStore(Path(db_path)).read()


def _print_progress_table(state: IndexingState, collections: list, watching: bool = False) -> int:
    """Print a merged progress table and return exit code (1 if any failed, else 0)."""
    # Build lookup of LanceDB collections by name
    col_by_name = {col.name: col for col in collections}

    # Merge: all state entries + LanceDB-only entries
    all_names: list[str] = list(state.collections.keys())
    for col in collections:
        if col.name not in state.collections:
            all_names.append(col.name)

    if not all_names:
        print("  No collections found.")
        return 0

    # Print header
    print(f"  {'Collection':<20} {'Status':<22} {'Progress'}")
    print(f"  {'\u2500' * 50}")

    has_failed = False
    for name in all_names:
        progress = state.collections.get(name)
        if progress is not None:
            status_str = str(progress.status)
            if progress.status == IndexingStatus.IN_PROGRESS and progress.processed_files > 0:
                status_str = "partial"
            if progress.status == IndexingStatus.PENDING:
                progress_str = "\u2014"
            else:
                progress_str = f"{progress.processed_files} / {progress.total_files} files"
                if progress.error:
                    progress_str += f"  ({progress.error})"
                if progress.status == IndexingStatus.IN_PROGRESS:
                    eta = compute_eta_seconds(progress)
                    if eta is not None:
                        if eta < 60:
                            progress_str += "  < 1 min remaining"
                        else:
                            progress_str += f"  ~{math.ceil(eta / 60)} min remaining"
            if progress.status == IndexingStatus.FAILED:
                has_failed = True
        else:
            # LanceDB-only: no state file entry
            col = col_by_name[name]
            status_str = "indexed"
            progress_str = f"{col.doc_count} docs, {col.chunk_count} chunks"

        if watching and progress is not None and progress.status in (
            IndexingStatus.DONE, IndexingStatus.IN_PROGRESS
        ):
            status_str += " (watch)"

        print(f"  {name:<20} {status_str:<22} {progress_str}")

    return 1 if has_failed else 0


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _run_ingest(args: argparse.Namespace) -> int:
    info = get_search_service().status()
    if info.running:
        print("Error: Search service is running. Stop it before ingesting to avoid data races.")
        print("  archon search stop")
        return 1

    from archon.search.sync import path_to_collection_name  # noqa: PLC0415

    cfg = load_config(require_token=False)
    path = Path(args.path) if args.path else Path(cfg.history.directory).expanduser() / "sessions"
    collection = args.collection or path_to_collection_name(
        str(Path(cfg.history.directory).expanduser() / "sessions")
    )

    pipeline = create_pipeline(cfg.search)

    async def _ingest() -> int:
        try:
            await pipeline.store.connect()
            results = await pipeline.ingest_directory(path, collection)
            ok = sum(1 for r in results if r.status == "ok")
            errors = sum(1 for r in results if r.status == "error")
            print(f"Ingest complete: {ok} ingested, {errors} errors.")
            return 0
        except Exception as exc:
            print(f"Ingest failed: {exc}")
            return 1
        finally:
            await pipeline.store.disconnect()

    return asyncio.run(_ingest())


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


def _run_sync(args: argparse.Namespace) -> int:
    """Reconcile all configured collections with LanceDB."""
    info = get_search_service().status()
    if info.running:
        print("Warning: Search service is running — write conflicts are possible.")

    cfg = load_config(require_token=False)
    pipeline = create_pipeline(cfg.search)

    def _progress(done: int, total: int) -> None:
        print(f"  [{done}/{total}] files processed")

    async def _do_sync():
        try:
            await pipeline.store.connect()
            state_store = IndexingStateStore(Path(cfg.search.db_path))
            sync = SearchCollectionSync(
                pipeline,
                state_store=state_store,
                pinned_collections=cfg.search.pinned_collections,
                embedding_model=cfg.search.embedding_model,
                chunk_size=cfg.search.chunk_size,
                auto_reindex_on_chunk_size_change=cfg.search.auto_reindex_on_chunk_size_change,
            )
            return await sync.sync(cfg.search.collections, progress_cb=_progress)
        finally:
            await pipeline.store.disconnect()

    result = asyncio.run(_do_sync())

    print(
        f"Sync complete: {len(result.added)} added, {len(result.updated)} updated, "
        f"{len(result.removed)} removed, {len(result.unchanged)} unchanged, "
        f"{len(result.errors)} errors."
    )
    for name in result.added:
        print(f"  + {name}")
    for name in result.updated:
        print(f"  \u21bb {name}")
    for name in result.removed:
        print(f"  - {name}")
    for err in result.errors:
        print(f"  ! {err}")

    return 1 if result.errors else 0


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
    """List all LanceDB collections with status, path, doc and chunk counts."""
    cfg = load_config(require_token=False)
    db_path = Path(cfg.search.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"
    store = SearchStore(cfg.search.db_path)

    # Load manifest: {collection_name: source_path}
    manifest: dict[str, str] = {}
    if manifest_path.exists():
        import json  # noqa: PLC0415

        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    # Build desired set from config: {collection_name: source_path}
    desired: dict[str, str] = {}
    for raw_path in cfg.search.collections:
        name = path_to_collection_name(raw_path)
        desired[name] = raw_path

    async def _list() -> list:
        try:
            await store.connect()
            return await store.list_collections()
        finally:
            await store.disconnect()

    collections = asyncio.run(_list())

    if not collections and not desired:
        print("No collections found.")
        return 0

    # Print LanceDB collections
    for col in collections:
        name = col.name
        # Determine path: manifest is primary, fallback to desired, else "(unknown)"
        if name in manifest:
            path_str = manifest[name]
        elif name in desired:
            path_str = desired[name]
        else:
            path_str = "(unknown)"

        # Determine status
        if name in desired:
            status = "indexed"
        elif name in manifest:
            status = "orphan (managed)"
        else:
            status = "unmanaged"

        print(f"{name}  path={path_str}  docs={col.doc_count}  chunks={col.chunk_count}  status={status}")

    # Print config paths not yet indexed
    indexed_names = {col.name for col in collections}
    for name, raw_path in desired.items():
        if name not in indexed_names:
            print(f"{name}  path={raw_path}  (not yet indexed)")

    return 0


def _run_collection_add(args: argparse.Namespace) -> int:
    """Register a new filesystem path as a RAG collection and ingest it immediately."""
    resolved = Path(args.path).expanduser().resolve()

    cfg = load_config(require_token=False)

    # Check if already registered (normalise stored paths for comparison)
    for stored in cfg.search.collections:
        if Path(stored).expanduser().resolve() == resolved:
            print(f"Already registered: {args.path}")
            return 0

    # Warn if service is running (write conflicts possible)
    info = get_search_service().status()
    if info.running:
        print("Warning: Search service is running — write conflicts are possible.")

    # Append to config first (so path survives even if ingest fails)
    config_collections_append(_CONFIG_PATH, args.path)

    # Determine collection name: use manifest entry if path was previously synced
    db_path = Path(cfg.search.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"
    col_name = manifest_lookup_by_path(manifest_path, str(resolved))
    if col_name is None:
        col_name = path_to_collection_name(args.path)

    pipeline = create_pipeline(cfg.search)

    def _progress(done: int, total: int) -> None:
        print(f"  [{done}/{total}] files processed")

    async def _ingest() -> int:
        try:
            await pipeline.store.connect()
            await pipeline.ingest_directory(resolved, col_name, progress_cb=_progress)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Ingest error: {exc}")
            return 1
        finally:
            await pipeline.store.disconnect()

    rc = asyncio.run(_ingest())
    if rc != 0:
        return rc

    print(f"Collection added and indexed: {args.path}")
    print("Run 'archon search stop && archon search start' for the service to start serving it.")
    return 0


def _run_collection_info(args: argparse.Namespace) -> int:
    """Print CollectionMeta for a named collection."""
    cfg = load_config(require_token=False)
    pipeline = create_pipeline(cfg.search)

    async def _fetch() -> int:
        try:
            await pipeline.store.connect()
            meta = await pipeline.get_collection_meta(args.collection_name)
            if meta is None:
                print(f"Error: collection {args.collection_name!r} not found.")
                return 1
            print(f"name:            {meta.name}")
            print(f"description:     {meta.description or '(none)'}")
            print(f"doc_count:       {meta.doc_count}")
            print(f"chunk_count:     {meta.chunk_count}")
            print(f"embedding_model: {meta.embedding_model}")
            centroid_status = "present" if meta.centroid is not None else "absent"
            print(f"centroid:        {centroid_status}")
            if meta.last_indexed is not None:
                print(f"last_indexed:    {meta.last_indexed.isoformat()}")
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"Error: {exc}")
            return 1
        finally:
            await pipeline.store.disconnect()

    return asyncio.run(_fetch())


def _run_collection_reindex(args: argparse.Namespace) -> int:
    """Force full re-ingest of a collection, bypassing all thresholds."""
    info = get_search_service().status()
    if info.running:
        print("Error: Search service is running. Stop it before reindexing to avoid data races.")
        print("  archon search stop")
        return 1

    cfg = load_config(require_token=False)

    # Resolve source directory: look for a matching collection in config
    col_name = args.collection_name
    source_path: str | None = None
    for raw_path in cfg.search.collections:
        if path_to_collection_name(raw_path) == col_name:
            source_path = raw_path
            break

    if source_path is None:
        print(f"Error: collection {col_name!r} not found in config. Add it first with 'archon search collection add'.")
        return 1

    resolved = Path(source_path).expanduser().resolve()
    print(f"Reindexing collection {col_name!r} from {resolved} ...")

    # Clear prior state (including processed_paths) to force a full re-index
    try:
        IndexingStateStore(Path(cfg.search.db_path)).remove_collection(col_name)
    except Exception:  # noqa: BLE001
        logger.warning("Failed to clear indexing state for %r before reindex", col_name)

    pipeline = create_pipeline(cfg.search)

    async def _reindex() -> int:
        try:
            await pipeline.store.connect()
            results = await pipeline.ingest_directory(
                resolved, col_name, force_regenerate_description=True
            )
            ok = sum(1 for r in results if r.status == "ok")
            errors = sum(1 for r in results if r.status == "error")
            print(f"Reindex complete: {ok} ingested, {errors} errors.")
            return 1 if errors else 0
        except Exception as exc:  # noqa: BLE001
            print(f"Reindex failed: {exc}")
            return 1
        finally:
            await pipeline.store.disconnect()

    return asyncio.run(_reindex())


def _run_collection_remove(args: argparse.Namespace) -> int:
    """Remove a registered filesystem path from RAG collections and drop its LanceDB table."""
    dry_run: bool = args.dry_run
    force: bool = args.force

    # Mutual exclusivity guard
    if dry_run and force:
        print("Error: --dry-run and --force are mutually exclusive.")
        return 1

    resolved = Path(args.path).expanduser().resolve()

    cfg = load_config(require_token=False)

    # Check if path is registered
    found = any(
        Path(stored).expanduser().resolve() == resolved
        for stored in cfg.search.collections
    )
    if not found:
        print(f"Error: not in collections: {args.path}")
        return 1

    # Determine collection name from manifest or fallback (needed for dry-run output too)
    db_path = Path(cfg.search.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"
    col_name = manifest_lookup_by_path(manifest_path, str(resolved))
    if col_name is None:
        col_name = path_to_collection_name(args.path)

    # Dry-run: print what would be removed and exit without executing
    if dry_run:
        print(f"Would remove config entry: {args.path}")
        print(f"Would drop LanceDB table: {col_name}")
        return 0

    # Check if service is running
    info = get_search_service().status()
    if info.running and not force:
        print("Error: Search service is running. Stop it before removing a collection.")
        print("  archon search stop")
        return 1
    if info.running and force:
        print("Warning: removing collection while service is running.")

    # Drop from LanceDB FIRST — only modify config if drop succeeds
    store = SearchStore(cfg.search.db_path)

    async def _drop() -> Exception | None:
        try:
            await store.connect()
            try:
                await store.drop_collection(col_name)
            except KeyError:
                pass  # already gone
            return None
        except Exception as exc:  # noqa: BLE001
            return exc
        finally:
            await store.disconnect()

    exc = asyncio.run(_drop())
    if exc is not None:
        print(f"Drop failed: {exc}")
        return 1

    # Only remove from config AFTER successful drop
    config_collections_remove(_CONFIG_PATH, args.path)

    # Clean up manifest entry (best-effort)
    manifest_remove_entry(manifest_path, col_name)

    print(f"Collection removed: {args.path}")
    return 0
