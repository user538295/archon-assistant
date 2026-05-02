"""IndexingNotificationMonitor — polls Search HTTP API and sends Telegram notification on completion.

Uses SearchClient (HTTP) for all state queries. No file-based state store dependency.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from archon.config.loader import NotificationsConfig

if TYPE_CHECKING:
    from aiogram import Bot
    from archon.ai.search_client import SearchClient

logger = logging.getLogger("archon")

_TERMINAL_STATUSES = {"done", "failed"}


class IndexingNotificationMonitor:
    """Background task that polls Search HTTP API and notifies Telegram when all collections reach terminal state."""

    def __init__(
        self,
        search_client: "SearchClient",
        bot: "Bot",
        allowed_user_ids: list[int],
        notifications_config: NotificationsConfig,
        poll_interval: float = 30.0,
    ) -> None:
        self._search_client = search_client
        self._bot = bot
        self._allowed_user_ids = allowed_user_ids
        self._notifications_config = notifications_config
        self._poll_interval = poll_interval
        self._notified: bool = False

    async def run(self) -> None:
        """Infinite loop: sleep then check. CancelledError propagates to the caller."""
        while True:
            await asyncio.sleep(self._poll_interval)
            try:
                await self._check_and_notify()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("IndexingNotificationMonitor: unexpected error in poll cycle: %s", exc, exc_info=True)

    async def _check_and_notify(self) -> None:
        """Fetch indexing state via HTTP, detect all-terminal condition, send notification."""
        if self._notified:
            return  # already notified, don't spam

        state = await self._search_client.indexing_state()

        if state is None:
            logger.debug("IndexingNotificationMonitor: no response from search service (connection refused or unavailable)")
            return

        collections = state.get("collections")
        if not collections:
            return

        trigger = state.get("trigger")
        if trigger not in ("install", "update"):
            return

        if not all(c.get("status") in _TERMINAL_STATUSES for c in collections.values()):
            return

        if self._notifications_config.mode == "quiet":
            return

        message = self._build_message(collections)
        self._notified = True
        await self._send_to_all(message)

    def _build_message(self, collections: dict) -> str:  # type: ignore[type-arg]
        """Compose notification text from terminal collection statuses."""
        failed = [name for name, c in collections.items() if c.get("status") == "failed"]
        done = [name for name, c in collections.items() if c.get("status") == "done"]

        if not failed:
            return f"✅ Search indexing complete — all {len(done)} collection(s) ready."
        if not done:
            return "❌ Search indexing failed — no collections are ready. Run <code>archon search status</code> for details."
        return f"⚠️ Search indexing finished — {len(failed)} collection(s) failed. Run <code>archon search status</code> for details."

    async def _send_to_all(self, message: str) -> None:
        """Send message to all allowed_user_ids; log and continue on failure."""
        if not self._allowed_user_ids:
            logger.warning("IndexingNotificationMonitor: no allowed_user_ids configured, skipping notification")
            return
        sent = 0
        for user_id in self._allowed_user_ids:
            try:
                await self._bot.send_message(user_id, message, parse_mode="HTML")
                sent += 1
            except Exception as exc:
                logger.warning(
                    "IndexingNotificationMonitor: failed to send notification to user %d: %s",
                    user_id,
                    exc,
                )
        if sent:
            logger.info("IndexingNotificationMonitor: sent search completion notification to %d user(s)", sent)
