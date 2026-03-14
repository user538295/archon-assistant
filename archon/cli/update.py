"""Update and version commands for the Archon CLI."""
from __future__ import annotations
import json
import re
import subprocess
import urllib.request
from pathlib import Path

_ARCHON_HOME = Path.home() / ".archon"
_GITHUB_API_URL = "https://api.github.com/repos/user538295/archon-assistant/releases/latest"


def _parse_version(text: str) -> tuple[int, ...]:
    """Parse a version string into a numeric tuple for comparison.

    NOTE: Intentionally duplicated in install.py (standalone script, no package imports).
    """
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.groups() if x is not None)


def _fetch_latest_tag() -> str | None:
    """Fetch the latest release tag from GitHub. Returns None on failure."""
    try:
        req = urllib.request.Request(_GITHUB_API_URL, headers={"User-Agent": "archon-cli/1.0"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tag = data.get("tag_name", "").lstrip("v")
        return tag if tag else None
    except Exception:
        return None


def run_update(args: object) -> int:
    install_py = _ARCHON_HOME / "app" / "install.py"
    if not install_py.exists():
        print(f"Installer not found: {install_py}")
        print("Re-run the installer to fix this.")
        return 1

    tag: str | None = getattr(args, "tag", None)
    if not tag:
        print("Resolving latest release...")
        tag = _fetch_latest_tag()
        if not tag:
            print("Error: could not fetch latest release from GitHub.")
            print("Check your internet connection or specify a tag: archon update --tag <version>")
            return 1

        # Downgrade protection: skip if already at or ahead of latest
        try:
            from archon.version import get_version
            current = get_version()
            if _parse_version(current) >= _parse_version(tag):
                print(f"Already up to date (v{current}).")
                return 0
        except Exception:
            pass  # version unavailable — proceed with update

    cmd = ["uv", "run", str(install_py), "--update", "--tag", tag]

    print(f"Updating Archon to v{tag}...")
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("Error: 'uv' not found in PATH. Install uv first: https://docs.astral.sh/uv/")
        return 1
    return result.returncode


def run_uninstall(args: object) -> int:
    install_py = _ARCHON_HOME / "app" / "install.py"
    if not install_py.exists():
        print(f"Installer not found: {install_py}")
        print("Re-run the installer to fix this.")
        return 1

    cmd = ["uv", "run", str(install_py), "--uninstall"]

    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("Error: 'uv' not found in PATH. Install uv first: https://docs.astral.sh/uv/")
        return 1
    return result.returncode


def run_version(args: object) -> int:
    try:
        from archon.version import get_version
        current = get_version()
    except Exception:
        current = "unknown"

    print(f"archon {current}")

    latest = _fetch_latest_tag()
    if latest and _parse_version(latest) > _parse_version(current):
        print(f"Latest available: {latest}  (run: archon update)")
    elif latest:
        print("Up to date.")

    return 0
