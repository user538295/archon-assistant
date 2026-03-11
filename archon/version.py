"""Runtime version computation: YY.M.<git-commit-count>."""
import logging
import subprocess
from datetime import datetime
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("archon")

_REPO_ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return version string; cached so subprocess runs at most once per process.

    Resolution order:
    1. Exact git tag — works with shallow clones in production (``git clone --depth 1``).
    2. Git commit count — works in development checkouts with full history.
    3. Fallback: ``YY.M.0`` — non-git environments (Docker, CI without git).
    """
    # Try tagged version first (works with shallow clones in production)
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_REPO_ROOT,
        )
        tag = result.stdout.strip().lstrip("v")
        if tag:
            return tag
    except Exception:
        pass

    # Fall back to commit count (works in dev checkouts with full history)
    now = datetime.now()
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_REPO_ROOT,
        )
        commits = result.stdout.strip() or "0"
    except Exception:
        logger.debug("git not available; using commit count 0 for version")
        commits = "0"
    return f"{now.year % 100}.{now.month}.{commits}"


def __getattr__(name: str) -> str:
    if name == "__version__":
        return get_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
