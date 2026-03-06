"""Log viewing command for the Archon CLI."""
from __future__ import annotations
import subprocess
import tomllib
from pathlib import Path


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

    if date:
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
        return 0

    result = subprocess.run(["tail", f"-{lines}", str(log_file)])
    return result.returncode
