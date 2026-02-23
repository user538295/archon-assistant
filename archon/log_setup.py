"""Logging setup for the archon daemon."""
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from archon.config.loader import LoggingConfig

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def _daily_log_namer(default_name: str) -> str:
    """Rename a rotated log file: archon.log.YYYY-MM-DD → archon.YYYY-MM-DD.log.

    Python's TimedRotatingFileHandler appends the date as a trailing suffix, e.g.
    ``archon.log.2026-02-22``.  This namer moves the date before the extension so
    the rotated file is named ``archon.2026-02-22.log``.
    """
    p = Path(default_name)
    date_suffix = p.suffix          # e.g. ".2026-02-22"
    base = Path(p.stem).stem        # strips inner ".log" → "archon"
    return str(p.parent / f"{base}{date_suffix}.log")


def _rotate_on_startup(log_path: Path) -> None:
    """Rename an existing log file if it is from a previous day.

    Handles the case where the daemon was stopped or crashed before midnight:
    the stale ``archon.log`` is renamed to ``archon.YYYY-MM-DD.log`` (using the
    file's modification date) so that a fresh log file can be opened.
    """
    if not log_path.exists():
        return
    mtime_date = datetime.fromtimestamp(log_path.stat().st_mtime).date()
    today = datetime.now().date()
    if mtime_date < today:
        dated_path = log_path.parent / f"{log_path.stem}.{mtime_date}.log"
        log_path.rename(dated_path)


def setup_logging(cfg: LoggingConfig) -> None:
    """Configure the 'archon' logger with a daily rotating file handler."""
    log_path = Path(cfg.log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    _rotate_on_startup(log_path)

    handler = logging.handlers.TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=0,
    )
    handler.namer = _daily_log_namer
    handler.setFormatter(logging.Formatter(_FORMAT))

    logger = logging.getLogger("archon")
    logger.setLevel(getattr(logging, cfg.log_level.upper()))
    for h in logger.handlers[:]:
        h.close()
    logger.handlers.clear()
    logger.addHandler(handler)
