"""archon rag — CLI subcommand for managing the RAG search service (Task 7.2)."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from archon.config.loader import load_config
from archon.platform import get_rag_service
from archon.rag.install import RagInstaller
from archon.rag.pipeline import create_pipeline
from archon.rag.store import RagStore
from archon.rag.sync import RagCollectionSync, path_to_collection_name

logger = logging.getLogger("archon")


def run_rag(
    args: argparse.Namespace,
    rag_parser: argparse.ArgumentParser | None = None,
    collection_parser: argparse.ArgumentParser | None = None,
) -> int:
    """Dispatch to the appropriate rag sub-action."""
    dispatch = {
        "install": _run_install,
        "uninstall": _run_uninstall,
        "start": _run_start,
        "stop": _run_stop,
        "status": _run_status,
        "ingest": _run_ingest,
        "sync": _run_sync,
        "collection": _run_collection,
    }
    action = dispatch.get(args.rag_command)
    if action is None:
        print("Usage: archon rag <install|uninstall|start|stop|status|ingest|sync|collection>")
        return 1
    return action(args)


# ---------------------------------------------------------------------------
# install / uninstall
# ---------------------------------------------------------------------------

def _run_install(args: argparse.Namespace) -> int:
    installer = RagInstaller(dry_run=args.dry_run)
    return installer.run(non_interactive=args.non_interactive)


def _run_uninstall(args: argparse.Namespace) -> int:
    installer = RagInstaller()
    return installer.run_uninstall(delete_db=args.delete_db)


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------

def _run_start(args: argparse.Namespace) -> int:
    rc = get_rag_service().start()
    if rc == 0:
        print("RAG service started.")
    else:
        print(f"RAG service start failed (exit code {rc}).")
    return rc


def _run_stop(args: argparse.Namespace) -> int:
    rc = get_rag_service().stop()
    if rc == 0:
        print("RAG service stopped.")
    else:
        print(f"RAG service stop failed (exit code {rc}).")
    return rc


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def _run_status(args: argparse.Namespace) -> int:
    info = get_rag_service().status()
    if not info.running:
        print(f"RAG service: stopped (unreachable)")
        return 1

    print(f"RAG service: running (pid={info.pid})")

    cfg = load_config()
    store = RagStore(cfg.rag.db_path)

    async def _get_stats() -> None:
        try:
            await store.connect()
            collections = await store.list_collections()
            if collections:
                for col in collections:
                    print(f"  collection={col.name}  docs={col.doc_count}  chunks={col.chunk_count}")
            else:
                print("  No collections found.")
        except Exception as exc:
            print(f"  Stats unavailable — server may be writing ({exc})")
        finally:
            await store.disconnect()

    try:
        asyncio.run(_get_stats())
    except Exception as exc:
        print(f"  Stats unavailable: {exc}")
    return 0


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _run_ingest(args: argparse.Namespace) -> int:
    info = get_rag_service().status()
    if info.running:
        print("Error: RAG service is running. Stop it before ingesting to avoid data races.")
        print("  archon rag stop")
        return 1

    from archon.rag.sync import path_to_collection_name  # noqa: PLC0415

    cfg = load_config()
    path = Path(args.path) if args.path else Path(cfg.history.directory).expanduser() / "sessions"
    collection = args.collection or path_to_collection_name(
        str(Path(cfg.history.directory).expanduser() / "sessions")
    )

    pipeline = create_pipeline(cfg.rag)

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
    info = get_rag_service().status()
    if info.running:
        print("Warning: RAG service is running — write conflicts are possible.")

    cfg = load_config()
    pipeline = create_pipeline(cfg.rag)

    async def _do_sync():
        try:
            await pipeline.store.connect()
            return await RagCollectionSync(pipeline).sync(cfg.rag.collections)
        finally:
            await pipeline.store.disconnect()

    result = asyncio.run(_do_sync())

    print(
        f"Sync complete: {len(result.added)} added, {len(result.removed)} removed, "
        f"{len(result.unchanged)} unchanged, {len(result.errors)} errors."
    )
    for name in result.added:
        print(f"  + {name}")
    for name in result.removed:
        print(f"  - {name}")
    for err in result.errors:
        print(f"  ! {err}")

    return 1 if result.errors else 0


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------


def _run_collection(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate collection sub-action."""
    dispatch = {
        "list": _run_collection_list,
    }
    action = dispatch.get(getattr(args, "collection_command", None))
    if action is None:
        print("Usage: archon rag collection <list>")
        return 1
    return action(args)


def _run_collection_list(args: argparse.Namespace) -> int:
    """List all LanceDB collections with status, path, doc and chunk counts."""
    cfg = load_config()
    db_path = Path(cfg.rag.db_path).expanduser()
    manifest_path = db_path / "sync_manifest.json"
    store = RagStore(cfg.rag.db_path)

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
    for raw_path in cfg.rag.collections:
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
