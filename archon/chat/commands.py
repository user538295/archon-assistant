"""Bot command handlers — /status, /stop, /clear, /restart, /notify, /settings,
/quiet, /normal, /verbose, /debug, /skills, /skill, /model, /context, /agents, /jobs,
/running_agents."""

import html
import logging
import os
import sys
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from archon.ai.agent_loader import AgentLoader
from archon.ai.plugin_loader import PluginLoader
from archon.ai.session_manager import SessionManager
from archon.ai.skill_loader import SkillLoader
from archon.config.loader import (
    ModelsConfig,
    NotificationsConfig,
    save_notifications_config,
)
from archon.version import __version__

if TYPE_CHECKING:
    from archon.ai.background_agent_manager import BackgroundAgentManager
    from archon.ai.cron_scheduler import CronScheduler

logger = logging.getLogger("archon")

# ──────────────────────────────────────────────────────────────────
# Inline keyboard helper
# ──────────────────────────────────────────────────────────────────

_MODES: list[tuple[str, str]] = [
    ("quiet", "🔇 Quiet"),
    ("normal", "🔔 Normal"),
    ("verbose", "📢 Verbose"),
    ("debug", "🔬 Debug"),
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
        row.append(
            InlineKeyboardButton(
                text=f"{label}{beacon}{mark}",
                callback_data=f"notify:{mode_id}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────────────────────────────────────
# Control commands
# ──────────────────────────────────────────────────────────────────


async def status_command(
    message: Message, session_manager: SessionManager, cwd: str
) -> None:
    """Handle /status — report session state, working directory, uptime and processing state."""
    user_id = message.from_user.id if message.from_user else 0
    if session_manager.has_session(user_id):
        started = session_manager.session_started_at(user_id)
        uptime = int(time.monotonic() - started) if started is not None else 0
        diag = session_manager.session_diagnostics(user_id)
        send_count = diag["send_count"] if diag else 0

        lines = [
            f"✅ Session active | v{__version__}",
            f"Working directory: {cwd}",
            f"Uptime: {uptime}s | Messages sent: {send_count}",
        ]

        if diag:
            if diag.get("is_processing"):
                proc_secs = diag.get("processing_seconds")
                if proc_secs is not None:
                    lines.append(f"🔄 Processing for {proc_secs:.1f}s")
                else:
                    lines.append("🔄 Processing")
            elif diag.get("idle_seconds") is not None:
                idle = diag["idle_seconds"]
                lines.append(f"💤 Idle for {idle:.1f}s")

        text = "\n".join(lines)
    else:
        text = f"ℹ️ No active session | v{__version__}"
    logger.info(
        "/status for user %d: %s",
        user_id,
        "active" if session_manager.has_session(user_id) else "inactive",
    )
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


async def stop_command(
    message: Message,
    session_manager: SessionManager,
    background_agent_manager: "BackgroundAgentManager | None" = None,
) -> None:
    """Handle /stop — terminate the user's active session and cancel background agents."""
    user_id = message.from_user.id if message.from_user else 0

    # Cancel running background agents for this user
    cancelled_count = 0
    if background_agent_manager is not None:
        running = background_agent_manager.list_running(user_id)
        for run in running:
            if await background_agent_manager.cancel(run.run_id):
                cancelled_count += 1

    has_session = session_manager.has_session(user_id)
    if not has_session and cancelled_count == 0:
        await message.answer("ℹ️ No active session")
        return

    if has_session:
        await session_manager.stop(user_id)

    logger.info("/stop for user %d (cancelled %d agents)", user_id, cancelled_count)

    parts = []
    if has_session:
        parts.append("Session stopped")
    if cancelled_count > 0:
        parts.append(
            f"{cancelled_count} background agent{'s' if cancelled_count != 1 else ''} cancelled"
        )
    await message.answer(f"✅ {', '.join(parts)}.")


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


def _fmt_context(stats: dict[str, Any]) -> str:
    """Format a usage-stats snapshot into a Telegram HTML message."""
    usage = stats.get("usage") or {}
    input_t = usage.get("input_tokens", 0)
    output_t = usage.get("output_tokens", 0)
    cache_r = usage.get("cache_read_input_tokens", 0)
    cache_c = usage.get("cache_creation_input_tokens", 0)
    cost = stats.get("total_cost_usd", 0.0)
    turns = stats.get("num_turns", 0)
    dur_s = stats.get("last_duration_ms", 0) / 1000

    # Context window = cumulative cache written across all turns + last turn's non-cached input.
    #
    # Why NOT use cache_read_input_tokens:
    #   Each Anthropic API call within one SDK query() reads the full cache.
    #   A turn with N tool calls produces: cache_read = N × context_size.
    #   Using cache_read would report N× the actual context (e.g. 554% for 14 tool calls).
    #
    # Why cumulative_cache_creation works:
    #   cache_creation_input_tokens only increases when new content is written to the cache.
    #   Summing it across all turns (tracked in ClaudeSession) gives the monotonically-growing
    #   context window size.  Adding the last turn's input_tokens covers non-cached user input.
    cumul_cc = stats.get("cumulative_cache_creation", 0)
    total_ctx = cumul_cc + input_t
    pct = round(100 * total_ctx / _CONTEXT_WINDOW_TOKENS)
    bar = _progress_bar(total_ctx, _CONTEXT_WINDOW_TOKENS)
    cost_str = f"${cost:.3f}" if cost >= 0.001 else f"${cost:.4f}"
    dur_str = f"{dur_s:.1f}s" if dur_s < 60 else f"{dur_s / 60:.1f}m"

    return (
        f"📊 <b>Context Window</b>\n\n"
        f"<code>[{bar}]</code> {pct}%\n"
        f"<b>{total_ctx:,} / {_CONTEXT_WINDOW_TOKENS:,} tokens</b>\n\n"
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


async def notify_command(
    message: Message, notifications: NotificationsConfig, config_file: str
) -> None:
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
                await message.answer(
                    f"⏱ Beacon interval: {notifications.interval_minutes} min"
                )
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
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_notify_keyboard(notifications)
        )  # type: ignore[union-attr]
    except TelegramBadRequest:
        pass  # markup unchanged — user tapped the already-active mode
    await callback.answer()


async def settings_command(
    message: Message, notifications: NotificationsConfig
) -> None:
    """Handle /settings — show inline keyboard (backward-compat alias for /notify)."""
    await message.answer(
        "⚙️ Notification mode",
        reply_markup=_notify_keyboard(notifications),
    )


# ──────────────────────────────────────────────────────────────────
# Quick-switch commands: /quiet [N], /normal, /verbose, /debug
# ──────────────────────────────────────────────────────────────────


async def quiet_command(
    message: Message, notifications: NotificationsConfig, config_file: str
) -> None:
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


async def normal_command(
    message: Message, notifications: NotificationsConfig, config_file: str
) -> None:
    """Handle /normal — switch to normal notification mode."""
    notifications.mode = "normal"
    save_notifications_config(notifications, config_file)
    logger.info("/normal")
    await message.answer("🔔 Normal mode", reply_markup=_notify_keyboard(notifications))


async def verbose_command(
    message: Message, notifications: NotificationsConfig, config_file: str
) -> None:
    """Handle /verbose — switch to verbose notification mode."""
    notifications.mode = "verbose"
    save_notifications_config(notifications, config_file)
    logger.info("/verbose")
    await message.answer(
        "📢 Verbose mode", reply_markup=_notify_keyboard(notifications)
    )


async def debug_command(
    message: Message, notifications: NotificationsConfig, config_file: str
) -> None:
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
        row.append(
            InlineKeyboardButton(
                text=f"{name}{mark}",
                callback_data=f"model:{name}",
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    # Always include a "Default (SDK)" button at the bottom
    default_mark = " ✓" if current is None else ""
    rows.append(
        [
            InlineKeyboardButton(
                text=f"Default (SDK){default_mark}",
                callback_data="model:default",
            )
        ]
    )
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
            await message.answer(
                label, reply_markup=_model_keyboard(models_config, current)
            )
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

    try:
        await callback.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=_model_keyboard(models_config, session_manager.get_model())
        )
    except TelegramBadRequest:
        pass  # markup unchanged — user tapped the already-active model
    await callback.answer()


# ──────────────────────────────────────────────────────────────────
# Agents command
# ──────────────────────────────────────────────────────────────────


async def agents_command(
    message: Message,
    agent_loader: AgentLoader | None = None,
) -> None:
    """Handle /agents — list all available agent types from filesystem.

    Agents are split into two sub-groups:

    * 🤖 **Archon agents** (name ends with ``-archon``) — included in every session.
    * 🔍 **Other agents** — present in the agents directory but TUI-only; not
      injected into Claude sessions by Archon.
    """
    filesystem_agents = agent_loader.load_all() if agent_loader else []
    archon_agents = [a for a in filesystem_agents if a.is_archon]
    other_agents = [a for a in filesystem_agents if not a.is_archon]

    if not filesystem_agents:
        await message.answer(
            "ℹ️ No agent types configured.\n\n"
            "Add <code>name-archon.md</code> files to <code>~/.claude/agents/</code>."
        )
        return

    lines: list[str] = []

    if archon_agents:
        lines.append("🤖 <b>Archon agents</b> <i>(active in sessions)</i>:\n")
        for agent in archon_agents:
            model_str = (
                f" (<code>{html.escape(agent.model)}</code>)" if agent.model else ""
            )
            tools_str = (
                f"\n  🔧 Tools: <code>{html.escape(', '.join(agent.tools))}</code>"
                if agent.tools
                else ""
            )
            lines.append(
                f"• <b>{html.escape(agent.name)}</b>{model_str}\n  {html.escape(agent.description)}{tools_str}"
            )

    if other_agents:
        if lines:
            lines.append("")
        lines.append("🔍 <b>Other agents</b> <i>(TUI-only, not injected)</i>:\n")
        for agent in other_agents:
            model_str = (
                f" (<code>{html.escape(agent.model)}</code>)" if agent.model else ""
            )
            tools_str = (
                f"\n  🔧 Tools: <code>{html.escape(', '.join(agent.tools))}</code>"
                if agent.tools
                else ""
            )
            lines.append(
                f"• <b>{html.escape(agent.name)}</b>{model_str}\n  {html.escape(agent.description)}{tools_str}"
            )

    logger.info(
        "/agents listed %d archon + %d other agents",
        len(archon_agents),
        len(other_agents),
    )
    await message.answer("\n".join(lines))


# ──────────────────────────────────────────────────────────────────
# Cron jobs command
# ──────────────────────────────────────────────────────────────────


async def jobs_command(
    message: Message,
    cron_scheduler: "CronScheduler | None" = None,
) -> None:
    """Handle /jobs — list scheduled cron jobs and their runtime status."""
    if cron_scheduler is None:
        await message.answer("ℹ️ Cron scheduler not configured.")
        return

    cron_scheduler.reload_jobs()

    statuses = cron_scheduler.job_statuses
    if not statuses:
        await message.answer("ℹ️ No cron jobs configured.")
        return

    next_runs = cron_scheduler.next_run_times()
    today = datetime.now().date()

    lines: list[str] = ["📅 <b>Cron Jobs</b>\n"]
    job_config_map = {j.name: j for j in cron_scheduler.job_configs}
    for name, s in statuses.items():
        job_config = job_config_map.get(name)

        if job_config and job_config.validation_error:
            state = "⚠️ invalid config"
        elif s.is_running:
            state = "🔄 running"
        elif s.last_run is not None:
            state = f"✅ {s.last_run.strftime('%H:%M:%S %Z')}"
        else:
            state = "⏳ waiting"

        lines.append(f"• <b>{html.escape(name)}</b>: {state} (runs: {s.run_count})")

        if job_config and job_config.validation_error:
            lines.append(
                f"  ❌ Config error: {html.escape(job_config.validation_error[:120])}"
            )
        elif s.last_error:
            lines.append(f"  ❌ {html.escape(s.last_error[:120])}")
        elif s.last_result:
            preview = s.last_result[:120].replace("\n", " ")
            lines.append(f"  └ <code>{html.escape(preview)}</code>")

        next_dt = next_runs.get(name)
        if next_dt is None:
            if job_config and job_config.validation_error:
                lines.append("  ⏭ next: fix config to enable")
            else:
                lines.append("  ⏭ next: disabled")
        elif next_dt.date() == today:
            lines.append(f"  ⏭ next: {next_dt.strftime('%H:%M %Z')}")
        else:
            lines.append(f"  ⏭ next: {next_dt.strftime('%b %d %H:%M %Z')}")

    logger.info("/jobs listed %d job(s)", len(statuses))
    await message.answer("\n".join(lines), parse_mode="HTML")


# ──────────────────────────────────────────────────────────────────
# Background agents commands — S15.4
# ──────────────────────────────────────────────────────────────────


async def running_agents_command(
    message: Message,
    background_agent_manager: "BackgroundAgentManager | None" = None,
) -> None:
    """Handle /running_agents — list active background agents with Cancel buttons."""
    user_id = message.from_user.id if message.from_user else 0

    if background_agent_manager is None:
        await message.answer("ℹ️ Background agents not enabled")
        return

    running = background_agent_manager.list_running(user_id)
    if not running:
        await message.answer("ℹ️ No background agents running")
        return

    now = time.monotonic()
    lines: list[str] = ["🤖 <b>Running agents:</b>\n"]
    rows: list[list[InlineKeyboardButton]] = []

    for run in running:
        elapsed = int(now - run.started_at)
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"
        task_snippet = run.task[:60] + "..." if len(run.task) > 60 else run.task
        lines.append(
            f"• <b>{html.escape(run.name)}</b> ({elapsed_str})\n"
            f"  <code>{html.escape(task_snippet)}</code>"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"❌ Cancel {run.name}",
                    callback_data=f"cancel_agent:{run.run_id}",
                )
            ]
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=rows)
    logger.info("/running_agents for user %d: %d agent(s)", user_id, len(running))
    await message.answer("\n".join(lines), reply_markup=keyboard)


async def cancel_agent_callback(
    callback: CallbackQuery,
    background_agent_manager: "BackgroundAgentManager | None" = None,
) -> None:
    """Handle inline keyboard taps: callback_data='cancel_agent:<run_id>'."""
    data = callback.data or ""
    run_id = data.removeprefix("cancel_agent:")

    if background_agent_manager is None:
        await callback.answer("ℹ️ Background agents not enabled")
        return

    run = background_agent_manager.get_run(run_id)
    if run is None:
        await callback.answer("❌ Agent not found")
        return

    cancelled = await background_agent_manager.cancel(run_id)
    if cancelled:
        logger.info("cancel_agent_callback: run %s (%s) cancelled", run_id, run.name)
        await callback.answer(f"✅ Agent {run.name} cancellation requested")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    else:
        await callback.answer("❌ Agent not found")
