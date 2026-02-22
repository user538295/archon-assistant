"""S5.5 — Live Claude Agent SDK test: real claude binary → ClaudeSession → Response event.

Skipped automatically if `claude` is not found in PATH.
Run with: uv run pytest -m live
"""
import asyncio
import shutil

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Response

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        shutil.which("claude") is None,
        reason="claude binary not found in PATH",
    ),
]

_PROMPT = "Say: OK"
_TIMEOUT = 30.0


async def test_live_sdk_session_receives_response() -> None:
    """ClaudeSession + real SDK emit at least one non-empty Response within 30s."""
    session = ClaudeSession()
    await session.start()
    assert session.is_alive

    events: list = []
    async with asyncio.timeout(_TIMEOUT):
        async for event in session.send(_PROMPT):
            events.append(event)

    await session.stop()
    assert not session.is_alive

    responses = [e for e in events if isinstance(e, Response)]
    assert responses, f"No Response event received (events: {[type(e).__name__ for e in events]})"
    assert responses[0].content, "Response content is empty"


async def test_live_sdk_session_stop_disconnects_cleanly() -> None:
    """ClaudeSession.stop() disconnects; is_alive returns False."""
    session = ClaudeSession()
    await session.start()
    assert session.is_alive

    await session.stop()
    assert not session.is_alive
