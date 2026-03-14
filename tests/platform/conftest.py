"""Platform test configuration — markers, auto-skip, singleton cleanup."""
from __future__ import annotations

import sys

import pytest

from archon.platform import reset


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
