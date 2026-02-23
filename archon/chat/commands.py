"""Bot command handlers — /status, /stop, /concise, /filter, /settings."""
import logging
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


async def stop_command(message: Message, session_manager: SessionManager) -> None:
    """Handle /stop — terminate the user's active session."""
    user_id = message.from_user.id if message.from_user else 0
    if not session_manager.has_session(user_id):
        await message.answer("ℹ️ No active session")
        return
    await session_manager.stop(user_id)
    logger.info("/stop for user %d", user_id)
    await message.answer("✅ Session stopped.")


async def concise_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /concise — toggle concise mode (working... + response only)."""
    notifications.concise_mode = not notifications.concise_mode
    save_notifications_config(notifications, config_file)
    state = "on" if notifications.concise_mode else "off"
    logger.info("/concise → %s", state)
    await message.answer(f"⚡ Concise mode: {state}")


async def filter_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /filter [thinking|tools] — toggle individual notification filters."""
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if arg == "thinking":
        notifications.show_thinking_result = not notifications.show_thinking_result
        save_notifications_config(notifications, config_file)
        state = "on" if notifications.show_thinking_result else "off"
        logger.info("/filter thinking → %s", state)
        await message.answer(f"💭 Thinking results: {state}")
    elif arg == "tools":
        notifications.brief_tool_output = not notifications.brief_tool_output
        save_notifications_config(notifications, config_file)
        state = "brief" if notifications.brief_tool_output else "full"
        logger.info("/filter tools → %s", state)
        await message.answer(f"🔧 Tool output: {state}")
    else:
        thinking = "on" if notifications.show_thinking_result else "off"
        tools = "brief" if notifications.brief_tool_output else "full"
        concise = "on" if notifications.concise_mode else "off"
        await message.answer(
            f"Current filters:\n"
            f"  💭 thinking results: {thinking}\n"
            f"  🔧 tool output: {tools}\n"
            f"  ⚡ concise mode: {concise}\n\n"
            f"Toggle: /filter thinking | /filter tools | /concise"
        )


async def settings_command(message: Message, notifications: NotificationsConfig) -> None:
    """Handle /settings — show current notification settings."""
    thinking = "on" if notifications.show_thinking_result else "off"
    tools = "brief" if notifications.brief_tool_output else "full"
    concise = "on" if notifications.concise_mode else "off"
    await message.answer(
        f"⚙️ Notification settings:\n"
        f"  💭 thinking results: {thinking}\n"
        f"  🔧 tool output: {tools}\n"
        f"  ⚡ concise mode: {concise}"
    )
