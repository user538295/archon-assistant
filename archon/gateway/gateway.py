from aiogram import Dispatcher

from archon.chat.middleware import WhitelistMiddleware


def register_middleware(dp: Dispatcher, allowed_user_ids: list[int]) -> None:
    """Register WhitelistMiddleware on the dispatcher's message router."""
    dp.message.middleware(WhitelistMiddleware(allowed_user_ids=allowed_user_ids))


class Gateway:
    """Orchestrator — wires the Telegram bot and session manager together.

    Fully implemented in S3.1.
    """

    @classmethod
    def start(cls) -> None:
        raise NotImplementedError("Implemented in S3.1")
