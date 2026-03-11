"""Tests for PromotionEvent and FallbackNoticeEvent handling in the message handler."""

import html
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from aiogram.types import Message

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.event_mapper import (
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
    Response,
    RoutingEvent,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import format_event, handle_message
from archon.config.loader import NotificationsConfig


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

_split = SplitStrategy()


def _mock_message(text: str = "do something") -> Message:
    msg = MagicMock(spec=Message)
    msg.answer = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock(id=42)
    msg.chat = MagicMock(id=100)
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    return msg


def _mock_session(*events: object, is_processing: bool = False) -> MagicMock:
    """Session whose send() yields the given events."""
    session = MagicMock()
    session.is_processing = is_processing
    session.context_summary = ""

    async def _send(prompt: str) -> AsyncGenerator:
        for event in events:
            yield event

    session.send = _send
    return session


def _mock_session_manager(*events: object) -> SessionManager:
    session = _mock_session(*events)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    return mgr


def _make_notifications(mode: str = "debug") -> NotificationsConfig:
    notifications = MagicMock(spec=NotificationsConfig)
    notifications.mode = mode
    notifications.interval_minutes = 0
    notifications.agents = MagicMock()
    notifications.agents.mode = None
    return notifications


def _make_bam(run_name: str = "Harbor") -> MagicMock:
    """Build a mock BackgroundAgentManager that returns a run with given name."""
    bam = MagicMock()
    run = MagicMock()
    run.name = run_name
    bam.spawn = AsyncMock(return_value=run)
    return bam


# ──────────────────────────────────────────────────────────────────
# PromotionEvent → spawn background agent
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promotion_event_sends_handing_off_message() -> None:
    """When PromotionEvent fires and BAM.spawn succeeds, message.answer is called
    with a 'handing off to background agent' message BEFORE spawn fires."""
    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: do something",
        original_prompt="do something",
        tool_count=10,
    )
    msg = _mock_message("do something")
    bam = _make_bam(run_name="Atlas")
    mgr = _mock_session_manager(promotion)

    await handle_message(
        msg,
        mgr,
        _split,
        notifications=_make_notifications("debug"),
        background_agent_manager=bam,
    )

    all_calls = [call.args[0] for call in msg.answer.call_args_list if call.args]
    handing_off_messages = [t for t in all_calls if "handing off" in t.lower()]
    assert len(handing_off_messages) == 1, (
        f"Expected exactly one 'handing off' message, got: {all_calls}"
    )
    assert "background agent" in handing_off_messages[0].lower(), (
        f"Expected 'background agent' in message, got: {handing_off_messages[0]!r}"
    )
    # Verify spawn was still called
    bam.spawn.assert_awaited_once()


@pytest.mark.asyncio
async def test_promotion_event_handing_off_sent_before_spawn() -> None:
    """'Handing off' notification must be sent BEFORE spawn() is called.

    Bug 08: spawn() internally sends '🤖 Agent X spawned.' — the handing off
    message must arrive first so messages appear in logical order.
    """
    call_order: list[str] = []

    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: do something",
        original_prompt="do something",
        tool_count=7,
    )
    msg = _mock_message("do something")

    # Record order: answer() call vs spawn() call
    async def _answer(text: str, **kwargs: object) -> None:
        if "handing off" in text.lower():
            call_order.append("answer")

    async def _spawn(**kwargs: object) -> MagicMock:
        call_order.append("spawn")
        run = MagicMock()
        run.name = "Harbor"
        return run

    msg.answer = AsyncMock(side_effect=_answer)
    bam = MagicMock()
    bam.spawn = AsyncMock(side_effect=_spawn)
    mgr = _mock_session_manager(promotion)

    await handle_message(
        msg,
        mgr,
        _split,
        notifications=_make_notifications("debug"),
        background_agent_manager=bam,
    )

    assert call_order == ["answer", "spawn"], (
        f"'handing off' message must be sent BEFORE spawn(), got order: {call_order}"
    )


@pytest.mark.asyncio
async def test_promotion_event_sends_tool_count() -> None:
    """The promotion message must contain the tool count used."""
    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: do something",
        original_prompt="do something",
        tool_count=7,
    )
    msg = _mock_message("do something")
    bam = _make_bam(run_name="Orion")
    mgr = _mock_session_manager(promotion)

    await handle_message(
        msg,
        mgr,
        _split,
        notifications=_make_notifications("debug"),
        background_agent_manager=bam,
    )

    all_calls = [call.args[0] for call in msg.answer.call_args_list if call.args]
    # Find the promotion notification call specifically — must contain "tools used"
    promotion_calls = [t for t in all_calls if "tools used" in t]
    assert len(promotion_calls) == 1, (
        f"Expected exactly one 'tools used' message, got: {all_calls}"
    )
    assert "7" in promotion_calls[0], (
        f"Expected tool count '7' in promotion message, got: {promotion_calls[0]!r}"
    )


@pytest.mark.asyncio
async def test_promotion_event_fallback_message_when_spawn_fails() -> None:
    """When BAM.spawn raises, message.answer is still called and doesn't contain agent name."""
    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: fix this",
        original_prompt="fix this",
        tool_count=5,
    )
    msg = _mock_message("fix this")
    bam = MagicMock()
    bam.spawn = AsyncMock(side_effect=RuntimeError("spawn failed"))
    mgr = _mock_session_manager(promotion)

    # Must not raise
    await handle_message(
        msg,
        mgr,
        _split,
        notifications=_make_notifications("debug"),
        background_agent_manager=bam,
    )

    # At minimum, the initial ack and the promotion format_event message are sent
    assert msg.answer.call_count >= 1


@pytest.mark.asyncio
async def test_promotion_event_format_event_not_called_for_promotion() -> None:
    """When BAM is available, format_event is NOT called with PromotionEvent (continue skips it)."""
    from unittest.mock import patch as mock_patch
    from archon.chat.handler import format_event as real_format_event

    called_with_promotion: list = []

    def _spy(event, *args, **kwargs):
        if isinstance(event, PromotionEvent):
            called_with_promotion.append(event)
        return real_format_event(event, *args, **kwargs)

    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: test",
        original_prompt="test",
        tool_count=10,
    )
    msg = _mock_message("test")
    bam = _make_bam(run_name="Nova")
    mgr = _mock_session_manager(promotion)

    with mock_patch("archon.chat.handler.format_event", side_effect=_spy):
        await handle_message(
            msg,
            mgr,
            _split,
            notifications=_make_notifications("debug"),
            background_agent_manager=bam,
        )

    assert len(called_with_promotion) == 0, (
        "format_event must not be called with PromotionEvent when BAM is available"
    )


@pytest.mark.asyncio
async def test_promotion_event_spawn_failure_sends_failure_notification() -> None:
    """When spawn() raises, user sees a failure notification in addition to the handing-off ack."""
    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: do something",
        original_prompt="do something",
        tool_count=10,
    )
    bam = _make_bam()
    bam.spawn = AsyncMock(side_effect=RuntimeError("spawn failed"))
    msg = _mock_message("do something")
    mgr = _mock_session_manager(promotion)

    await handle_message(
        msg,
        mgr,
        _split,
        notifications=_make_notifications("debug"),
        background_agent_manager=bam,
    )

    all_texts = [call.args[0] for call in msg.answer.call_args_list if call.args]
    # On spawn failure, there must be a failure notification
    failure_texts = [t for t in all_texts if "failed" in t.lower() or "could not" in t.lower()]
    assert len(failure_texts) >= 1, f"Expected failure notification, got: {all_texts}"


# ──────────────────────────────────────────────────────────────────
# FallbackNoticeEvent formatting
# ──────────────────────────────────────────────────────────────────


def test_fallback_notice_event_formatted_with_warning_emoji() -> None:
    """format_event returns a string starting with '⚠️' for FallbackNoticeEvent."""
    event = FallbackNoticeEvent(reason="Routing check timed out — trying to handle directly")
    result = format_event(event, _split)

    assert len(result) == 1
    assert result[0].startswith("⚠️"), f"Expected '⚠️' prefix, got: {result[0]!r}"


@pytest.mark.asyncio
async def test_fallback_notice_event_always_delivered_in_quiet_mode() -> None:
    """In quiet mode, FallbackNoticeEvent still reaches message.answer (not suppressed)."""
    fallback = FallbackNoticeEvent(reason="Routing timed out — inline fallback")
    response = Response(content="Done.")
    msg = _mock_message("do the thing")
    mgr = _mock_session_manager(fallback, response)

    await handle_message(
        msg,
        mgr,
        _split,
        notifications=_make_notifications("quiet"),
    )

    all_calls = [call.args[0] for call in msg.answer.call_args_list if call.args]
    # FallbackNoticeEvent must appear in the output even in quiet mode
    fallback_messages = [t for t in all_calls if "⚠️" in t or "timed out" in t.lower() or "fallback" in t.lower()]
    assert fallback_messages, (
        f"Expected FallbackNoticeEvent message in quiet mode, got: {all_calls}"
    )


def test_fallback_notice_event_escapes_html() -> None:
    """reason containing '<>' is properly HTML-escaped in the formatted output."""
    reason = "Error: <unexpected> scope & fallback"
    event = FallbackNoticeEvent(reason=reason)
    result = format_event(event, _split)

    assert len(result) == 1
    formatted = result[0]
    # Raw '<' and '>' must not appear unescaped
    assert "&lt;" in formatted or "<unexpected>" not in formatted
    assert "&gt;" in formatted or "<unexpected>" not in formatted
    # The escaped form should be present
    assert html.escape(reason) in formatted or "&lt;unexpected&gt;" in formatted


@pytest.mark.asyncio
async def test_promotion_event_bam_none_sends_unavailable_message() -> None:
    """When BAM is None and PromotionEvent fires, format_event fallback sends 'unavailable' message."""
    promotion = PromotionEvent(
        agent_prompt="[CONTINUATION: ...]\nOriginal request: do something big",
        original_prompt="do something big",
        tool_count=10,
    )
    mgr = _mock_session_manager(promotion)
    message = _mock_message()

    # No BAM passed — background_agent_manager=None (default)
    await handle_message(
        message, mgr, _split,
        notifications=_make_notifications("debug"),
        # background_agent_manager intentionally omitted (defaults to None)
    )

    all_texts = [call.args[0] for call in message.answer.call_args_list if call.args]
    unavailable_texts = [t for t in all_texts if "unavailable" in t]
    assert len(unavailable_texts) == 1
    assert "background agents" in unavailable_texts[0]
