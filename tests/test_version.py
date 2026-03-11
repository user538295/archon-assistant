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

    with patch("archon.version.subprocess.run", return_value=mock_result) as mock_run:
        v1 = version_mod.get_version()
        v2 = version_mod.get_version()
        v3 = version_mod.get_version()

    assert v1 == v2 == v3
    assert mock_run.call_count == 1, "subprocess.run must be called exactly once (cached)"


def test_version_fallback_on_git_failure() -> None:
    from archon.version import get_version

    with patch("archon.version.subprocess.run", side_effect=FileNotFoundError):
        v = get_version()
    # Fallback must be a real version string "YY.M.0", not bare "0"
    assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.0", v), f"unexpected fallback format: {v}"


def _make_describe_fail_side_effect(commit_count: str):  # type: ignore[no-untyped-def]
    """Return a subprocess.run side effect that makes git describe fail and rev-list return commit_count."""
    import subprocess as sp

    count_result = MagicMock()
    count_result.stdout = f"{commit_count}\n"

    def _side_effect(*args: object, **kwargs: object) -> MagicMock:
        cmd = args[0]
        if "describe" in cmd:
            raise sp.CalledProcessError(128, cmd)
        return count_result

    return _side_effect


def test_version_commit_count_from_git() -> None:
    from archon.version import get_version

    with patch("archon.version.subprocess.run", side_effect=_make_describe_fail_side_effect("42")):
        v = get_version()
    assert v.endswith(".42")


def test_version_git_format() -> None:
    from archon.version import get_version

    with patch("archon.version.subprocess.run", side_effect=_make_describe_fail_side_effect("100")):
        v = get_version()
    assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d+", v), f"unexpected format: {v}"


def test_version_uses_git_not_importlib() -> None:
    """importlib.metadata must NOT be consulted — git commit count always wins."""
    from archon.version import get_version

    with patch("archon.version.subprocess.run", side_effect=_make_describe_fail_side_effect("302")) as mock_git:
        v = get_version()

    assert v.endswith(".302")
    assert mock_git.call_count == 2  # one describe (fails) + one rev-list


def test_version_git_describe_tag_takes_priority() -> None:
    """When git describe returns an exact tag, that tag is used directly."""
    from archon.version import get_version

    tag_result = MagicMock()
    tag_result.stdout = "v26.3.198\n"

    # First call (git describe) succeeds; second call (rev-list) should NOT be reached.
    with patch("archon.version.subprocess.run", return_value=tag_result) as mock_run:
        v = get_version()

    assert v == "26.3.198"
    # Only the describe call should have been made
    mock_run.assert_called_once()


def test_version_git_describe_falls_back_to_commit_count_on_failure() -> None:
    """When git describe fails (no exact tag), rev-list commit count is used."""
    from archon.version import get_version

    count_result = MagicMock()
    count_result.stdout = "55\n"

    def _side_effect(*args: object, **kwargs: object) -> MagicMock:
        cmd = args[0]
        if "describe" in cmd:
            import subprocess as sp
            raise sp.CalledProcessError(128, cmd)
        return count_result

    with patch("archon.version.subprocess.run", side_effect=_side_effect):
        v = get_version()

    assert v.endswith(".55")


def test_version_empty_stdout_falls_back_to_zero() -> None:
    """Empty stdout from rev-list must produce commit count '0', not blank."""
    from archon.version import get_version

    def _side_effect(*args: object, **kwargs: object) -> MagicMock:
        cmd = args[0]
        if "describe" in cmd:
            import subprocess as sp
            raise sp.CalledProcessError(128, cmd)
        result = MagicMock()
        result.stdout = ""  # empty — simulates pathological git output
        return result

    with patch("archon.version.subprocess.run", side_effect=_side_effect):
        v = get_version()

    assert re.fullmatch(r"\d{1,2}\.\d{1,2}\.0", v), f"unexpected format: {v}"


def test_version_git_describe_strips_leading_v() -> None:
    """Tag 'v26.3.1' must be returned as '26.3.1' (no leading 'v')."""
    from archon.version import get_version

    tag_result = MagicMock()
    tag_result.stdout = "v26.3.1\n"

    with patch("archon.version.subprocess.run", return_value=tag_result):
        v = get_version()

    assert v == "26.3.1"
    assert not v.startswith("v")


def test_dunder_version_is_string() -> None:
    from archon import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_dunder_version_from_version_module() -> None:
    from archon.version import __version__

    assert isinstance(__version__, str)
    assert len(__version__) > 0
