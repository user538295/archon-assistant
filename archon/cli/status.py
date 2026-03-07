"""Status panel for the Archon daemon."""
from __future__ import annotations
import platform
import re
import subprocess
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

_ARCHON_HOME = Path.home() / ".archon"
_CONFIG_PATH = _ARCHON_HOME / "config.toml"
_SERVICE_LABEL = "com.archon.assistant"
_SYSTEMD_SERVICE = "archon"
_DEFAULT_BG_HOST = "localhost"
_DEFAULT_BG_PORT = 18182


@dataclass
class ServiceInfo:
    running: bool
    pid: int | None
    uptime: str | None


@dataclass
class HealthInfo:
    reachable: bool
    latency_ms: int | None


def _get_service_info() -> ServiceInfo:
    if platform.system() == "Darwin":
        r = subprocess.run(
            ["launchctl", "list", _SERVICE_LABEL],
            capture_output=True, text=True, check=False,
        )
        if r.returncode != 0:
            return ServiceInfo(running=False, pid=None, uptime=None)
        m = re.search(r'"PID"\s*=\s*(\d+)', r.stdout)
        if m and int(m.group(1)) > 0:
            pid = int(m.group(1))
            uptime = _get_uptime(pid)
            return ServiceInfo(running=True, pid=pid, uptime=uptime)
        return ServiceInfo(running=False, pid=None, uptime=None)
    else:
        r = subprocess.run(
            ["systemctl", "--user", "is-active", _SYSTEMD_SERVICE],
            capture_output=True, text=True, check=False,
        )
        if r.stdout.strip() != "active":
            return ServiceInfo(running=False, pid=None, uptime=None)
        r2 = subprocess.run(
            ["systemctl", "--user", "show", _SYSTEMD_SERVICE, "--property=MainPID"],
            capture_output=True, text=True, check=False,
        )
        m = re.search(r"MainPID=(\d+)", r2.stdout)
        pid = int(m.group(1)) if m and int(m.group(1)) > 0 else None
        uptime = _get_uptime(pid) if pid else None
        return ServiceInfo(running=True, pid=pid, uptime=uptime)


def _get_uptime(pid: int) -> str | None:
    r = subprocess.run(
        ["ps", "-p", str(pid), "-o", "etime="],
        capture_output=True, text=True, check=False,
    )
    val = r.stdout.strip()
    return val if val else None


def _check_health(host: str, port: int) -> HealthInfo:
    url = f"http://{host}:{port}/health"
    t0 = time.monotonic()
    try:
        resp = urllib.request.urlopen(url, timeout=2)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return HealthInfo(reachable=(resp.status == 200), latency_ms=latency_ms)
    except Exception:
        return HealthInfo(reachable=False, latency_ms=None)


def _load_config_raw() -> dict:
    try:
        with open(_CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _count_plugins(plugins_dir: str) -> int:
    d = Path(plugins_dir).expanduser()
    if not d.exists():
        return 0
    try:
        return sum(1 for p in d.iterdir() if p.is_dir())
    except Exception:
        return 0


def run_status(args: object) -> int:
    cfg = _load_config_raw()
    host = cfg.get("background_agents", {}).get("host", _DEFAULT_BG_HOST)
    port = cfg.get("background_agents", {}).get("port", _DEFAULT_BG_PORT)

    svc = _get_service_info()
    health = _check_health(host, port)

    try:
        from archon.version import get_version
        ver = get_version()
    except Exception:
        ver = "unknown"

    state_sym = "●" if svc.running else "○"
    state_str = "running" if svc.running else "stopped"
    print(f"{state_sym} Archon v{ver}  —  {state_str}")
    print("──────────────────────────────────────────")

    if svc.running:
        platform_name = "launchd" if platform.system() == "Darwin" else "systemd"
        pid_str = f"PID {svc.pid}" if svc.pid else "PID unknown"
        uptime_str = f"· uptime {svc.uptime}" if svc.uptime else ""
        print(f"  Service    {platform_name} · {pid_str} {uptime_str}".rstrip())

    health_sym = "✔" if health.reachable else "✗"
    latency_str = f"({health.latency_ms}ms)" if health.latency_ms is not None else "(unreachable)"
    print(f"  Health     {host}:{port} {health_sym} {latency_str}")
    print(f"  MCP        {host}:{port} (archon-mcp)")

    plugins_dir = cfg.get("plugins", {}).get("plugins_dir", "") or "~/.claude/plugins/"
    plugin_count = _count_plugins(plugins_dir)
    print(f"  Plugins    {plugin_count} loaded")

    model = cfg.get("models", {}).get("default") or "not set"
    print(f"  Model      {model}")

    notify_mode = cfg.get("notifications", {}).get("mode", "normal")
    notify_interval = cfg.get("notifications", {}).get("interval_minutes", 2)
    print(f"  Notify     {notify_mode} · beacon {notify_interval} min")

    voice_enabled = cfg.get("voice", {}).get("enabled", False)
    if voice_enabled:
        stt_model = cfg.get("voice", {}).get("stt", {}).get("model", "medium")
        tts_provider = cfg.get("voice", {}).get("tts", {}).get("provider", "openai")
        tts_voice = cfg.get("voice", {}).get("tts", {}).get("voice", "nova")
        tts_auto = cfg.get("voice", {}).get("tts", {}).get("auto", "inbound")
        print(f"  Voice      STT whisper/{stt_model} · TTS {tts_provider}/{tts_voice} ({tts_auto})")
    else:
        print("  Voice      disabled")

    log_file = cfg.get("logging", {}).get("log_file", "~/.archon/logs/archon.log")
    print(f"  Log        {log_file}")

    config_note = "" if _CONFIG_PATH.exists() else " (not found)"
    print(f"  Config     {_CONFIG_PATH}{config_note}")

    return 0
