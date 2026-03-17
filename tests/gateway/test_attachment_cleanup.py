"""Tests for attachment cleanup integration in gateway."""

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Dispatcher

from archon.ai.attachment_store import AttachmentStore
from archon.gateway.gateway import _periodic_attachment_cleanup, _setup_dp


class TestStartupCleanup:
    def test_cleanup_actually_deletes_expired_files(self, tmp_path: Path) -> None:
        """AttachmentStore.cleanup deletes files older than max_age_hours."""
        store = AttachmentStore(tmp_path)
        date_dir = tmp_path / "2026-03-10"
        date_dir.mkdir()

        old_file = date_dir / "expired.txt"
        old_file.write_bytes(b"old content")
        old_mtime = time.time() - (48 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        recent_file = date_dir / "fresh.txt"
        recent_file.write_bytes(b"new content")

        deleted = store.cleanup(max_age_hours=24)
        assert deleted == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_cleanup_skipped_when_disabled(self, tmp_path: Path) -> None:
        """Cleanup should return 0 when max_age_hours is 0 (disabled)."""
        store = AttachmentStore(tmp_path)
        result = store.cleanup(0)
        assert result == 0


class TestPeriodicCleanup:
    @pytest.mark.asyncio
    async def test_periodic_cleanup_deletes_files_and_is_cancellable(
        self, tmp_path: Path,
    ) -> None:
        """_periodic_attachment_cleanup calls store.cleanup and is cancellable."""
        store = AttachmentStore(tmp_path)

        # Pre-create an old file so cleanup has something to delete
        date_dir = tmp_path / "2026-01-01"
        date_dir.mkdir()
        old_file = date_dir / "stale.txt"
        old_file.write_bytes(b"stale")
        old_mtime = time.time() - (100 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        # Patch sleep to return immediately once (triggering cleanup), then cancel
        call_count = 0

        async def _fake_sleep(_seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError

        with patch("archon.gateway.gateway.asyncio.sleep", side_effect=_fake_sleep):
            with pytest.raises(asyncio.CancelledError):
                await _periodic_attachment_cleanup(store, max_age_hours=24)

        # The file should have been deleted during the first loop iteration
        assert not old_file.exists()

    @pytest.mark.asyncio
    async def test_periodic_cleanup_uses_to_thread(self, tmp_path: Path) -> None:
        """Periodic cleanup must use asyncio.to_thread to avoid blocking the event loop."""
        store = AttachmentStore(tmp_path)
        store.cleanup = MagicMock(return_value=2)  # type: ignore[method-assign]

        with patch(
            "archon.gateway.gateway.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=2,
        ) as mock_to_thread:
            call_count = 0

            async def limited_sleep(_seconds: float) -> None:
                nonlocal call_count
                call_count += 1
                if call_count > 1:
                    raise asyncio.CancelledError

            with patch("archon.gateway.gateway.asyncio.sleep", side_effect=limited_sleep):
                with pytest.raises(asyncio.CancelledError):
                    await _periodic_attachment_cleanup(store, 24.0)

            mock_to_thread.assert_called_once_with(store.cleanup, 24.0)


class TestDispatcherContext:
    def test_store_accessible_via_setup_dp_workflow(self, tmp_path: Path) -> None:
        """_setup_dp places the AttachmentStore in dp so handlers can retrieve it."""
        dp = Dispatcher()
        store = AttachmentStore(tmp_path)

        # Provide minimal mocks for required _setup_dp parameters
        cfg = MagicMock()
        cfg.access.allowed_user_ids = []
        cfg.output.truncation_strategy = "split"
        cfg.notifications.mode = "normal"
        cfg.notifications.agents.mode = "normal"
        cfg.notifications.interval_minutes = 0
        cfg.background_agents.spawn_rule = "off"
        cfg.background_agents.max_parallel = 1
        cfg.background_agents.tool_promotion_threshold = 0
        cfg.voice.enabled = False
        cfg.history.enabled = False

        _setup_dp(
            dp,
            cfg,
            session_manager=MagicMock(),
            skill_loader=MagicMock(),
            plugin_loader=MagicMock(),
            agent_loader=MagicMock(),
            config_file="config.toml",
            attachment_store=store,
        )

        assert dp["attachment_store"] is store
