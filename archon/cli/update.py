"""Update and version commands for the Archon CLI."""
from __future__ import annotations
import json
import logging
import re
import subprocess
import urllib.request
from pathlib import Path

logger = logging.getLogger("archon")

_ARCHON_HOME = Path.home() / ".archon"
_GITHUB_REPO = "user538295/archon-assistant"
_RELEASES_URL = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
_TAGS_URL = f"https://api.github.com/repos/{_GITHUB_REPO}/tags?per_page=20"
_HEADERS = {"User-Agent": "archon-cli/1.0"}


def _parse_version(text: str) -> tuple[int, ...]:
    """Parse a version string into a numeric tuple for comparison.

    NOTE: Intentionally duplicated in install.py (standalone script, no package imports).
    """
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.groups() if x is not None)


def _fetch_latest_tag() -> str | None:
    """Fetch the latest release tag from GitHub, falling back to tags API."""
    tag = _fetch_from_releases()
    if tag:
        return tag
    return _fetch_from_tags()


def _fetch_from_releases() -> str | None:
    """Try the releases/latest endpoint."""
    try:
        req = urllib.request.Request(_RELEASES_URL, headers=_HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        tag = data.get("tag_name", "").lstrip("v")
        return tag if tag else None
    except Exception:
        logger.debug("releases endpoint failed", exc_info=True)
        return None


def _fetch_from_tags() -> str | None:
    """Fallback: fetch recent tags and pick the highest version.

    The tags API sorts by commit date, not version, so we fetch multiple
    tags and select the one with the highest parsed version.
    """
    try:
        req = urllib.request.Request(_TAGS_URL, headers=_HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if not data or not isinstance(data, list):
            return None
        best_tag: str | None = None
        best_version: tuple[int, ...] = (0,)
        for entry in data:
            name = entry.get("name", "").lstrip("v")
            version = _parse_version(name)
            if version > best_version:
                best_version = version
                best_tag = name
        return best_tag
    except Exception:
        logger.debug("tags endpoint failed", exc_info=True)
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


def _uninstall_search_service() -> None:
    """Stop and unregister the standalone search service if installed. Never raises."""
    try:
        result = subprocess.run(
            ["archon-search", "uninstall"],
            capture_output=True,
        )
        if result.returncode != 0:
            logger.debug("archon-search uninstall exited %d", result.returncode)
    except FileNotFoundError:
        logger.debug("archon-search not found — search service was never installed")
    except Exception:
        logger.debug("Search service uninstall skipped", exc_info=True)


def run_uninstall(args: object) -> int:
    install_py = _ARCHON_HOME / "app" / "install.py"
    if not install_py.exists():
        print(f"Installer not found: {install_py}")
        print("Re-run the installer to fix this.")
        return 1

    _uninstall_search_service()

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
