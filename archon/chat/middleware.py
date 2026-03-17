"""Whitelist middleware — silently drops messages from non-whitelisted users."""
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger("archon")


class WhitelistMiddleware(BaseMiddleware):
    """Pass events through only if from_user.id is in allowed_user_ids.

    Handles both Message and CallbackQuery events.
    """

    def __init__(self, allowed_user_ids: list[int]) -> None:
        self._allowed: frozenset[int] = frozenset(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)):
            logger.warning("Dropped unknown event type %s", type(event).__name__)
            return None
        user_id = event.from_user.id if event.from_user else None
        if user_id not in self._allowed:
            logger.warning("Dropped %s from unauthorized user %s", type(event).__name__, user_id)
            return None
        return await handler(event, data)
