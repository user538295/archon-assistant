"""Bot command handlers — /status and /stop."""
import logging
import time

from aiogram.types import Message

from archon.ai.session_manager import SessionManager

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
