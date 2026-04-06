"""Tests for archon.cli.console — Console output helper."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from archon.cli.console import Console


def test_console_info_prints(capsys: pytest.CaptureFixture[str]) -> None:
    Console().info("x")
    out, _ = capsys.readouterr()
    assert out.strip() != ""


def test_console_success_prints(capsys: pytest.CaptureFixture[str]) -> None:
    Console().success("x")
    out, _ = capsys.readouterr()
    assert out.strip() != ""


def test_console_warn_prints(capsys: pytest.CaptureFixture[str]) -> None:
    Console().warn("x")
    out, _ = capsys.readouterr()
    assert out.strip() != ""


def test_console_error_prints_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    Console().error("boom")
    out, err = capsys.readouterr()
    assert err.strip() != ""
    assert out == ""


def test_console_ask_returns_input() -> None:
    with patch("builtins.input", return_value="answer"):
        result = Console().ask("question?")
    assert result == "answer"


def test_console_quiet_suppresses_info(capsys: pytest.CaptureFixture[str]) -> None:
    Console(quiet=True).info("x")
    out, _ = capsys.readouterr()
    assert out == ""


def test_console_quiet_suppresses_success(capsys: pytest.CaptureFixture[str]) -> None:
    Console(quiet=True).success("x")
    out, _ = capsys.readouterr()
    assert out == ""


def test_console_quiet_suppresses_warn(capsys: pytest.CaptureFixture[str]) -> None:
    Console(quiet=True).warn("x")
    out, _ = capsys.readouterr()
    assert out == ""


def test_console_quiet_does_not_suppress_error(capsys: pytest.CaptureFixture[str]) -> None:
    Console(quiet=True).error("boom")
    _, err = capsys.readouterr()
    assert err.strip() != ""


def test_console_quiet_ask_returns_empty_string() -> None:
    result = Console(quiet=True).ask("?")
    assert result == ""
