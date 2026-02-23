"""Tests for /status and /stop command handlers — S2.4."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message

from archon.ai.session_manager import SessionManager
from archon.chat.commands import (
    concise_command,
    filter_command,
    settings_command,
    status_command,
    stop_command,
)
from archon.config.loader import NotificationsConfig


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


# ──────────────────────────────────────────────────────────────────
# /concise — toggle concise mode
# ──────────────────────────────────────────────────────────────────


def _mock_msg_with_text(text: str, user_id: int = 42) -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=user_id)
    msg.text = text
    return msg


async def test_concise_command_off_cycles_to_full() -> None:
    notif = NotificationsConfig(concise_mode="off")
    msg = _mock_msg_with_text("/concise")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "full"


async def test_concise_command_full_cycles_to_partial() -> None:
    notif = NotificationsConfig(concise_mode="full")
    msg = _mock_msg_with_text("/concise")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "partial"


async def test_concise_command_partial_cycles_to_off() -> None:
    notif = NotificationsConfig(concise_mode="partial")
    msg = _mock_msg_with_text("/concise")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "off"


async def test_concise_command_explicit_off() -> None:
    notif = NotificationsConfig(concise_mode="full")
    msg = _mock_msg_with_text("/concise off")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "off"


async def test_concise_command_explicit_full() -> None:
    notif = NotificationsConfig(concise_mode="off")
    msg = _mock_msg_with_text("/concise full")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "full"


async def test_concise_command_explicit_partial() -> None:
    notif = NotificationsConfig(concise_mode="off")
    msg = _mock_msg_with_text("/concise partial")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "partial"


async def test_concise_command_partial_with_interval() -> None:
    notif = NotificationsConfig(concise_mode="off", concise_interval_minutes=2)
    msg = _mock_msg_with_text("/concise partial 5")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "partial"
    assert notif.concise_interval_minutes == 5


async def test_concise_command_partial_invalid_interval_ignored() -> None:
    notif = NotificationsConfig(concise_mode="off", concise_interval_minutes=2)
    msg = _mock_msg_with_text("/concise partial abc")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    assert notif.concise_mode == "partial"
    assert notif.concise_interval_minutes == 2  # unchanged


async def test_concise_command_saves_config() -> None:
    notif = NotificationsConfig(concise_mode="off")
    msg = _mock_msg_with_text("/concise")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await concise_command(msg, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_concise_command_reply_includes_mode() -> None:
    notif = NotificationsConfig(concise_mode="off")
    msg = _mock_msg_with_text("/concise full")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    text: str = msg.answer.call_args[0][0]
    assert "full" in text.lower()


async def test_concise_command_partial_reply_includes_interval() -> None:
    notif = NotificationsConfig(concise_mode="off", concise_interval_minutes=3)
    msg = _mock_msg_with_text("/concise partial 3")

    with patch("archon.chat.commands.save_notifications_config"):
        await concise_command(msg, notif, "config.toml")

    text: str = msg.answer.call_args[0][0]
    assert "3" in text


# ──────────────────────────────────────────────────────────────────
# /filter — toggle thinking results and tool details
# ──────────────────────────────────────────────────────────────────


async def test_filter_thinking_toggles_off() -> None:
    notif = NotificationsConfig(show_thinking_result=True)
    msg = _mock_msg_with_text("/filter thinking")

    with patch("archon.chat.commands.save_notifications_config"):
        await filter_command(msg, notif, "config.toml")

    assert notif.show_thinking_result is False


async def test_filter_thinking_toggles_on() -> None:
    notif = NotificationsConfig(show_thinking_result=False)
    msg = _mock_msg_with_text("/filter thinking")

    with patch("archon.chat.commands.save_notifications_config"):
        await filter_command(msg, notif, "config.toml")

    assert notif.show_thinking_result is True


async def test_filter_tools_toggles_brief_on() -> None:
    notif = NotificationsConfig(brief_tool_output=False)
    msg = _mock_msg_with_text("/filter tools")

    with patch("archon.chat.commands.save_notifications_config"):
        await filter_command(msg, notif, "config.toml")

    assert notif.brief_tool_output is True


async def test_filter_tools_toggles_brief_off() -> None:
    notif = NotificationsConfig(brief_tool_output=True)
    msg = _mock_msg_with_text("/filter tools")

    with patch("archon.chat.commands.save_notifications_config"):
        await filter_command(msg, notif, "config.toml")

    assert notif.brief_tool_output is False


async def test_filter_saves_config() -> None:
    notif = NotificationsConfig()
    msg = _mock_msg_with_text("/filter thinking")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await filter_command(msg, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_filter_no_arg_shows_status() -> None:
    notif = NotificationsConfig()
    msg = _mock_msg_with_text("/filter")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await filter_command(msg, notif, "config.toml")

    msg.answer.assert_awaited_once()
    mock_save.assert_not_called()


async def test_filter_reply_includes_setting_name() -> None:
    notif = NotificationsConfig(show_thinking_result=True)
    msg = _mock_msg_with_text("/filter thinking")

    with patch("archon.chat.commands.save_notifications_config"):
        await filter_command(msg, notif, "config.toml")

    text: str = msg.answer.call_args[0][0]
    assert "thinking" in text.lower()


# ──────────────────────────────────────────────────────────────────
# /settings — show current notification settings
# ──────────────────────────────────────────────────────────────────


async def test_settings_command_shows_concise_mode() -> None:
    notif = NotificationsConfig(concise_mode=True)
    msg = _mock_msg_with_text("/settings")

    await settings_command(msg, notif)

    text: str = msg.answer.call_args[0][0]
    assert "concise" in text.lower()


async def test_settings_command_shows_thinking_result() -> None:
    notif = NotificationsConfig(show_thinking_result=False)
    msg = _mock_msg_with_text("/settings")

    await settings_command(msg, notif)

    text: str = msg.answer.call_args[0][0]
    assert "thinking" in text.lower()


async def test_settings_command_shows_tool_output() -> None:
    notif = NotificationsConfig(brief_tool_output=True)
    msg = _mock_msg_with_text("/settings")

    await settings_command(msg, notif)

    text: str = msg.answer.call_args[0][0]
    assert "tool" in text.lower()
