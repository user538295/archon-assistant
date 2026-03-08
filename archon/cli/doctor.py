"""Pre-flight checks for the Archon daemon."""
# NOTE: print() is intentionally used in CLI modules for user-facing output. The no-print() rule applies to daemon modules only (archon/ai/, archon/chat/, archon/gateway/).
from __future__ import annotations
import os
import re
import subprocess
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_ARCHON_HOME = Path.home() / ".archon"
_DEFAULT_BG_PORT = 18182


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
    ok = "TELEGRAM_BOT_TOKEN" in content
    return CheckResult(
        "env file", ok,
        str(env) if ok else "TELEGRAM_BOT_TOKEN missing in .env"
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
    port = _DEFAULT_BG_PORT
    try:
        cfg = _ARCHON_HOME / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            port = data.get("background_agents", {}).get("port", _DEFAULT_BG_PORT)
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


def run_doctor() -> int:
    checks = [
        _check_git, _check_uv, _check_python, _check_claude,
        _check_env_file, _check_config_file, _check_logs_dir,
        _check_health, _check_app_dir,
    ]
    print("Archon Doctor — pre-flight checks")
    print("──────────────────────────────────────")
    results = [fn() for fn in checks]
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
