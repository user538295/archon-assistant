"""Pre-flight checks for the Archon daemon."""
# NOTE: print() is intentionally used in CLI modules for user-facing output. The no-print() rule applies to daemon modules only (archon/ai/, archon/chat/, archon/gateway/).
from __future__ import annotations
import asyncio
import importlib.util
import json
import socket
import tomllib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from archon.ai.search_client import SearchClient
from archon.platform import get_search_service
from archon.diagnostics import (
    CheckResult,
    _ARCHON_HOME,
    _check_git,
    _check_uv,
    _check_python,
    _check_claude,
    _check_env_file,
    _check_config_file,
    _check_logs_dir,
    _check_health,
    _check_app_dir,
    _check_bot_token,
    _check_context_windows,
)


def _check_search_server(cfg: Any) -> CheckResult:
    """Check search server reachability and return a first-class CheckResult."""
    search = cfg.search
    if not search.enabled:
        return CheckResult("search server", True, "disabled")

    if importlib.util.find_spec("lancedb") is None:
        return CheckResult("search server", False, "search not installed — run: archon search install")

    if not get_search_service().is_installed():
        return CheckResult("search server", False, "service not registered — run: archon search install")

    try:
        host, port = search.host_port
        with socket.create_connection((host, port), timeout=2):
            pass
        return CheckResult("search server", True, "running")
    except OSError:
        return CheckResult("search server", False, "not running — run: archon search start")


_SEARCH_JSONRPC_PAYLOAD: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "get_collections_meta", "arguments": {}},
    "id": 1,
}

_SEARCH_STALE_DAYS = 7


async def _check_search_health(cfg: Any) -> None:
    """Check search collection health and print warnings.

    Uses SearchClient HTTP API: health() to check reachability,
    indexing_state() for per-collection progress. Also queries JSON-RPC
    for collection metadata (staleness, model, centroid). Skips all checks
    when search is disabled or server is unreachable.
    """
    search = cfg.search

    if not search.enabled:
        print("Search: disabled")
        return

    search_url = search.url
    async with SearchClient(search_url) as client:
        # Check reachability via health endpoint
        health = await client.health()
        if health is None:
            print("Search: not running")
            return

        # Fetch per-collection indexing state via HTTP
        state_data = await client.indexing_state()
    col_state: dict[str, Any] = (state_data or {}).get("collections", {})

    # Fetch metadata from search server (staleness / model / centroid checks)
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            response = await http.post(search_url, json=_SEARCH_JSONRPC_PAYLOAD)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
    except httpx.HTTPError:
        print("Search server is not running — search health checks skipped")
        return

    content_blocks: list[dict[str, Any]] = data.get("result", {}).get("content", [])
    if not content_blocks or content_blocks[0].get("type") != "text":
        return

    try:
        raw_collections: list[dict[str, Any]] = json.loads(content_blocks[0]["text"])
    except (json.JSONDecodeError, KeyError):
        return

    now = datetime.now(timezone.utc)
    indexed_names: set[str] = set()
    for col in raw_collections:
        if not isinstance(col, dict) or "name" not in col:
            continue
        name: str = col["name"]
        indexed_names.add(name)

        # Check indexing state — suppress false alarms for in-progress/pending
        cp: dict[str, Any] | None = col_state.get(name)
        status = cp.get("status") if cp else None
        processed = cp.get("processed_files", 0) if cp else 0
        total = cp.get("total_files", 0) if cp else 0
        error = cp.get("error") if cp else None

        if status == "in_progress":
            if processed > 0:
                print(f"⏳ Collection '{name}' — partial ({processed}/{total} files)")
            else:
                print(f"⏳ Collection '{name}' — indexing starting")
            continue
        elif status == "pending":
            if processed > 0:
                print(f"⚠️ Collection '{name}' — partial ({processed}/{total} files)")
            else:
                print(f"⏳ Collection '{name}' — pending")
            continue
        elif status == "failed":
            print(f"❌ Collection '{name}' — failed: {error}")
            continue
        # "done" or unknown: fall through to staleness/model checks below

        has_warning = False

        # Staleness check
        last_indexed_raw = col.get("last_indexed")
        if last_indexed_raw:
            try:
                last_indexed = datetime.fromisoformat(last_indexed_raw)
                if last_indexed.tzinfo is None:
                    last_indexed = last_indexed.replace(tzinfo=timezone.utc)
                days_old = (now - last_indexed).days
                if days_old > _SEARCH_STALE_DAYS:
                    has_warning = True
                    print(f"⚠ Collection '{name}' last indexed {days_old} days ago")
            except ValueError:
                has_warning = True

        # Embedding model / chunk size mismatch checks moved to archon-search service.
        # archon doctor only has access to client-side config; server-side fields
        # (embedding_model, chunk_size) are reported by archon-search status endpoint.

        # Empty collection
        if col.get("doc_count", 0) == 0:
            has_warning = True
            print(f"⚠ Collection '{name}' is empty")

        # Missing centroid
        if col.get("centroid") is None:
            has_warning = True
            print(f"⚠ Collection '{name}' has no centroid — routing disabled for this collection")

        if status == "done" and not has_warning:
            print(f"✅ Collection '{name}' — done ({col.get('doc_count', 0)} docs)")

    # Print state-only entries (in indexing state but not yet in LanceDB)
    for name, cp_data in col_state.items():
        if name in indexed_names:
            continue
        status = cp_data.get("status")
        processed = cp_data.get("processed_files", 0)
        total = cp_data.get("total_files", 0)
        error = cp_data.get("error")
        if status == "in_progress":
            if processed > 0:
                print(f"⏳ Collection '{name}' — in_progress ({processed}/{total} files)")
            else:
                print(f"⏳ Collection '{name}' — indexing starting")
        elif status == "pending":
            if processed > 0:
                print(f"⚠️ Collection '{name}' — partial ({processed}/{total} files)")
            else:
                print(f"⏳ Collection '{name}' — pending")
        elif status == "failed":
            print(f"❌ Collection '{name}' — failed: {error}")
        # "done" in state but absent from LanceDB is an inconsistency — skip silently


def run_doctor() -> int:
    checks = [
        _check_git, _check_uv, _check_python, _check_claude,
        _check_env_file, _check_config_file, _check_logs_dir,
        _check_health, _check_app_dir, _check_bot_token,
    ]
    print("Archon Doctor — pre-flight checks")
    print("──────────────────────────────────────")
    results = [fn() for fn in checks]

    # Context window mismatch check
    results.append(_check_context_windows())

    # RAG server check and per-collection health (if config available)
    search_server_ok = False
    try:
        cfg_path = _ARCHON_HOME / "config.toml"
        if cfg_path.exists():
            from archon.config import config  # noqa: PLC0415
            search_result = _check_search_server(config)
            results.append(search_result)
            search_server_ok = search_result.ok
            if search_server_ok and config.search.enabled:
                asyncio.run(_check_search_health(config))
    except Exception as e:
        print(f"RAG health check failed: {e}")

    for r in results:
        if r.warn and r.ok:
            mark = "⚠"
        elif r.ok:
            mark = "✔"
        else:
            mark = "✗"
        print(f"  {mark}  {r.name:<20} {r.detail}")
    failures = [r for r in results if not r.ok]

    print()
    if failures:
        count = len(failures)
        print(f"{count} issue{'s' if count != 1 else ''} found.")
        return 1
    print("All checks passed.")
    return 0
