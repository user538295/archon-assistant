"""Bot command handlers — /status, /stop, /clear, /restart, /notify, /settings,
/quiet, /normal, /verbose, /debug, /skills, /skill, /model, /context, /agents."""
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

from archon.ai.plugin_loader import PluginLoader
from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import SkillLoader
from archon.config.loader import AgentsConfig, ModelsConfig, NotificationsConfig, save_notifications_config

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
# /context — context window usage
# ──────────────────────────────────────────────────────────────────

_CONTEXT_WINDOW_TOKENS = 200_000  # all current Claude models


def _progress_bar(current: int, total: int, width: int = 20) -> str:
    """Return a Unicode block progress bar of the given width."""
    if total <= 0:
        return "░" * width
    filled = round(width * current / total)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _fmt_context(stats: dict) -> str:
    """Format a usage-stats snapshot into a Telegram HTML message."""
    usage    = stats.get("usage") or {}
    input_t  = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    cache_r  = usage.get("cache_read_input_tokens", 0)
    cache_c  = usage.get("cache_creation_input_tokens", 0)
    cost     = stats.get("total_cost_usd", 0.0)
    turns    = stats.get("num_turns", 0)
    dur_s    = stats.get("last_duration_ms", 0) / 1000

    pct      = round(100 * input_t / _CONTEXT_WINDOW_TOKENS)
    bar      = _progress_bar(input_t, _CONTEXT_WINDOW_TOKENS)
    cost_str = f"${cost:.3f}" if cost >= 0.001 else f"${cost:.4f}"
    dur_str  = f"{dur_s:.1f}s" if dur_s < 60 else f"{dur_s / 60:.1f}m"

    return (
        f"📊 <b>Context Window</b>\n\n"
        f"<code>[{bar}]</code> {pct}%\n"
        f"<b>{input_t:,} / {_CONTEXT_WINDOW_TOKENS:,} tokens</b>\n\n"
        f"📥 Input:       {input_t:>8,} t\n"
        f"📤 Output:      {output_t:>8,} t\n"
        f"♻️ Cache read:  {cache_r:>8,} t\n"
        f"🆕 Cache new:   {cache_c:>8,} t\n\n"
        f"🔄 {turns} turns  💰 {cost_str}  ⏱ {dur_str}"
    )


async def context_command(message: Message, session_manager: SessionManager) -> None:
    """Handle /context — show context window usage (token counts, cost, turns)."""
    user_id = message.from_user.id if message.from_user else 0
    if not session_manager.has_session(user_id):
        logger.info("/context for user %d: no session", user_id)
        await message.answer("ℹ️ No active session")
        return
    stats = session_manager.context_stats(user_id)
    if stats is None:
        logger.info("/context for user %d: no data yet", user_id)
        await message.answer("📊 No context data yet — send a message first")
        return
    logger.info(
        "/context for user %d: %s input tokens",
        user_id,
        (stats.get("usage") or {}).get("input_tokens", 0),
    )
    await message.answer(_fmt_context(stats))


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


async def skills_command(
    message: Message,
    skill_loader: SkillLoader,
    plugin_loader: PluginLoader | None = None,
) -> None:
    """Handle /skills — list personal and plugin-bundled skills with descriptions."""
    personal = skill_loader.load_all()
    plugin_infos = plugin_loader.load_all() if plugin_loader else []
    plugin_skills = plugin_loader.get_skills() if plugin_loader else []

    if not personal and not plugin_skills:
        await message.answer("No skills available.")
        return

    lines: list[str] = []

    if personal:
        lines.append("🎯 <b>Personal skills:</b>\n")
        for skill in personal:
            lines.append(f"• <b>{skill.name}</b>\n  {skill.description}")

    if plugin_infos:
        if lines:
            lines.append("")
        lines.append("🔌 <b>Plugin skills:</b>\n")
        for plugin in plugin_infos:
            if plugin.skills:
                lines.append(f"<i>[{plugin.key} v{plugin.version}]</i>")
                for s in plugin.skills:
                    lines.append(f"• <b>{s.name}</b>\n  {s.description}")

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


# ──────────────────────────────────────────────────────────────────
# Model command
# ──────────────────────────────────────────────────────────────────


def _model_keyboard(models: ModelsConfig, current: str | None) -> InlineKeyboardMarkup:
    """Build an inline keyboard listing all configured models.

    Models are shown in two columns.  The currently active model (or the
    Default button when no override is set) is check-marked.
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for name in models.available:
        mark = " ✓" if name == current else ""
        row.append(InlineKeyboardButton(
            text=f"{name}{mark}",
            callback_data=f"model:{name}",
        ))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # Always include a "Default (SDK)" button at the bottom
    default_mark = " ✓" if current is None else ""
    rows.append([InlineKeyboardButton(
        text=f"Default (SDK){default_mark}",
        callback_data="model:default",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def model_command(
    message: Message,
    session_manager: SessionManager,
    models_config: ModelsConfig,
) -> None:
    """Handle /model [name|default] — show or switch the Claude model.

    Usage:
      /model              — show inline keyboard (if models list configured) or
                            print the current model override
      /model <name>       — switch to a named model (clears the active session)
      /model default      — revert to the SDK default model (clears the active session)
    """
    user_id = message.from_user.id if message.from_user else 0
    parts = (message.text or "").split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        current = session_manager.get_model()
        if models_config.available:
            label = (
                f"🤖 Current: <code>{current}</code>"
                if current
                else "🤖 Current: <i>default (SDK)</i>"
            )
            await message.answer(label, reply_markup=_model_keyboard(models_config, current))
        else:
            if current:
                await message.answer(f"🤖 Current model: <code>{current}</code>")
            else:
                await message.answer("🤖 Current model: <i>default (SDK)</i>")
        return

    arg = parts[1].strip()

    if arg.lower() in ("default", "reset", "none"):
        session_manager.set_model(None)
        if session_manager.has_session(user_id):
            await session_manager.stop(user_id)
        logger.info("/model → default for user %d", user_id)
        await message.answer("🤖 Model reset to <i>default (SDK)</i>. Session cleared.")
    else:
        session_manager.set_model(arg)
        if session_manager.has_session(user_id):
            await session_manager.stop(user_id)
        logger.info("/model → %s for user %d", arg, user_id)
        await message.answer(f"🤖 Model set to <code>{arg}</code>. Session cleared.")


async def model_callback(
    callback: CallbackQuery,
    session_manager: SessionManager,
    models_config: ModelsConfig,
) -> None:
    """Handle inline keyboard taps: callback_data='model:<name|default>'."""
    user_id = callback.from_user.id if callback.from_user else 0
    data = callback.data or ""
    name = data.removeprefix("model:")

    if name.lower() in ("default", "reset", "none"):
        session_manager.set_model(None)
        if session_manager.has_session(user_id):
            await session_manager.stop(user_id)
        logger.info("model_callback → default for user %d", user_id)
    else:
        session_manager.set_model(name)
        if session_manager.has_session(user_id):
            await session_manager.stop(user_id)
        logger.info("model_callback → %s for user %d", name, user_id)

    await callback.message.edit_reply_markup(
        reply_markup=_model_keyboard(models_config, session_manager.get_model())
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────────────
# Agents command
# ──────────────────────────────────────────────────────────────────


async def agents_command(
    message: Message,
    agents_config: AgentsConfig | None = None,
) -> None:
    """Handle /agents — list defined custom agent types with descriptions and tools."""
    if (
        agents_config is None
        or not agents_config.enabled
        or not agents_config.definitions
    ):
        await message.answer("ℹ️ No agent types configured.\n\nAdd <code>[agents]</code> definitions to <code>config.toml</code> to create a custom agent team.")
        return

    lines: list[str] = ["🤖 <b>Agent team:</b>\n"]
    for defn in agents_config.definitions:
        model_str = f" (<code>{defn.model}</code>)" if defn.model else ""
        tools_str = (
            f"\n  🔧 Tools: <code>{', '.join(defn.tools)}</code>"
            if defn.tools
            else ""
        )
        lines.append(
            f"• <b>{defn.name}</b>{model_str}\n"
            f"  {defn.description}{tools_str}"
        )

    logger.info("/agents listed %d definitions", len(agents_config.definitions))
    await message.answer("\n".join(lines))
