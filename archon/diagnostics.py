"""Synchronous pre-flight checks shared between CLI doctor and other consumers."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

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


_SYNC_CHECK_NAMES = [
    "_check_git",
    "_check_uv",
    "_check_python",
    "_check_claude",
    "_check_env_file",
    "_check_config_file",
    "_check_logs_dir",
    "_check_app_dir",
    "_check_bot_token",
]


def run_checks() -> list[CheckResult]:
    """Run all synchronous pre-flight checks and return results.

    Excludes _check_health (tautological inside the daemon).
    Wraps individual check exceptions into a failed CheckResult.
    """
    import archon.diagnostics as _self  # noqa: PLC0415

    results: list[CheckResult] = []
    for name in _SYNC_CHECK_NAMES:
        fn = getattr(_self, name)
        try:
            results.append(fn())
        except Exception as exc:
            results.append(CheckResult(name.replace("_check_", "", 1), False, str(exc)))
    return results
