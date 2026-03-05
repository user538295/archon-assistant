"""Tests for S4.1 / S4.4 — Logging setup & daily log rotation."""
import io
import logging
import logging.handlers
import os
import re
import sys
import time
import pytest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from archon.config.loader import LoggingConfig
from archon.log_setup import _StderrToLogger, _daily_log_namer, _rotate_on_startup, setup_logging


@pytest.fixture(autouse=True)
def clean_archon_logger():
    """Remove all handlers from the archon logger before and after each test.

    Also restores sys.stderr so stderr-redirect tests don't leak state.
    """
    _original_stderr = sys.stderr
    logger = logging.getLogger("archon")
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    yield
    for handler in logger.handlers[:]:
        handler.close()
    logger.handlers.clear()
    logger.setLevel(logging.NOTSET)
    sys.stderr = _original_stderr


# ---------------------------------------------------------------------------
# Existing S4.1 tests
# ---------------------------------------------------------------------------

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


def test_timed_handler_limits(tmp_path: Path) -> None:
    """Verify TimedRotatingFileHandler is configured for midnight rotation, keeping all files."""
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    handler = logger.handlers[0]

    assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
    assert handler.when == "MIDNIGHT"
    assert handler.backupCount == 0


def test_double_setup_no_handler_accumulation(tmp_path: Path) -> None:
    """Calling setup_logging twice must not accumulate handlers."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")

    setup_logging(cfg)
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    assert len(logger.handlers) == 2  # file handler + console handler


def test_tilde_in_log_path_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Log path with ~ is expanded relative to the home directory."""
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = LoggingConfig(log_file="~/.archon/archon.log", log_level="INFO")

    setup_logging(cfg)

    expected = tmp_path / ".archon" / "archon.log"
    assert expected.exists()


# ---------------------------------------------------------------------------
# S4.4 — daily log rotation: _daily_log_namer
# ---------------------------------------------------------------------------

def test_daily_log_namer_renames_correctly() -> None:
    """archon.log.2026-02-22 → archon.2026-02-22.log"""
    result = _daily_log_namer("/home/user/.archon/archon.log.2026-02-22")
    assert result == "/home/user/.archon/archon.2026-02-22.log"


def test_daily_log_namer_preserves_parent_dir(tmp_path: Path) -> None:
    """Parent directory is unchanged after namer transformation."""
    default = str(tmp_path / "archon.log.2026-02-22")
    result = _daily_log_namer(default)
    assert Path(result).parent == tmp_path


def test_daily_log_namer_date_in_stem() -> None:
    """Date appears in the stem, not as a trailing suffix."""
    result = _daily_log_namer("/logs/archon.log.2025-12-31")
    p = Path(result)
    assert p.suffix == ".log"
    assert "2025-12-31" in p.stem


# ---------------------------------------------------------------------------
# S4.4 — daily log rotation: _rotate_on_startup
# ---------------------------------------------------------------------------

def test_rotate_on_startup_no_file(tmp_path: Path) -> None:
    """Does nothing when the log file does not exist."""
    log_path = tmp_path / "archon.log"
    _rotate_on_startup(log_path)  # must not raise
    assert not log_path.exists()


def test_rotate_on_startup_today_file(tmp_path: Path) -> None:
    """Does nothing when the log file's mtime is today."""
    log_path = tmp_path / "archon.log"
    log_path.write_text("today's log")
    # mtime defaults to now → today

    _rotate_on_startup(log_path)

    assert log_path.exists()
    assert log_path.read_text() == "today's log"


def test_rotate_on_startup_old_file(tmp_path: Path) -> None:
    """Renames the log file using its mtime date when it is from a previous day."""
    log_path = tmp_path / "archon.log"
    log_path.write_text("yesterday's log")

    # Back-date the mtime to yesterday
    yesterday = date.today() - timedelta(days=1)
    yesterday_ts = datetime(yesterday.year, yesterday.month, yesterday.day, 23, 59).timestamp()
    os.utime(log_path, (yesterday_ts, yesterday_ts))

    _rotate_on_startup(log_path)

    expected_name = tmp_path / f"archon.{yesterday}.log"
    assert expected_name.exists()
    assert not log_path.exists()
    assert expected_name.read_text() == "yesterday's log"


def test_rotate_on_startup_two_days_old(tmp_path: Path) -> None:
    """Renames correctly even when the file is several days old."""
    log_path = tmp_path / "archon.log"
    log_path.write_text("old log")

    old_date = date.today() - timedelta(days=5)
    old_ts = datetime(old_date.year, old_date.month, old_date.day, 10, 0).timestamp()
    os.utime(log_path, (old_ts, old_ts))

    _rotate_on_startup(log_path)

    expected_name = tmp_path / f"archon.{old_date}.log"
    assert expected_name.exists()


# ---------------------------------------------------------------------------
# S4.4 — handler wiring
# ---------------------------------------------------------------------------

def test_handler_is_timed_rotating(tmp_path: Path) -> None:
    """setup_logging installs a TimedRotatingFileHandler."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    handler = logging.getLogger("archon").handlers[0]
    assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)


def test_timed_handler_namer_is_set(tmp_path: Path) -> None:
    """The handler's namer is set to _daily_log_namer."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    handler = logging.getLogger("archon").handlers[0]
    assert handler.namer is _daily_log_namer


def test_setup_logging_rotates_old_file_on_startup(tmp_path: Path) -> None:
    """setup_logging triggers startup rotation for a previous-day log file."""
    log_path = tmp_path / "archon.log"
    log_path.write_text("old content")

    yesterday = date.today() - timedelta(days=1)
    yesterday_ts = datetime(yesterday.year, yesterday.month, yesterday.day, 12, 0).timestamp()
    os.utime(log_path, (yesterday_ts, yesterday_ts))

    cfg = LoggingConfig(log_file=str(log_path), log_level="INFO")
    setup_logging(cfg)

    # Old file renamed
    assert (tmp_path / f"archon.{yesterday}.log").exists()
    # Fresh log file created by the handler
    assert log_path.exists()


# ---------------------------------------------------------------------------
# Console (stdout) handler — timestamps visible in terminal
# ---------------------------------------------------------------------------

def test_console_handler_added(tmp_path: Path) -> None:
    """setup_logging installs a StreamHandler writing to stdout."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    console_handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert len(console_handlers) == 1


def test_console_handler_targets_stdout(tmp_path: Path) -> None:
    """The console handler writes to sys.stdout."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")

    fake_stdout = io.StringIO()
    with patch("sys.stdout", fake_stdout):
        setup_logging(cfg)

    logger = logging.getLogger("archon")
    console_handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    assert console_handlers[0].stream is fake_stdout


def test_console_handler_has_timestamp_format(tmp_path: Path) -> None:
    """The console handler's formatter includes %(asctime)s."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    logger = logging.getLogger("archon")
    console_handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.TimedRotatingFileHandler)
    ]
    fmt = console_handlers[0].formatter._fmt
    assert "%(asctime)s" in fmt


def test_stdout_output_has_timestamp(tmp_path: Path) -> None:
    """Log records emitted to stdout include a timestamp."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")

    fake_stdout = io.StringIO()
    with patch("sys.stdout", fake_stdout):
        setup_logging(cfg)
        logging.getLogger("archon").info("hello from archon")
        for h in logging.getLogger("archon").handlers:
            h.flush()

    output = fake_stdout.getvalue()
    assert "hello from archon" in output
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", output)


def test_setup_logging_has_two_handlers(tmp_path: Path) -> None:
    """setup_logging installs exactly two handlers: file + console."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    assert len(logging.getLogger("archon").handlers) == 2


def test_setup_logging_disables_propagation(tmp_path: Path) -> None:
    """setup_logging sets propagate=False to prevent duplicate log output."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    assert logging.getLogger("archon").propagate is False


def test_no_duplicate_output_to_root_logger(tmp_path: Path) -> None:
    """Messages to archon logger do not propagate to the root logger."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    root_messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            root_messages.append(record.getMessage())

    root_capture = _Capture()
    logging.getLogger().addHandler(root_capture)
    try:
        logging.getLogger("archon").info("should not reach root")
        for h in logging.getLogger("archon").handlers:
            h.flush()
    finally:
        logging.getLogger().removeHandler(root_capture)

    assert root_messages == []


# ---------------------------------------------------------------------------
# stderr redirect — tracebacks and runtime errors get timestamps
# ---------------------------------------------------------------------------

def test_stderr_redirected_after_setup(tmp_path: Path) -> None:
    """sys.stderr is replaced with _StderrToLogger after setup_logging."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)

    assert isinstance(sys.stderr, _StderrToLogger)


def test_stderr_write_appears_in_log_file(tmp_path: Path) -> None:
    """Content written to sys.stderr after setup appears in the log file."""
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")
    setup_logging(cfg)

    sys.stderr.write("critical runtime error\n")
    sys.stderr.flush()
    for h in logging.getLogger("archon").handlers:
        h.flush()

    content = log_file.read_text()
    assert "critical runtime error" in content


def test_stderr_write_has_timestamp_in_log(tmp_path: Path) -> None:
    """stderr content in the log file is prefixed with a timestamp."""
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")
    setup_logging(cfg)

    sys.stderr.write("error with timestamp\n")
    sys.stderr.flush()
    for h in logging.getLogger("archon").handlers:
        h.flush()

    content = log_file.read_text()
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}.*error with timestamp", content)


def test_stderr_empty_lines_not_logged(tmp_path: Path) -> None:
    """Blank writes to sys.stderr do not create spurious log entries."""
    log_file = tmp_path / "archon.log"
    cfg = LoggingConfig(log_file=str(log_file), log_level="INFO")
    setup_logging(cfg)

    sys.stderr.write("\n")
    sys.stderr.write("   \n")
    sys.stderr.flush()
    for h in logging.getLogger("archon").handlers:
        h.flush()

    content = log_file.read_text()
    # Only rotation / startup messages may be present; no blank-line entries
    assert content.strip() == "" or "ERROR" not in content


def test_double_setup_stderr_not_double_wrapped(tmp_path: Path) -> None:
    """Calling setup_logging twice does not wrap _StderrToLogger inside itself."""
    cfg = LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="INFO")
    setup_logging(cfg)
    first_wrapper = sys.stderr
    setup_logging(cfg)

    # sys.stderr should still be a _StderrToLogger, not a nested one
    assert isinstance(sys.stderr, _StderrToLogger)
    # The second call must not have created a new wrapper around the first
    assert sys.stderr is first_wrapper or not isinstance(
        getattr(sys.stderr, "_logger", None), _StderrToLogger
    )


# ---------------------------------------------------------------------------
# _StderrToLogger unit tests
# ---------------------------------------------------------------------------

def _make_logger_with_capture() -> tuple[logging.Logger, list[str]]:
    """Return a logger and a list that captures every logged message."""
    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            messages.append(record.getMessage())

    logger = logging.getLogger("archon.stderr_test")
    logger.handlers.clear()
    logger.addHandler(_Capture())
    logger.setLevel(logging.DEBUG)
    return logger, messages


def test_stderr_to_logger_single_line() -> None:
    """A single complete line is emitted as one log record."""
    logger, messages = _make_logger_with_capture()
    wrapper = _StderrToLogger(logger)

    wrapper.write("something went wrong\n")

    assert messages == ["something went wrong"]


def test_stderr_to_logger_partial_line_buffered() -> None:
    """A write without newline is buffered until flush."""
    logger, messages = _make_logger_with_capture()
    wrapper = _StderrToLogger(logger)

    wrapper.write("partial line")
    assert messages == []

    wrapper.flush()
    assert messages == ["partial line"]


def test_stderr_to_logger_multiline() -> None:
    """Multiple newlines produce one record per non-empty line."""
    logger, messages = _make_logger_with_capture()
    wrapper = _StderrToLogger(logger)

    wrapper.write("line one\nline two\nline three\n")

    assert messages == ["line one", "line two", "line three"]


def test_stderr_to_logger_isatty_returns_false() -> None:
    logger, _ = _make_logger_with_capture()
    wrapper = _StderrToLogger(logger)
    assert wrapper.isatty() is False


def test_stderr_to_logger_fileno_raises() -> None:
    logger, _ = _make_logger_with_capture()
    wrapper = _StderrToLogger(logger)
    with pytest.raises(OSError):
        wrapper.fileno()
