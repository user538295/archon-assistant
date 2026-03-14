"""Status panel for the Archon daemon."""
from __future__ import annotations

import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from archon.platform import get_service

_ARCHON_HOME = Path.home() / ".archon"
_CONFIG_PATH = _ARCHON_HOME / "config.toml"
_DEFAULT_BG_HOST = "localhost"
_DEFAULT_BG_PORT = 18182


@dataclass
class HealthInfo:
    reachable: bool
    latency_ms: int | None


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

    service = None
    try:
        service = get_service()
        svc = service.status()
    except Exception as exc:
        print(f"Error: {exc}")
        if service is not None and hasattr(service, "remediation_hint"):
            print(service.remediation_hint())
        return 1
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
        pid_str = f"PID {svc.pid}" if svc.pid else "PID unknown"
        uptime_str = f"· uptime {svc.uptime}" if svc.uptime else ""
        print(f"  Service    {service.service_name} · {pid_str} {uptime_str}".rstrip())

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
