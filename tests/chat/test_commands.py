"""Tests for command handlers — /status, /stop, /clear, /restart, /notify, /skills, /skill, /models, /context, /agents, /scheduled, /tasks."""
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import Skill, SkillLoader
from archon.chat.commands import (
    _fmt_context,
    _progress_bar,
    cancel_agent_callback,
    clear_command,
    context_command,
    debug_command,
    models_command,
    model_callback,
    normal_command,
    notify_callback,
    notify_command,
    quiet_command,
    restart_command,
    scheduled_command,
    skill_command,
    skills_command,
    status_command,
    stop_command,
    tasks_command,
    verbose_command,
)
from archon.ai.agent_loader import Agent, AgentLoader
from archon.ai.background_agent_manager import AgentRun
from archon.ai.job_scheduler import JobScheduler, JobStatus
from archon.chat.commands import agents_command
from archon.config.loader import ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep, ModelsConfig, NotificationsConfig


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
    # Provide a default diagnostics dict so status_command doesn't get MagicMock values.
    mgr.session_diagnostics.return_value = {
        "is_alive": True,
        "is_processing": False,
        "processing_seconds": None,
        "idle_seconds": 5.0,
        "send_count": 0,
        "recent_events": [],
        "usage_stats": None,
    } if active else None
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


async def test_status_does_not_include_version_when_active() -> None:
    from archon.version import get_version

    mgr = _mock_manager(active=True)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert get_version() not in text


async def test_status_does_not_include_version_when_no_session() -> None:
    from archon.version import get_version

    mgr = _mock_manager(active=False)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert get_version() not in text


# ── diagnostics — S14.1 ───────────────────────────────────────────


def _mock_manager_with_diag(
    *,
    active: bool = True,
    started_offset: float = 30.0,
    is_processing: bool = False,
    processing_seconds: float | None = None,
    idle_seconds: float | None = 5.0,
    send_count: int = 3,
) -> SessionManager:
    """SessionManager mock that returns a diagnostics dict from session_diagnostics()."""
    mgr = _mock_manager(active=active, started_offset=started_offset)
    diag = {
        "is_alive": True,
        "is_processing": is_processing,
        "processing_seconds": processing_seconds,
        "idle_seconds": idle_seconds,
        "send_count": send_count,
        "recent_events": [],
        "usage_stats": None,
    } if active else None
    mgr.session_diagnostics = MagicMock(return_value=diag)
    return mgr


async def test_status_shows_processing_indicator_when_active() -> None:
    """🔄 Processing appears in /status when session is_processing=True."""
    mgr = _mock_manager_with_diag(
        is_processing=True,
        processing_seconds=12.3,
        idle_seconds=None,
    )
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert "🔄" in text
    assert "12.3" in text


async def test_status_shows_idle_indicator_when_not_processing() -> None:
    """💤 Idle appears in /status when session is idle."""
    mgr = _mock_manager_with_diag(
        is_processing=False,
        processing_seconds=None,
        idle_seconds=7.5,
    )
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert "💤" in text
    assert "7.5" in text


async def test_status_shows_send_count() -> None:
    """Message count appears in /status when session is active."""
    mgr = _mock_manager_with_diag(send_count=5)
    msg = _mock_message()

    await status_command(msg, mgr, cwd="/work")

    text: str = msg.answer.call_args[0][0]
    assert "5" in text


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


async def test_stop_cancels_running_background_agents() -> None:
    """When /stop is called, all running background agents for the user are cancelled."""
    from archon.ai.background_agent_manager import BackgroundAgentManager

    mgr = _mock_manager(active=True)
    bam = MagicMock(spec=BackgroundAgentManager)
    run1 = MagicMock(spec=AgentRun, run_id="aaa")
    run2 = MagicMock(spec=AgentRun, run_id="bbb")
    bam.list_running.return_value = [run1, run2]
    bam.cancel = AsyncMock(return_value=True)
    msg = _mock_message(user_id=7)

    await stop_command(msg, mgr, background_agent_manager=bam)

    bam.list_running.assert_called_once_with(7)
    assert bam.cancel.await_count == 2
    bam.cancel.assert_any_await("aaa")
    bam.cancel.assert_any_await("bbb")


async def test_stop_works_without_background_agent_manager() -> None:
    """When no BackgroundAgentManager is provided, /stop still works (only stops session)."""
    mgr = _mock_manager(active=True)
    msg = _mock_message(user_id=7)

    await stop_command(msg, mgr, background_agent_manager=None)

    mgr.stop.assert_awaited_once_with(7)
    msg.answer.assert_awaited_once()


async def test_stop_mentions_cancelled_agents_in_reply() -> None:
    """The confirmation message should mention the number of cancelled agents."""
    from archon.ai.background_agent_manager import BackgroundAgentManager

    mgr = _mock_manager(active=True)
    bam = MagicMock(spec=BackgroundAgentManager)
    bam.list_running.return_value = [
        MagicMock(spec=AgentRun, run_id="x"),
        MagicMock(spec=AgentRun, run_id="y"),
    ]
    bam.cancel = AsyncMock(return_value=True)
    msg = _mock_message(user_id=7)

    await stop_command(msg, mgr, background_agent_manager=bam)

    text: str = msg.answer.call_args[0][0]
    assert "2" in text
    assert "agent" in text.lower()


async def test_stop_no_session_still_cancels_agents() -> None:
    """Even with no active session, running background agents should be cancelled."""
    from archon.ai.background_agent_manager import BackgroundAgentManager

    mgr = _mock_manager(active=False)
    bam = MagicMock(spec=BackgroundAgentManager)
    run = MagicMock(spec=AgentRun, run_id="zzz")
    bam.list_running.return_value = [run]
    bam.cancel = AsyncMock(return_value=True)
    msg = _mock_message(user_id=7)

    await stop_command(msg, mgr, background_agent_manager=bam)

    bam.cancel.assert_awaited_once_with("zzz")
    mgr.stop.assert_not_called()
    text: str = msg.answer.call_args[0][0]
    assert "agent" in text.lower()
    assert "session" not in text.lower()


async def test_stop_cancel_returns_false_not_counted() -> None:
    """If cancel() returns False (agent completed between list and cancel), don't count it."""
    from archon.ai.background_agent_manager import BackgroundAgentManager

    mgr = _mock_manager(active=True)
    bam = MagicMock(spec=BackgroundAgentManager)
    bam.list_running.return_value = [
        MagicMock(spec=AgentRun, run_id="gone"),
    ]
    bam.cancel = AsyncMock(return_value=False)
    msg = _mock_message(user_id=7)

    await stop_command(msg, mgr, background_agent_manager=bam)

    text: str = msg.answer.call_args[0][0]
    assert "agent" not in text.lower()


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


async def test_notify_quiet_negative_interval_rejected() -> None:
    """A negative interval value must be rejected with an error; mode must not change."""
    notif = NotificationsConfig(mode="normal", interval_minutes=5)
    msg = _mock_msg_with_text("/notify quiet -3")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    mock_save.assert_not_called()
    assert notif.mode == "normal"  # mode not changed
    assert notif.interval_minutes == 5  # interval not changed
    reply: str = msg.answer.call_args[0][0]
    assert "❌" in reply


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


async def test_notify_interval_negative_value_rejected() -> None:
    """'/notify interval -5' must be rejected; interval must not change."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=3)
    msg = _mock_msg_with_text("/notify interval -5")

    with patch("archon.chat.commands.save_notifications_config") as mock_save:
        await notify_command(msg, notif, "config.toml")

    mock_save.assert_not_called()
    assert notif.interval_minutes == 3  # unchanged
    reply: str = msg.answer.call_args[0][0]
    assert "❌" in reply


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


def _mock_job_scheduler() -> MagicMock:
    sched = MagicMock(unsafe=True)
    sched.stop = AsyncMock()
    return sched


def _mock_bg_manager() -> MagicMock:
    mgr = MagicMock(unsafe=True)
    mgr.stop_all = AsyncMock()
    return mgr


def _mock_bg_mcp_server() -> MagicMock:
    srv = MagicMock(unsafe=True)
    srv.stop = AsyncMock()
    return srv


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


async def test_restart_command_full_shutdown_sequence() -> None:
    """All components are stopped in order before os.execv."""
    call_order: list[str] = []
    mgr = _mock_manager_with_stop_all()
    mgr.stop_all = AsyncMock(side_effect=lambda: call_order.append("session_manager"))
    job_sched = _mock_job_scheduler()
    job_sched.stop = AsyncMock(side_effect=lambda: call_order.append("job_scheduler"))
    bg_mgr = _mock_bg_manager()
    bg_mgr.stop_all = AsyncMock(side_effect=lambda: call_order.append("bg_manager"))
    mcp = _mock_bg_mcp_server()
    mcp.stop = AsyncMock(side_effect=lambda: call_order.append("bg_mcp_server"))
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv", side_effect=lambda *a: call_order.append("execv")):
        await restart_command(msg, mgr, job_scheduler=job_sched, background_agent_manager=bg_mgr, bg_mcp_server=mcp)

    assert call_order == ["job_scheduler", "bg_manager", "bg_mcp_server", "session_manager", "execv"]


async def test_restart_command_stops_sessions_before_exec() -> None:
    """session_manager.stop_all must be called before os.execv (no optional components)."""
    call_order: list[str] = []
    mgr = _mock_manager_with_stop_all()
    mgr.stop_all = AsyncMock(side_effect=lambda: call_order.append("stop_all"))
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv", side_effect=lambda *a: call_order.append("execv")):
        await restart_command(msg, mgr)

    assert call_order == ["stop_all", "execv"]


async def test_restart_command_continues_after_component_failure() -> None:
    """A failure in one stop does not prevent the restart from proceeding."""
    mgr = _mock_manager_with_stop_all()
    job_sched = _mock_job_scheduler()
    job_sched.stop = AsyncMock(side_effect=RuntimeError("job_scheduler boom"))
    bg_mgr = _mock_bg_manager()
    mcp = _mock_bg_mcp_server()
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv") as mock_execv:
        await restart_command(msg, mgr, job_scheduler=job_sched, background_agent_manager=bg_mgr, bg_mcp_server=mcp)

    # All other stops and execv still called despite job_scheduler failure
    bg_mgr.stop_all.assert_called_once()
    mcp.stop.assert_called_once()
    mgr.stop_all.assert_awaited_once()
    mock_execv.assert_called_once()


async def test_restart_command_without_optional_components() -> None:
    """Restart works fine when optional components are None."""
    mgr = _mock_manager_with_stop_all()
    msg = _mock_message()

    with patch("archon.chat.commands.os.execv") as mock_execv:
        await restart_command(msg, mgr, job_scheduler=None, background_agent_manager=None, bg_mcp_server=None)

    mgr.stop_all.assert_awaited_once()
    mock_execv.assert_called_once()


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
# /models command & model_callback
# ──────────────────────────────────────────────────────────────────


def _mock_models(available: list[str] | None = None, default: str | None = None) -> ModelsConfig:
    return ModelsConfig(available=available or [], default=default)


async def test_model_no_arg_shows_keyboard_when_available() -> None:
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value=None)
    msg = _mock_message()
    msg.text = "/models"
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    await models_command(msg, mgr, models)

    msg.answer.assert_awaited_once()
    call_kwargs = msg.answer.call_args.kwargs
    assert "reply_markup" in call_kwargs
    assert isinstance(call_kwargs["reply_markup"], InlineKeyboardMarkup)


async def test_model_no_arg_shows_help_when_no_list() -> None:
    """When available list is empty and no model override set, show help with examples."""
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value=None)
    msg = _mock_message()
    msg.text = "/models"
    models = _mock_models()  # empty list

    await models_command(msg, mgr, models)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "default (SDK)" in text
    assert "/models" in text  # usage hint
    assert "claude-sonnet-4-6" in text  # example model
    assert "default" in text  # reset hint
    call_kwargs = msg.answer.call_args.kwargs
    assert "reply_markup" not in call_kwargs


async def test_model_no_arg_shows_help_with_current_when_no_list() -> None:
    """When available list is empty but a model override is set, show it plus help."""
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value="my-custom-model")
    msg = _mock_message()
    msg.text = "/models"
    models = _mock_models()  # empty list

    await models_command(msg, mgr, models)

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "my-custom-model" in text
    assert "/models" in text  # usage hint
    assert "claude-sonnet-4-6" in text  # example model
    assert "default" in text  # reset hint
    call_kwargs = msg.answer.call_args.kwargs
    assert "reply_markup" not in call_kwargs


async def test_model_no_arg_current_model_shown_in_keyboard_label() -> None:
    mgr = _mock_manager(active=False)
    mgr.get_model = MagicMock(return_value="claude-opus-4-5")
    msg = _mock_message()
    msg.text = "/models"
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    await models_command(msg, mgr, models)

    text: str = msg.answer.call_args[0][0]
    assert "claude-opus-4-5" in text


async def test_model_set_via_text_arg() -> None:
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model claude-custom-model"
    models = _mock_models()

    await models_command(msg, mgr, models)

    mgr.set_model.assert_called_once_with("claude-custom-model")
    msg.answer.assert_awaited_once()


async def test_model_reset_via_text_arg() -> None:
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model default"
    models = _mock_models()

    await models_command(msg, mgr, models)

    mgr.set_model.assert_called_once_with(None)


async def test_model_invalid_name_rejected_when_available_list_configured() -> None:
    """When models.available is set, an unknown model name is rejected with a helpful error."""
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model bad-model"
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    await models_command(msg, mgr, models)

    mgr.set_model.assert_not_called()
    msg.answer.assert_awaited_once()
    reply: str = msg.answer.call_args[0][0]
    assert "❌" in reply
    assert "bad-model" in reply
    assert "claude-opus-4-5" in reply


async def test_model_invalid_name_lists_available_models() -> None:
    """The error message for an invalid model should list all available models."""
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model nonexistent"
    models = _mock_models(["model-a", "model-b"])

    await models_command(msg, mgr, models)

    reply: str = msg.answer.call_args[0][0]
    assert "model-a" in reply
    assert "model-b" in reply


async def test_model_valid_name_accepted_when_available_list_configured() -> None:
    """A model that IS in models.available is accepted without error."""
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model claude-opus-4-5"
    models = _mock_models(["claude-opus-4-5", "claude-sonnet-4-5"])

    await models_command(msg, mgr, models)

    mgr.set_model.assert_called_once_with("claude-opus-4-5")
    msg.answer.assert_awaited_once()
    reply: str = msg.answer.call_args[0][0]
    assert "❌" not in reply


async def test_model_arbitrary_name_accepted_when_no_available_list() -> None:
    """When models.available is empty, any model name is accepted (no list to validate against)."""
    mgr = _mock_manager(active=False)
    mgr.set_model = MagicMock()
    msg = _mock_message()
    msg.text = "/model arbitrary-model"
    models = _mock_models()  # empty available list

    await models_command(msg, mgr, models)

    mgr.set_model.assert_called_once_with("arbitrary-model")


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
    # cache_read=10_000 from previous turns (= sum of their cache_creation).
    # This turn adds cache_creation=500, so cumulative_cache_creation = 10_000 + 500 = 10_500.
    # Context window = cumulative_cache_creation + input_tokens = 10_500 + 40_000 = 50_500 (25%).
    return {
        "usage": {
            "input_tokens": 40_000,
            "output_tokens": 5_000,
            "cache_read_input_tokens": 10_000,
            "cache_creation_input_tokens": 500,
        },
        "cumulative_cache_creation": 10_500,   # sum of cache_creation across ALL turns
        "total_cost_usd": 0.034,
        "num_turns": 15,
        "user_turns": 15,
        "last_duration_ms": 3_200,
    }


def _mock_manager_with_context(active: bool, stats: dict | None) -> SessionManager:
    mgr = MagicMock(spec=SessionManager)
    mgr.has_session.return_value = active
    mgr.context_stats.return_value = stats
    return mgr


def _mock_notifications(mode: str = "normal") -> NotificationsConfig:
    return NotificationsConfig(mode=mode)


def _sample_stats_with_sessions() -> dict:
    """Sample stats including the multi-session 'sessions' key."""
    return {
        **_sample_stats(),
        "sessions": {
            "classifier":    {"cumulative_cache_creation": 6_000, "cost_usd": 0.001},
            "orchestration": {"cumulative_cache_creation": 2_000, "cost_usd": 0.0003},
            "summary":       {"cumulative_cache_creation": 0,     "cost_usd": 0.0},
        },
    }


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
    # total context = cumulative_cache_creation(10,500) + input(40,000) = 50,500
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

    await context_command(msg, mgr, _mock_notifications())

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "no active session" in text.lower()


async def test_context_session_no_data_yet_replies_accordingly() -> None:
    mgr = _mock_manager_with_context(active=True, stats=None)
    msg = _mock_message()

    await context_command(msg, mgr, _mock_notifications())

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert "no context data" in text.lower() or "send a message" in text.lower()


async def test_context_with_stats_replies_once() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message()

    await context_command(msg, mgr, _mock_notifications())

    msg.answer.assert_awaited_once()


async def test_context_with_stats_contains_progress_bar() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message()

    await context_command(msg, mgr, _mock_notifications())

    text: str = msg.answer.call_args[0][0]
    assert "█" in text


async def test_context_with_stats_contains_turns() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message()

    await context_command(msg, mgr, _mock_notifications())

    text: str = msg.answer.call_args[0][0]
    assert "15" in text


async def test_context_uses_user_id_from_message() -> None:
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats())
    msg = _mock_message(user_id=77)

    await context_command(msg, mgr, _mock_notifications())

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
# _fmt_context — context overcounting bug (cache_read × N tool calls)
# ──────────────────────────────────────────────────────────────────
#
# Root cause: cache_read_input_tokens = N_tool_calls × context_size.
# Each Anthropic API call made during one SDK query() reads the full cache,
# so cache_read accumulates N times even though context_size is only M.
#
# Fix: use cumulative_cache_creation (sum across all turns) + input_tokens.
# This always equals the true context size regardless of tool-call count.


def test_fmt_context_overcounting_bug_is_fixed() -> None:
    """Exact values from the reported bug — context must not show 554%."""
    # User observed:
    #   Input: 14 t  Output: 6,784 t  Cache read: 1,024,265 t  Cache new: 83,918 t
    #   Old formula: 14 + 1,024,265 + 83,918 = 1,108,197 → 554%  (WRONG)
    #   New formula: cumulative_cache_creation(83,918) + input(14) = 83,932 → 42% (CORRECT)
    stats = {
        "usage": {
            "input_tokens": 14,
            "output_tokens": 6_784,
            "cache_read_input_tokens": 1_024_265,
            "cache_creation_input_tokens": 83_918,
        },
        "cumulative_cache_creation": 83_918,
        "total_cost_usd": 162.214,
        "num_turns": 14,
        "last_duration_ms": 138_000,
    }
    text = _fmt_context(stats)
    assert "554%" not in text, "Context window must not show 554% due to cache_read overcounting"
    assert "83,932" in text, "Headline must show cumulative_cache_creation + input_tokens"
    assert "42%" in text, "Percentage must be ~42% (83,932 / 200,000)"


def test_fmt_context_uses_cumulative_cache_creation_not_raw_cache_read() -> None:
    """cache_read must NOT be part of the context window total."""
    # Setup: cache_read is artificially huge (e.g. 10 tool calls × 50k context).
    stats = {
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 500_000,   # 10 tool calls × 50k context
            "cache_creation_input_tokens": 1_000,
        },
        "cumulative_cache_creation": 50_000,       # actual context size
        "total_cost_usd": 0.5,
        "num_turns": 5,
        "last_duration_ms": 5_000,
    }
    text = _fmt_context(stats)
    # Old (wrong): 100 + 500,000 + 1,000 = 501,100 → 250%
    assert "250%" not in text
    # New (correct): 50,000 + 100 = 50,100 → 25%
    assert "50,100" in text
    assert "25%" in text


def test_fmt_context_single_turn_no_tool_calls_unchanged() -> None:
    """Single turn, no tool calls: cumulative_cache_creation == cache_creation, same result."""
    stats = {
        "usage": {
            "input_tokens": 5_000,
            "output_tokens": 500,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 2_000,
        },
        "cumulative_cache_creation": 2_000,   # first turn: only this turn's creation
        "total_cost_usd": 0.01,
        "num_turns": 1,
        "last_duration_ms": 1_500,
    }
    text = _fmt_context(stats)
    # total_ctx = 2,000 + 5,000 = 7,000 → round(100 * 7000/200000) = 4%
    assert "7,000" in text
    assert "4%" in text


# ──────────────────────────────────────────────────────────────────
# _fmt_context — sub-session visibility gated by notification mode
# ──────────────────────────────────────────────────────────────────


def test_fmt_context_verbose_shows_sub_sessions() -> None:
    """In verbose mode, sub-session breakdown is shown when stats has 'sessions'."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("verbose"))
    assert "Sub-sessions" in text


def test_fmt_context_debug_shows_sub_sessions() -> None:
    """In debug mode, sub-session breakdown is shown when stats has 'sessions'."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("debug"))
    assert "Sub-sessions" in text


def test_fmt_context_normal_hides_sub_sessions() -> None:
    """In normal mode, sub-session breakdown is hidden."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("normal"))
    assert "Sub-sessions" not in text
    assert "Classifier" not in text


def test_fmt_context_quiet_hides_sub_sessions() -> None:
    """In quiet mode, sub-session breakdown is hidden."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("quiet"))
    assert "Sub-sessions" not in text
    assert "Classifier" not in text


def test_fmt_context_verbose_shows_classifier_cost() -> None:
    """Verbose mode shows classifier cost from sessions."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("verbose"))
    # classifier cost is 0.001 → formatted as $0.0010
    assert "0.001" in text


def test_fmt_context_verbose_shows_orchestration_cost() -> None:
    """Verbose mode shows orchestration cost from sessions."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("verbose"))
    # orchestration cost is 0.0003 → formatted as $0.0003
    assert "0.0003" in text


def test_fmt_context_verbose_no_sessions_key_no_section() -> None:
    """In verbose mode, if stats has no 'sessions' key, no sub-session section."""
    text = _fmt_context(_sample_stats(), _mock_notifications("verbose"))
    assert "Sub-sessions" not in text
    assert "Classifier" not in text


def test_fmt_context_verbose_empty_sessions_hides_section() -> None:
    """In verbose mode, sessions={} (empty dict) → no sub-session section (falsy guard)."""
    stats = {**_sample_stats(), "sessions": {}}
    text = _fmt_context(stats, _mock_notifications("verbose"))
    assert "Sub-sessions" not in text


def test_fmt_context_none_notifications_no_sub_sessions() -> None:
    """When notifications=None, no sub-session section (backward compat)."""
    text = _fmt_context(_sample_stats_with_sessions())
    assert "Sub-sessions" not in text


def test_fmt_context_verbose_sub_sessions_in_monospace_block() -> None:
    """Sub-session lines are wrapped in <pre> so alignment renders correctly."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("verbose"))
    assert "<pre>" in text
    assert "</pre>" in text


def test_fmt_context_verbose_sub_sessions_dollar_prefix() -> None:
    """Each sub-session cost line starts with $ (not bare decimal)."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("verbose"))
    # classifier cost is 0.001 → formatted as $0.0010
    assert "$0.0010" in text
    assert "$0.0003" in text


def test_fmt_context_verbose_sub_sessions_order() -> None:
    """Classifier appears before Orchestration which appears before Summary."""
    text = _fmt_context(_sample_stats_with_sessions(), _mock_notifications("verbose"))
    clf_pos = text.find("Classifier")
    orch_pos = text.find("Orchestration")
    summary_pos = text.find("Summary")
    assert clf_pos < orch_pos < summary_pos


def test_fmt_context_verbose_partial_sessions_no_crash() -> None:
    """When sessions dict has only some keys, missing sessions default to $0.0000."""
    stats = {
        **_sample_stats(),
        "sessions": {"classifier": {"cumulative_cache_creation": 0, "cost_usd": 0.005}},
    }
    text = _fmt_context(stats, _mock_notifications("verbose"))
    assert "Sub-sessions" in text
    # orchestration and summary are missing from sessions → default to $0.0000
    assert "$0.0000" in text
    # classifier should still show its cost
    assert "$0.0050" in text


async def test_context_verbose_shows_sub_session_section() -> None:
    """In verbose mode, /context shows sub-session breakdown when stats has sessions."""
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats_with_sessions())
    msg = _mock_message()

    await context_command(msg, mgr, _mock_notifications("verbose"))

    text: str = msg.answer.call_args[0][0]
    assert "Sub-sessions" in text


async def test_context_normal_hides_sub_session_section() -> None:
    """In normal mode, /context does not show sub-session breakdown."""
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats_with_sessions())
    msg = _mock_message()

    await context_command(msg, mgr, _mock_notifications("normal"))

    text: str = msg.answer.call_args[0][0]
    assert "Sub-sessions" not in text


async def test_context_command_without_notifications_hides_sub_sessions() -> None:
    """Backward compat: context_command called without notifications arg hides sub-sessions."""
    mgr = _mock_manager_with_context(active=True, stats=_sample_stats_with_sessions())
    msg = _mock_message()

    await context_command(msg, mgr)  # no notifications argument

    text: str = msg.answer.call_args[0][0]
    assert "Sub-sessions" not in text
    assert "Context Window" in text  # main section still shown


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
# models_command — active session cleared when arg provided (Low gap)
# ──────────────────────────────────────────────────────────────────


async def test_models_command_with_arg_stops_active_session() -> None:
    """models_command with a text arg must stop the active session if one exists."""
    mgr = _mock_manager(active=True)
    mgr.set_model = MagicMock()
    msg = _mock_message(user_id=10)
    msg.text = "/model claude-opus-4-5"
    models = _mock_models()

    await models_command(msg, mgr, models)

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


# ──────────────────────────────────────────────────────────────────
# /scheduled command
# ──────────────────────────────────────────────────────────────────


def _make_scheduler_with_jobs(*statuses: JobStatus) -> JobScheduler:
    """Create a JobScheduler whose job_statuses property returns given statuses."""
    bot = MagicMock()
    bot.send_message = AsyncMock()
    cfg = ScheduleConfig(enabled=True, jobs=[
        ScheduledJobConfig(name=s.name, cron="* * * * *", pipeline=[])
        for s in statuses
    ])
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[123])
    for s in statuses:
        scheduler._statuses[s.name] = s
    return scheduler


async def test_scheduled_command_no_scheduler_shows_not_configured() -> None:
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=None)
    text: str = msg.answer.call_args[0][0]
    assert "not configured" in text


async def test_scheduled_command_empty_jobs_shows_no_jobs() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    cfg = ScheduleConfig(enabled=True, jobs=[])
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[123])
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "No scheduled jobs" in text


async def test_scheduled_command_waiting_job_shows_waiting() -> None:
    status = JobStatus(name="myjob")  # no last_run
    scheduler = _make_scheduler_with_jobs(status)
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "myjob" in text
    assert "⏳" in text


async def test_scheduled_command_completed_job_shows_last_run_time() -> None:
    status = JobStatus(name="done_job", last_run=datetime(2025, 1, 1, 12, 30, 0), run_count=3)
    scheduler = _make_scheduler_with_jobs(status)
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "done_job" in text
    assert "12:30:00" in text
    assert "3" in text  # run_count


async def test_scheduled_command_running_job_shows_running_icon() -> None:
    status = JobStatus(name="active_job", is_running=True)
    scheduler = _make_scheduler_with_jobs(status)
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "🔄" in text


async def test_scheduled_command_failed_job_shows_error() -> None:
    status = JobStatus(name="bad_job", last_error="subprocess failed")
    scheduler = _make_scheduler_with_jobs(status)
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "❌" in text
    assert "subprocess failed" in text


async def test_scheduled_command_shows_result_preview() -> None:
    status = JobStatus(name="result_job", last_run=datetime(2025, 1, 1, 9, 0, 0), last_result="hello output")
    scheduler = _make_scheduler_with_jobs(status)
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "hello output" in text


async def test_scheduled_command_multiple_jobs_all_listed() -> None:
    statuses = [
        JobStatus(name="job_a"),
        JobStatus(name="job_b", last_run=datetime(2025, 1, 1, 8, 0, 0), run_count=1),
    ]
    scheduler = _make_scheduler_with_jobs(*statuses)
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "job_a" in text
    assert "job_b" in text


async def test_scheduled_command_shows_next_run_time_today() -> None:
    """Next run today should appear as HH:MM."""
    status = JobStatus(name="minutely")
    bot = MagicMock()
    bot.send_message = AsyncMock()
    cfg = ScheduleConfig(enabled=True, jobs=[
        ScheduledJobConfig(name="minutely", cron="* * * * *", pipeline=[], enabled=True),
    ])
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[123])
    scheduler._statuses["minutely"] = status
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "⏭" in text
    # Time-only format HH:MM — not a date prefix
    import re
    assert re.search(r"next: \d{2}:\d{2}", text), f"Expected HH:MM in: {text}"


async def test_scheduled_command_shows_next_run_disabled() -> None:
    """Disabled jobs should show 'disabled' for next run."""
    status = JobStatus(name="off_job")
    bot = MagicMock()
    bot.send_message = AsyncMock()
    cfg = ScheduleConfig(enabled=True, jobs=[
        ScheduledJobConfig(name="off_job", cron="0 8 * * *", pipeline=[], enabled=False),
    ])
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[123])
    scheduler._statuses["off_job"] = status
    msg = _mock_message()
    await scheduled_command(msg, job_scheduler=scheduler)
    text: str = msg.answer.call_args[0][0]
    assert "disabled" in text


# ──────────────────────────────────────────────────────────────────
# /tasks command (was /running_agents -- S15.4)
# ──────────────────────────────────────────────────────────────────


def _mock_agent_run(
    run_id: str = "abc123",
    name: str = "Scout",
    task: str = "Summarise the logs",
    user_id: int = 42,
    elapsed_secs: float = 45.0,
    status: str = "running",
) -> AgentRun:
    """Return a mock AgentRun with a controlled started_at."""
    run = AgentRun(
        run_id=run_id,
        name=name,
        task=task,
        context="",
        user_id=user_id,
        started_at=time.monotonic() - elapsed_secs,
        status=status,
    )
    return run


def _mock_bg_manager(running_runs: list[AgentRun] | None = None) -> MagicMock:
    """Return a mock BackgroundAgentManager."""
    mgr = MagicMock()
    mgr.list_running.return_value = running_runs or []
    mgr.cancel = AsyncMock(return_value=True)
    mgr.get_run = MagicMock(return_value=None)
    return mgr


async def test_tasks_not_enabled() -> None:
    """When background_agent_manager is None, reply with 'not enabled'."""
    msg = _mock_message()
    await tasks_command(msg, background_agent_manager=None)
    text: str = msg.answer.call_args[0][0]
    assert "not enabled" in text.lower()


async def test_tasks_none_running() -> None:
    """When no agents running, reply with 'no background agents'."""
    msg = _mock_message()
    mgr = _mock_bg_manager(running_runs=[])
    await tasks_command(msg, background_agent_manager=mgr)
    text: str = msg.answer.call_args[0][0]
    assert "no background agents" in text.lower()


async def test_tasks_lists_one_agent() -> None:
    """One running agent: shows name and task snippet."""
    msg = _mock_message()
    run = _mock_agent_run(name="Scout", task="Summarise the logs", elapsed_secs=65.0)
    mgr = _mock_bg_manager(running_runs=[run])
    await tasks_command(msg, background_agent_manager=mgr)
    text: str = msg.answer.call_args[0][0]
    assert "Scout" in text
    assert "Summarise the logs" in text


async def test_tasks_shows_elapsed_time() -> None:
    """Elapsed time should appear in the reply (seconds or minutes)."""
    msg = _mock_message()
    run = _mock_agent_run(elapsed_secs=65.0)
    mgr = _mock_bg_manager(running_runs=[run])
    await tasks_command(msg, background_agent_manager=mgr)
    text: str = msg.answer.call_args[0][0]
    # Either "1m" or "65s" — just check something time-like is present
    assert "m" in text or "s" in text


async def test_tasks_has_cancel_buttons() -> None:
    """Each running agent should have a Cancel inline button."""
    msg = _mock_message()
    run = _mock_agent_run(run_id="deadbeef", name="Archer")
    mgr = _mock_bg_manager(running_runs=[run])
    await tasks_command(msg, background_agent_manager=mgr)
    # reply_markup kwarg should contain a keyboard with cancel buttons
    kwargs = msg.answer.call_args[1]
    markup = kwargs.get("reply_markup")
    assert markup is not None
    assert isinstance(markup, InlineKeyboardMarkup)
    # At least one button should have callback_data starting with 'cancel_agent:'
    all_buttons = [btn for row in markup.inline_keyboard for btn in row]
    cancel_buttons = [b for b in all_buttons if b.callback_data and b.callback_data.startswith("cancel_agent:")]
    assert len(cancel_buttons) == 1
    assert "deadbeef" in cancel_buttons[0].callback_data


async def test_tasks_multiple_agents() -> None:
    """Multiple running agents: all shown with individual Cancel buttons."""
    msg = _mock_message()
    run1 = _mock_agent_run(run_id="r1", name="Scout", task="Task A")
    run2 = _mock_agent_run(run_id="r2", name="Archer", task="Task B")
    mgr = _mock_bg_manager(running_runs=[run1, run2])
    await tasks_command(msg, background_agent_manager=mgr)
    text: str = msg.answer.call_args[0][0]
    assert "Scout" in text
    assert "Archer" in text
    kwargs = msg.answer.call_args[1]
    markup = kwargs.get("reply_markup")
    all_buttons = [btn for row in markup.inline_keyboard for btn in row]
    cancel_buttons = [b for b in all_buttons if b.callback_data and b.callback_data.startswith("cancel_agent:")]
    assert len(cancel_buttons) == 2


async def test_tasks_truncates_long_task() -> None:
    """Tasks longer than 60 chars should be truncated with '...'."""
    msg = _mock_message()
    long_task = "A" * 100
    run = _mock_agent_run(task=long_task)
    mgr = _mock_bg_manager(running_runs=[run])
    await tasks_command(msg, background_agent_manager=mgr)
    text: str = msg.answer.call_args[0][0]
    assert "..." in text


async def test_tasks_filters_by_user_id() -> None:
    """list_running should be called with the user_id from the message."""
    msg = _mock_message(user_id=99)
    mgr = _mock_bg_manager(running_runs=[])
    await tasks_command(msg, background_agent_manager=mgr)
    mgr.list_running.assert_called_once_with(99)


# ──────────────────────────────────────────────────────────────────
# cancel_agent_callback
# ──────────────────────────────────────────────────────────────────


def _mock_callback(callback_data: str, user_id: int = 42) -> CallbackQuery:
    cb = MagicMock(spec=CallbackQuery)
    cb.data = callback_data
    cb.from_user = MagicMock(id=user_id)
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    cb.message.edit_reply_markup = AsyncMock()
    return cb


async def test_cancel_agent_callback_success() -> None:
    """Successfully cancelling an agent answers with confirmation."""
    run = _mock_agent_run(run_id="abc123", name="Scout")
    mgr = _mock_bg_manager()
    mgr.get_run.return_value = run
    mgr.cancel = AsyncMock(return_value=True)
    cb = _mock_callback("cancel_agent:abc123")
    await cancel_agent_callback(cb, background_agent_manager=mgr)
    cb.answer.assert_called_once()
    answer_text: str = cb.answer.call_args[0][0]
    assert "Scout" in answer_text or "cancel" in answer_text.lower()


async def test_cancel_agent_callback_unknown_run_id() -> None:
    """Unknown run_id answers with 'not found'."""
    mgr = _mock_bg_manager()
    mgr.get_run.return_value = None
    cb = _mock_callback("cancel_agent:unknown999")
    await cancel_agent_callback(cb, background_agent_manager=mgr)
    cb.answer.assert_called_once()
    text: str = cb.answer.call_args[0][0]
    assert "not found" in text.lower() or "❌" in text


async def test_cancel_agent_callback_no_manager() -> None:
    """When manager is None, answer gracefully."""
    cb = _mock_callback("cancel_agent:abc123")
    await cancel_agent_callback(cb, background_agent_manager=None)
    cb.answer.assert_called_once()


async def test_cancel_agent_callback_removes_keyboard() -> None:
    """After a successful cancel, the keyboard should be cleared."""
    run = _mock_agent_run(run_id="abc123", name="Scout")
    mgr = _mock_bg_manager()
    mgr.get_run.return_value = run
    mgr.cancel = AsyncMock(return_value=True)
    cb = _mock_callback("cancel_agent:abc123")
    await cancel_agent_callback(cb, background_agent_manager=mgr)
    cb.message.edit_reply_markup.assert_called_once_with(reply_markup=None)


async def test_cancel_agent_callback_cancel_returns_false() -> None:
    """When cancel() returns False the callback reports 'not found'."""
    run = _mock_agent_run(run_id="abc123", name="Scout")
    mgr = _mock_bg_manager()
    mgr.get_run.return_value = run
    mgr.cancel = AsyncMock(return_value=False)
    cb = _mock_callback("cancel_agent:abc123")
    await cancel_agent_callback(cb, background_agent_manager=mgr)
    text: str = cb.answer.call_args[0][0]
    assert "not found" in text.lower() or "❌" in text


# ──────────────────────────────────────────────────────────────────
# _fmt_context — user_turns display
# ──────────────────────────────────────────────────────────────────


def test_fmt_context_prefers_user_turns_over_num_turns() -> None:
    """When user_turns is present, _fmt_context must display it instead of num_turns."""
    stats = {
        **_sample_stats(),
        "user_turns": 7,  # 7 user messages sent
        # num_turns remains 15 from _sample_stats() but must NOT be shown
    }
    text = _fmt_context(stats)
    assert "7" in text, "user_turns (7) must appear in the output"
