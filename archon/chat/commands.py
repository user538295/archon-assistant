"""Bot command handlers — /status, /stop, /clear, /restart, /notify, /settings,
/quiet, /normal, /verbose, /debug, /skills, /skill."""
import logging
import os
import sys
import time

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import SkillLoader
from archon.config.loader import NotificationsConfig, save_notifications_config

logger = logging.getLogger("archon")

# ──────────────────────────────────────────────────────────────────
# Inline keyboard helper
# ──────────────────────────────────────────────────────────────────

_MODES: list[tuple[str, str]] = [
    ("quiet",   "🔇 Quiet"),
    ("normal",  "🔔 Normal"),
    ("verbose", "📢 Verbose"),
    ("debug",   "🔬 Debug"),
]

_VALID_MODES: frozenset[str] = frozenset(m for m, _ in _MODES)


def _notify_keyboard(notifications: NotificationsConfig) -> InlineKeyboardMarkup:
    """Build a 2×2 inline keyboard with the active mode check-marked.

    When quiet mode is active and a beacon interval is configured the Quiet
    button shows the interval, e.g. ``🔇 Quiet 🔦2m ✓``.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for mode_id, label in _MODES:
        beacon = (
            f" 🔦{notifications.interval_minutes}m"
            if mode_id == "quiet" and notifications.interval_minutes > 0
            else ""
        )
        mark = " ✓" if mode_id == notifications.mode else ""
        row.append(InlineKeyboardButton(
            text=f"{label}{beacon}{mark}",
            callback_data=f"notify:{mode_id}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────────────────────────────────────
# Control commands
# ──────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────
# Notification commands
# ──────────────────────────────────────────────────────────────────


async def notify_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /notify [quiet [N] | normal | verbose | debug | interval N].

    Subcommands:
      quiet [N]   — set quiet mode; optional N sets the beacon interval in minutes
                    (0 = no beacon)
      normal      — set normal mode
      verbose     — set verbose mode
      debug       — set debug mode
      interval N  — change beacon interval without changing mode
      (no arg)    — show inline keyboard panel
    """
    parts = (message.text or "").split(maxsplit=2)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if arg in _VALID_MODES:
        notifications.mode = arg
        if arg == "quiet" and len(parts) == 3:
            try:
                notifications.interval_minutes = int(parts[2])
            except ValueError:
                pass  # invalid number — keep current interval
        save_notifications_config(notifications, config_file)
        logger.info("/notify → mode: %s", notifications.mode)
        if arg == "quiet" and notifications.interval_minutes > 0:
            reply = f"🔇 Quiet mode — beacon every {notifications.interval_minutes} min"
        elif arg == "quiet":
            reply = "🔇 Quiet mode"
        else:
            labels = {m: lbl for m, lbl in _MODES}
            reply = f"{labels[arg]} mode"
        await message.answer(reply)

    elif arg == "interval":
        if len(parts) == 3:
            try:
                notifications.interval_minutes = int(parts[2])
                save_notifications_config(notifications, config_file)
                logger.info("/notify interval → %d min", notifications.interval_minutes)
                await message.answer(f"⏱ Beacon interval: {notifications.interval_minutes} min")
                return
            except ValueError:
                pass  # fall through to keyboard
        # No valid number provided — show keyboard
        await message.answer(
            "⚙️ Notification mode",
            reply_markup=_notify_keyboard(notifications),
        )

    else:
        # No arg or unrecognised — show inline keyboard panel
        await message.answer(
            "⚙️ Notification mode",
            reply_markup=_notify_keyboard(notifications),
        )


async def notify_callback(
    callback: CallbackQuery,
    notifications: NotificationsConfig,
    config_file: str,
) -> None:
    """Handle inline keyboard taps: callback_data='notify:<mode>'."""
    data = callback.data or ""
    mode = data.removeprefix("notify:")
    if mode in _VALID_MODES:
        notifications.mode = mode
        save_notifications_config(notifications, config_file)
        logger.info("notify_callback → mode: %s", mode)
    await callback.message.edit_reply_markup(reply_markup=_notify_keyboard(notifications))
    await callback.answer()


async def settings_command(message: Message, notifications: NotificationsConfig) -> None:
    """Handle /settings — show inline keyboard (backward-compat alias for /notify)."""
    await message.answer(
        "⚙️ Notification mode",
        reply_markup=_notify_keyboard(notifications),
    )


# ──────────────────────────────────────────────────────────────────
# Quick-switch commands: /quiet [N], /normal, /verbose, /debug
# ──────────────────────────────────────────────────────────────────


async def quiet_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /quiet [N] — switch to quiet mode; N sets beacon interval in minutes."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        try:
            notifications.interval_minutes = int(parts[1])
        except ValueError:
            pass  # invalid number — keep current interval
    notifications.mode = "quiet"
    save_notifications_config(notifications, config_file)
    logger.info("/quiet → interval_minutes=%d", notifications.interval_minutes)
    if notifications.interval_minutes > 0:
        reply = f"🔇 Quiet mode — beacon every {notifications.interval_minutes} min"
    else:
        reply = "🔇 Quiet mode"
    await message.answer(reply, reply_markup=_notify_keyboard(notifications))


async def normal_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /normal — switch to normal notification mode."""
    notifications.mode = "normal"
    save_notifications_config(notifications, config_file)
    logger.info("/normal")
    await message.answer("🔔 Normal mode", reply_markup=_notify_keyboard(notifications))


async def verbose_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /verbose — switch to verbose notification mode."""
    notifications.mode = "verbose"
    save_notifications_config(notifications, config_file)
    logger.info("/verbose")
    await message.answer("📢 Verbose mode", reply_markup=_notify_keyboard(notifications))


async def debug_command(message: Message, notifications: NotificationsConfig, config_file: str) -> None:
    """Handle /debug — switch to debug notification mode."""
    notifications.mode = "debug"
    save_notifications_config(notifications, config_file)
    logger.info("/debug")
    await message.answer("🔬 Debug mode", reply_markup=_notify_keyboard(notifications))


# ──────────────────────────────────────────────────────────────────
# Skills commands — S6.1
# ──────────────────────────────────────────────────────────────────


async def skills_command(message: Message, skill_loader: SkillLoader) -> None:
    """Handle /skills — list all available skills with their descriptions."""
    skills = skill_loader.load_all()
    if not skills:
        await message.answer("No skills available.")
        return
    lines = ["🎯 <b>Available skills:</b>\n"]
    for skill in skills:
        lines.append(f"• <b>{skill.name}</b>\n  {skill.description}")
    await message.answer("\n".join(lines))


async def skill_command(
    message: Message,
    session_manager: SessionManager,
    skill_loader: SkillLoader,
) -> None:
    """Handle /skill <name> — activate a named skill for the current session."""
    user_id = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer("Usage: /skill &lt;name&gt;")
        return

    skill_name = parts[1].strip()
    skill = skill_loader.get(skill_name)

    if skill is None:
        logger.info("/skill %s — unknown skill for user %d", skill_name, user_id)
        await message.answer(
            f"❌ Unknown skill <code>{skill_name}</code>. Use /skills to see available skills"
        )
        return

    if not session_manager.has_session(user_id):
        logger.info("/skill %s — no session for user %d", skill_name, user_id)
        await message.answer("No active session. Send a message first to start one")
        return

    session = await session_manager.get_or_create(user_id)
    session.activate_skill(skill)
    logger.info("/skill %s activated for user %d", skill_name, user_id)
    await message.answer(
        f"✅ Skill <code>{skill_name}</code> activated — it will be applied to your next message"
    )
