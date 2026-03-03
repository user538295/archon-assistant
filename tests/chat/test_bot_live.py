"""Live bot connectivity tests — require real TELEGRAM_BOT_TOKEN and TELEGRAM_LIVE_CHAT_ID.

Run with: uv run pytest -m live tests/chat/test_bot_live.py -v
"""
import os

import pytest
from dotenv import load_dotenv

from archon.chat.bot import create_bot

pytestmark = [pytest.mark.live, pytest.mark.requires_telegram]

# Load .env so the token is available in local dev without exporting it manually.
load_dotenv()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set in environment")
    return value


async def test_bot_connects_to_telegram() -> None:
    """Bot token is valid and Telegram confirms it is a bot account."""
    from aiogram.utils.token import TokenValidationError

    token = _require_env("TELEGRAM_BOT_TOKEN")
    try:
        bot = create_bot(token)
    except TokenValidationError:
        pytest.skip("TELEGRAM_BOT_TOKEN is set but has invalid format")
    try:
        me = await bot.get_me()
        assert me.is_bot
        assert me.username is not None
    finally:
        await bot.session.close()


async def test_bot_can_send_message_to_live_chat() -> None:
    """Bot successfully delivers a message to TELEGRAM_LIVE_CHAT_ID."""
    from aiogram.utils.token import TokenValidationError

    token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = int(_require_env("TELEGRAM_LIVE_CHAT_ID"))
    try:
        bot = create_bot(token)
    except TokenValidationError:
        pytest.skip("TELEGRAM_BOT_TOKEN is set but has invalid format")
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text="✅ Archon bot connectivity test — OK",
        )
        assert sent.message_id > 0
    finally:
        await bot.session.close()
