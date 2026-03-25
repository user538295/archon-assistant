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

logger = logging.getLogger("archon")


def run_rag(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate rag sub-action."""
    dispatch = {
        "install": _run_install,
        "uninstall": _run_uninstall,
        "start": _run_start,
        "stop": _run_stop,
        "status": _run_status,
        "ingest": _run_ingest,
    }
    action = dispatch.get(args.rag_command)
    if action is None:
        print("Usage: archon rag <install|uninstall|start|stop|status|ingest>")
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

    cfg = load_config()
    path = Path(args.path) if args.path else Path(cfg.history.directory).expanduser() / "sessions"
    collection = args.collection or cfg.rag.history_collection

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
