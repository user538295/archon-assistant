"""archon — CLI management tool for the Archon daemon."""
from __future__ import annotations
import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    # Handle -h/--help manually to print help and return 0 instead of SystemExit
    if argv is None:
        argv = sys.argv[1:]
    if any(a in ("-h", "--help") for a in argv):
        argv = ["help"]

    parser = argparse.ArgumentParser(prog="archon", description="Manage the Archon daemon", add_help=False)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("start", help="Start the Archon service")
    sub.add_parser("stop", help="Stop the Archon service")
    sub.add_parser("restart", help="Restart the Archon service")
    sub.add_parser("status", help="Show service status and health")

    p_logs = sub.add_parser("logs", help="Show or follow the Archon log")
    p_logs.add_argument("--lines", "-n", type=int, default=50, metavar="N")
    p_logs.add_argument("--follow", "-f", action="store_true")
    p_logs.add_argument("--date", metavar="YYYY-MM-DD")

    p_update = sub.add_parser("update", help="Update Archon to the latest version")
    p_update.add_argument("--tag", metavar="X.Y.Z")

    sub.add_parser("version", help="Show installed version")
    sub.add_parser("doctor", help="Run pre-flight checks")
    sub.add_parser("uninstall", help="Stop the service and remove ~/.archon/app")
    sub.add_parser("help", help="Show this help message")

    p_config = sub.add_parser("config", help="View or modify configuration")
    config_sub = p_config.add_subparsers(dest="config_command", metavar="<action>")
    config_sub.add_parser("show", help="Print current config")
    config_sub.add_parser("edit", help="Open config in $EDITOR")
    p_get = config_sub.add_parser("get", help="Get a config value")
    p_get.add_argument("key", help="Dotted key, e.g. notifications.mode")
    p_set = config_sub.add_parser("set", help="Set a config value")
    p_set.add_argument("key")
    p_set.add_argument("value")

    p_rag = sub.add_parser("rag", help="Manage the RAG search service")
    rag_sub = p_rag.add_subparsers(dest="rag_command", metavar="<action>")

    p_rag_install = rag_sub.add_parser("install", help="Install the RAG service")
    p_rag_install.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_rag_install.add_argument("--non-interactive", action="store_true", dest="non_interactive")

    p_rag_uninstall = rag_sub.add_parser("uninstall", help="Uninstall the RAG service")
    p_rag_uninstall.add_argument("--delete-db", action="store_true", dest="delete_db")

    rag_sub.add_parser("start", help="Start the RAG service")
    rag_sub.add_parser("stop", help="Stop the RAG service")
    rag_sub.add_parser("status", help="Show RAG service status")

    p_rag_ingest = rag_sub.add_parser("ingest", help="Ingest documents into the RAG store")
    p_rag_ingest.add_argument("path", nargs="?", default=None, help="Directory to ingest (default: history sessions dir)")
    p_rag_ingest.add_argument("--collection", default=None, help="Target collection name")

    rag_sub.add_parser("sync", help="Reconcile configured collections with LanceDB")
    rag_sub.add_parser("help", help="Show rag help")

    p_collection = rag_sub.add_parser("collection", help="Manage RAG collections")
    collection_sub = p_collection.add_subparsers(dest="collection_command", metavar="<action>")
    collection_sub.add_parser("list", help="List all collections")
    p_col_add = collection_sub.add_parser("add", help="Add and index a path as a collection")
    p_col_add.add_argument("path")
    p_col_remove = collection_sub.add_parser("remove", help="Remove a collection")
    p_col_remove.add_argument("path")
    p_col_remove.add_argument("--force", action="store_true", default=False)
    p_col_info = collection_sub.add_parser("info", help="Show CollectionMeta for a collection")
    p_col_info.add_argument("collection_name")
    p_col_reindex = collection_sub.add_parser("reindex", help="Force full re-ingest of a collection")
    p_col_reindex.add_argument("collection_name")
    collection_sub.add_parser("help", help="Show collection help")

    args = parser.parse_args(argv)

    if args.command is None or args.command == "help":
        parser.print_help()
        return 0

    if args.command == "start":
        from archon.cli.service import run_start
        return run_start()
    if args.command == "stop":
        from archon.cli.service import run_stop
        return run_stop()
    if args.command == "restart":
        from archon.cli.service import run_restart
        return run_restart()
    if args.command == "status":
        from archon.cli.status import run_status
        return run_status(args)
    if args.command == "logs":
        from archon.cli.logs import run_logs
        return run_logs(args)
    if args.command == "update":
        from archon.cli.update import run_update
        return run_update(args)
    if args.command == "version":
        from archon.cli.update import run_version
        return run_version(args)
    if args.command == "doctor":
        from archon.cli.doctor import run_doctor
        return run_doctor()
    if args.command == "uninstall":
        from archon.cli.update import run_uninstall
        return run_uninstall(args)
    if args.command == "config":
        from archon.cli.config_cmd import run_config
        return run_config(args)
    if args.command == "rag":
        from archon.cli.rag_cmd import run_rag
        return run_rag(args, rag_parser=p_rag, collection_parser=p_collection)

    return 0


if __name__ == "__main__":
    sys.exit(main())
