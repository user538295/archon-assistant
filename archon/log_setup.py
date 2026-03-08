"""Logging setup for the archon daemon."""
import logging
import logging.handlers
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from archon.config.loader import LoggingConfig

_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


class _StderrToLogger:
    """Route sys.stderr writes to the archon logger at ERROR level.

    Buffers partial writes and emits each complete line as a separate log
    record so that Python tracebacks and runtime errors appear in both the
    log file and the console (stdout) with timestamps when the gateway is
    started from a terminal.
    """

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._buf = ""

    def write(self, msg: str) -> None:
        self._buf += msg
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._logger.error(line)

    def flush(self) -> None:
        if self._buf.strip():
            self._logger.error(self._buf.strip())
            self._buf = ""

    def fileno(self) -> int:
        raise OSError("fileno not supported by _StderrToLogger")

    def isatty(self) -> bool:
        return False

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)


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
        try:
            log_path.rename(dated_path)
        except OSError:
            pass  # Already renamed by a concurrent restart — safe to ignore


def setup_logging(cfg: LoggingConfig) -> None:
    """Configure the 'archon' logger with timestamped console and file output.

    Installs two handlers on the root 'archon' logger:
    * A daily-rotating file handler that persists all records to disk.
    * A StreamHandler(sys.stdout) so that every log record is printed to the
      terminal with a timestamp when the gateway is started interactively.

    Also redirects sys.stderr to :class:`_StderrToLogger` so that Python
    tracebacks and other runtime errors are captured with timestamps in both
    the log file and the terminal.  The redirect is idempotent: calling
    setup_logging a second time does not wrap stderr twice.
    """
    log_path = Path(cfg.log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    _rotate_on_startup(log_path)

    fmt = logging.Formatter(_FORMAT)

    # File handler — daily rotating, keeps all backups
    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=0,
    )
    file_handler.namer = _daily_log_namer
    file_handler.setFormatter(fmt)

    logger = logging.getLogger("archon")
    logger.setLevel(getattr(logging, cfg.log_level.upper()))
    for h in logger.handlers[:]:
        h.close()
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(file_handler)

    # Console handler — only attached when running interactively (stdout is a TTY).
    # Under launchd/systemd the process stdout is already redirected to the log file
    # via StandardOutPath/StandardOutput; adding a StreamHandler here would cause
    # every record to be written twice to the same file.
    if sys.stdout.isatty():
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)

    # Redirect stderr → logger (idempotent: skip if already wrapped)
    if not isinstance(sys.stderr, _StderrToLogger):
        sys.stderr = _StderrToLogger(logger)
