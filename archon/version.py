"""Runtime version computation: YY.M.<git-commit-count>."""
import subprocess
from datetime import datetime


def get_version() -> str:
    """Return version string as YY.M.<git-commit-count>."""
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
        commits = "0"
    return f"{now.year % 100}.{now.month}.{commits}"


__version__ = get_version()
