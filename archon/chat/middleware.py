"""Whitelist middleware — silently drops messages from non-whitelisted users."""
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

logger = logging.getLogger("archon")


class WhitelistMiddleware(BaseMiddleware):
    """Pass messages through only if from_user.id is in allowed_user_ids."""

    def __init__(self, allowed_user_ids: list[int]) -> None:
        self._allowed: frozenset[int] = frozenset(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            user_id = event.from_user.id if event.from_user else None
            if user_id not in self._allowed:
                logger.warning("Dropped message from unauthorized user %s", user_id)
                return None
        return await handler(event, data)
