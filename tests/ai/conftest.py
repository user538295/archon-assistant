"""Shared AI test fixtures."""
import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from archon.ai.background_agent_manager import BackgroundAgentManager
from archon.ai.archon_toolkit import ArchonToolkit


def _make_slow_claude_session(delay: float = 10.0) -> MagicMock:
    """Return a mock ClaudeSession that sleeps for `delay` seconds."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        await asyncio.sleep(delay)
        from archon.ai.event_mapper import Response
        yield Response(content="slow result")

    session.send = _send
    return session


@pytest.fixture
def toolkit_with_real_bam():
    """Factory fixture: real BackgroundAgentManager + ArchonToolkit.

    Usage::

        async def test_something(toolkit_with_real_bam):
            toolkit, bam, sm, bot = toolkit_with_real_bam()

    Returns a factory so each test builds a fresh instance.
    """

    def _factory(config=None):
        bot = MagicMock()
        bot.send_message = AsyncMock()

        sm = MagicMock()
        sm.get_or_create = AsyncMock()
        sm.track_context = MagicMock()
        sm.inject_agent_context = MagicMock()

        bam = BackgroundAgentManager(bot=bot, session_manager=sm)
        toolkit = ArchonToolkit(bg_manager=bam, config=config)
        return toolkit, bam, sm, bot

    return _factory
