"""Startup notification broadcast to all whitelisted Telegram users."""
import html
import logging
from datetime import datetime

from aiogram import Bot

logger = logging.getLogger("archon")


async def send_startup_notification(
    bot: Bot,
    allowed_user_ids: list[int],
    *,
    mode: str,
    version: str,
    skill_count: int,
    plugin_count: int,
    agent_count: int,
    job_count: int,
    restart_chat_id: int | None = None,
) -> None:
    """Broadcast startup notification to all whitelisted users.

    Args:
        bot: aiogram Bot instance
        allowed_user_ids: list of Telegram user IDs to notify
        mode: notification mode (quiet/normal/verbose/debug)
        version: Archon version string
        skill_count, plugin_count, agent_count, job_count: loaded item counts
        restart_chat_id: if set, this user already received a /restart ack --
                          skip sending them the standalone notification
    """
    if mode == "quiet":
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    safe_version = html.escape(version)

    lines = [
        f"\U0001f680 <b>Archon started</b>",
        f"Version: {safe_version}",
        timestamp,
    ]

    if mode in ("verbose", "debug"):
        lines.append(
            f"Skills: {skill_count} \u00b7 Plugins: {plugin_count} "
            f"\u00b7 Agents: {agent_count} \u00b7 Jobs: {job_count}"
        )

    message = "\n".join(lines)

    for user_id in allowed_user_ids:
        if user_id == restart_chat_id:
            continue
        try:
            await bot.send_message(user_id, message, parse_mode="HTML")
        except Exception:
            logger.warning(
                "Failed to send startup notification to user %d",
                user_id,
                exc_info=True,
            )
