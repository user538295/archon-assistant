"""Tests for S4.1 — Logging setup."""
import logging
import pytest
from pathlib import Path

from archon.config.loader import LoggingConfig
from archon.log_setup import setup_logging


@pytest.fixture(autouse=True)
def clean_archon_logger():
    """Remove all handlers from the archon logger before and after each test."""
    logger = logging.getLogger("archon")
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    yield
    for handler in logger.handlers[:]:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)


def test_log_file_created(tmp_path: Path) -> None:
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")

    setup_logging(cfg)

    assert log_file.exists()


def test_log_file_parent_dirs_created(tmp_path: Path) -> None:
    log_file = tmp_path / "subdir" / "nested" / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")

    setup_logging(cfg)

    assert log_file.exists()


def test_info_level_filters_debug_messages(tmp_path: Path) -> None:
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    logger.debug("secret debug message")
    logger.info("visible info message")
    logging.getLogger("archon").handlers[0].flush()

    content = log_file.read_text()
    assert "secret debug message" not in content
    assert "visible info message" in content


def test_debug_level_includes_debug_messages(tmp_path: Path) -> None:
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="DEBUG")
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    logger.debug("debug message here")
    logger.handlers[0].flush()

    content = log_file.read_text()
    assert "debug message here" in content


def test_rotating_handler_limits(tmp_path: Path) -> None:
    """Verify RotatingFileHandler is configured with correct maxBytes and backupCount."""
    import logging.handlers

    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    handler = logger.handlers[0]

    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == 10 * 1024 * 1024
    assert handler.backupCount == 5


def test_tilde_in_log_path_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Log path with ~ is expanded relative to the home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = LoggingConfig(log_file="~/.archon/archon.log", log_level="INFO")

    setup_logging(cfg)

    expected = tmp_path / ".archon" / "archon.log"
    assert expected.exists()
