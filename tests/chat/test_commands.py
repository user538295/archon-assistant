"""Tests for /status, /stop, and /clear command handlers — S2.4, S2.5."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message

from archon.ai.session_manager import SessionManager
from archon.chat.commands import (
    clear_command,
    concise_command,
    filter_command,
    restart_command,
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


# ──────────────────────────────────────────────────────────────────
# /restart
# ──────────────────────────────────────────────────────────────────


def _mock_manager_with_stop_all(active: bool = True) -> SessionManager:
    mgr = MagicMock(spec=SessionManager)
    mgr.has_session.return_value = active
    mgr.stop = AsyncMock()
    mgr.stop_all = AsyncMock()
    return mgr


async def test_restart_command_sends_confirmation() -> None:
    mgr = _mock_manager_with_stop_all()
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv"):
        await restart_command(msg, mgr)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "restart" in text.lower()


async def test_restart_command_stops_all_sessions() -> None:
    mgr = _mock_manager_with_stop_all()
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv"):
        await restart_command(msg, mgr)

    mgr.stop_all.assert_awaited_once()


async def test_restart_command_stops_sessions_before_exec() -> None:
    """stop_all must be called before os.execv."""
    call_order: list[str] = []
    mgr = _mock_manager_with_stop_all()
    mgr.stop_all = AsyncMock(side_effect=lambda: call_order.append("stop_all"))
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv", side_effect=lambda *a: call_order.append("execv")):
        await restart_command(msg, mgr)

    assert call_order == ["stop_all", "execv"]


async def test_restart_command_calls_execv_with_current_interpreter() -> None:
    mgr = _mock_manager_with_stop_all()
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv") as mock_execv, \
         patch("archon.chat.commands.sys.executable", "/usr/bin/python3"), \
         patch("archon.chat.commands.sys.argv", ["main.py"]):
        await restart_command(msg, mgr)

    mock_execv.assert_called_once_with("/usr/bin/python3", ["/usr/bin/python3", "main.py"])


# ──────────────────────────────────────────────────────────────────
# /clear — S2.5
# ──────────────────────────────────────────────────────────────────


def _mock_manager_for_clear() -> SessionManager:
    mgr = MagicMock(spec=SessionManager)
    mgr.stop = AsyncMock()
    mgr.get_or_create = AsyncMock()
    return mgr


async def test_clear_command_calls_stop_with_user_id() -> None:
    mgr = _mock_manager_for_clear()
    msg = _mock_message(user_id=99)

    await clear_command(msg, mgr)

    mgr.stop.assert_awaited_once_with(99)


async def test_clear_command_calls_get_or_create_after_stop() -> None:
    mgr = _mock_manager_for_clear()
    msg = _mock_message(user_id=99)

    await clear_command(msg, mgr)

    mgr.get_or_create.assert_awaited_once_with(99)


async def test_clear_command_stop_called_before_get_or_create() -> None:
    """stop() must be awaited before get_or_create() to ensure a fresh session."""
    call_order: list[str] = []
    mgr = _mock_manager_for_clear()
    mgr.stop = AsyncMock(side_effect=lambda _: call_order.append("stop"))
    mgr.get_or_create = AsyncMock(side_effect=lambda _: call_order.append("get_or_create"))
    msg = _mock_message(user_id=1)

    await clear_command(msg, mgr)

    assert call_order == ["stop", "get_or_create"]


async def test_clear_command_replies_with_confirmation() -> None:
    mgr = _mock_manager_for_clear()
    msg = _mock_message()

    await clear_command(msg, mgr)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "cleared" in text.lower()
    assert "new session" in text.lower()


async def test_clear_command_reply_contains_broom_emoji() -> None:
    mgr = _mock_manager_for_clear()
    msg = _mock_message()

    await clear_command(msg, mgr)

    text: str = msg.answer.call_args[0][0]
    assert "🧹" in text


async def test_clear_command_works_with_no_prior_session() -> None:
    """stop() is a no-op when no session exists; clear must still succeed."""
    mgr = _mock_manager_for_clear()
    mgr.stop = AsyncMock(return_value=None)  # no-op — manager handles missing session
    msg = _mock_message(user_id=55)

    await clear_command(msg, mgr)

    mgr.stop.assert_awaited_once_with(55)
    mgr.get_or_create.assert_awaited_once_with(55)
    msg.answer.assert_awaited_once()
