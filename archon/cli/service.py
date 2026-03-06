"""Service lifecycle management: start, stop, restart."""
from __future__ import annotations
import platform
import subprocess
from pathlib import Path

_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.archon.assistant.plist"
_SERVICE_LABEL = "com.archon.assistant"
_SYSTEMD_SERVICE = "archon"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def run_start() -> int:
    """Start the Archon service. Returns 0 on success, 1 on failure."""
    if _is_macos():
        if not _PLIST_PATH.exists():
            print(f"Plist not found: {_PLIST_PATH}")
            print("Run the installer first: uv run install.py")
            return 1
        result = subprocess.run(["launchctl", "load", str(_PLIST_PATH)], check=False)
    else:
        result = subprocess.run(["systemctl", "--user", "start", _SYSTEMD_SERVICE], check=False)
    if result.returncode == 0:
        print("Archon started")
        return 0
    print("Failed to start Archon")
    return 1


def run_stop() -> int:
    """Stop the Archon service. Returns 0 on success, 1 on failure."""
    if _is_macos():
        result = subprocess.run(["launchctl", "unload", str(_PLIST_PATH)], check=False)
    else:
        result = subprocess.run(["systemctl", "--user", "stop", _SYSTEMD_SERVICE], check=False)
    if result.returncode == 0:
        print("Archon stopped")
        return 0
    print("Failed to stop Archon")
    return 1


def run_restart() -> int:
    """Restart the Archon service. Returns 0 on success, 1 on failure."""
    rc = run_stop()
    if rc != 0:
        return 1
    rc = run_start()
    if rc != 0:
        return 1
    print("Archon restarted")
    return 0
