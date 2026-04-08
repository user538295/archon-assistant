"""Shared test fixtures — canonical mock session factory."""

import os
import re
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from archon.ai.decomposer import TaskOutput
    from archon.ai.event_mapper import Event

_EXAMPLE_CONFIG = Path(__file__).parent.parent / "examples" / "config.toml.example"


@pytest.fixture(autouse=True, scope="session")
def _load_example_config():
    """Load config.toml.example as the global config singleton for all tests.

    Prevents tests from depending on ~/.archon/config.toml (installed product).
    All file paths are resolved within the project tree.
    """
    import archon.config as _config_module
    from archon.config.loader import load_config

    _project_root = _EXAMPLE_CONFIG.parent.parent
    _workspace = _project_root / "tests" / ".workspace"
    _workspace.mkdir(exist_ok=True)

    # Substitute required fields so load_config() succeeds
    text = _EXAMPLE_CONFIG.read_text()
    text = re.sub(r"^allowed_user_ids\s*=.*$", "allowed_user_ids = [99999]", text, flags=re.MULTILINE)
    text = re.sub(
        r"^working_directory\s*=.*$",
        f'working_directory = "{_workspace}"',
        text,
        flags=re.MULTILINE,
    )

    cfg_file = _workspace / "test_config.toml"
    cfg_file.write_text(text)
    env_file = _workspace / "test.env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=test_token_abc\n")

    _config_module._config = load_config(env_file=env_file, config_file=cfg_file)
    yield
    _config_module._config = None


def _mock_session_factory(
    *events: object,
    is_processing: bool = False,
    is_alive: bool = True,
    model: str = "claude-sonnet-4-6",
) -> MagicMock:
    """Build a mock ClaudeSession that yields given events from send().

    This is the canonical helper — covers the union of attributes used across
    test_classifier, test_decomposer, test_handler, test_voice, and
    test_background_agent_manager.
    """
    from archon.ai.claude_session import ClaudeSession

    session = MagicMock(spec=ClaudeSession)
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = is_processing
    session.is_alive = is_alive
    session.model = model

    # Diagnostics / state attributes (used by test_decomposer)
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.diagnostics = {"is_alive": is_alive}

    # Track send() calls for assertion (used by test_decomposer)
    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator[object, None]:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send

    # Skill / context injection (used by test_decomposer)
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.flush_pending_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])

    return session


@pytest.fixture
def mock_session_factory():
    """Pytest fixture exposing the canonical mock session factory."""
    return _mock_session_factory


async def collect_route_task(
    decomposer: object, prompt: str
) -> "tuple[list[Event], TaskOutput]":
    """Collect events and TaskOutput sentinel from route_task() generator.

    Usage::

        events, sentinel = await collect_route_task(decomposer, "do something")
        assert sentinel.scope == "small"
    """
    from archon.ai.decomposer import TaskOutput

    events: list[object] = []
    sentinel: "TaskOutput | None" = None
    async for item in decomposer.route_task(prompt):  # type: ignore[union-attr]
        if isinstance(item, TaskOutput):
            sentinel = item
        else:
            events.append(item)
    assert sentinel is not None, "route_task() must yield exactly one TaskOutput sentinel"
    return events, sentinel  # type: ignore[return-value]


async def mock_route_task(*events: object, task_output: object) -> AsyncGenerator:
    """Async generator helper that yields router events then a TaskOutput sentinel.

    Usage::

        decomposer.route_task = lambda prompt: mock_route_task(
            ToolStarted(name="Read", input="/some/file"),
            task_output=TaskOutput(scope="small", prompt="do it"),
        )
    """
    for event in events:
        yield event
    yield task_output


class _RouteTaskGenMock:
    """Callable mock that returns an async generator for route_task().

    Replaces ``AsyncMock(return_value=TaskOutput(...))`` with a generator-based mock
    that also tracks calls (await_count, assert_awaited_once, assert_awaited_once_with).

    Usage::

        decomposer.route_task = _RouteTaskGenMock(
            TaskOutput(scope="small", prompt="do it")
        )
    """

    def __init__(self, task_output: object, events: list = None) -> None:
        self._task_output = task_output
        self._events = events or []
        self.await_count = 0
        self.call_args_list: list = []

    def __call__(self, prompt: str, search_pre_context: str | None = None) -> AsyncGenerator:
        self.await_count += 1
        self.call_args_list.append(prompt)

        task_output = self._task_output
        events = self._events

        async def _gen():
            for event in events:
                yield event
            yield task_output

        return _gen()

    def assert_awaited_once(self) -> None:
        assert self.await_count == 1, f"Expected 1 call, got {self.await_count}"

    def assert_awaited_once_with(self, prompt: str) -> None:
        assert self.await_count == 1, f"Expected 1 call, got {self.await_count}"
        assert self.call_args_list[-1] == prompt, (
            f"Expected call with {prompt!r}, got {self.call_args_list[-1]!r}"
        )

    def assert_not_awaited(self) -> None:
        assert self.await_count == 0, f"Expected 0 calls, got {self.await_count}"
