"""Shared test fixtures — canonical mock session factory."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from archon.ai.decomposer import TaskOutput
    from archon.ai.event_mapper import Event


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

    def __call__(self, prompt: str) -> AsyncGenerator:
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
