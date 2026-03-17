"""Shared test fixtures — canonical mock session factory."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest


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
