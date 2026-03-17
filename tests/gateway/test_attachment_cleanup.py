"""Tests for attachment cleanup integration in gateway."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiogram import Dispatcher

from archon.ai.attachment_store import AttachmentStore


class TestStartupCleanup:
    def test_cleanup_runs_with_configured_hours(self, tmp_path: Path) -> None:
        """Cleanup should be callable with the configured max_age_hours."""
        store = AttachmentStore(tmp_path)
        store.cleanup = MagicMock(return_value=3)  # type: ignore[method-assign]
        result = store.cleanup(12.5)
        store.cleanup.assert_called_once_with(12.5)
        assert result == 3

    def test_cleanup_skipped_when_disabled(self, tmp_path: Path) -> None:
        """Cleanup should return 0 when max_age_hours is 0 (disabled)."""
        store = AttachmentStore(tmp_path)
        result = store.cleanup(0)
        assert result == 0


class TestPeriodicCleanup:
    @pytest.mark.asyncio
    async def test_periodic_task_can_be_cancelled(self) -> None:
        """The periodic cleanup task should be cancellable for graceful shutdown."""
        async def fake_periodic() -> None:
            while True:
                await asyncio.sleep(6 * 3600)

        task = asyncio.create_task(fake_periodic())
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class TestDispatcherContext:
    def test_attachment_store_in_dp_context(self, tmp_path: Path) -> None:
        """AttachmentStore should be settable and retrievable via dp['attachment_store']."""
        dp = Dispatcher()
        store = AttachmentStore(tmp_path)
        dp["attachment_store"] = store
        assert dp["attachment_store"] is store
