"""Tests for archon.version."""
import re
from unittest.mock import patch


def test_version_format() -> None:
    from archon.version import get_version

    v = get_version()
    assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d+", v), f"unexpected format: {v}"


def test_version_year_and_month() -> None:
    from datetime import datetime
    from archon.version import get_version

    now = datetime.now()
    v = get_version()
    parts = v.split(".")
    assert parts[0] == str(now.year % 100)
    assert parts[1] == str(now.month)


def test_version_commit_count_from_git() -> None:
    from unittest.mock import MagicMock
    from archon.version import get_version

    mock_result = MagicMock()
    mock_result.stdout = "42\n"
    with patch("archon.version.subprocess.run", return_value=mock_result):
        v = get_version()
    assert v.endswith(".42")


def test_version_fallback_on_git_failure() -> None:
    from archon.version import get_version

    with patch("archon.version.subprocess.run", side_effect=FileNotFoundError):
        v = get_version()
    assert v.endswith(".0")


def test_dunder_version_is_string() -> None:
    from archon import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0
