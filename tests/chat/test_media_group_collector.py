"""Tests for MediaGroupCollector."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiogram.types import Message


from archon.chat.media_group_collector import MediaGroupCollector


def _mock_group_message(
    group_id: str | None = "group1",
    caption: str | None = None,
) -> Message:
    msg = MagicMock(spec=Message)
    msg.media_group_id = group_id
    msg.photo = []
    msg.document = None
    msg.caption = caption
    return msg


class TestMediaGroupCollector:
    @pytest.mark.asyncio
    async def test_non_group_passes_through(self) -> None:
        """A message without media_group_id returns immediately as [message]."""
        collector = MediaGroupCollector()
        msg = _mock_group_message(group_id=None)
        result = await collector.add(msg)
        assert result == [msg]

    @pytest.mark.asyncio
    async def test_single_message_group(self) -> None:
        """A single message in a group is returned after timeout."""
        collector = MediaGroupCollector(timeout=0.1)
        msg = _mock_group_message("g1")
        result = await collector.add(msg)
        assert result is not None
        assert len(result) == 1
        assert result[0] is msg

    @pytest.mark.asyncio
    async def test_multiple_messages_collected(self) -> None:
        """Multiple messages with the same group_id are collected into one list."""
        collector = MediaGroupCollector(timeout=0.2)
        msg1 = _mock_group_message("g1")
        msg2 = _mock_group_message("g1")
        msg3 = _mock_group_message("g1")

        async def add_later() -> None:
            await asyncio.sleep(0.05)
            r2 = await collector.add(msg2)
            assert r2 is None  # Not the first handler
            await asyncio.sleep(0.05)
            r3 = await collector.add(msg3)
            assert r3 is None

        task = asyncio.create_task(add_later())
        result = await collector.add(msg1)
        await task

        assert result is not None
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_different_groups_independent(self) -> None:
        """Messages with different group_ids are collected independently."""
        collector = MediaGroupCollector(timeout=0.1)
        msg_a = _mock_group_message("a")
        msg_b = _mock_group_message("b")

        r_a, r_b = await asyncio.gather(
            collector.add(msg_a),
            collector.add(msg_b),
        )
        assert r_a is not None
        assert r_b is not None
        assert len(r_a) == 1
        assert len(r_b) == 1

    @pytest.mark.asyncio
    async def test_second_handler_returns_none(self) -> None:
        """The second handler for the same group returns None (exits early)."""
        collector = MediaGroupCollector(timeout=0.1)
        msg1 = _mock_group_message("g1")
        msg2 = _mock_group_message("g1")

        async def second() -> list[Message] | None:
            await asyncio.sleep(0.02)
            return await collector.add(msg2)

        task = asyncio.create_task(second())
        result1 = await collector.add(msg1)
        result2 = await task

        assert result1 is not None  # First handler processes
        assert result2 is None  # Second handler returns early

    @pytest.mark.asyncio
    async def test_timer_resets_on_new_message(self) -> None:
        """Timer resets when a new message arrives, extending the wait."""
        collector = MediaGroupCollector(timeout=0.15)
        msg1 = _mock_group_message("g1")
        msg2 = _mock_group_message("g1")

        async def add_delayed() -> None:
            # Add second message after 0.1s (before the 0.15s timeout)
            await asyncio.sleep(0.1)
            await collector.add(msg2)

        task = asyncio.create_task(add_delayed())
        result = await collector.add(msg1)
        await task

        # Both messages should be collected (timer was reset)
        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_cleanup_after_resolve(self) -> None:
        """Internal state is cleaned up after a group resolves."""
        collector = MediaGroupCollector(timeout=0.05)
        msg = _mock_group_message("g1")

        await collector.add(msg)

        # Internal dicts should be empty after resolution
        assert "g1" not in collector._groups
        assert "g1" not in collector._futures
        assert "g1" not in collector._timers

    @pytest.mark.asyncio
    async def test_sequential_groups_same_id(self) -> None:
        """A new group with the same ID after the first resolves works correctly."""
        collector = MediaGroupCollector(timeout=0.05)

        msg1 = _mock_group_message("g1")
        result1 = await collector.add(msg1)
        assert result1 is not None
        assert len(result1) == 1

        # Second group with same ID
        msg2 = _mock_group_message("g1")
        result2 = await collector.add(msg2)
        assert result2 is not None
        assert len(result2) == 1
        assert result2[0] is msg2

    @pytest.mark.asyncio
    async def test_close_cancels_pending(self) -> None:
        """close() cancels pending timers and futures."""
        collector = MediaGroupCollector(timeout=10.0)  # long timeout
        msg = _mock_group_message("g1")

        # Start a group but don't await the future to completion
        task = asyncio.create_task(collector.add(msg))
        await asyncio.sleep(0.01)  # let it register

        collector.close()

        # The task should raise CancelledError since the future was cancelled
        with pytest.raises(asyncio.CancelledError):
            await task

        # Internal state should be clean
        assert len(collector._groups) == 0
        assert len(collector._timers) == 0
        assert len(collector._futures) == 0
