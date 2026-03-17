"""Log viewing command for the Archon CLI."""
from __future__ import annotations
import re
import subprocess
import tomllib
from pathlib import Path

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _log_path() -> Path:
    """Read log file path from config; fall back to default."""
    config = Path.home() / ".archon" / "config.toml"
    try:
        with open(config, "rb") as f:
            data = tomllib.load(f)
        val = data.get("logging", {}).get("log_file", "")
        if val:
            return Path(val).expanduser()
    except Exception:
        pass
    return Path.home() / ".archon" / "logs" / "archon.log"


def run_logs(args: object) -> int:
    date: str | None = getattr(args, "date", None)
    follow: bool = getattr(args, "follow", False)
    lines: int = getattr(args, "lines", 50)

    if lines <= 0:
        print("--lines must be a positive integer")
        return 1

    if date:
        if not _DATE_RE.match(date):
            print(f"Invalid --date format: {date!r}. Expected YYYY-MM-DD.")
            return 1
        log_file = _log_path().parent / f"archon.{date}.log"
    else:
        log_file = _log_path()

    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        return 1

    if follow:
        try:
            subprocess.run(["tail", "-f", str(log_file)])
        except KeyboardInterrupt:
            pass
        except FileNotFoundError:
            print("tail not found")
            return 1
        return 0

    try:
        result = subprocess.run(["tail", f"-{lines}", str(log_file)])
    except FileNotFoundError:
        print("tail not found")
        return 1
    return result.returncode
