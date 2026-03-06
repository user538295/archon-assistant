"""Service lifecycle management: start, stop, restart."""
from __future__ import annotations
import os
import platform
import subprocess
from pathlib import Path

_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.archon.assistant.plist"
_SERVICE_LABEL = "com.archon.assistant"
_SYSTEMD_SERVICE = "archon"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _macos_is_loaded() -> bool:
    """Return True if the launchd service is currently loaded."""
    try:
        result = subprocess.run(
            ["launchctl", "list", _SERVICE_LABEL],
            check=False,
            capture_output=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def run_start() -> int:
    """Start the Archon service. Returns 0 on success, 1 on failure."""
    if _is_macos():
        if not _PLIST_PATH.exists():
            print(f"Plist not found: {_PLIST_PATH}")
            print("Run the installer first: uv run install.py")
            return 1
        if _macos_is_loaded():
            print("Archon is already loaded")
            return 0
        try:
            result = subprocess.run(["launchctl", "load", str(_PLIST_PATH)], check=False)
        except FileNotFoundError:
            print("launchctl not found")
            return 1
    else:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "start", _SYSTEMD_SERVICE], check=False
            )
        except FileNotFoundError:
            print("systemctl not found")
            return 1
    if result.returncode == 0:
        print("Archon started")
        return 0
    print("Failed to start Archon")
    return 1


def run_stop() -> int:
    """Stop the Archon service. Returns 0 on success, 1 on failure."""
    if _is_macos():
        if not _macos_is_loaded():
            print("Archon is not loaded")
            return 0
        if _PLIST_PATH.exists():
            cmd = ["launchctl", "unload", str(_PLIST_PATH)]
        else:
            uid = os.getuid()
            cmd = ["launchctl", "bootout", f"gui/{uid}/{_SERVICE_LABEL}"]
        try:
            result = subprocess.run(cmd, check=False)
        except FileNotFoundError:
            print("launchctl not found")
            return 1
    else:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "stop", _SYSTEMD_SERVICE], check=False
            )
        except FileNotFoundError:
            print("systemctl not found")
            return 1
    if result.returncode == 0:
        print("Archon stopped")
        return 0
    print("Failed to stop Archon")
    return 1


def run_restart() -> int:
    """Restart the Archon service. Returns 0 on success, 1 on failure."""
    if _is_macos():
        if not _PLIST_PATH.exists():
            print(f"Plist not found: {_PLIST_PATH}")
            return 1
        # Unload if currently loaded (skip gracefully if already stopped)
        if _macos_is_loaded():
            try:
                unload = subprocess.run(
                    ["launchctl", "unload", str(_PLIST_PATH)], check=False
                )
            except FileNotFoundError:
                print("launchctl not found")
                return 1
            if unload.returncode != 0:
                print("Failed to stop Archon")
                return 1
        try:
            load = subprocess.run(["launchctl", "load", str(_PLIST_PATH)], check=False)
        except FileNotFoundError:
            print("launchctl not found")
            return 1
        if load.returncode != 0:
            print("Failed to start Archon")
            return 1
    else:
        try:
            result = subprocess.run(
                ["systemctl", "--user", "restart", _SYSTEMD_SERVICE], check=False
            )
        except FileNotFoundError:
            print("systemctl not found")
            return 1
        if result.returncode != 0:
            print("Failed to restart Archon")
            return 1
    print("Archon restarted")
    return 0
