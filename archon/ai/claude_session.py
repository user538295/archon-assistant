"""Claude session — wraps ClaudeSDKClient to provide typed event streaming."""
import logging
import os
from typing import AsyncGenerator

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from archon.ai.event_mapper import Event, EventMapper

logger = logging.getLogger("archon")


class ClaudeSession:
    """Manages a single Claude conversation via the Claude Agent SDK."""

    def __init__(self, cwd: str | None = None) -> None:
        self._cwd = cwd
        self._client: ClaudeSDKClient | None = None
        self._mapper = EventMapper()
        self._connected = False

    async def start(self) -> None:
        """Connect the SDK client and start the Claude process."""
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=self._cwd,
        )
        self._client = ClaudeSDKClient(options=options)
        # Strip CLAUDECODE so the subprocess isn't rejected as a nested session
        claudecode = os.environ.pop("CLAUDECODE", None)
        try:
            await self._client.connect()
        finally:
            if claudecode is not None:
                os.environ["CLAUDECODE"] = claudecode
        self._connected = True
        logger.info("Claude session started (cwd=%s)", self._cwd)

    async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Send a prompt and yield typed archon events for the response."""
        if self._client is None or not self._connected:
            raise RuntimeError("Session not started")
        await self._client.query(prompt)
        async for event in self._mapper.map_messages(self._client.receive_response()):
            yield event

    async def stop(self) -> None:
        """Disconnect the SDK client."""
        if self._client is not None and self._connected:
            await self._client.disconnect()
            self._connected = False
            logger.info("Claude session stopped")

    @property
    def is_alive(self) -> bool:
        """True if the session is connected."""
        return self._connected
