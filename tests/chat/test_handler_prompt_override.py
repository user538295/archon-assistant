"""Tests for handle_message prompt_override parameter — file attachment support."""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from archon.ai.event_mapper import Response
from archon.ai.history_manager import HistoryManager
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import handle_message

_split = SplitStrategy()


def _mock_message(text: str | None = "hello") -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    return msg


def _mock_session_manager(*events: object) -> tuple[SessionManager, MagicMock]:
    """Return (manager, session) so tests can inspect session.send calls."""
    session = MagicMock()
    session.is_processing = False

    async def _send(prompt: str) -> AsyncGenerator:
        for event in events:
            yield event

    session.send = MagicMock(side_effect=_send)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    return mgr, session


@pytest.mark.asyncio
async def test_prompt_override_used_instead_of_text() -> None:
    """When prompt_override is provided, session.send() receives the override, not message.text."""
    mgr, session = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(text="hello")

    override = "[Attachment: code.py (50 lines)]\nUser message: review this"
    await handle_message(msg, mgr, _split, prompt_override=override)

    session.send.assert_called_once_with(override)


@pytest.mark.asyncio
async def test_prompt_override_none_uses_message_text() -> None:
    """When prompt_override is None, session.send() receives message.text (regression guard)."""
    mgr, session = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(text="hello")

    await handle_message(msg, mgr, _split, prompt_override=None)

    session.send.assert_called_once_with("hello")


@pytest.mark.asyncio
async def test_prompt_override_with_no_message_text() -> None:
    """When message.text is None but prompt_override is provided, the message is processed."""
    mgr, session = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(text=None)

    override = "[Attachment: data.csv (100 lines)]"
    await handle_message(msg, mgr, _split, prompt_override=override)

    session.send.assert_called_once_with(override)


@pytest.mark.asyncio
async def test_no_prompt_override_no_text_returns_early() -> None:
    """When both message.text and prompt_override are None, handler returns early."""
    mgr, session = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(text=None)

    await handle_message(msg, mgr, _split, prompt_override=None)

    mgr.get_or_create.assert_not_called()
    session.send.assert_not_called()


@pytest.mark.asyncio
async def test_from_user_none_still_returns_early() -> None:
    """When message.from_user is None, handler returns early even with prompt_override."""
    mgr, session = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(text="hello")
    msg.from_user = None

    await handle_message(msg, mgr, _split, prompt_override="something")

    mgr.get_or_create.assert_not_called()
    session.send.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_override_recorded_in_history() -> None:
    """When prompt_override is provided, history records the override text."""
    mgr, _session = _mock_session_manager(Response(content="done"))
    msg = _mock_message("original text")

    history_manager = MagicMock(spec=HistoryManager)
    history_manager.record_user_message = AsyncMock()
    history_manager.record_archon_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    override = "[Attachment: file.py]\nUser message: review"
    await handle_message(
        message=msg,
        session_manager=mgr,
        truncation=_split,
        prompt_override=override,
        history_manager=history_manager,
    )

    # History should record the override text, not message.text
    history_manager.record_user_message.assert_called_once()
    recorded_text = history_manager.record_user_message.call_args[0][1]
    assert recorded_text == override
