"""Collect Telegram media group messages before processing."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import Message

logger = logging.getLogger("archon")

_GROUP_TIMEOUT = 1.0  # seconds after last message


class MediaGroupCollector:
    """Accumulates messages by media_group_id with timeout.

    When a message with a media_group_id arrives, it is buffered.
    After ``timeout`` seconds of no new messages for that group,
    the complete list is returned to the first handler that called add().
    All subsequent handlers for the same group receive None and should
    return early.
    """

    def __init__(self, timeout: float = _GROUP_TIMEOUT, max_pending: int = 50) -> None:
        self._timeout = timeout
        self._max_pending = max_pending
        self._groups: dict[str, list[Message]] = {}
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._futures: dict[str, asyncio.Future[list[Message]]] = {}

    async def add(self, message: "Message") -> "list[Message] | None":
        """Add a message to its media group.

        Returns the complete list when timeout expires (for the first
        message's handler only). Returns None for subsequent handlers
        (they should return early). Returns [message] for non-group
        messages (pass-through).
        """
        group_id = message.media_group_id
        if group_id is None:
            return [message]

        loop = asyncio.get_running_loop()
        is_first = group_id not in self._groups

        # Bound pending groups to prevent unbounded memory growth
        if is_first and len(self._groups) >= self._max_pending:
            logger.warning(
                "Max pending media groups (%d) reached — returning single message for group %s",
                self._max_pending, group_id,
            )
            return [message]

        if is_first:
            self._groups[group_id] = []
            self._futures[group_id] = loop.create_future()

        self._groups[group_id].append(message)

        # Reset timer on each new message
        if group_id in self._timers:
            self._timers[group_id].cancel()
        self._timers[group_id] = loop.call_later(
            self._timeout, self._resolve, group_id
        )

        # Only the first handler waits and processes
        if is_first:
            try:
                return await self._futures[group_id]
            except asyncio.CancelledError:
                return None
        return None

    def _resolve(self, group_id: str) -> None:
        """Called when timeout expires -- resolve the future with collected messages."""
        messages = self._groups.pop(group_id, [])
        future = self._futures.pop(group_id, None)
        self._timers.pop(group_id, None)
        if future and not future.done():
            future.set_result(messages)

    def close(self) -> None:
        """Cancel all pending timers and futures for graceful shutdown."""
        for timer in self._timers.values():
            timer.cancel()
        for future in self._futures.values():
            if not future.done():
                future.cancel()
        self._groups.clear()
        self._timers.clear()
        self._futures.clear()
