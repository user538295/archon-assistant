"""Tests for command handlers — /status, /stop, /clear, /restart, /notify, /settings, /skills, /skill, /model, /context, /agents."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import Skill, SkillLoader
from archon.chat.commands import (
    _fmt_context,
    _progress_bar,
    clear_command,
    context_command,
    debug_command,
    model_callback,
    model_command,
    normal_command,
    notify_callback,
    notify_command,
    quiet_command,
    restart_command,
    settings_command,
    skill_command,
    skills_command,
    status_command,
    stop_command,
    verbose_command,
)
from archon.ai.agent_loader import Agent, AgentLoader
from archon.chat.commands import agents_command
from archon.config.loader import ModelsConfig, NotificationsConfig


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _mock_message(user_id: int = 42) -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=user_id)
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
# /notify — no arg shows inline keyboard (S8.3)
# ──────────────────────────────────────────────────────────────────


def _mock_msg_with_text(text: str, user_id: int = 42) -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.from_user = MagicMock(id=user_id)
    msg.text = text
    return msg


async def test_notify_no_arg_sends_inline_keyboard() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    msg.answer.assert_awaited_once()
    mock_save.assert_not_called()
    # reply_markup kwarg must be an InlineKeyboardMarkup
    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


async def test_notify_no_arg_keyboard_has_four_buttons() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    kb: InlineKeyboardMarkup = msg.answer.call_args[1]["reply_markup"]
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 4


async def test_notify_no_arg_current_mode_marked() -> None:
    notif = NotificationsConfig(mode="verbose")
    msg = _mock_msg_with_text("/notify")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    kb: InlineKeyboardMarkup = msg.answer.call_args[1]["reply_markup"]
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    marked = [btn for btn in buttons if "✓" in btn.text]
    assert len(marked) == 1
    assert "verbose" in marked[0].text.lower()


# ──────────────────────────────────────────────────────────────────
# /notify — mode subcommands (S8.3)
# ──────────────────────────────────────────────────────────────────


async def test_notify_quiet_sets_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify quiet")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"


async def test_notify_quiet_with_interval_sets_both() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=2)
    msg = _mock_msg_with_text("/notify quiet 5")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"
    assert notif.interval_minutes == 5


async def test_notify_quiet_zero_interval_sets_no_beacon() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=2)
    msg = _mock_msg_with_text("/notify quiet 0")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"
    assert notif.interval_minutes == 0


async def test_notify_normal_sets_mode() -> None:
    notif = NotificationsConfig(mode="quiet")
    msg = _mock_msg_with_text("/notify normal")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "normal"


async def test_notify_verbose_sets_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify verbose")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "verbose"


async def test_notify_debug_sets_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify debug")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "debug"


async def test_notify_mode_saves_config() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify verbose")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_notify_mode_reply_mentions_mode_name() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify verbose")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    text: str = msg.answer.call_args[0][0]
    assert "verbose" in text.lower()


async def test_notify_quiet_reply_mentions_mode() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=0)
    msg = _mock_msg_with_text("/notify quiet")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    text: str = msg.answer.call_args[0][0]
    assert "quiet" in text.lower()


async def test_notify_quiet_beacon_reply_mentions_interval() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=2)
    msg = _mock_msg_with_text("/notify quiet 3")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    text: str = msg.answer.call_args[0][0]
    assert "3" in text


async def test_notify_quiet_invalid_interval_ignored() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=2)
    msg = _mock_msg_with_text("/notify quiet abc")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"
    assert notif.interval_minutes == 2  # unchanged


# ──────────────────────────────────────────────────────────────────
# /notify interval subcommand (S8.3)
# ──────────────────────────────────────────────────────────────────


async def test_notify_interval_changes_only_interval() -> None:
    notif = NotificationsConfig(mode="quiet", interval_minutes=2)
    msg = _mock_msg_with_text("/notify interval 10")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_command(msg, notif, "config.toml")

    assert notif.interval_minutes == 10
    assert notif.mode == "quiet"  # unchanged


async def test_notify_interval_saves_config() -> None:
    notif = NotificationsConfig(mode="quiet", interval_minutes=2)
    msg = _mock_msg_with_text("/notify interval 10")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_notify_interval_invalid_shows_keyboard() -> None:
    """'/notify interval' with no number falls back to showing keyboard."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=2)
    msg = _mock_msg_with_text("/notify interval")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    mock_save.assert_not_called()
    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


# ──────────────────────────────────────────────────────────────────
# /notify — invalid subcommand falls back to keyboard (S8.3)
# ──────────────────────────────────────────────────────────────────


async def test_notify_invalid_arg_shows_keyboard() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/notify unknown")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    mock_save.assert_not_called()
    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


# ──────────────────────────────────────────────────────────────────
# notify_callback — inline keyboard taps (S8.3)
# ──────────────────────────────────────────────────────────────────


def _mock_callback(data: str, user_id: int = 42) -> CallbackQuery:
    cb = MagicMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = MagicMock(id=user_id)
    cb.message = MagicMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()
    return cb


async def test_notify_callback_updates_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:verbose")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_callback(cb, notif, "config.toml")

    assert notif.mode == "verbose"


async def test_notify_callback_saves_config() -> None:
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:quiet")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_callback(cb, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_notify_callback_edits_keyboard_in_place() -> None:
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:debug")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_callback(cb, notif, "config.toml")

    cb.message.edit_reply_markup.assert_awaited_once()
    kwargs = cb.message.edit_reply_markup.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


async def test_notify_callback_answers_callback() -> None:
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:quiet")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_callback(cb, notif, "config.toml")

    cb.answer.assert_awaited_once()


async def test_notify_callback_all_modes() -> None:
    for mode in ("quiet", "normal", "verbose", "debug"):
        notif = NotificationsConfig(mode="normal")
        cb = _mock_callback(f"notify:{mode}")

        with patch("archon.chat.commands.save_notifications_config"):
            await notify_callback(cb, notif, "config.toml")

        assert notif.mode == mode


async def test_notify_callback_updated_keyboard_marks_new_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:debug")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_callback(cb, notif, "config.toml")

    kb: InlineKeyboardMarkup = cb.message.edit_reply_markup.call_args[1]["reply_markup"]
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    marked = [btn for btn in buttons if "✓" in btn.text]
    assert len(marked) == 1
    assert "debug" in marked[0].text.lower()


# ──────────────────────────────────────────────────────────────────
# /settings — shows inline keyboard (S8.3)
# ──────────────────────────────────────────────────────────────────


async def test_settings_command_sends_inline_keyboard() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/settings")

    await settings_command(msg, notif)

    msg.answer.assert_awaited_once()
    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


async def test_settings_command_marks_current_mode() -> None:
    notif = NotificationsConfig(mode="verbose")
    msg = _mock_msg_with_text("/settings")

    await settings_command(msg, notif)

    kb: InlineKeyboardMarkup = msg.answer.call_args[1]["reply_markup"]
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    marked = [btn for btn in buttons if "✓" in btn.text]
    assert len(marked) == 1
    assert "verbose" in marked[0].text.lower()


# ──────────────────────────────────────────────────────────────────
# Quick-switch commands: /quiet /normal /verbose /debug (S8.4)
# ──────────────────────────────────────────────────────────────────


async def test_quiet_command_sets_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/quiet")

    with patch("archon.chat.commands.save_notifications_config"):
        await quiet_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"


async def test_quiet_command_with_interval() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=2)
    msg = _mock_msg_with_text("/quiet 5")

    with patch("archon.chat.commands.save_notifications_config"):
        await quiet_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"
    assert notif.interval_minutes == 5


async def test_quiet_command_zero_interval() -> None:
    notif = NotificationsConfig(mode="normal", interval_minutes=2)
    msg = _mock_msg_with_text("/quiet 0")

    with patch("archon.chat.commands.save_notifications_config"):
        await quiet_command(msg, notif, "config.toml")

    assert notif.mode == "quiet"
    assert notif.interval_minutes == 0


async def test_quiet_command_saves_config() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/quiet")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await quiet_command(msg, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_quiet_command_replies_with_keyboard() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/quiet")

    with patch("archon.chat.commands.save_notifications_config"):
        await quiet_command(msg, notif, "config.toml")

    msg.answer.assert_awaited_once()
    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


async def test_normal_command_sets_mode() -> None:
    notif = NotificationsConfig(mode="quiet")
    msg = _mock_msg_with_text("/normal")

    with patch("archon.chat.commands.save_notifications_config"):
        await normal_command(msg, notif, "config.toml")

    assert notif.mode == "normal"


async def test_normal_command_replies_with_keyboard() -> None:
    notif = NotificationsConfig(mode="quiet")
    msg = _mock_msg_with_text("/normal")

    with patch("archon.chat.commands.save_notifications_config"):
        await normal_command(msg, notif, "config.toml")

    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


async def test_verbose_command_sets_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/verbose")

    with patch("archon.chat.commands.save_notifications_config"):
        await verbose_command(msg, notif, "config.toml")

    assert notif.mode == "verbose"


async def test_debug_command_sets_mode() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/debug")

    with patch("archon.chat.commands.save_notifications_config"):
        await debug_command(msg, notif, "config.toml")

    assert notif.mode == "debug"


async def test_verbose_command_saves_config() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/verbose")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await verbose_command(msg, notif, "config.toml")

    mock_save.assert_called_once_with(notif, "config.toml")


async def test_debug_command_replies_with_keyboard() -> None:
    notif = NotificationsConfig(mode="normal")
    msg = _mock_msg_with_text("/debug")

    with patch("archon.chat.commands.save_notifications_config"):
        await debug_command(msg, notif, "config.toml")

    kwargs = msg.answer.call_args[1]
    assert isinstance(kwargs.get("reply_markup"), InlineKeyboardMarkup)


async def test_quick_commands_reply_keyboard_marks_correct_mode() -> None:
    """Each quick command reply keyboard should checkmark its own mode."""
    for cmd_fn, mode_name in [
        (normal_command, "normal"),
        (verbose_command, "verbose"),
        (debug_command, "debug"),
    ]:
        notif = NotificationsConfig(mode="quiet")
        msg = _mock_msg_with_text(f"/{mode_name}")

        with patch("archon.chat.commands.save_notifications_config"):
            await cmd_fn(msg, notif, "config.toml")

        kb: InlineKeyboardMarkup = msg.answer.call_args[1]["reply_markup"]
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        marked = [btn for btn in buttons if "✓" in btn.text]
        assert len(marked) == 1, f"Expected 1 marked button for {mode_name}"
        assert mode_name in marked[0].text.lower()


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


# ──────────────────────────────────────────────────────────────────
# /skills — S6.1
# ──────────────────────────────────────────────────────────────────


def _mock_skill_loader(skills: list[Skill]) -> SkillLoader:
    loader = MagicMock(spec=SkillLoader)
    loader.load_all.return_value = skills
    loader.get.side_effect = lambda name: next((s for s in skills if s.name == name), None)
    return loader


async def test_skills_command_lists_skill_names_and_descriptions() -> None:
    skills = [
        Skill("skill-one", "Does one thing", "body one"),
        Skill("skill-two", "Does two things", "body two"),
    ]
    loader = _mock_skill_loader(skills)
    msg = _mock_message()

    await skills_command(msg, loader)

    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args.args[0]
    assert "skill-one" in reply
    assert "Does one thing" in reply
    assert "skill-two" in reply
    assert "Does two things" in reply


async def test_skills_command_empty_list_replies() -> None:
    loader = _mock_skill_loader([])
    msg = _mock_message()

    await skills_command(msg, loader)

    msg.answer.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# /skill <name> — S6.1
# ──────────────────────────────────────────────────────────────────


async def test_skill_command_valid_name_activates_skill() -> None:
    from archon.ai.claude_session import ClaudeSession

    skill = Skill("my-skill", "desc", "content")
    loader = _mock_skill_loader([skill])

    mock_session = MagicMock(spec=ClaudeSession)
    mock_session.activate_skill = MagicMock()

    mgr = _mock_manager(active=True)
    mgr.get_or_create = AsyncMock(return_value=mock_session)

    msg = _mock_message()
    msg.text = "/skill my-skill"

    await skill_command(msg, mgr, loader)

    mock_session.activate_skill.assert_called_once_with(skill)
    msg.answer.assert_awaited_once()
    assert "my-skill" in msg.answer.call_args.args[0]


async def test_skill_command_valid_name_reply_confirms_activation() -> None:
    from archon.ai.claude_session import ClaudeSession

    skill = Skill("confirm-skill", "desc", "body")
    loader = _mock_skill_loader([skill])

    mock_session = MagicMock(spec=ClaudeSession)
    mock_session.activate_skill = MagicMock()

    mgr = _mock_manager(active=True)
    mgr.get_or_create = AsyncMock(return_value=mock_session)

    msg = _mock_message()
    msg.text = "/skill confirm-skill"

    await skill_command(msg, mgr, loader)

    reply = msg.answer.call_args.args[0]
    # Should contain a confirmation with the skill name
    assert "confirm-skill" in reply
    assert "activated" in reply.lower() or "✅" in reply


async def test_skill_command_unknown_name_replies_error() -> None:
    loader = _mock_skill_loader([])

    mgr = _mock_manager(active=True)

    msg = _mock_message()
    msg.text = "/skill nonexistent"

    await skill_command(msg, mgr, loader)

    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args.args[0]
    assert "nonexistent" in reply
    assert "❌" in reply or "Unknown" in reply


async def test_skill_command_no_session_replies_error() -> None:
    skill = Skill("some-skill", "desc", "body")
    loader = _mock_skill_loader([skill])

    mgr = _mock_manager(active=False)

    msg = _mock_message()
    msg.text = "/skill some-skill"

    await skill_command(msg, mgr, loader)

    msg.answer.assert_awaited_once()
    reply = msg.answer.call_args.args[0]
    assert "No active session" in reply


async def test_skill_command_no_arg_replies_usage() -> None:
    loader = _mock_skill_loader([])
    mgr = _mock_manager(active=True)
    msg = _mock_message()
    msg.text = "/skill"

    await skill_command(msg, mgr, loader)

    msg.answer.assert_awaited_once()


async def test_skill_command_does_not_create_session_when_none_exists() -> None:
    skill = Skill("s", "d", "c")
    loader = _mock_skill_loader([skill])

    mgr = _mock_manager(active=False)
    mgr.get_or_create = AsyncMock()

    msg = _mock_message()
    msg.text = "/skill s"

    await skill_command(msg, mgr, loader)


# ──────────────────────────────────────────────────────────────────
# /model command & model_callback
# ──────────────────────────────────────────────────────────────────


def _mock_models(available: list[str] | None = None, default: str | None = None) -> ModelsConfig:
    return ModelsConfig(available=available or [], default=default)


async def test_model_no_arg_shows_keyboard_when_available() -> None:
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value=None)
    msg = _mock_message()
    msg.text = "/model"
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    await model_command(msg, mgr, models)

    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args.kwargs
    assert "reply_markup" in call_kwargs
    assert isinstance(call_kwargs["reply_markup"], InlineKeyboardMarkup)


async def test_model_no_arg_shows_text_when_no_list() -> None:
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value=None)
    msg = _mock_message()
    msg.text = "/model"
    models = _mock_models()  # empty list

    await model_command(msg, mgr, models)

    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args.kwargs
    assert "reply_markup" not in call_kwargs


async def test_model_no_arg_current_model_shown_in_keyboard_label() -> None:
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value="claude-opus-4-5")
    msg = _mock_message()
    msg.text = "/model"
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    await model_command(msg, mgr, models)

    text: str = msg.answer.call_args[0][0]
    assert "claude-opus-4-5" in text


async def test_model_set_via_text_arg() -> None:
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model claude-custom-model"
    models = _mock_models()

    await model_command(msg, mgr, models)

    mgr.set_model.assert_called_once_with("claude-custom-model")
    msg.answer.assert_awaited_once()


async def test_model_reset_via_text_arg() -> None:
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model default"
    models = _mock_models()

    await model_command(msg, mgr, models)

    mgr.set_model.assert_called_once_with(None)


async def test_model_callback_sets_model_and_updates_keyboard() -> None:
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    mgr.get_model = MagicMock(return_value="claude-opus-4-5")
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    cb = MagicMock(spec=CallbackQuery)
    cb.data = "model:claude-opus-4-5"
    cb.from_user = MagicMock(id=42)
    cb.message = MagicMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()

    await model_callback(cb, mgr, models)

    mgr.set_model.assert_called_once_with("claude-opus-4-5")
    cb.message.edit_reply_markup.assert_awaited_once()
    cb.answer.assert_awaited_once()


async def test_model_callback_default_resets_model() -> None:
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    mgr.get_model = MagicMock(return_value=None)
    models = _mock_models(["claude-opus-4-5"])

    cb = MagicMock(spec=CallbackQuery)
    cb.data = "model:default"
    cb.from_user = MagicMock(id=42)
    cb.message = MagicMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()

    await model_callback(cb, mgr, models)

    mgr.set_model.assert_called_once_with(None)


async def test_model_callback_clears_session_when_active() -> None:
    mgr = _mock_manager(active=True)
    mgr.set_model = MagicMock()
    mgr.get_model = MagicMock(return_value="claude-sonnet-4-5")
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    cb = MagicMock(spec=CallbackQuery)
    cb.data = "model:claude-sonnet-4-5"
    cb.from_user = MagicMock(id=42)
    cb.message = MagicMock()
    cb.message.edit_reply_markup = AsyncMock()
    cb.answer = AsyncMock()

    await model_callback(cb, mgr, models)

    mgr.stop.assert_awaited_once_with(42)

    mgr.get_or_create.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# /context — helpers and command
# ──────────────────────────────────────────────────────────────────


def _sample_stats() -> dict:
    return {
        "usage": {
            "input_tokens": 40_000,
            "output_tokens": 5_000,
            "cache_read_input_tokens": 10_000,
            "cache_creation_input_tokens": 500,
        },
        "total_cost_usd": 0.034,
        "num_turns": 15,
        "last_duration_ms": 3_200,
    }


def _mock_manager_with_context(active: bool, stats: dict | None) -> SessionManager:
    mgr = MagicMock(spec=SessionManager)
    mgr.has_session.return_value = active
    mgr.context_stats.return_value = stats
    return mgr


# _progress_bar


def test_progress_bar_empty_at_zero() -> None:
    bar = _progress_bar(0, 200_000)
    assert "█" not in bar
    assert len(bar) == 20


def test_progress_bar_full_at_max() -> None:
    bar = _progress_bar(200_000, 200_000)
    assert "░" not in bar
    assert len(bar) == 20


def test_progress_bar_half_filled() -> None:
    bar = _progress_bar(100_000, 200_000)
    assert bar.count("█") == 10
    assert bar.count("░") == 10


def test_progress_bar_custom_width() -> None:
    bar = _progress_bar(50, 100, width=10)
    assert len(bar) == 10
    assert bar.count("█") == 5


def test_progress_bar_clamps_above_total() -> None:
    bar = _progress_bar(300_000, 200_000, width=20)
    assert "░" not in bar  # fully filled


def test_progress_bar_zero_total_returns_empty_bar() -> None:
    bar = _progress_bar(0, 0, width=20)
    assert len(bar) == 20
    assert "█" not in bar


# _fmt_context


def test_fmt_context_contains_percentage() -> None:
    text = _fmt_context(_sample_stats())
    # total context = input(40k) + cache_read(10k) + cache_new(0.5k) = 50,500
    # round(100 * 50_500 / 200_000) = 25
    assert "25%" in text


def test_fmt_context_contains_input_token_count() -> None:
    # 40,000 still appears in the detail line "📥 Input: 40,000 t"
    text = _fmt_context(_sample_stats())
    assert "40,000" in text


def test_fmt_context_headline_shows_total_context_tokens() -> None:
    # The headline "X / 200,000 tokens" must reflect the full context window
    # usage: input + cache_read + cache_creation (not just bare input_tokens).
    text = _fmt_context(_sample_stats())
    # 40,000 + 10,000 + 500 = 50,500
    assert "50,500" in text


def test_fmt_context_contains_output_token_count() -> None:
    text = _fmt_context(_sample_stats())
    assert "5,000" in text


def test_fmt_context_contains_cost() -> None:
    text = _fmt_context(_sample_stats())
    assert "0.034" in text


def test_fmt_context_contains_turns() -> None:
    text = _fmt_context(_sample_stats())
    assert "15" in text


def test_fmt_context_contains_duration() -> None:
    text = _fmt_context(_sample_stats())
    assert "3.2s" in text


def test_fmt_context_sub_cent_cost_uses_4_decimal_places() -> None:
    stats = {**_sample_stats(), "total_cost_usd": 0.0003}
    text = _fmt_context(stats)
    assert "0.0003" in text


def test_fmt_context_contains_progress_bar_chars() -> None:
    text = _fmt_context(_sample_stats())
    assert "█" in text
    assert "░" in text


# context_command


async def test_context_no_session_replies_no_session() -> None:
    mgr = _mock_manager_with_context(active=False, stats=None)
    msg = _mock_message()

    await context_command(msg, mgr)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "no active session" in text.lower()


async def test_context_session_no_data_yet_replies_accordingly() -> None:
    mgr = _mock_manager_with_context(active=True, stats=None)
    msg = _mock_message()

    await context_command(msg, mgr)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "no context data" in text.lower() or "send a message" in text.lower()


async def test_context_with_stats_replies_once() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message()

    await context_command(msg, mgr)

    msg.answer.assert_awaited_once()


async def test_context_with_stats_contains_progress_bar() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message()

    await context_command(msg, mgr)

    text: str = msg.answer.call_args[0][0]
    assert "█" in text


async def test_context_with_stats_contains_turns() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message()

    await context_command(msg, mgr)

    text: str = msg.answer.call_args[0][0]
    assert "15" in text


async def test_context_uses_user_id_from_message() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message(user_id=77)

    await context_command(msg, mgr)

    mgr.has_session.assert_called_with(77)


# ──────────────────────────────────────────────────────────────────
# _fmt_context — duration ≥ 60 s uses minutes format (Medium gap)
# ──────────────────────────────────────────────────────────────────


def test_fmt_context_duration_60s_uses_minutes() -> None:
    """Durations ≥ 60 s must be formatted as '<N>m' not '<N>s'."""
    stats = {**_sample_stats(), "last_duration_ms": 90_000}  # 90 s = 1.5 m
    text = _fmt_context(stats)
    assert "1.5m" in text
    assert "90.0s" not in text


def test_fmt_context_duration_exactly_60s_uses_minutes() -> None:
    stats = {**_sample_stats(), "last_duration_ms": 60_000}  # exactly 60 s = 1.0 m
    text = _fmt_context(stats)
    assert "1.0m" in text


def test_fmt_context_duration_below_60s_uses_seconds() -> None:
    stats = {**_sample_stats(), "last_duration_ms": 59_000}  # 59 s
    text = _fmt_context(stats)
    assert "59.0s" in text
    assert "m" not in text.split("⏱")[1]  # no minutes suffix after timer emoji


# ──────────────────────────────────────────────────────────────────
# /skills with plugin_loader — High gap
# ──────────────────────────────────────────────────────────────────


async def test_skills_command_with_plugin_loader_shows_plugin_section() -> None:
    """When a plugin_loader is provided, plugin skills must appear in the reply."""
    from archon.ai.plugin_loader import PluginInfo, PluginLoader

    plugin_skill = Skill("myplugin:helper", "Helpful plugin skill", "plugin body")
    plugin_info = PluginInfo(
        key="myplugin@vendor",
        name="myplugin",
        marketplace="vendor",
        version="2.0.0",
        install_path="/fake/path",
        description="A test plugin",
        skills=[plugin_skill],
    )
    mock_plugin_loader = MagicMock(spec=PluginLoader)
    mock_plugin_loader.load_all.return_value = [plugin_info]
    mock_plugin_loader.get_skills.return_value = [plugin_skill]

    loader = _mock_skill_loader([])  # no personal skills
    msg = _mock_message()

    await skills_command(msg, loader, plugin_loader=mock_plugin_loader)

    mock_plugin_loader.load_all.assert_called_once()
    reply = msg.answer.call_args.args[0]
    assert "myplugin@vendor" in reply
    assert "Helpful plugin skill" in reply


async def test_skills_command_plugin_loader_with_personal_skills_shows_both() -> None:
    """Both personal skills and plugin skills must appear when both are present."""
    from archon.ai.plugin_loader import PluginInfo, PluginLoader

    personal = [Skill("my-skill", "Personal skill", "body")]
    plugin_skill = Skill("plug:tool", "Plugin tool", "plug body")
    plugin_info = PluginInfo(
        key="plug@vendor",
        name="plug",
        marketplace="vendor",
        version="1.0.0",
        install_path="/p",
        description="",
        skills=[plugin_skill],
    )
    mock_plugin_loader = MagicMock(spec=PluginLoader)
    mock_plugin_loader.load_all.return_value = [plugin_info]
    mock_plugin_loader.get_skills.return_value = [plugin_skill]

    loader = _mock_skill_loader(personal)
    msg = _mock_message()

    await skills_command(msg, loader, plugin_loader=mock_plugin_loader)

    reply = msg.answer.call_args.args[0]
    assert "my-skill" in reply
    assert "Personal skill" in reply
    assert "plug@vendor" in reply
    assert "Plugin tool" in reply


# ──────────────────────────────────────────────────────────────────
# notify_callback — unrecognized mode data (Medium gap)
# ──────────────────────────────────────────────────────────────────


async def test_notify_callback_invalid_mode_does_not_change_mode() -> None:
    """notify_callback with an unrecognised mode prefix must not mutate notifications.mode."""
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:invalid")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_callback(cb, notif, "config.toml")

    assert notif.mode == "normal"
    mock_save.assert_not_called()


async def test_notify_callback_invalid_mode_still_edits_keyboard() -> None:
    """edit_reply_markup must be called even when the mode is unrecognised."""
    notif = NotificationsConfig(mode="verbose")
    cb = _mock_callback("notify:unknown_mode")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_callback(cb, notif, "config.toml")

    cb.message.edit_reply_markup.assert_awaited_once()


async def test_notify_callback_invalid_mode_still_answers() -> None:
    """callback.answer() must be called even for an unrecognised mode."""
    notif = NotificationsConfig(mode="normal")
    cb = _mock_callback("notify:bogus")

    with patch("archon.chat.commands.save_notifications_config"):
        await notify_callback(cb, notif, "config.toml")

    cb.answer.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# model_command — active session cleared when arg provided (Low gap)
# ──────────────────────────────────────────────────────────────────


async def test_model_command_with_arg_stops_active_session() -> None:
    """model_command with a text arg must stop the active session if one exists."""
    mgr = _mock_manager(active=True)
    mgr.set_model = MagicMock()
    msg = _mock_message(user_id=10)
    msg.text = "/model claude-opus-4-5"
    models = _mock_models()

    await model_command(msg, mgr, models)

    mgr.stop.assert_awaited_once_with(10)
    mgr.set_model.assert_called_once_with("claude-opus-4-5")


# ──────────────────────────────────────────────────────────────────
# /agents command
# ──────────────────────────────────────────────────────────────────


async def test_agents_command_no_loader_replies_info() -> None:
    msg = _mock_message()
    await agents_command(msg, agent_loader=None)
    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "No agent" in text or "not configured" in text.lower() or "ℹ️" in text


async def test_agents_command_empty_loader_replies_info() -> None:
    loader = _make_agent_loader([])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "No agent" in text or "ℹ️" in text


async def test_agents_command_lists_agent_names() -> None:
    loader = _make_agent_loader([
        Agent(name="researcher-archon", description="Web research specialist", prompt="p"),
        Agent(name="coder-archon", description="Expert code writer", prompt="p"),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "researcher-archon" in text
    assert "coder-archon" in text


async def test_agents_command_shows_descriptions() -> None:
    loader = _make_agent_loader([
        Agent(name="researcher-archon", description="Web research specialist", prompt="p"),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    assert "Web research specialist" in text


async def test_agents_command_shows_model_when_set() -> None:
    loader = _make_agent_loader([
        Agent(name="researcher-archon", description="Desc", prompt="p", model="haiku"),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    assert "haiku" in text


async def test_agents_command_shows_tools_when_set() -> None:
    loader = _make_agent_loader([
        Agent(name="researcher-archon", description="Desc", prompt="p", tools=["WebSearch", "Read"]),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    assert "WebSearch" in text
    assert "Read" in text


async def test_agents_command_no_tools_shown_when_empty() -> None:
    loader = _make_agent_loader([
        Agent(name="coder-archon", description="Coder", prompt="p", tools=[]),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    # Should not show "Tools:" section when tools list is empty
    assert "🔧 Tools" not in text


async def test_agents_command_archon_agents_use_robot_emoji_header() -> None:
    """Archon agents (name ends with -archon) are shown under a 🤖 section."""
    loader = _make_agent_loader([
        Agent(name="x-archon", description="d", prompt="p"),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    assert "🤖" in text


# ──────────────────────────────────────────────────────────────────
# /agents — HTML injection / escaping
# ──────────────────────────────────────────────────────────────────


def _make_agent_loader(agents: list[Agent]) -> AgentLoader:
    """Return an AgentLoader whose load_all() returns the given agents."""
    loader = MagicMock(spec=AgentLoader)
    loader.load_all.return_value = agents
    return loader


async def test_agents_command_html_in_description_does_not_crash() -> None:
    """Agent description containing HTML-like tags must be escaped, not sent raw."""
    loader = _make_agent_loader([
        Agent(
            name="demo-archon",
            description="Use <example> tags to illustrate usage",
            prompt="p",
        ),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "&lt;example&gt;" in text
    assert "<example>" not in text


async def test_agents_command_html_in_filesystem_agent_description_is_escaped() -> None:
    """Filesystem agent descriptions with angle brackets are HTML-escaped."""
    agent = Agent(
        name="tool-archon",
        description="Handles <script> and <b>bold</b> tags",
        prompt="p",
    )
    loader = _make_agent_loader([agent])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "&lt;script&gt;" in text
    assert "<script>" not in text


async def test_agents_command_html_in_agent_name_is_escaped() -> None:
    """Agent names containing angle brackets are HTML-escaped."""
    loader = _make_agent_loader([
        Agent(
            name="<bad>-archon",
            description="normal description",
            prompt="p",
        ),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    assert "&lt;bad&gt;" in text
    assert "<bad>" not in text


async def test_agents_command_ampersand_in_description_is_escaped() -> None:
    """Ampersands in descriptions are escaped to &amp; for valid HTML."""
    loader = _make_agent_loader([
        Agent(
            name="demo-archon",
            description="Search & summarise results",
            prompt="p",
        ),
    ])
    msg = _mock_message()
    await agents_command(msg, agent_loader=loader)
    text: str = msg.answer.call_args[0][0]
    assert "&amp;" in text
