"""Pre-flight checks for the Archon daemon."""
# NOTE: print() is intentionally used in CLI modules for user-facing output. The no-print() rule applies to daemon modules only (archon/ai/, archon/chat/, archon/gateway/).
from __future__ import annotations
import asyncio
import json
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_ARCHON_HOME = Path.home() / ".archon"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _check_git() -> CheckResult:
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        ok = r.returncode == 0
        return CheckResult("git", ok, r.stdout.strip() if ok else "not found")
    except FileNotFoundError:
        return CheckResult("git", False, "not found")


def _check_uv() -> CheckResult:
    try:
        r = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=False)
        ok = r.returncode == 0
        return CheckResult("uv", ok, r.stdout.strip() if ok else "not found")
    except FileNotFoundError:
        return CheckResult("uv", False, "not found")


def _check_python() -> CheckResult:
    try:
        r = subprocess.run(
            ["uv", "run", "python", "--version"],
            capture_output=True, text=True, check=False,
        )
        text = (r.stdout or r.stderr).strip()
        m = re.search(r"(\d+)\.(\d+)", text)
        if m:
            major, minor = int(m.group(1)), int(m.group(2))
            ok = (major, minor) >= (3, 12)
            return CheckResult("python", ok, text if ok else f"{text} (need >=3.12)")
        return CheckResult("python", False, f"could not parse version: {text}")
    except FileNotFoundError:
        return CheckResult("python", False, "uv not found")


def _check_claude() -> CheckResult:
    try:
        r = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=False)
        ok = r.returncode == 0
        return CheckResult("claude", ok, r.stdout.strip() if ok else "not found")
    except FileNotFoundError:
        return CheckResult("claude", False, "not found")


def _check_env_file() -> CheckResult:
    env = _ARCHON_HOME / ".env"
    if not env.exists():
        return CheckResult("env file", False, f"{env} not found")
    content = env.read_text()
    ok = bool(re.search(r"^TELEGRAM_BOT_TOKEN=\S+", content, re.MULTILINE))
    return CheckResult(
        "env file", ok,
        str(env) if ok else "TELEGRAM_BOT_TOKEN missing or empty in .env"
    )


def _check_config_file() -> CheckResult:
    cfg = _ARCHON_HOME / "config.toml"
    if not cfg.exists():
        return CheckResult("config file", False, f"{cfg} not found")
    try:
        with open(cfg, "rb") as f:
            tomllib.load(f)
        return CheckResult("config file", True, f"{cfg} OK")
    except Exception as e:
        return CheckResult("config file", False, f"parse error: {e}")


def _check_logs_dir() -> CheckResult:
    d = _ARCHON_HOME / "logs"
    if not d.exists():
        return CheckResult("logs dir", False, f"{d} not found")
    ok = os.access(d, os.W_OK)
    return CheckResult("logs dir", ok, f"{d} {'writable' if ok else 'not writable'}")


def _check_health() -> CheckResult:
    port = 18182
    try:
        cfg = _ARCHON_HOME / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            port = data.get("background_agents", {}).get("port", 18182)
    except Exception:
        pass
    url = f"http://localhost:{port}/health"
    try:
        urllib.request.urlopen(url, timeout=2)
        return CheckResult("health check", True, f"{url} OK")
    except Exception:
        return CheckResult("health check", False, f"{url} unreachable — is Archon running?")


def _check_app_dir() -> CheckResult:
    d = _ARCHON_HOME / "app"
    ok = d.exists()
    return CheckResult("app dir", ok, str(d) + (" exists" if ok else " not found"))


def _check_bot_token() -> CheckResult:
    """Validate the Telegram bot token by calling the getMe API endpoint."""
    env = _ARCHON_HOME / ".env"
    if not env.exists():
        return CheckResult("bot token", False, ".env file not found")
    content = env.read_text()
    m = re.search(r"^TELEGRAM_BOT_TOKEN=(\S+)", content, re.MULTILINE)
    if not m:
        return CheckResult("bot token", False, "TELEGRAM_BOT_TOKEN not set in .env")
    token = m.group(1)
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        username = data.get("result", {}).get("username", "?")
        return CheckResult("bot token", True, f"@{username}")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return CheckResult("bot token", False, "invalid — check TELEGRAM_BOT_TOKEN in ~/.archon/.env")
        return CheckResult("bot token", False, f"HTTP {e.code} from Telegram")
    except Exception as e:
        return CheckResult("bot token", False, f"could not reach Telegram: {e}")


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
    for r in results:
        mark = "✔" if r.ok else "✗"
        print(f"  {mark}  {r.name:<20} {r.detail}")
    failures = [r for r in results if not r.ok]

    # RAG health checks (if config available and RAG enabled)
    try:
        cfg_path = _ARCHON_HOME / "config.toml"
        if cfg_path.exists():
            from archon.config import config  # noqa: PLC0415
            if config.rag.enabled:
                asyncio.run(_check_rag_health(config))
    except Exception as e:
        print(f"RAG health check failed: {e}")

    print()
    if failures:
        count = len(failures)
        print(f"{count} issue{'s' if count != 1 else ''} found.")
        return 1
    print("All checks passed.")
    return 0
