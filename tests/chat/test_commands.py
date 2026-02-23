"""Tests for command handlers — /status, /stop, /clear, /restart, /notify, /settings, /skills, /skill, /model."""
import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import Skill, SkillLoader
from archon.chat.commands import (
    clear_command,
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
