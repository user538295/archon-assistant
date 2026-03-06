"""Update and version commands for the Archon CLI."""
from __future__ import annotations
import json
import subprocess
import urllib.request
from pathlib import Path

_ARCHON_HOME = Path.home() / ".archon"


def run_update(args: object) -> int:
    install_py = _ARCHON_HOME / "app" / "install.py"
    if not install_py.exists():
        print(f"Installer not found: {install_py}")
        print("Re-run the installer to fix this.")
        return 1

    tag: str | None = getattr(args, "tag", None)
    cmd = ["uv", "run", str(install_py), "--update"]
    if tag:
        cmd += ["--tag", tag]

    print(f"Updating Archon{' to v' + tag if tag else ''}...")
    result = subprocess.run(cmd)
    return result.returncode


def run_version(args: object) -> int:
    try:
        from archon.version import get_version
        current = get_version()
    except Exception:
        current = "unknown"

    print(f"archon {current}")

    try:
        url = "https://api.github.com/repos/user538295/archon-assistant/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "archon-cli/1.0"})
        resp = urllib.request.urlopen(req, timeout=3)
        data = json.loads(resp.read())
        latest = data.get("tag_name", "").lstrip("v")
        if latest and latest != current:
            print(f"Latest available: {latest}  (run: archon update --tag {latest})")
        elif latest:
            print("Up to date.")
    except Exception:
        pass

    return 0
