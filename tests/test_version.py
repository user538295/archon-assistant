"""Tests for archon.version."""
import re
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def clear_version_cache() -> None:
    """Clear lru_cache before each test so mocks take effect."""
    from archon.version import get_version

    get_version.cache_clear()
    yield  # type: ignore[misc]
    get_version.cache_clear()


def test_version_is_string() -> None:
    from archon.version import get_version

    v = get_version()
    assert isinstance(v, str) and len(v) > 0


def test_version_cached() -> None:
    """Subprocess must be called at most once even if get_version() is called many times."""
    import archon.version as version_mod

    mock_result = MagicMock()
    mock_result.stdout = "99\n"

    with patch("importlib.metadata.version", side_effect=Exception("not installed")):
        with patch("archon.version.subprocess.run", return_value=mock_result) as mock_run:
            v1 = version_mod.get_version()
            v2 = version_mod.get_version()
            v3 = version_mod.get_version()

    assert v1 == v2 == v3
    assert mock_run.call_count == 1, "subprocess.run must be called exactly once (cached)"


def test_version_fallback_on_git_failure() -> None:
    from archon.version import get_version

    with patch("importlib.metadata.version", side_effect=Exception("not installed")):
        with patch("archon.version.subprocess.run", side_effect=FileNotFoundError):
            v = get_version()
    # Fallback must be a real version string "YY.M.0", not bare "0"
    assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.0", v), f"unexpected fallback format: {v}"


def test_version_commit_count_from_git() -> None:
    from archon.version import get_version

    mock_result = MagicMock()
    mock_result.stdout = "42\n"

    with patch("importlib.metadata.version", side_effect=Exception("not installed")):
        with patch("archon.version.subprocess.run", return_value=mock_result):
            v = get_version()
    assert v.endswith(".42")


def test_version_git_format() -> None:
    from archon.version import get_version

    mock_result = MagicMock()
    mock_result.stdout = "100\n"

    with patch("importlib.metadata.version", side_effect=Exception("not installed")):
        with patch("archon.version.subprocess.run", return_value=mock_result):
            v = get_version()
    assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d+", v), f"unexpected format: {v}"


def test_version_prefers_importlib_metadata() -> None:
    """When package is installed, importlib.metadata takes priority over git."""
    from archon.version import get_version

    with patch("importlib.metadata.version", return_value="1.2.3") as mock_meta:
        with patch("archon.version.subprocess.run") as mock_git:
            v = get_version()

    assert v == "1.2.3"
    mock_meta.assert_called_once_with("archon")
    mock_git.assert_not_called()


def test_dunder_version_is_string() -> None:
    from archon import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_dunder_version_from_version_module() -> None:
    from archon.version import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0
