"""Platform test configuration — markers, auto-skip, singleton cleanup."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from archon.platform import reset


def mock_loop(task_done: bool = False) -> MagicMock:
    """Create a mock event loop whose create_task closes coroutines to avoid warnings."""
    loop = MagicMock()

    def _close_coro(coro):
        coro.close()
        task = MagicMock()
        task.done.return_value = task_done
        return task

    loop.create_task.side_effect = _close_coro
    return loop


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "macos: macOS-only test")
    config.addinivalue_line("markers", "linux: Linux-only test")
    config.addinivalue_line("markers", "live: requires real OS service manager")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("macos") and sys.platform != "darwin":
            item.add_marker(pytest.mark.skip(reason="macOS only"))
        if item.get_closest_marker("linux") and sys.platform != "linux":
            item.add_marker(pytest.mark.skip(reason="Linux only"))
    # @pytest.mark.live is excluded via pyproject.toml addopts, NOT here


@pytest.fixture(autouse=True)
def _reset_platform_singletons():
    yield
    reset()
