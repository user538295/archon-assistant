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

from archon.platform import get_rag_service
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
)


def _check_rag_server(cfg: Any) -> CheckResult:
    """Check RAG server reachability and return a first-class CheckResult."""
    rag = cfg.rag
    if not rag.enabled:
        return CheckResult("rag server", True, "disabled")

    if importlib.util.find_spec("lancedb") is None:
        return CheckResult("rag server", False, "RAG not installed — run: archon rag install")

    if not get_rag_service().is_installed():
        return CheckResult("rag server", False, "service not registered — run: archon rag install")

    try:
        with socket.create_connection((rag.host, rag.port), timeout=2):
            pass
        return CheckResult("rag server", True, "running")
    except OSError:
        return CheckResult("rag server", False, "not running — run: archon rag start")


_RAG_JSONRPC_PAYLOAD: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {"name": "get_collections_meta", "arguments": {}},
    "id": 1,
}

_RAG_STALE_DAYS = 7


async def _check_rag_health(cfg: Any) -> None:
    """Check RAG collection health and print warnings.

    Always checks pinned_collections against rag.collections (config-only).
    Skips per-collection checks when the RAG server is unreachable.
    """
    rag = cfg.rag

    # Config-only check: pinned not declared in collections
    collections_set = set(rag.collections)
    for path in rag.pinned_collections:
        if path not in collections_set:
            print(
                f"⚠ Pinned collection '{path}' is not declared in rag.collections"
                " — it will be skipped at runtime"
            )

    # Fetch metadata from RAG server
    rag_url = f"http://{rag.host}:{rag.port}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(rag_url, json=_RAG_JSONRPC_PAYLOAD)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
    except httpx.HTTPError:
        print("RAG server is not running — RAG health checks skipped")
        return

    content_blocks: list[dict[str, Any]] = data.get("result", {}).get("content", [])
    if not content_blocks or content_blocks[0].get("type") != "text":
        return

    try:
        raw_collections: list[dict[str, Any]] = json.loads(content_blocks[0]["text"])
    except (json.JSONDecodeError, KeyError):
        return

    now = datetime.now(timezone.utc)
    for col in raw_collections:
        if not isinstance(col, dict) or "name" not in col:
            continue
        name: str = col["name"]

        # Staleness check
        last_indexed_raw = col.get("last_indexed")
        if last_indexed_raw:
            try:
                last_indexed = datetime.fromisoformat(last_indexed_raw)
                if last_indexed.tzinfo is None:
                    last_indexed = last_indexed.replace(tzinfo=timezone.utc)
                days_old = (now - last_indexed).days
                if days_old > _RAG_STALE_DAYS:
                    print(f"⚠ Collection '{name}' last indexed {days_old} days ago")
            except ValueError:
                pass

        # Embedding model mismatch
        indexed_model = col.get("embedding_model", "")
        if indexed_model and indexed_model != rag.embedding_model:
            print(
                f"⚠ Collection '{name}' indexed with '{indexed_model}',"
                f" current model is '{rag.embedding_model}' — reindex required"
            )

        # Empty collection
        if col.get("doc_count", 0) == 0:
            print(f"⚠ Collection '{name}' is empty")

        # Missing centroid
        if col.get("centroid") is None:
            print(f"⚠ Collection '{name}' has no centroid — routing disabled for this collection")


def run_doctor() -> int:
    checks = [
        _check_git, _check_uv, _check_python, _check_claude,
        _check_env_file, _check_config_file, _check_logs_dir,
        _check_health, _check_app_dir, _check_bot_token,
    ]
    print("Archon Doctor — pre-flight checks")
    print("──────────────────────────────────────")
    results = [fn() for fn in checks]

    # RAG server check and per-collection health (if config available)
    rag_server_ok = False
    try:
        cfg_path = _ARCHON_HOME / "config.toml"
        if cfg_path.exists():
            from archon.config import config  # noqa: PLC0415
            rag_result = _check_rag_server(config)
            results.append(rag_result)
            rag_server_ok = rag_result.ok
            if rag_server_ok and config.rag.enabled:
                asyncio.run(_check_rag_health(config))
    except Exception as e:
        print(f"RAG health check failed: {e}")

    for r in results:
        mark = "✔" if r.ok else "✗"
        print(f"  {mark}  {r.name:<20} {r.detail}")
    failures = [r for r in results if not r.ok]

    print()
    if failures:
        count = len(failures)
        print(f"{count} issue{'s' if count != 1 else ''} found.")
        return 1
    print("All checks passed.")
    return 0
