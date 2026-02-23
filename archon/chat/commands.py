"""Bot command handlers — /status, /stop, /clear, /restart, /notify, /settings."""
import logging
import os
import sys
import time

from aiogram.types import Message

from archon.ai.session_manager import SessionManager
from archon.config.loader import NotificationsConfig, save_notifications_config

logger = logging.getLogger("archon")


async def status_command(message: Message, session_manager: SessionManager, cwd: str) -> None:
    """Handle /status — report session state, working directory and uptime."""
    user_id = message.from_user.id if message.from_user else 0
    if session_manager.has_session(user_id):
        started = session_manager.session_started_at(user_id)
        uptime = int(time.monotonic() - started) if started is not None else 0
        text = (
            f"✅ Session active\n"
            f"Working directory: {cwd}\n"
            f"Uptime: {uptime}s"
        )
    else:
        text = "ℹ️ No active session"
    logger.info("/status for user %d: %s", user_id, "active" if session_manager.has_session(user_id) else "inactive")
    await message.answer(text)


async def clear_command(message: Message, session_manager: SessionManager) -> None:
    """Handle /clear — stop current session and immediately start a fresh one."""
    user_id = message.from_user.id if message.from_user else 0
    await session_manager.stop(user_id)
    await session_manager.get_or_create(user_id)
    logger.info("/clear for user %d", user_id)
    await message.answer("🧹 Context cleared. New session started.")


async def restart_command(message: Message, session_manager: SessionManager) -> None:
    """Handle /restart — gracefully stop all sessions then exec a fresh process."""
    chat_id = message.chat.id
    logger.info("/restart requested by chat %d", chat_id)
    await message.answer("♻️ Restarting...")
    await session_manager.stop_all()
    os.environ["ARCHON_RESTART_NOTIFY_CHAT_ID"] = str(chat_id)
    logger.info("/restart: replacing process")
    os.execv(sys.executable, [sys.executable] + sys.argv)


async def stop_command(message: Message, session_manager: SessionManager) -> None:
    """Handle /stop — terminate the user's active session."""
    user_id = message.from_user.id if message.from_user else 0
    if not session_manager.has_session(user_id):
        await message.answer("ℹ️ No active session")
        return
    await session_manager.stop(user_id)
    logger.info("/stop for user %d", user_id)
    await message.answer("✅ Session stopped.")


_CONCISE_MODES = frozenset({"off", "full", "partial"})


async def notify_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /notify [thinking|tools|off|full|partial [N]] — manage notification settings.

    Subcommands:
      thinking        — toggle thinking-result messages on/off
      tools           — toggle tool output between full and brief
      off|full|partial [N] — set the streaming mode (partial accepts an interval in minutes)
      (no arg)        — show current settings
    """
    parts = (message.text or "").split(maxsplit=2)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if arg == "thinking":
        notifications.show_thinking_result = not notifications.show_thinking_result
        save_notifications_config(notifications, config_file)
        state = "on" if notifications.show_thinking_result else "off"
        logger.info("/notify thinking → %s", state)
        await message.answer(f"💭 Thinking results: {state}")

    elif arg == "tools":
        notifications.brief_tool_output = not notifications.brief_tool_output
        save_notifications_config(notifications, config_file)
        state = "brief" if notifications.brief_tool_output else "full"
        logger.info("/notify tools → %s", state)
        await message.answer(f"🔧 Tool output: {state}")

    elif arg in _CONCISE_MODES:
        notifications.concise_mode = arg
        if arg == "partial" and len(parts) == 3:
            try:
                minutes = int(parts[2])
                if minutes > 0:
                    notifications.concise_interval_minutes = minutes
            except ValueError:
                pass  # invalid number — keep current interval
        save_notifications_config(notifications, config_file)
        logger.info("/notify → mode: %s", notifications.concise_mode)
        if notifications.concise_mode == "partial":
            reply = f"⚡ Mode: partial (every {notifications.concise_interval_minutes} min)"
        else:
            reply = f"⚡ Mode: {notifications.concise_mode}"
        await message.answer(reply)

    else:
        # No valid arg: show current settings
        thinking = "on" if notifications.show_thinking_result else "off"
        tools = "brief" if notifications.brief_tool_output else "full"
        concise = notifications.concise_mode
        if concise == "partial":
            concise = f"partial ({notifications.concise_interval_minutes} min)"
        await message.answer(
            f"⚙️ Notification settings:\n"
            f"  💭 thinking results: {thinking}\n"
            f"  🔧 tool output: {tools}\n"
            f"  ⚡ mode: {concise}\n\n"
            f"Change: /notify thinking | /notify tools | /notify off|full|partial [N]"
        )


async def settings_command(message: Message, notifications: NotificationsConfig) -> None:
    """Handle /settings — show current notification settings."""
    thinking = "on" if notifications.show_thinking_result else "off"
    tools = "brief" if notifications.brief_tool_output else "full"
    concise = notifications.concise_mode
    if concise == "partial":
        concise = f"partial ({notifications.concise_interval_minutes} min)"
    await message.answer(
        f"⚙️ Notification settings:\n"
        f"  💭 thinking results: {thinking}\n"
        f"  🔧 tool output: {tools}\n"
        f"  ⚡ mode: {concise}"
    )
