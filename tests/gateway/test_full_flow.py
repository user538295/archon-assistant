"""S5.3 — Full message flow e2e test.

Gateway wired via _setup_dp with:
- Scripted ClaudeSession fake (replaces SDK boundary)
- Message.answer patched to record Telegram replies (replaces Telegram API)

Verifies the correct ordered sequence of formatted Telegram messages,
long-content splitting, and log output.
"""
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import Bot
from aiogram.types import Chat, Message, Update, User

from archon.ai.event_mapper import Response, ThinkingResult, ToolResult, ToolStarted
from archon.ai.session_manager import SessionManager
from archon.chat.bot import create_dispatcher
from archon.config.loader import AccessConfig, Config, LoggingConfig, NotificationsConfig, OutputConfig, SessionConfig
from archon.gateway.gateway import _setup_dp

_FAKE_TOKEN = "12345:AAFakeTokenForTestingPurposesOnly123"
_USER_ID = 100

# The canonical 4-event sequence per the S5.3 spec.
_FULL_SEQUENCE = [
    ThinkingResult(content="I need to check the files."),
    ToolStarted(name="bash"),
    ToolResult(content="total 10\nfile.txt"),
    Response(content="Done! Found 10 files."),
]


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_config(max_message_length: int = 4000) -> Config:
    return Config(
        telegram_bot_token=_FAKE_TOKEN,
        access=AccessConfig(allowed_user_ids=[_USER_ID]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(max_message_length=max_message_length),
        logging=LoggingConfig(),
        notifications=NotificationsConfig(mode="debug"),  # show all 5 event types
    )


def _make_update(text: str = "hi") -> Update:
    user = User(id=_USER_ID, is_bot=False, first_name="Test")
    chat = Chat(id=_USER_ID, type="private")
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
    )
    return Update(update_id=1, message=msg)


def _scripted_mgr(events: list[object]) -> MagicMock:
    session = MagicMock()
    session.is_processing = False  # idle session — no queued notification

    async def _send(prompt: str) -> AsyncGenerator[object, None]:
        for ev in events:
            yield ev

    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    return mgr


async def _run(
    events: list[object],
    text: str = "hi",
    max_message_length: int = 4000,
) -> list[str]:
    """Wire gateway, inject one message, return captured reply texts in order."""
    cfg = _make_config(max_message_length=max_message_length)
    dp = create_dispatcher()
    _setup_dp(dp, cfg, _scripted_mgr(events))
    bot = Bot(token=_FAKE_TOKEN)

    with patch("aiogram.types.Message.answer", new_callable=AsyncMock) as mock_answer, \
         patch("aiogram.Bot.send_chat_action", new_callable=AsyncMock):
        mock_answer.return_value = MagicMock(message_id=1)
        await dp.feed_update(bot, _make_update(text))

    # When patched at class level (non-descriptor), called as mock_answer(text, ...)
    # Return all messages including the ack (⏳ Processing... / ⏳ Working...) —
    # the ack is part of the observable flow that the tests verify.
    return [str(call.args[0]) for call in mock_answer.call_args_list]


# ──────────────────────────────────────────────────────────────────
# Full ordered sequence — S5.3 core
# ──────────────────────────────────────────────────────────────────


async def test_full_sequence_produces_four_messages() -> None:
    # handler sends 1 ack ("⏳ Processing...") + 4 event messages = 5 total
    texts = await _run(_FULL_SEQUENCE)
    assert len(texts) == 5


async def test_full_sequence_correct_order() -> None:
    texts = await _run(_FULL_SEQUENCE)
    assert texts[0].startswith("⏳")           # ack
    assert texts[1].startswith("💭 Thinking:")
    assert texts[2] == "🔧 Tool: bash"
    assert texts[3].startswith("📤 Result:")
    assert texts[4].startswith("✅ Response:")


async def test_thinking_result_contains_content() -> None:
    texts = await _run(_FULL_SEQUENCE)
    assert "I need to check the files." in texts[1]


async def test_tool_result_contains_content() -> None:
    texts = await _run(_FULL_SEQUENCE)
    assert "file.txt" in texts[3]


async def test_response_contains_content() -> None:
    texts = await _run(_FULL_SEQUENCE)
    assert "Found 10 files" in texts[4]


# ──────────────────────────────────────────────────────────────────
# Long content split by SplitStrategy
# ──────────────────────────────────────────────────────────────────


async def test_long_response_is_split_into_multiple_messages() -> None:
    long_content = "x" * 9000
    texts = await _run([Response(content=long_content)], max_message_length=4000)
    # Must produce more than one message
    assert len(texts) > 1


async def test_split_chunks_respect_max_len() -> None:
    long_content = "x" * 9000
    texts = await _run([Response(content=long_content)], max_message_length=4000)
    for t in texts:
        # max_message_length applies to content chunks; format_event adds a small
        # fixed prefix ("✅ Response:\n", "[N/M] ") — total stays within Telegram's 4096 limit.
        assert len(t) <= 4096


async def test_split_chunks_are_labeled() -> None:
    long_content = "x" * 9000
    texts = await _run([Response(content=long_content)], max_message_length=4000)
    # Each chunk (skip the leading ack) should contain a [N/M] label
    for t in texts[1:]:
        assert "[" in t and "/" in t


# ──────────────────────────────────────────────────────────────────
# Log entries
# ──────────────────────────────────────────────────────────────────


async def test_message_processing_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.INFO, logger="archon"):
        await _run([Response(content="ok")], text="hello world")

    messages = [r.message for r in caplog.records]
    assert any(str(_USER_ID) in m for m in messages)
