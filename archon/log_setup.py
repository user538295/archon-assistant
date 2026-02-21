"""Logging setup for the archon daemon."""
import logging
import logging.handlers
from pathlib import Path

from archon.config.loader import LoggingConfig

_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5
_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"


def setup_logging(cfg: LoggingConfig) -> None:
    """Configure the 'archon' logger with a rotating file handler."""
    log_path = Path(cfg.log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
    )
    handler.setFormatter(logging.Formatter(_FORMAT))

    logger = logging.getLogger("archon")
    logger.setLevel(getattr(logging, cfg.log_level.upper()))
    logger.addHandler(handler)
