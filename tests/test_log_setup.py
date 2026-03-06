"""Tests for archon/log_setup.py — TTY-guarded StreamHandler behaviour.

Three layers:
- Unit:        handler presence/absence based on isatty()
- Integration: no duplicate records in the log file when stdout is redirected
- E2E:         subprocess with stdout redirected to the log file (real launchd scenario)
"""
import logging
import logging.handlers
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from archon.config.loader import LoggingConfig
from archon.log_setup import setup_logging


# ── helpers ────────────────────────────────────────────────────────────────


def _cfg(tmp_path: Path) -> LoggingConfig:
    return LoggingConfig(log_file=str(tmp_path / "archon.log"), log_level="WARNING")


def _stream_handlers(logger: logging.Logger) -> list[logging.StreamHandler]:
    """Return StreamHandlers that are NOT FileHandler subclasses."""
    return [
        h for h in logger.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
    ]


def _file_handlers(logger: logging.Logger) -> list[logging.handlers.TimedRotatingFileHandler]:
    return [h for h in logger.handlers if isinstance(h, logging.handlers.TimedRotatingFileHandler)]


def _teardown(logger: logging.Logger, saved_stderr: object) -> None:
    """Close and remove all handlers; restore logger and sys.stderr to defaults."""
    for h in logger.handlers[:]:
        h.close()
    logger.handlers.clear()
    logger.propagate = True       # setup_logging sets this False — restore for caplog
    logger.setLevel(logging.NOTSET)  # remove level override set by setup_logging
    sys.stderr = saved_stderr  # type: ignore[assignment]


# ── Unit tests ─────────────────────────────────────────────────────────────


class TestStreamHandlerTTYGuard:
    """setup_logging must conditionally attach StreamHandler based on isatty()."""

    def test_no_stream_handler_when_not_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In daemon mode (not a TTY), no StreamHandler should be added — prevents
        duplicate writes when launchd redirects stdout to the same log file."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(_cfg(tmp_path))
            assert _stream_handlers(logger) == [], (
                "StreamHandler must NOT be added when stdout is not a TTY "
                "(daemon mode: launchd already writes stdout to the log file)"
            )
        finally:
            _teardown(logger, saved_stderr)

    def test_stream_handler_added_when_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In interactive mode (TTY), exactly one StreamHandler should be present."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(_cfg(tmp_path))
            assert len(_stream_handlers(logger)) == 1, (
                "Exactly one StreamHandler expected in interactive (TTY) mode"
            )
        finally:
            _teardown(logger, saved_stderr)

    def test_file_handler_always_present_non_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """TimedRotatingFileHandler must always be present regardless of TTY."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(_cfg(tmp_path))
            assert len(_file_handlers(logger)) >= 1, "FileHandler must be present in daemon mode"
        finally:
            _teardown(logger, saved_stderr)

    def test_file_handler_always_present_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """TimedRotatingFileHandler must always be present in interactive mode too."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(_cfg(tmp_path))
            assert len(_file_handlers(logger)) >= 1, "FileHandler must be present in TTY mode"
        finally:
            _teardown(logger, saved_stderr)

    def test_stream_handler_targets_stdout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The StreamHandler (when present) must write to sys.stdout."""
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(_cfg(tmp_path))
            handlers = _stream_handlers(logger)
            assert len(handlers) == 1
            assert handlers[0].stream is sys.stdout, (
                "StreamHandler.stream must be sys.stdout"
            )
        finally:
            _teardown(logger, saved_stderr)


# ── Integration tests ──────────────────────────────────────────────────────


class TestIntegration:
    """Verify record counts in the log file under simulated daemon/TTY conditions."""

    def test_no_duplicate_in_log_file_non_tty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Daemon mode + stdout redirected to log file → each record appears once.

        This is the exact launchd scenario: StandardOutPath points to the same file
        as the Python file handler.  Without the isatty() guard, every record would
        be written twice (once by FileHandler, once by StreamHandler → stdout → file).

        Critical: stdout must be redirected BEFORE setup_logging is called, so that
        the StreamHandler (if created) captures the file — not the original terminal.
        Opening in append mode ('a') ensures both O_APPEND file descriptors write
        sequentially rather than overlapping at position 0.
        """
        log_file = tmp_path / "archon.log"
        # Open in append mode so both fd's (FileHandler + StreamHandler) append
        # sequentially — no overwrite at position 0 that would hide the duplicate.
        stdout_redirect = log_file.open("a")
        # Redirect sys.stdout BEFORE setup_logging: the StreamHandler captures the
        # file object, and file.isatty() returns False naturally (no monkeypatching).
        monkeypatch.setattr(sys, "stdout", stdout_redirect)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(LoggingConfig(log_file=str(log_file), log_level="WARNING"))
            logger.warning("UNIQUE_MARKER_NON_TTY_DUPLICATE_CHECK")
            for h in logger.handlers:
                h.flush()
            stdout_redirect.flush()
        finally:
            _teardown(logger, saved_stderr)
            stdout_redirect.close()
        content = log_file.read_text()
        count = content.count("UNIQUE_MARKER_NON_TTY_DUPLICATE_CHECK")
        assert count == 1, (
            f"Expected 1 occurrence but got {count}.\n"
            f"Duplicate logging detected — StreamHandler must not be active in daemon mode.\n{content}"
        )

    def test_records_appear_in_file_tty_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In TTY mode the FileHandler still writes each record exactly once."""
        log_file = tmp_path / "archon.log"
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(LoggingConfig(log_file=str(log_file), log_level="WARNING"))
            logger.warning("UNIQUE_MARKER_TTY_FILE_CHECK")
            for h in logger.handlers:
                h.flush()
            content = log_file.read_text()
            assert content.count("UNIQUE_MARKER_TTY_FILE_CHECK") == 1, (
                f"Expected exactly 1 occurrence in file in TTY mode.\n{content}"
            )
        finally:
            _teardown(logger, saved_stderr)

    def test_records_appear_in_file_non_tty_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """In daemon mode the FileHandler still writes each record exactly once."""
        log_file = tmp_path / "archon.log"
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        logger = logging.getLogger("archon")
        saved_stderr = sys.stderr
        try:
            setup_logging(LoggingConfig(log_file=str(log_file), log_level="WARNING"))
            logger.warning("UNIQUE_MARKER_NON_TTY_FILE_CHECK")
            for h in logger.handlers:
                h.flush()
            content = log_file.read_text()
            assert content.count("UNIQUE_MARKER_NON_TTY_FILE_CHECK") == 1, (
                f"Expected exactly 1 occurrence in file in daemon mode.\n{content}"
            )
        finally:
            _teardown(logger, saved_stderr)


# ── E2E test ───────────────────────────────────────────────────────────────


class TestE2E:
    """End-to-end: subprocess with stdout redirected to the log file.

    This mirrors exactly what launchd does via StandardOutPath.
    stdout.isatty() naturally returns False inside a subprocess whose stdout
    is a file — no monkeypatching needed.
    """

    def test_e2e_no_duplicate_when_stdout_redirected_to_log_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "archon.log"
        script = textwrap.dedent(f"""\
            import sys, logging
            from archon.config.loader import LoggingConfig
            from archon.log_setup import setup_logging

            cfg = LoggingConfig(log_file={str(log_file)!r}, log_level="WARNING")
            setup_logging(cfg)
            logger = logging.getLogger("archon")
            logger.warning("E2E_UNIQUE_MARKER_LAUNCHD_SCENARIO")
            for h in logger.handlers:
                h.flush()
        """)
        script_file = tmp_path / "run_logging.py"
        script_file.write_text(script, encoding="utf-8")

        # Open in append mode (O_APPEND) — this is how launchd's StandardOutPath
        # works.  Both the subprocess stdout fd and the FileHandler fd have O_APPEND,
        # so their writes are sequential rather than overlapping at position 0.
        # open("w") would truncate the file and both writes would land at offset 0,
        # overwriting each other and hiding the duplicate.
        with log_file.open("a", encoding="utf-8") as out_fh:
            result = subprocess.run(
                [sys.executable, str(script_file)],
                stdout=out_fh,
                stderr=subprocess.PIPE,
                # Run from project root so `import archon` resolves correctly.
                cwd=str(Path(__file__).parent.parent),
            )

        if result.returncode != 0:
            pytest.fail(
                f"Subprocess failed (exit {result.returncode}):\n{result.stderr.decode()}"
            )

        content = log_file.read_text(encoding="utf-8")
        count = content.count("E2E_UNIQUE_MARKER_LAUNCHD_SCENARIO")
        assert count == 1, (
            f"E2E: log message appeared {count} time(s) — expected exactly 1.\n"
            f"Duplicate detected: StreamHandler must not write to stdout when stdout is not a TTY.\n"
            f"File contents:\n{content}"
        )
