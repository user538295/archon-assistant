"""Tests for startup notification broadcast."""
import asyncio
import html
import logging
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from aiogram import Bot, Dispatcher

from archon.gateway.startup_notification import send_startup_notification


_FAKE_TOKEN = "12345:AAFakeTokenForTestingPurposesOnly123"


def _make_bot() -> MagicMock:
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()
    return bot


# ──────────────────────────────────────────────────────────────────
# 1. Sends to all whitelisted users
# ──────────────────────────────────────────────────────────────────


async def test_sends_to_all_whitelisted_users() -> None:
    bot = _make_bot()
    user_ids = [100, 200, 300]

    await send_startup_notification(
        bot, user_ids,
        mode="normal", version="26.3.364",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
    )

    assert bot.send_message.await_count == 3
    sent_ids = [call.args[0] for call in bot.send_message.call_args_list]
    assert sorted(sent_ids) == [100, 200, 300]


# ──────────────────────────────────────────────────────────────────
# 2. Suppressed in quiet mode
# ──────────────────────────────────────────────────────────────────


async def test_quiet_mode_sends_nothing() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100, 200],
        mode="quiet", version="26.3.364",
        skill_count=5, plugin_count=2, agent_count=3, job_count=4,
    )

    bot.send_message.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# 3. Normal mode — base message (version + timestamp, no counts)
# ──────────────────────────────────────────────────────────────────


async def test_normal_mode_base_message() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100],
        mode="normal", version="26.3.364",
        skill_count=5, plugin_count=2, agent_count=3, job_count=4,
    )

    bot.send_message.assert_awaited_once()
    text = bot.send_message.call_args.args[1]
    assert "Archon started" in text
    assert "26.3.364" in text
    # Normal mode must NOT include counts
    assert "Skills:" not in text
    assert "Plugins:" not in text


# ──────────────────────────────────────────────────────────────────
# 4. Verbose mode — rich message (includes counts)
# ──────────────────────────────────────────────────────────────────


async def test_verbose_mode_rich_message() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100],
        mode="verbose", version="26.3.364",
        skill_count=5, plugin_count=2, agent_count=3, job_count=4,
    )

    text = bot.send_message.call_args.args[1]
    assert "Archon started" in text
    assert "26.3.364" in text
    assert "Skills: 5" in text
    assert "Plugins: 2" in text
    assert "Agents: 3" in text
    assert "Jobs: 4" in text


# ──────────────────────────────────────────────────────────────────
# 5. Debug mode — rich message (includes counts)
# ──────────────────────────────────────────────────────────────────


async def test_debug_mode_rich_message() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100],
        mode="debug", version="26.3.364",
        skill_count=10, plugin_count=0, agent_count=1, job_count=7,
    )

    text = bot.send_message.call_args.args[1]
    assert "Skills: 10" in text
    assert "Plugins: 0" in text
    assert "Agents: 1" in text
    assert "Jobs: 7" in text


# ──────────────────────────────────────────────────────────────────
# 6. Per-user error isolation
# ──────────────────────────────────────────────────────────────────


async def test_per_user_error_isolation() -> None:
    """If sending to user A fails, user B still gets the message."""
    bot = _make_bot()
    bot.send_message = AsyncMock(
        side_effect=[Exception("API error"), None]
    )

    await send_startup_notification(
        bot, [100, 200],
        mode="normal", version="26.3.364",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
    )

    assert bot.send_message.await_count == 2


# ──────────────────────────────────────────────────────────────────
# 7. Logs warning on send failure
# ──────────────────────────────────────────────────────────────────


async def test_logs_warning_on_send_failure() -> None:
    bot = _make_bot()
    bot.send_message = AsyncMock(side_effect=RuntimeError("network down"))

    with patch("archon.gateway.startup_notification.logger") as mock_logger:
        await send_startup_notification(
            bot, [42],
            mode="normal", version="26.3.364",
            skill_count=0, plugin_count=0, agent_count=0, job_count=0,
        )

    mock_logger.warning.assert_called_once()
    # Verify user ID is mentioned in the warning
    call_args = mock_logger.warning.call_args
    assert 42 in call_args.args


# ──────────────────────────────────────────────────────────────────
# 8. restart_chat_id user is skipped
# ──────────────────────────────────────────────────────────────────


async def test_restart_chat_id_user_skipped() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100, 200, 300],
        mode="normal", version="26.3.364",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
        restart_chat_id=200,
    )

    assert bot.send_message.await_count == 2
    sent_ids = [call.args[0] for call in bot.send_message.call_args_list]
    assert 200 not in sent_ids
    assert sorted(sent_ids) == [100, 300]


# ──────────────────────────────────────────────────────────────────
# 9. Empty allowed_user_ids — no error
# ──────────────────────────────────────────────────────────────────


async def test_empty_user_ids_no_error() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [],
        mode="normal", version="26.3.364",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
    )

    bot.send_message.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# 10. Version string with special HTML chars is escaped
# ──────────────────────────────────────────────────────────────────


async def test_html_chars_in_version_escaped() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100],
        mode="normal", version="<script>alert('xss')</script>",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
    )

    text = bot.send_message.call_args.args[1]
    # Raw HTML must not appear — should be escaped
    assert "<script>" not in text
    assert html.escape("<script>alert('xss')</script>") in text


# ──────────────────────────────────────────────────────────────────
# 11. parse_mode="HTML" is used
# ──────────────────────────────────────────────────────────────────


async def test_uses_html_parse_mode() -> None:
    bot = _make_bot()

    await send_startup_notification(
        bot, [100],
        mode="normal", version="26.3.364",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
    )

    kwargs = bot.send_message.call_args.kwargs
    assert kwargs.get("parse_mode") == "HTML"


# ──────────────────────────────────────────────────────────────────
# 12. Message contains timestamp
# ──────────────────────────────────────────────────────────────────


async def test_message_contains_timestamp() -> None:
    """The message must contain a YYYY-MM-DD HH:MM timestamp."""
    bot = _make_bot()

    await send_startup_notification(
        bot, [100],
        mode="normal", version="26.3.364",
        skill_count=0, plugin_count=0, agent_count=0, job_count=0,
    )

    import re
    text = bot.send_message.call_args.args[1]
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text)


# ══════════════════════════════════════════════════════════════════
# Gateway integration tests — startup hook + restart ack wiring
# ══════════════════════════════════════════════════════════════════

from archon.gateway.gateway import (
    _notify_restart,
    _register_restart_notification,
    _register_startup_notification,
)


# ──────────────────────────────────────────────────────────────────
# 13. Startup hook registered — calls guard then broadcast
# ──────────────────────────────────────────────────────────────────


async def test_startup_hook_calls_guard_then_broadcast() -> None:
    """The registered startup hook must check the crash-loop guard,
    and if it returns True, call send_startup_notification."""
    dp = Dispatcher()

    _register_startup_notification(
        dp,
        allowed_user_ids=[100, 200],
        mode="normal",
        version="26.3.364",
        skill_count=5,
        plugin_count=2,
        agent_count=3,
        job_count=4,
        restart_chat_id=None,
    )

    bot = _make_bot()

    with patch(
        "archon.gateway.gateway.should_send_startup_notification",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_guard, patch(
        "archon.gateway.gateway.send_startup_notification",
        new_callable=AsyncMock,
    ) as mock_send:
        await dp.startup.trigger(bot)

    mock_guard.assert_awaited_once()
    mock_send.assert_awaited_once_with(
        bot,
        [100, 200],
        mode="normal",
        version="26.3.364",
        skill_count=5,
        plugin_count=2,
        agent_count=3,
        job_count=4,
        restart_chat_id=None,
    )


# ──────────────────────────────────────────────────────────────────
# 14. Crash-loop guard returns False — notification skipped
# ──────────────────────────────────────────────────────────────────


async def test_crash_loop_skips_notification() -> None:
    """When should_send_startup_notification returns False,
    send_startup_notification must NOT be called."""
    dp = Dispatcher()

    _register_startup_notification(
        dp,
        allowed_user_ids=[100],
        mode="normal",
        version="26.3.364",
        skill_count=0,
        plugin_count=0,
        agent_count=0,
        job_count=0,
        restart_chat_id=None,
    )

    bot = _make_bot()

    with patch(
        "archon.gateway.gateway.should_send_startup_notification",
        new_callable=AsyncMock,
        return_value=False,
    ), patch(
        "archon.gateway.gateway.send_startup_notification",
        new_callable=AsyncMock,
    ) as mock_send:
        await dp.startup.trigger(bot)

    mock_send.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# 15. Restart ack includes startup info (version + timestamp)
# ──────────────────────────────────────────────────────────────────


async def test_restart_ack_includes_version_and_timestamp() -> None:
    """_notify_restart must include version and a YYYY-MM-DD HH:MM timestamp."""
    import re

    bot = _make_bot()

    await _notify_restart(bot, 42, version="26.3.364", mode="normal")

    text = bot.send_message.call_args.args[1]
    assert "Restarted" in text
    assert "26.3.364" in text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text)


# ──────────────────────────────────────────────────────────────────
# 16. Restart ack includes counts in verbose/debug mode
# ──────────────────────────────────────────────────────────────────


async def test_restart_ack_includes_counts_in_verbose_mode() -> None:
    """In verbose mode, the restart ack must include loader counts."""
    bot = _make_bot()

    await _notify_restart(
        bot, 42,
        version="26.3.364",
        mode="verbose",
        skill_count=5,
        plugin_count=2,
        agent_count=3,
        job_count=4,
    )

    text = bot.send_message.call_args.args[1]
    assert "Skills: 5" in text
    assert "Plugins: 2" in text
    assert "Agents: 3" in text
    assert "Jobs: 4" in text


async def test_restart_ack_no_counts_in_normal_mode() -> None:
    """In normal mode, the restart ack must NOT include loader counts."""
    bot = _make_bot()

    await _notify_restart(
        bot, 42,
        version="26.3.364",
        mode="normal",
        skill_count=5,
        plugin_count=2,
        agent_count=3,
        job_count=4,
    )

    text = bot.send_message.call_args.args[1]
    assert "Skills:" not in text


# ──────────────────────────────────────────────────────────────────
# 17. Both hooks fire on /restart boot — restart user skipped
#     from broadcast, but gets restart ack
# ──────────────────────────────────────────────────────────────────


async def test_both_hooks_fire_on_restart_boot() -> None:
    """On /restart boot, the restart ack goes to the restart user,
    and the startup broadcast skips that user."""
    dp = Dispatcher()
    restart_chat_id = "200"

    _register_restart_notification(
        dp, restart_chat_id,
        version="26.3.364",
        mode="normal",
    )
    _register_startup_notification(
        dp,
        allowed_user_ids=[100, 200, 300],
        mode="normal",
        version="26.3.364",
        skill_count=0,
        plugin_count=0,
        agent_count=0,
        job_count=0,
        restart_chat_id=200,
    )

    bot = _make_bot()

    with patch(
        "archon.gateway.gateway.should_send_startup_notification",
        new_callable=AsyncMock,
        return_value=True,
    ), patch(
        "archon.gateway.gateway.send_startup_notification",
        new_callable=AsyncMock,
    ) as mock_broadcast:
        await dp.startup.trigger(bot)

    # Restart ack sent to user 200
    restart_calls = [
        c for c in bot.send_message.call_args_list
        if c.args[0] == 200
    ]
    assert len(restart_calls) == 1
    assert "Restarted" in restart_calls[0].args[1]

    # Broadcast called with restart_chat_id=200 so user 200 is skipped
    mock_broadcast.assert_awaited_once()
    assert mock_broadcast.call_args.kwargs["restart_chat_id"] == 200


# ──────────────────────────────────────────────────────────────────
# 18. _register_startup_notification adds exactly one hook
# ──────────────────────────────────────────────────────────────────


def test_register_startup_notification_adds_one_hook() -> None:
    """_register_startup_notification must register exactly one startup hook."""
    dp = Dispatcher()
    before = len(dp.startup.handlers)

    _register_startup_notification(
        dp,
        allowed_user_ids=[100],
        mode="normal",
        version="26.3.364",
        skill_count=0,
        plugin_count=0,
        agent_count=0,
        job_count=0,
        restart_chat_id=None,
    )

    assert len(dp.startup.handlers) == before + 1


# ──────────────────────────────────────────────────────────────────
# 19. Quiet mode — _register_startup_notification is a no-op
# ──────────────────────────────────────────────────────────────────


def test_register_startup_notification_quiet_mode_no_hook() -> None:
    """In quiet mode, no startup hook should be registered."""
    dp = Dispatcher()
    before = len(dp.startup.handlers)

    _register_startup_notification(
        dp,
        allowed_user_ids=[100],
        mode="quiet",
        version="26.3.364",
        skill_count=0,
        plugin_count=0,
        agent_count=0,
        job_count=0,
        restart_chat_id=None,
    )

    assert len(dp.startup.handlers) == before


# ──────────────────────────────────────────────────────────────────
# 20. Deprecated history_collection — notification sent to users
# ──────────────────────────────────────────────────────────────────


from archon.gateway.gateway import _register_deprecated_search_notification


async def test_gateway_sends_notification_on_deprecated_history_collection() -> None:
    """When deprecated_history_collection is True, a warning notification is sent to all users."""
    dp = Dispatcher()

    _register_deprecated_search_notification(dp, allowed_user_ids=[100, 200])

    bot = _make_bot()
    await dp.startup.trigger(bot)

    assert bot.send_message.await_count == 2
    sent_ids = [c.args[0] for c in bot.send_message.call_args_list]
    assert sorted(sent_ids) == [100, 200]
    # Check message content mentions the deprecated key
    text = bot.send_message.call_args_list[0].args[1]
    assert "history_collection" in text


async def test_gateway_no_notification_when_flag_false() -> None:
    """When deprecated_history_collection is False, no extra notification is sent."""
    dp = Dispatcher()

    # Nothing registered — so no hook fires
    bot = _make_bot()
    await dp.startup.trigger(bot)

    bot.send_message.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# 21/22. Gateway._run() wires the deprecated-RAG hook iff flag=True
# ──────────────────────────────────────────────────────────────────

from archon.config.loader import (
    AccessConfig,
    Config,
    LoggingConfig,
    OutputConfig,
    RagConfig,
    SessionConfig,
)
from archon.gateway.gateway import Gateway


def _make_mcp_mock() -> MagicMock:
    m = MagicMock()
    m.start = AsyncMock()
    m.stop = AsyncMock()
    m.mcp_url = "http://localhost:9999/mcp"
    m.token = "fake-token"
    return m


def _make_full_config(*, deprecated: bool) -> Config:
    return Config(
        telegram_bot_token=_FAKE_TOKEN,
        access=AccessConfig(allowed_user_ids=[100]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
        rag=RagConfig(deprecated_history_collection=deprecated),
    )


def _gateway_run_patches(cfg: Config) -> list:
    """Return a list of patch context managers sufficient to run Gateway._run()."""
    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()
    mock_bot.send_message = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.start_polling = AsyncMock()
    mock_dp.startup = MagicMock()
    mock_dp.startup.register = MagicMock()
    mock_dp.startup.trigger = AsyncMock()
    mock_dp.get = MagicMock(return_value=None)

    mock_mgr = MagicMock()
    mock_mgr.stop_all = AsyncMock()

    return [
        patch("archon.config.loader.load_config", return_value=cfg),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.JobScheduler", return_value=MagicMock(
            start=AsyncMock(), stop=AsyncMock(),
            job_configs=[],
        )),
        patch("archon.gateway.gateway.RestartCoordinator", return_value=MagicMock(
            wait=AsyncMock(side_effect=asyncio.CancelledError),
            write_restart_timestamp=MagicMock(),
        )),
        patch("archon.gateway.gateway.ArchonToolkit", return_value=MagicMock(
            set_late_deps=MagicMock(),
        )),
        patch("archon.gateway.gateway.get_version", return_value="26.3.0"),
        patch("archon.gateway.gateway.get_runtime", return_value=MagicMock(
            register_signals=MagicMock(),
        )),
        patch("archon.gateway.gateway._ensure_search_server", new_callable=AsyncMock, return_value=False),
        patch("archon.gateway.gateway.AttachmentStore", return_value=MagicMock(
            cleanup=MagicMock(return_value=0),
        )),
    ]


async def test_gateway_registers_deprecated_notification_when_flag_is_true() -> None:
    """Gateway._run() must call _register_deprecated_search_notification when flag=True."""
    cfg = _make_full_config(deprecated=True)

    import contextlib
    patches = _gateway_run_patches(cfg)

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch(
            "archon.gateway.gateway._register_deprecated_search_notification",
        ) as mock_register:
            await Gateway._run()

    mock_register.assert_called_once()


async def test_gateway_skips_deprecated_notification_when_flag_is_false() -> None:
    """Gateway._run() must NOT call _register_deprecated_search_notification when flag=False."""
    cfg = _make_full_config(deprecated=False)

    import contextlib
    patches = _gateway_run_patches(cfg)

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        with patch(
            "archon.gateway.gateway._register_deprecated_search_notification",
        ) as mock_register:
            await Gateway._run()

    mock_register.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# 23-26. Gateway._run() wires RAG state notifications (Task E.3)
# ──────────────────────────────────────────────────────────────────

import contextlib


def _make_full_config_rag(
    *,
    rag_enabled: bool = True,
    deprecated: bool = False,
) -> Config:
    return Config(
        telegram_bot_token=_FAKE_TOKEN,
        access=AccessConfig(allowed_user_ids=[100]),
        session=SessionConfig(working_directory="/tmp"),
        output=OutputConfig(),
        logging=LoggingConfig(),
        rag=RagConfig(deprecated_history_collection=deprecated, enabled=rag_enabled),
    )


def _gateway_run_patches_v2(cfg: Config) -> list:
    """Like _gateway_run_patches but does NOT patch _ensure_search_server (tests replace _detect_search_state)."""
    mock_bot = MagicMock()
    mock_bot.session = MagicMock()
    mock_bot.session.close = AsyncMock()
    mock_bot.send_message = AsyncMock()

    mock_dp = MagicMock()
    mock_dp.start_polling = AsyncMock()
    mock_dp.startup = MagicMock()
    mock_dp.startup.register = MagicMock()
    mock_dp.startup.trigger = AsyncMock()
    mock_dp.get = MagicMock(return_value=None)

    mock_mgr = MagicMock()
    mock_mgr.stop_all = AsyncMock()

    return [
        patch("archon.config.loader.load_config", return_value=cfg),
        patch("archon.gateway.gateway.setup_logging"),
        patch("archon.gateway.gateway.create_bot", return_value=mock_bot),
        patch("archon.gateway.gateway.create_dispatcher", return_value=mock_dp),
        patch("archon.gateway.gateway.SessionManager", return_value=mock_mgr),
        patch("archon.gateway.gateway._setup_dp"),
        patch("archon.gateway.gateway.ArchonMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.ArchonRouterMCPServer", return_value=_make_mcp_mock()),
        patch("archon.gateway.gateway.BackgroundAgentManager", return_value=mock_mgr),
        patch("archon.gateway.gateway.JobScheduler", return_value=MagicMock(
            start=AsyncMock(), stop=AsyncMock(),
            job_configs=[],
        )),
        patch("archon.gateway.gateway.RestartCoordinator", return_value=MagicMock(
            wait=AsyncMock(side_effect=asyncio.CancelledError),
            write_restart_timestamp=MagicMock(),
        )),
        patch("archon.gateway.gateway.ArchonToolkit", return_value=MagicMock(
            set_late_deps=MagicMock(),
        )),
        patch("archon.gateway.gateway.get_version", return_value="26.3.0"),
        patch("archon.gateway.gateway.get_runtime", return_value=MagicMock(
            register_signals=MagicMock(),
        )),
        patch("archon.gateway.gateway.AttachmentStore", return_value=MagicMock(
            cleanup=MagicMock(return_value=0),
        )),
    ]


async def test_gateway_auto_starts_when_state_is_not_running() -> None:
    """When _detect_search_state returns NOT_RUNNING, _auto_start_search_service must be called."""
    from archon.gateway.gateway import SearchState

    cfg = _make_full_config_rag(rag_enabled=True)

    with contextlib.ExitStack() as stack:
        for p in _gateway_run_patches_v2(cfg):
            stack.enter_context(p)
        mock_detect = stack.enter_context(
            patch("archon.gateway.gateway._detect_search_state", new_callable=AsyncMock, return_value=SearchState.NOT_RUNNING)
        )
        mock_auto_start = stack.enter_context(
            patch("archon.gateway.gateway._auto_start_search_service", new_callable=AsyncMock, return_value=True)
        )
        stack.enter_context(patch("archon.gateway.gateway._register_search_state_notification"))
        await Gateway._run()

    mock_detect.assert_awaited_once()
    mock_auto_start.assert_awaited_once()


async def test_gateway_skips_auto_start_when_not_installed() -> None:
    """When state is NOT_INSTALLED, _auto_start_search_service must NOT be called."""
    from archon.gateway.gateway import SearchState

    cfg = _make_full_config_rag(rag_enabled=True)

    with contextlib.ExitStack() as stack:
        for p in _gateway_run_patches_v2(cfg):
            stack.enter_context(p)
        stack.enter_context(
            patch("archon.gateway.gateway._detect_search_state", new_callable=AsyncMock, return_value=SearchState.NOT_INSTALLED)
        )
        mock_auto_start = stack.enter_context(
            patch("archon.gateway.gateway._auto_start_search_service", new_callable=AsyncMock)
        )
        stack.enter_context(patch("archon.gateway.gateway._register_search_state_notification"))
        await Gateway._run()

    mock_auto_start.assert_not_awaited()


async def test_gateway_skips_auto_start_when_not_registered() -> None:
    """When state is NOT_REGISTERED, _auto_start_search_service must NOT be called."""
    from archon.gateway.gateway import SearchState

    cfg = _make_full_config_rag(rag_enabled=True)

    with contextlib.ExitStack() as stack:
        for p in _gateway_run_patches_v2(cfg):
            stack.enter_context(p)
        stack.enter_context(
            patch("archon.gateway.gateway._detect_search_state", new_callable=AsyncMock, return_value=SearchState.NOT_REGISTERED)
        )
        mock_auto_start = stack.enter_context(
            patch("archon.gateway.gateway._auto_start_search_service", new_callable=AsyncMock)
        )
        stack.enter_context(patch("archon.gateway.gateway._register_search_state_notification"))
        await Gateway._run()

    mock_auto_start.assert_not_awaited()


async def test_gateway_rag_url_none_when_auto_start_fails() -> None:
    """When _detect_search_state returns NOT_RUNNING and _auto_start_search_service returns False,
    SessionManager must be constructed with rag_url=None."""
    from archon.gateway.gateway import SearchState

    cfg = _make_full_config_rag(rag_enabled=True)

    captured_rag_urls: list[str | None] = []

    def _capture_session_manager(*args: object, **kwargs: object) -> MagicMock:
        captured_rag_urls.append(kwargs.get("rag_url"))
        m = MagicMock()
        m.stop_all = AsyncMock()
        m.set_model = MagicMock()
        return m

    with contextlib.ExitStack() as stack:
        for p in _gateway_run_patches_v2(cfg):
            stack.enter_context(p)
        stack.enter_context(
            patch("archon.gateway.gateway._detect_search_state", new_callable=AsyncMock, return_value=SearchState.NOT_RUNNING)
        )
        stack.enter_context(
            patch("archon.gateway.gateway._auto_start_search_service", new_callable=AsyncMock, return_value=False)
        )
        stack.enter_context(patch("archon.gateway.gateway._register_search_state_notification"))
        stack.enter_context(
            patch("archon.gateway.gateway.SessionManager", side_effect=_capture_session_manager)
        )
        await Gateway._run()

    assert len(captured_rag_urls) == 1
    assert captured_rag_urls[0] is None


async def test_gateway_updates_rag_url_after_successful_auto_start() -> None:
    """When auto_start succeeds, rag_url must be passed (not None) to SessionManager."""
    from archon.gateway.gateway import SearchState

    cfg = _make_full_config_rag(rag_enabled=True)

    captured_rag_urls: list[str | None] = []

    def _capture_session_manager(*args: object, **kwargs: object) -> MagicMock:
        captured_rag_urls.append(kwargs.get("rag_url"))
        m = MagicMock()
        m.stop_all = AsyncMock()
        m.set_model = MagicMock()
        return m

    with contextlib.ExitStack() as stack:
        for p in _gateway_run_patches_v2(cfg):
            stack.enter_context(p)
        stack.enter_context(
            patch("archon.gateway.gateway._detect_search_state", new_callable=AsyncMock, return_value=SearchState.NOT_RUNNING)
        )
        stack.enter_context(
            patch("archon.gateway.gateway._auto_start_search_service", new_callable=AsyncMock, return_value=True)
        )
        stack.enter_context(patch("archon.gateway.gateway._register_search_state_notification"))
        stack.enter_context(
            patch("archon.gateway.gateway.SessionManager", side_effect=_capture_session_manager)
        )
        await Gateway._run()

    assert len(captured_rag_urls) == 1
    assert captured_rag_urls[0] is not None


async def test_gateway_no_notification_when_rag_disabled() -> None:
    """When rag.enabled=False, _register_search_state_notification must NOT be called."""
    cfg = _make_full_config_rag(rag_enabled=False)

    with contextlib.ExitStack() as stack:
        for p in _gateway_run_patches_v2(cfg):
            stack.enter_context(p)
        mock_register = stack.enter_context(
            patch("archon.gateway.gateway._register_search_state_notification")
        )
        stack.enter_context(
            patch("archon.gateway.gateway._ensure_search_server", new_callable=AsyncMock, return_value=False)
        )
        await Gateway._run()

    mock_register.assert_not_called()
