"""Runtime version computation: importlib.metadata or YY.M.<git-commit-count>."""
import logging
import subprocess
from datetime import datetime
from functools import lru_cache

logger = logging.getLogger("archon")


@lru_cache(maxsize=1)
def get_version() -> str:
    """Return version string; cached so subprocess runs at most once per process.

    Resolution order:
    1. ``importlib.metadata.version("archon")`` — works in installed packages.
    2. Git commit count — works in development checkouts.
    3. Fallback: ``YY.M.0`` — non-git environments (Docker, CI without git).
    """
    try:
        from importlib.metadata import version

        return version("archon")
    except Exception:
        pass

    now = datetime.now()
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        commits = result.stdout.strip()
    except Exception:
        logger.debug("git not available; using commit count 0 for version")
        commits = "0"
    return f"{now.year % 100}.{now.month}.{commits}"


def __getattr__(name: str) -> str:
    if name == "__version__":
        return get_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
