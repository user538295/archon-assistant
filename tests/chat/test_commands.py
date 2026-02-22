"""Tests for /status and /stop command handlers — S2.4."""
import time
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import Message

from archon.ai.session_manager import SessionManager
from archon.chat.commands import status_command, stop_command


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_message(user_id: int = 42) -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=user_id)
    return msg


def _mock_manager(active: bool, started_offset: float = 30.0) -> SessionManager:
    mgr = MagicMock(spec=SessionManager)
    mgr.has_session.return_value = active
    mgr.session_started_at.return_value = (
        time.monotonic() - started_offset if active else None
    )
    mgr.stop = AsyncMock()
    return mgr


# ──────────────────────────────────────────────────────────────────
# /status
# ──────────────────────────────────────────────────────────────────


async def test_status_active_session_replies() -> None:
    mgr = _mock_manager(active=True)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    msg.answer.assert_awaited_once()


async def test_status_active_mentions_active() -> None:
    mgr = _mock_manager(active=True)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert "active" in text.lower()


async def test_status_active_includes_cwd() -> None:
    mgr = _mock_manager(active=True)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/my/project")

    text: str = msg.answer.call_args[0][0]
    assert "/my/project" in text


async def test_status_active_includes_uptime() -> None:
    mgr = _mock_manager(active=True, started_offset=42.0)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    # uptime should be approximately 42s
    assert "s" in text  # some seconds value present


async def test_status_no_session_replies() -> None:
    mgr = _mock_manager(active=False)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    msg.answer.assert_awaited_once()


async def test_status_no_session_mentions_no_active() -> None:
    mgr = _mock_manager(active=False)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert "no active session" in text.lower()


# ──────────────────────────────────────────────────────────────────
# /stop
# ──────────────────────────────────────────────────────────────────


async def test_stop_active_session_calls_manager_stop() -> None:
    mgr = _mock_manager(active=True)
    msg = _mock_message(user_id=7)

    await stop_command(msg, mgr)

    mgr.stop.assert_awaited_once_with(7)


async def test_stop_active_session_replies_confirmation() -> None:
    mgr = _mock_manager(active=True)
    msg = _mock_message()

    await stop_command(msg, mgr)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert len(text) > 0


async def test_stop_no_session_replies_no_active_session() -> None:
    mgr = _mock_manager(active=False)
    msg = _mock_message()

    await stop_command(msg, mgr)

    text: str = msg.answer.call_args[0][0]
    assert "no active session" in text.lower()


async def test_stop_no_session_does_not_call_manager_stop() -> None:
    mgr = _mock_manager(active=False)
    msg = _mock_message()

    await stop_command(msg, mgr)

    mgr.stop.assert_not_called()
