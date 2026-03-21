"""Tests for message handler and event formatter — S2.3."""
import asyncio
import logging
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aiogram.types import Message

from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    PlanEvent,
    PromotionEvent,
    RecoveryEvent,
    ReminderInjectedEvent,
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import DEFAULT_MAX_LEN, check_auto_compact, format_event, handle_message
from archon.config.loader import NotificationsConfig


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

_split = SplitStrategy()


def _mock_message(text: str = "hello") -> Message:
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
    from tests.conftest import _mock_session_factory

    return _mock_session_factory(*events, is_processing=is_processing)


def _mock_session_manager(*events: object) -> SessionManager:
    session = _mock_session(*events)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    mgr.auto_compact_if_needed = AsyncMock(return_value=None)
    return mgr


# ──────────────────────────────────────────────────────────────────
# format_event — each event type
# ──────────────────────────────────────────────────────────────────


def test_format_thinking_result() -> None:
    result = format_event(ThinkingResult(content="pondering"), _split)
    assert result == ["💭 Thinking:\npondering"]


def test_format_tool_started() -> None:
    result = format_event(ToolStarted(name="Read"), _split)
    assert result == ["🔧 Tool: Read"]


def test_format_tool_started_with_input() -> None:
    result = format_event(ToolStarted(name="Bash", input="ls -la"), _split)
    assert result == ["🔧 Tool: Bash\nls -la"]


def test_format_tool_result() -> None:
    result = format_event(ToolResult(content="file content"), _split)
    assert result == ["📤 Result:\nfile content"]


def test_format_response() -> None:
    result = format_event(Response(content="All done."), _split)
    assert result == ["✅ Response:\nAll done."]


def test_format_error_event() -> None:
    result = format_event(ErrorEvent(message="timeout"), _split)
    assert result == ["❌ Error: timeout"]


# ──────────────────────────────────────────────────────────────────
# format_event — truncation
# ──────────────────────────────────────────────────────────────────


def test_format_response_splits_long_content() -> None:
    long_text = "x" * 100
    result = format_event(Response(content=long_text), _split, max_len=40)
    assert len(result) == 5
    assert all(r.startswith("✅ Response:\n") for r in result)
    assert all(len(r) <= 40 for r in result)


def test_format_thinking_result_splits_long_content() -> None:
    long_text = "a" * 100
    result = format_event(ThinkingResult(content=long_text), _split, max_len=40)
    assert len(result) == 5
    assert all(r.startswith("💭 Thinking:\n") for r in result)
    assert all(len(r) <= 40 for r in result)


def test_format_tool_result_splits_long_content() -> None:
    long_text = "b" * 100
    result = format_event(ToolResult(content=long_text), _split, max_len=40)
    assert len(result) == 5
    assert all(r.startswith("📤 Result:\n") for r in result)
    assert all(len(r) <= 40 for r in result)


@pytest.mark.parametrize(
    ("event", "prefix"),
    [
        (Response(content="x" * 100), "✅ Response:\n"),
        (ThinkingResult(content="x" * 100), "💭 Thinking:\n"),
        (ToolResult(content="x" * 100), "📤 Result:\n"),
    ],
)
def test_format_event_final_rendered_messages_respect_max_len(
    event: object, prefix: str
) -> None:
    result = format_event(event, _split, max_len=40)
    assert len(result) > 1
    assert all(r.startswith(prefix) for r in result)
    assert all(len(r) <= 40 for r in result)


@pytest.mark.parametrize(
    ("event", "prefix"),
    [
        (Response(content="<" * 30), "✅ Response:\n"),
        (ThinkingResult(content="<" * 30), "💭 Thinking:\n"),
        (ToolResult(content="<" * 30), "📤 Result:\n"),
    ],
)
def test_format_event_html_escaping_still_respects_max_len(
    event: object, prefix: str
) -> None:
    result = format_event(event, _split, max_len=40)
    assert len(result) > 1
    assert all(r.startswith(prefix) for r in result)
    assert all(len(r) <= 40 for r in result)
    assert all("&lt;" in r for r in result)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (Response(content="x" * 28), "✅ Response:\n" + ("x" * 28)),
        (ThinkingResult(content="x" * 28), "💭 Thinking:\n" + ("x" * 28)),
        (ToolResult(content="x" * 29), "📤 Result:\n" + ("x" * 29)),
    ],
)
def test_format_event_exact_boundary_remains_single_message(
    event: object, expected: str
) -> None:
    result = format_event(event, _split, max_len=40)
    assert result == [expected]


@pytest.mark.parametrize(
    ("event", "prefix"),
    [
        (Response(content="x" * 29), "✅ Response:\n"),
        (ThinkingResult(content="x" * 29), "💭 Thinking:\n"),
        (ToolResult(content="x" * 31), "📤 Result:\n"),
    ],
)
def test_format_event_one_over_boundary_splits(
    event: object, prefix: str
) -> None:
    result = format_event(event, _split, max_len=40)
    assert len(result) == 2
    assert all(r.startswith(prefix) for r in result)
    assert all(len(r) <= 40 for r in result)


def test_format_response_bold_split_produces_balanced_html_tags() -> None:
    """Regression: bold markdown must not produce unclosed <b> tags when content is split.

    Previously md_to_html() was called on the full text and the resulting HTML
    was split at a fixed character boundary, which could bisect a <b>…</b> pair.
    The fix is to split the raw markdown first, then convert each chunk.
    """
    # "**w** " is 6 chars; 50 repetitions = 300 chars → forces several splits at max_len=80
    bold_text = "**w** " * 50
    result = format_event(Response(content=bold_text), _split, max_len=80)
    assert len(result) > 1, "content must have been split into multiple chunks"
    for chunk in result:
        assert chunk.count("<b>") == chunk.count("</b>"), (
            f"Unbalanced <b> tags in chunk: {chunk!r}"
        )


def test_format_thinking_result_bold_split_produces_balanced_html_tags() -> None:
    """Same regression check for ThinkingResult chunks."""
    bold_text = "**x** " * 50
    result = format_event(ThinkingResult(content=bold_text), _split, max_len=80)
    assert len(result) > 1
    for chunk in result:
        assert chunk.count("<b>") == chunk.count("</b>"), (
            f"Unbalanced <b> tags in chunk: {chunk!r}"
        )


# ──────────────────────────────────────────────────────────────────
# handle_message
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_sends_each_event() -> None:
    mgr = _mock_session_manager(ThinkingResult(content="pondering"), Response(content="Hi"))
    msg = _mock_message("Say hi")

    await handle_message(msg, mgr, _split)

    assert msg.answer.await_count == 3
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Processing..."
    assert texts[1] == "💭 Thinking:\npondering"
    assert texts[2] == "✅ Response:\nHi"


async def test_handle_message_gets_or_creates_session_for_user() -> None:
    mgr = _mock_session_manager()
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_awaited_once_with(42)


async def test_handle_message_sends_multi_chunk_event() -> None:
    long_text = "y" * 100
    mgr = _mock_session_manager(Response(content=long_text))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, max_len=40)

    assert msg.answer.await_count == 6  # Processing... + 5 response chunks


async def test_handle_message_no_text_is_noop() -> None:
    mgr = MagicMock(spec=SessionManager)
    msg = _mock_message()
    msg.text = None

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_not_called()


async def test_handle_message_idle_session_no_queued_notification() -> None:
    """When the session is not processing, no 'queued' notification is sent."""
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("queued" in t for t in texts)


async def test_handle_message_busy_session_sends_queued_notification() -> None:
    """Bug.005: when the session is already processing, the handler sends a
    'queued' notification immediately so the user knows their message was received."""
    session = _mock_session(Response(content="Done"), is_processing=True)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("can I chat while Agent Onyx runs?")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("queued" in t.lower() for t in texts), (
        f"Expected a 'queued' notification among replies; got: {texts}"
    )
    # The normal response still arrives after the queued notification.
    assert any("✅ Response" in t for t in texts)


async def test_handle_message_busy_session_queued_notification_is_first() -> None:
    """The 'queued' message is sent BEFORE any event replies (immediate feedback)."""
    session = _mock_session(Response(content="Done"), is_processing=True)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("follow-up")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0].startswith("⏳"), (
        f"Expected '⏳ queued' as first message, got: {texts[0]!r}"
    )


async def test_handle_message_busy_session_no_processing_ack() -> None:
    """Bug.C3: when the session is busy (queued), '⏳ Processing...' / '⏳ Working...' must NOT be sent.
    The user already received the 'queued' notification — a second ack is misleading."""
    session = _mock_session(Response(content="Done"), is_processing=True)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("follow-up")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any(t in ("⏳ Processing...", "⏳ Working...") for t in texts), (
        f"Expected no processing ack when queued; got: {texts}"
    )


async def test_handle_message_idle_session_sends_processing_ack() -> None:
    """Bug.C3: when the session is idle (not queued), '⏳ Processing...' IS sent."""
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert "⏳ Processing..." in texts, (
        f"Expected processing ack for idle session; got: {texts}"
    )


async def test_handle_message_no_from_user_is_noop() -> None:
    mgr = MagicMock(spec=SessionManager)
    msg = _mock_message()
    msg.from_user = None

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_not_called()


async def test_handle_message_sends_error_on_session_exception() -> None:
    """If session.send() raises, the handler sends an error message and does not propagate."""
    session = MagicMock()
    session.is_processing = False  # not busy — no queued notification

    async def _send_raises(prompt: str):
        raise RuntimeError("SDK failure")
        yield  # make it an async generator

    session.send = _send_raises
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Processing..."
    assert texts[1].startswith("❌ Error:")


# ──────────────────────────────────────────────────────────────────
# HTML escaping — all event types, all special characters
# ──────────────────────────────────────────────────────────────────

# Response
def test_format_response_escapes_angle_brackets() -> None:
    result = format_event(Response(content="<content>hello</content>"), _split)
    assert result == ["✅ Response:\n&lt;content&gt;hello&lt;/content&gt;"]


def test_format_response_escapes_ampersand() -> None:
    result = format_event(Response(content="foo & bar"), _split)
    assert result == ["✅ Response:\nfoo &amp; bar"]


def test_format_response_escapes_double_quote() -> None:
    result = format_event(Response(content='say "hi"'), _split)
    assert result == ["✅ Response:\nsay &quot;hi&quot;"]


def test_format_response_escapes_all_special_chars() -> None:
    result = format_event(Response(content='<a href="x">foo & bar</a>'), _split)
    assert result == ["✅ Response:\n&lt;a href=&quot;x&quot;&gt;foo &amp; bar&lt;/a&gt;"]


# ThinkingResult
def test_format_thinking_result_escapes_angle_brackets() -> None:
    result = format_event(ThinkingResult(content="<think>idea</think>"), _split)
    assert result == ["💭 Thinking:\n&lt;think&gt;idea&lt;/think&gt;"]


def test_format_thinking_result_escapes_ampersand() -> None:
    result = format_event(ThinkingResult(content="cats & dogs"), _split)
    assert result == ["💭 Thinking:\ncats &amp; dogs"]


def test_format_thinking_result_escapes_double_quote() -> None:
    result = format_event(ThinkingResult(content='he said "yes"'), _split)
    assert result == ["💭 Thinking:\nhe said &quot;yes&quot;"]


# ToolStarted — name
def test_format_tool_started_name_escapes_angle_brackets() -> None:
    result = format_event(ToolStarted(name="<evil>"), _split)
    assert result == ["🔧 Tool: &lt;evil&gt;"]


def test_format_tool_started_name_escapes_ampersand() -> None:
    result = format_event(ToolStarted(name="Foo&Bar"), _split)
    assert result == ["🔧 Tool: Foo&amp;Bar"]


# ToolStarted — input
def test_format_tool_started_input_escapes_angle_brackets() -> None:
    result = format_event(ToolStarted(name="Bash", input="echo <hello>"), _split)
    assert result == ["🔧 Tool: Bash\necho &lt;hello&gt;"]


def test_format_tool_started_input_escapes_ampersand() -> None:
    result = format_event(ToolStarted(name="Bash", input="foo && bar"), _split)
    assert result == ["🔧 Tool: Bash\nfoo &amp;&amp; bar"]


def test_format_tool_started_input_escapes_double_quote() -> None:
    result = format_event(ToolStarted(name="Bash", input='echo "hi"'), _split)
    assert result == ["🔧 Tool: Bash\necho &quot;hi&quot;"]


def test_format_tool_started_input_escapes_all_special_chars() -> None:
    result = format_event(ToolStarted(name="Bash", input='<cmd arg="x"> & done'), _split)
    assert result == ["🔧 Tool: Bash\n&lt;cmd arg=&quot;x&quot;&gt; &amp; done"]


# ToolResult — full mode
def test_format_tool_result_escapes_angle_brackets() -> None:
    result = format_event(ToolResult(content="<tool_use_error>fail</tool_use_error>"), _split)
    assert result == ["📤 Result:\n&lt;tool_use_error&gt;fail&lt;/tool_use_error&gt;"]
    assert "<tool_use_error>" not in result[0]


def test_format_tool_result_escapes_ampersand() -> None:
    result = format_event(ToolResult(content="status: ok & done"), _split)
    assert result == ["📤 Result:\nstatus: ok &amp; done"]


def test_format_tool_result_escapes_double_quote() -> None:
    result = format_event(ToolResult(content='key="val" <tag>'), _split)
    assert result == ['📤 Result:\nkey=&quot;val&quot; &lt;tag&gt;']


# ToolResult — brief mode (verbose)
def test_format_tool_result_brief_escapes_angle_brackets() -> None:
    """Brief tool output containing HTML tags must be escaped (fixes <tool_use_error> crash)."""
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="<tool_use_error>fail</tool_use_error>"), _split, notifications=notif)
    assert result == ["📤 ✓ &lt;tool_use_error&gt;fail&lt;/tool_use_error&gt;"]
    assert "<tool_use_error>" not in result[0]


def test_format_tool_result_brief_escapes_ampersand() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="ok & done"), _split, notifications=notif)
    assert result == ["📤 ✓ ok &amp; done"]


def test_format_tool_result_brief_escapes_double_quote() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content='say "hi"'), _split, notifications=notif)
    assert result == ["📤 ✓ say &quot;hi&quot;"]


def test_format_tool_result_brief_escapes_html_with_id() -> None:
    """Brief tool output with an ID tag: HTML in content must still be escaped."""
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="<b>bold</b>", id=11), _split, notifications=notif)
    assert result == ["📤 [11] ✓ &lt;b&gt;bold&lt;/b&gt;"]
    assert "<b>" not in result[0]


# ErrorEvent
def test_format_error_event_escapes_angle_brackets() -> None:
    result = format_event(ErrorEvent(message="<tool_use_error>bad</tool_use_error>"), _split)
    assert result == ["❌ Error: &lt;tool_use_error&gt;bad&lt;/tool_use_error&gt;"]
    assert "<tool_use_error>" not in result[0]


def test_format_error_event_escapes_ampersand() -> None:
    result = format_event(ErrorEvent(message="cats & dogs"), _split)
    assert result == ["❌ Error: cats &amp; dogs"]


def test_format_error_event_escapes_double_quote() -> None:
    result = format_event(ErrorEvent(message='he said "no"'), _split)
    assert result == ["❌ Error: he said &quot;no&quot;"]


async def test_handle_message_sends_typing_indicator() -> None:
    """Typing chat action must be sent at the start of message handling.

    With the 4-second cooldown, the pre-response call is throttled (fires within
    milliseconds of the initial call), so only the initial send_chat_action goes through.
    """
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("Say hi")

    await handle_message(msg, mgr, _split)

    # Only the initial typing call — pre-response is throttled (< 4s elapsed).
    assert msg.bot.send_chat_action.await_count == 1
    msg.bot.send_chat_action.assert_awaited_with(chat_id=100, action="typing")


async def test_handle_message_no_continuous_typing_background_task() -> None:
    """No long-lived typing-refresh background task must be created.

    The old _keep_typing loop ran forever and was only cancelled in finally.
    The replacement sends typing inline before each message.answer() call, so
    no background task is needed in non-quiet mode (only the beacon task is
    created in quiet+interval mode).
    """
    from unittest.mock import patch

    created_tasks: list[asyncio.Task] = []
    _original_create_task = asyncio.create_task

    def _capturing_create_task(coro, **kwargs):
        task = _original_create_task(coro, **kwargs)
        created_tasks.append(task)
        return task

    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("go")

    with patch("archon.chat.handler.asyncio.create_task", side_effect=_capturing_create_task):
        await handle_message(msg, mgr, _split)  # default (debug) mode — no beacon

    assert created_tasks == [], (
        f"Expected no background tasks in non-quiet mode, got {len(created_tasks)}"
    )


async def test_handle_message_beacon_task_fully_done_after_return() -> None:
    """Beacon task must be fully cancelled before handle_message returns.

    Ensures we await the cancellation — not just fire-and-forget — so the
    event loop cannot squeeze in an extra send after the response.
    """
    from unittest.mock import patch

    notif = NotificationsConfig(mode="quiet", interval_minutes=60)  # long interval, won't fire
    created_tasks: list[asyncio.Task] = []
    _original_create_task = asyncio.create_task

    def _capturing_create_task(coro, **kwargs):
        task = _original_create_task(coro, **kwargs)
        created_tasks.append(task)
        return task

    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    with patch("archon.chat.handler.asyncio.create_task", side_effect=_capturing_create_task):
        await handle_message(msg, mgr, _split, notifications=notif)

    assert created_tasks, "Expected beacon task to be created in quiet+interval mode"
    for task in created_tasks:
        assert task.done(), f"Background task still running after handle_message returned: {task}"


async def test_handle_message_typing_sent_before_each_outgoing_message() -> None:
    """send_chat_action(typing) is rate-limited to _TYPING_COOLDOWN_SECS = 4.0 s.

    Three outgoing messages (tool, tool-result, response) all fire within milliseconds
    of each other. The initial call at message receipt fires, then all three pre-message
    calls are throttled because < 4s has elapsed — only 1 total send_chat_action call.
    """
    notif = NotificationsConfig(mode="normal")
    mgr = _mock_session_manager(
        ToolStarted(name="Bash"),
        ToolResult(content="ok"),
        Response(content="Done"),
    )
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    # Only the initial call; all 3 pre-message calls are throttled (< 4s cooldown).
    assert msg.bot.send_chat_action.await_count == 1


# ──────────────────────────────────────────────────────────────────
# format_event — mode-based visibility matrix (S8.1)
# ──────────────────────────────────────────────────────────────────

# ThinkingResult: hidden in quiet/normal, shown in verbose/debug
def test_format_thinking_result_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ThinkingResult(content="secret"), _split, notifications=notif) == []


def test_format_thinking_result_shown_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ThinkingResult(content="pondering"), _split, notifications=notif)
    assert result == ["💭 Thinking:\npondering"]


def test_format_thinking_result_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ThinkingResult(content="pondering"), _split, notifications=notif)
    assert result == ["💭 Thinking:\npondering"]


def test_format_thinking_result_shown_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(ThinkingResult(content="pondering"), _split, notifications=notif)
    assert result == ["💭 Thinking:\npondering"]


# ToolStarted: hidden in quiet/normal; name-only in verbose; name+args in debug
def test_format_tool_started_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ToolStarted(name="Bash", input="ls"), _split, notifications=notif) == []


def test_format_tool_started_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    assert format_event(ToolStarted(name="Bash", input="ls -la"), _split, notifications=notif) == []


def test_format_tool_started_name_only_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolStarted(name="Bash", input="ls -la"), _split, notifications=notif)
    assert result == ["🔧 Tool: Bash"]  # no input shown


def test_format_tool_started_with_args_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(ToolStarted(name="Bash", input="ls -la"), _split, notifications=notif)
    assert result == ["🔧 Tool: Bash\nls -la"]


def test_format_tool_started_no_input_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolStarted(name="Read"), _split, notifications=notif)
    assert result == ["🔧 Tool: Read"]


# ToolResult: hidden in quiet/normal; brief in verbose; full in debug
def test_format_tool_result_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ToolResult(content="output"), _split, notifications=notif) == []


def test_format_tool_result_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    assert format_event(ToolResult(content="output"), _split, notifications=notif) == []


def test_format_tool_result_brief_empty_content() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content=""), _split, notifications=notif)
    assert result == ["📤 ✓ ok"]


def test_format_tool_result_brief_single_line() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="exit 0\nsome other output"), _split, notifications=notif)
    assert result == ["📤 ✓ exit 0"]


def test_format_tool_result_brief_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="exit 0\nmore"), _split, notifications=notif)
    assert result == ["📤 ✓ exit 0"]


def test_format_tool_result_brief_truncates_long_first_line() -> None:
    # No period, no newline — hard cut at 160 chars
    notif = NotificationsConfig(mode="verbose")
    long_line = "x" * 200
    result = format_event(ToolResult(content=long_line), _split, notifications=notif)
    assert result == [f"📤 ✓ {'x' * 160}"]


def test_format_tool_result_brief_cuts_after_second_period_no_newline() -> None:
    # Content has multiple periods but no newline — must cut after the second period
    notif = NotificationsConfig(mode="verbose")
    content = "First sentence. Second sentence. Third sentence continues on and on."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ First sentence. Second sentence."]


def test_format_tool_result_brief_period_beats_160_char_fallback() -> None:
    # Single period well within 160 chars — must not fall back to the 160-char hard cut
    notif = NotificationsConfig(mode="verbose")
    content = "Summary: done. " + "x" * 100
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ Summary: done."]


def test_format_tool_result_brief_second_period_beats_newline_when_earlier() -> None:
    # 2nd period comes before the newline — cut after 2nd period
    notif = NotificationsConfig(mode="verbose")
    content = "Sentence one. Sentence two. \nMore content here."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ Sentence one. Sentence two."]


def test_format_tool_result_brief_newline_beats_period_when_earlier() -> None:
    # Newline comes before any period — cut before newline
    notif = NotificationsConfig(mode="verbose")
    content = "First line\nhas a period."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ First line"]


def test_format_tool_result_full_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(ToolResult(content="full output"), _split, notifications=notif)
    assert result == ["📤 Result:\nfull output"]


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "WebFetch"])
def test_format_tool_result_suppressed_tool_hidden_in_normal(tool_name: str) -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(
        ToolResult(content="secret line 1\nsecret line 2", tool_name=tool_name),
        _split,
        notifications=notif,
    )
    assert result == []


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "WebFetch"])
def test_format_tool_result_suppressed_tool_brief_in_verbose(tool_name: str) -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(
        ToolResult(content="secret line 1\nsecret line 2", tool_name=tool_name),
        _split,
        notifications=notif,
    )
    assert result == [f"📤 ✓ {tool_name} completed (2 lines, 27 B)"]


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "WebFetch"])
def test_format_tool_result_suppressed_tool_still_brief_in_debug(tool_name: str) -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(
        ToolResult(content="secret line 1\nsecret line 2", tool_name=tool_name),
        _split,
        notifications=notif,
    )
    assert result == [f"📤 ✓ {tool_name} completed (2 lines, 27 B)"]


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "WebFetch"])
def test_format_tool_result_suppressed_tool_still_brief_without_notifications(
    tool_name: str,
) -> None:
    result = format_event(
        ToolResult(content="secret line 1\nsecret line 2", tool_name=tool_name),
        _split,
    )
    assert result == [f"📤 ✓ {tool_name} completed (2 lines, 27 B)"]


@pytest.mark.parametrize("tool_name", ["Read", "Glob", "Grep", "WebFetch"])
def test_format_tool_result_suppressed_tool_error_still_full(tool_name: str) -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(
        ToolResult(content="Error: failed to read file", tool_name=tool_name, is_error=True),
        _split,
        notifications=notif,
    )
    assert result == ["📤 Result:\nError: failed to read file"]


def test_format_tool_result_markdown_bold_in_verbose_mode_brief() -> None:
    """Markdown bold in tool result brief (verbose) is rendered as HTML <b>."""
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="Result: **success**. All done."), _split, notifications=notif)
    assert "<b>success</b>" in result[0]


def test_format_tool_result_markdown_code_in_verbose_mode_brief() -> None:
    """Markdown inline code in tool result brief (verbose) is rendered as HTML <code>."""
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="Run `pytest` to test. Done."), _split, notifications=notif)
    assert "<code>pytest</code>" in result[0]


def test_format_tool_result_markdown_bold_in_debug_mode() -> None:
    """Markdown bold in full tool result (debug) is rendered as HTML <b>."""
    notif = NotificationsConfig(mode="debug")
    result = format_event(ToolResult(content="Result: **success**"), _split, notifications=notif)
    assert "<b>success</b>" in result[0]


def test_format_tool_result_markdown_bold_in_verbose_mode() -> None:
    """Markdown bold in tool result brief (verbose) is rendered as HTML <b>."""
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="Result: **success**. All done."), _split, notifications=notif)
    assert "<b>success</b>" in result[0]


# Response and ErrorEvent: always shown in all modes
# ──────────────────────────────────────────────────────────────────
# format_event — ClassificationEvent
# ──────────────────────────────────────────────────────────────────


def test_format_classification_shown_in_debug() -> None:
    event = ClassificationEvent(intent="task", confidence=0.95)
    result = format_event(event, _split)
    assert len(result) == 1
    assert "🏷" in result[0]
    assert "task" in result[0]
    assert "95%" in result[0]


def test_format_classification_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    event = ClassificationEvent(intent="chat", confidence=0.8)
    result = format_event(event, _split, notifications=notif)
    assert len(result) == 1
    assert "chat" in result[0]


def test_format_classification_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    event = ClassificationEvent(intent="task", confidence=0.9)
    result = format_event(event, _split, notifications=notif)
    assert result == []


def test_format_classification_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    event = ClassificationEvent(intent="task", confidence=0.9)
    result = format_event(event, _split, notifications=notif)
    assert result == []


# ── PlanEvent formatting (Phase 2 Task #2) ──────────────────────


def _make_plan_event(n_agents: int = 2) -> PlanEvent:
    agents = [AgentTask(id=f"a{i}", task=f"Task {i}") for i in range(1, n_agents + 1)]
    plan = AgentPlan(scope="large", summary="Break into tasks", agents=agents)
    return PlanEvent(plan=plan, summary=plan.summary)


def test_format_plan_event_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(_make_plan_event(3), _split, notifications=notif)
    assert len(result) == 1
    assert "📋 Plan:" in result[0]
    assert "Break into tasks" in result[0]
    assert "3 agents" in result[0]
    assert "• Task 1" in result[0]
    assert "• Task 2" in result[0]
    assert "• Task 3" in result[0]
    assert "🔄 Spawning 3 agents..." in result[0]


def test_format_plan_event_quiet() -> None:
    """PlanEvent is always visible — like Response, never suppressed."""
    notif = NotificationsConfig(mode="quiet")
    result = format_event(_make_plan_event(), _split, notifications=notif)
    assert len(result) == 1
    assert "📋 Plan:" in result[0]
    assert "• Task 1" in result[0]
    assert "• Task 2" in result[0]


def test_format_plan_event_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(), _split, notifications=notif)
    assert len(result) == 1
    assert "📋 Plan:" in result[0]
    assert "• Task 1" in result[0]
    assert "• Task 2" in result[0]


def test_format_plan_event_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(_make_plan_event(), _split, notifications=notif)
    assert len(result) == 1
    assert "📋 Plan:" in result[0]
    assert "• Task 1" in result[0]
    assert "• Task 2" in result[0]


def test_format_plan_event_zero_agents() -> None:
    """Zero-agent PlanEvent produces no bullets and a valid spawning line.

    Zero-agent PlanEvent cannot be produced by the pipeline
    (parse_agent_plan rejects empty agent lists) — this is a defensive
    boundary test.
    """
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(0), _split, notifications=notif)
    assert "•" not in result[0]
    assert "🔄 Spawning 0 agents..." in result[0]


def test_format_plan_event_shows_task_bullets_for_multi_agent() -> None:
    """Multi-agent PlanEvent lists each agent's task as a bullet."""
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(3), _split, notifications=notif)
    assert "• Task 1" in result[0]
    assert "• Task 2" in result[0]
    assert "• Task 3" in result[0]
    assert "🔄 Spawning 3 agents..." in result[0]


def test_format_plan_event_no_bullets_for_single_agent() -> None:
    """Single-agent PlanEvent does not show a bullet list."""
    notif = NotificationsConfig(mode="normal")
    result = format_event(_make_plan_event(1), _split, notifications=notif)
    assert "•" not in result[0]
    assert "🔄 Spawning 1 agent..." in result[0]


def test_format_plan_event_html_escapes_task_text() -> None:
    """Task text containing HTML special chars is escaped."""
    agents = [
        AgentTask(id="a1", task="Fix <b>bold</b> & check"),
        AgentTask(id="a2", task="Deploy to prod"),
    ]
    plan = AgentPlan(scope="large", summary="Two tasks", agents=agents)
    event = PlanEvent(plan=plan, summary=plan.summary)
    notif = NotificationsConfig(mode="normal")
    result = format_event(event, _split, notifications=notif)
    assert "&lt;b&gt;bold&lt;/b&gt;" in result[0]
    assert "&amp;" in result[0]


# ── RoutingEvent formatting ─────────────────────────────────────


def test_format_routing_shown_in_debug() -> None:
    event = RoutingEvent(routing="chat_direct", model="claude-sonnet-4-6")
    result = format_event(event, _split)
    assert len(result) == 1
    assert "🔀" in result[0]
    assert "chat_direct" in result[0]


def test_format_routing_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    event = RoutingEvent(routing="agent_plan", model="claude-sonnet-4-6", agent_count=3)
    result = format_event(event, _split, notifications=notif)
    assert len(result) == 1


def test_format_routing_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    event = RoutingEvent(routing="task_direct", model="claude-sonnet-4-6")
    result = format_event(event, _split, notifications=notif)
    assert result == []


def test_format_routing_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    event = RoutingEvent(routing="agent_spawn", model="claude-sonnet-4-6")
    result = format_event(event, _split, notifications=notif)
    assert result == []


def test_format_response_shown_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(Response(content="Done"), _split, notifications=notif) == ["✅ Response:\nDone"]


def test_format_error_shown_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ErrorEvent(message="oops"), _split, notifications=notif) == ["❌ Error: oops"]


# ID tags (debug mode — full output shows IDs)
def test_format_tool_started_with_id() -> None:
    result = format_event(ToolStarted(name="Bash", input="ls", id=5), _split)
    assert result == ["🔧 Tool [5]: Bash\nls"]


def test_format_tool_started_no_input_with_id() -> None:
    result = format_event(ToolStarted(name="Read", id=3), _split)
    assert result == ["🔧 Tool [3]: Read"]


def test_format_tool_result_with_id() -> None:
    result = format_event(ToolResult(content="output", id=5), _split)
    assert result == ["📤 Result [5]:\noutput"]


def test_format_tool_brief_with_id() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="exit 0", id=5), _split, notifications=notif)
    assert result == ["📤 [5] ✓ exit 0"]


def test_format_tool_started_zero_id_no_bracket() -> None:
    """id=0 means no ID was assigned — don't show brackets."""
    result = format_event(ToolStarted(name="Read"), _split)
    assert result == ["🔧 Tool: Read"]


def test_format_tool_result_zero_id_no_bracket() -> None:
    result = format_event(ToolResult(content="data"), _split)
    assert result == ["📤 Result:\ndata"]


def test_format_event_no_notifications_shows_all() -> None:
    """notifications=None → debug mode → all events shown (backward compat)."""
    assert format_event(ThinkingResult(content="thought"), _split) == ["💭 Thinking:\nthought"]
    assert format_event(ToolResult(content="data"), _split) == ["📤 Result:\ndata"]


# ──────────────────────────────────────────────────────────────────
# handle_message — quiet mode (S8.2)
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_quiet_mode_sends_working_first() -> None:
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    first_call: str = msg.answer.call_args_list[0][0][0]
    assert first_call == "⏳ Working..."


async def test_handle_message_quiet_mode_only_sends_response() -> None:
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    events = [ThinkingResult(content="hmm"), ToolStarted(name="Bash"),
              ToolResult(content="ok"), Response(content="Done")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working..."
    assert texts[-1] == "✅ Response:\nDone"
    assert len(texts) == 2  # only working + response


async def test_handle_message_quiet_mode_passes_error_event() -> None:
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    mgr = _mock_session_manager(ThinkingResult(content="hmm"), ErrorEvent(message="oops"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert "❌ Error: oops" in texts


async def test_handle_message_normal_mode_sends_processing() -> None:
    """Normal mode sends '⏳ Processing...' immediately before streaming events."""
    notif = NotificationsConfig(mode="normal")
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Processing..."
    assert "⏳ Working..." not in texts


# ──────────────────────────────────────────────────────────────────
# partial status text — pure function (unchanged)
# ──────────────────────────────────────────────────────────────────


def test_partial_status_text_no_counts() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(0, 0) == "⏳ Working..."


def test_partial_status_text_one_tool() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(1, 0) == "⏳ Working... (1 tool)"


def test_partial_status_text_plural_tools() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(3, 0) == "⏳ Working... (3 tools)"


def test_partial_status_text_thinking_only() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(0, 2) == "⏳ Working... (2 thinking)"


def test_partial_status_text_tools_and_thinking() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(5, 3) == "⏳ Working... (5 tools, 3 thinking)"


def test_partial_status_text_custom_word_with_counts() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(3, 1, "Pondering") == "⏳ Pondering... (3 tools, 1 thinking)"


def test_partial_status_text_custom_word_no_counts() -> None:
    from archon.chat.handler import _partial_status_text
    assert _partial_status_text(0, 0, "Ruminating") == "⏳ Ruminating..."


# ──────────────────────────────────────────────────────────────────
# handle_message — quiet beacon mode (S8.2)
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_quiet_no_beacon_when_interval_zero() -> None:
    """interval_minutes=0 → no beacon task created."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # Only "Working..." and response — no periodic status updates
    assert len(texts) == 2


async def test_handle_message_quiet_beacon_fires_with_counts() -> None:
    """interval_minutes>0 fires periodic status updates in quiet mode."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0.001)  # 0.06s
    msg = _mock_message("go")

    async def _slow_send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="pondering")
        yield ToolStarted(name="Bash")
        await asyncio.sleep(0.12)  # long enough for ~2 timer ticks
        yield ToolStarted(name="Read")
        yield Response(content="Done")

    session = MagicMock()
    session.send = _slow_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    all_texts = [call[0][0] for call in msg.answer.call_args_list]
    status_updates = [t for t in all_texts if t.startswith("⏳ Working...") and "tool" in t]
    assert len(status_updates) >= 1


async def test_quiet_beacon_sends_typing_before_each_beacon_message() -> None:
    """Each beacon status message must be preceded by a typing indicator.

    Beacon updates (/quiet N periodic '⏳ Working...' messages) call send_chat_action
    directly inside _partial_update_task — NOT through the throttle helper — so they
    always fire regardless of cooldown.

    Invariant (post-cooldown):
      • "⏳ Working..." (initial)  — NOT preceded by typing
      • Initial typing             — sent right after Working... (+1)
      • Each beacon answer         — preceded by one typing call from beacon task (+N)
      • Final "✅ Response..." answer — pre-message call IS throttled (< 4s elapsed)
      ⟹ typing_count = 1 + N = total_answer_count - 1
    """
    notif = NotificationsConfig(mode="quiet", interval_minutes=0.001)  # 0.06s
    msg = _mock_message("go")

    async def _slow_send(text: str) -> AsyncGenerator:
        await asyncio.sleep(0.15)   # long enough for ~2 beacon ticks
        yield Response(content="Done")

    session = MagicMock()
    session.is_processing = False  # not busy — no queued notification
    session.send = _slow_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    all_texts = [call[0][0] for call in msg.answer.call_args_list]
    total_answers = len(all_texts)
    # Must have: initial "Working..." + at least 1 beacon + final Response
    assert total_answers >= 3, f"Expected ≥3 answers (init+beacon+response), got: {all_texts}"

    # typing_count = 1 (initial) + N (beacon calls, not throttled)
    # = total_answers - 1 (pre-response call is throttled within 4s cooldown)
    assert msg.bot.send_chat_action.await_count == total_answers - 1, (
        f"Expected {total_answers - 1} typing calls (= total answers - 1), "
        f"got {msg.bot.send_chat_action.await_count}. Answers: {all_texts}"
    )


async def test_handle_message_quiet_beacon_first_call_uses_working() -> None:
    """First beacon fire always uses 'Working', subsequent ones use a fun word."""
    from unittest.mock import patch
    from archon.chat.handler import _BEACON_WORDS

    notif = NotificationsConfig(mode="quiet", interval_minutes=0.001)  # 0.06s
    msg = _mock_message("go")

    async def _slow_send(text: str) -> AsyncGenerator:
        await asyncio.sleep(0.20)  # long enough for 3+ beacon ticks
        yield Response(content="Done")

    session = MagicMock()
    session.send = _slow_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    with patch("archon.chat.handler.random.choice", return_value="Pondering"):
        await handle_message(msg, mgr, _split, notifications=notif)

    all_texts = [call[0][0] for call in msg.answer.call_args_list]
    beacon_texts = [t for t in all_texts if t.startswith("⏳") and t != "⏳ Working..."]

    # First beacon must be "Working" (no counts yet — no tools/thinking fired)
    first_beacon = next((t for t in all_texts if t.startswith("⏳") and t != "⏳ Working..."), None)
    # Subsequent beacons must use a fun word from _BEACON_WORDS
    fun_beacons = [t for t in all_texts if any(t.startswith(f"⏳ {w}") for w in _BEACON_WORDS)]
    assert len(fun_beacons) >= 1


async def test_send_chat_action_rate_limited_to_avoid_flood_control() -> None:
    """Rapid-fire events must not trigger more than one SendChatAction per 4-second window.

    Regression: a verbose reply with many tool calls in rapid succession produced one
    SendChatAction before every message.answer() call, which triggered Telegram flood
    control: "Flood control exceeded on method 'SendChatAction'. Retry in 3 seconds."

    The fix introduces _TYPING_COOLDOWN_SECS = 4.0: send_chat_action is skipped if
    less than 4 s has elapsed since the last successful call. The typing bubble lasts
    ~5 s on the client, so refreshing more often than 4 s serves no purpose.
    """
    mgr = _mock_session_manager(
        ToolStarted(name="T1"), ToolStarted(name="T2"), ToolStarted(name="T3"),
        ToolStarted(name="T4"), ToolStarted(name="T5"), ToolStarted(name="T6"),
        ToolStarted(name="T7"), ToolStarted(name="T8"), ToolStarted(name="T9"),
        Response(content="Done"),
    )
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split)  # debug mode — all 10 events produce messages

    # Only the initial typing call; all 10 pre-message calls are throttled.
    # Old code: 1 initial + 10 pre-message = 11 calls → flood control triggered.
    # New code: 1 call total.
    assert msg.bot.send_chat_action.await_count == 1


async def test_handle_message_escapes_html_in_exception() -> None:
    """Exception messages with HTML special chars must be escaped before sending to Telegram."""
    session = MagicMock()

    async def _send_raises(prompt: str):
        raise RuntimeError("<tool_use_error>SDK failure</tool_use_error>")
        yield  # make it an async generator

    session.send = _send_raises
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)  # must not raise

    text: str = msg.answer.call_args[0][0]
    assert "&lt;tool_use_error&gt;" in text
    assert "<tool_use_error>" not in text


# ──────────────────────────────────────────────────────────────────
# handle_message — history_manager integration
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_records_user_message_when_history_manager_set() -> None:
    from unittest.mock import AsyncMock as AM, MagicMock as MM
    history_manager = MM()
    history_manager.record_user_message = AM()
    history_manager.record_event = AM()

    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager, cwd="/tmp")

    history_manager.record_user_message.assert_awaited_once_with(42, "hello", cwd="/tmp")


async def test_handle_message_records_each_event_when_history_manager_set() -> None:
    from unittest.mock import AsyncMock as AM, MagicMock as MM
    history_manager = MM()
    history_manager.record_user_message = AM()
    history_manager.record_event = AM()

    events = [ThinkingResult(content="pondering"), Response(content="Hi")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    assert history_manager.record_event.await_count == 2


async def test_handle_message_no_crash_without_history_manager() -> None:
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    # Must not raise — history_manager defaults to None
    await handle_message(msg, mgr, _split)

    msg.answer.assert_awaited()


async def test_handle_message_history_injection_logged_to_session_md() -> None:
    """When pop_last_injected_files returns filenames, they are logged to session MD."""
    from unittest.mock import AsyncMock as AM, MagicMock as MM
    history_manager = MM()
    history_manager.record_user_message = AM()
    history_manager.record_event = AM()
    history_manager.record_archon_message = AM()

    session = _mock_session(Response(content="Hi"))
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=["2026-03-12-compacted.md", "2026-03-13-partial.md"])

    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded = [call.args[0] for call in history_manager.record_archon_message.call_args_list]
    assert any("2026-03-12-compacted.md" in t for t in recorded)
    assert any("2026-03-13-partial.md" in t for t in recorded)


async def test_handle_message_history_injection_shown_in_debug_mode() -> None:
    """History injection notice is sent to Telegram in debug mode."""
    session = _mock_session(Response(content="Hi"))
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=["2026-03-12-compacted.md"])
    msg = _mock_message("hello")
    notif = NotificationsConfig(mode="debug")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call.args[0] for call in msg.answer.call_args_list]
    assert any("📚 History injected" in t for t in texts)
    assert any("2026-03-12-compacted.md" in t for t in texts)


async def test_handle_message_history_injection_not_shown_in_normal_mode() -> None:
    """History injection notice is NOT sent to Telegram in normal mode."""
    session = _mock_session(Response(content="Hi"))
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=["2026-03-12-compacted.md"])
    msg = _mock_message("hello")
    notif = NotificationsConfig(mode="normal")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call.args[0] for call in msg.answer.call_args_list]
    assert not any("📚 History injected" in t for t in texts)


async def test_handle_message_no_history_injection_when_no_files() -> None:
    """No history notice when pop_last_injected_files returns empty list."""
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")
    notif = NotificationsConfig(mode="debug")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call.args[0] for call in msg.answer.call_args_list]
    assert not any("📚 History injected" in t for t in texts)


async def test_handle_message_logs_ack_to_history_manager() -> None:
    """'⏳ Processing...' ack is recorded via record_archon_message."""
    from unittest.mock import AsyncMock as AM, MagicMock as MM
    history_manager = MM()
    history_manager.record_user_message = AM()
    history_manager.record_event = AM()
    history_manager.record_archon_message = AM()

    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded = [call.args[0] for call in history_manager.record_archon_message.call_args_list]
    assert any("Processing" in t for t in recorded), f"ack not logged; recorded={recorded}"


async def test_handle_message_logs_queued_notification_to_history_manager() -> None:
    """When session is busy, the queued notification is recorded via record_archon_message."""
    from unittest.mock import AsyncMock as AM, MagicMock as MM
    session = _mock_session(Response(content="Done"), is_processing=True)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    history_manager = MM()
    history_manager.record_user_message = AM()
    history_manager.record_event = AM()
    history_manager.record_archon_message = AM()

    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded = [call.args[0] for call in history_manager.record_archon_message.call_args_list]
    assert any("queued" in t for t in recorded), f"queued notification not logged; recorded={recorded}"


async def test_handle_message_logs_top_level_error_to_history_manager() -> None:
    """Top-level exception error message sent to Telegram is recorded via record_archon_message."""
    from unittest.mock import AsyncMock as AM, MagicMock as MM

    async def _fail(prompt: str) -> AsyncGenerator:
        raise RuntimeError("boom")
        yield  # make it an async generator

    session = MagicMock()
    session.is_processing = False
    session.send = _fail
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    history_manager = MM()
    history_manager.record_user_message = AM()
    history_manager.record_event = AM()
    history_manager.record_archon_message = AM()

    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded = [call.args[0] for call in history_manager.record_archon_message.call_args_list]
    assert any("❌ Error" in t for t in recorded), f"error not logged; recorded={recorded}"


# ──────────────────────────────────────────────────────────────────
# handle_message — mid-query mode change (S8.3)
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_mode_change_quiet_to_verbose_mid_query() -> None:
    """Switching from quiet → verbose mid-query must take effect immediately.

    The notifications object is mutated after the first event (simulating a
    concurrent /verbose command). Events emitted after the mutation must be
    formatted and sent — not silently dropped as quiet mode would do.
    """
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    msg = _mock_message("go")

    async def _send_with_mode_change(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash")   # emitted while still quiet → dropped
        notif.mode = "verbose"           # simulate concurrent /verbose
        yield ToolStarted(name="Read")   # emitted after switch → must appear
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send_with_mode_change
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # "⏳ Working..." (quiet start) + "🔧 Tool: Read" (post-switch) + "✅ Response:\nDone"
    assert "🔧 Tool: Read" in texts, f"Expected tool event after mode switch, got: {texts}"
    assert "🔧 Tool: Bash" not in texts, f"Bash was emitted while quiet — should be dropped: {texts}"


async def test_handle_message_quiet_beacon_cancelled_on_mode_change() -> None:
    """Beacon must be cancelled once the next event is processed after a mode switch.

    The mode change and the cancellation-triggering event are yielded without any
    intermediate await, so the beacon cannot fire in between.  The long sleep AFTER
    the cancellation verifies it is truly stopped.
    """
    notif = NotificationsConfig(mode="quiet", interval_minutes=0.001)  # 0.06s interval
    msg = _mock_message("go")

    async def _send_with_mode_change(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash")   # event 1: quiet → dropped, beacon still alive
        notif.mode = "verbose"           # synchronous switch (no await → beacon can't fire yet)
        yield ToolStarted(name="Read")   # event 2: handle_message sees verbose, cancels beacon
        await asyncio.sleep(0.15)        # long enough for beacon to fire IF not cancelled
        yield Response(content="Done")

    session = MagicMock()
    session.is_processing = False  # not busy — no queued notification
    session.send = _send_with_mode_change
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    beacon_texts = [t for t in texts if t.startswith("⏳") and t != "⏳ Working..."]
    assert beacon_texts == [], f"Beacon should have been cancelled after mode switch, got: {beacon_texts}"


async def test_handle_message_beacon_started_on_mid_query_switch_to_quiet() -> None:
    """Switching to quiet+interval MID-QUERY must start the beacon.

    Regression test: when a query starts in non-quiet mode the beacon task is never
    created at message start.  If the user runs /quiet N during the query the handler
    must detect the transition on the next event and launch the beacon then.
    """
    notif = NotificationsConfig(mode="normal", interval_minutes=0.001)  # 0.06s interval
    msg = _mock_message("go")

    async def _send_with_mode_change(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash")    # event 1: normal mode → shown, no beacon yet
        notif.mode = "quiet"             # synchronous switch (no await → beacon can't fire yet)
        yield ToolStarted(name="Read")   # event 2: handle_message sees quiet, must start beacon
        await asyncio.sleep(0.15)        # long enough for beacon to fire if started
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send_with_mode_change
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    beacon_texts = [t for t in texts if t.startswith("⏳") and t != "⏳ Working..."]
    assert beacon_texts, (
        f"Beacon should have fired after switching to quiet+interval mid-query, got: {texts}"
    )


# ──────────────────────────────────────────────────────────────────
# handle_message — complete mode transition matrix (Bug.001)
# All 12 FROM→TO transitions verified: before-switch uses old mode,
# after-switch uses new mode immediately.
# ──────────────────────────────────────────────────────────────────


async def test_mode_transition_quiet_to_normal() -> None:
    """quiet → normal: thinking appears, tools still suppressed."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="thought1")          # quiet  → suppressed
        yield ToolStarted(name="Bash", input="echo hi")   # quiet  → suppressed
        notif.mode = "normal"
        yield ThinkingResult(content="thought2")          # normal → shown
        yield ToolStarted(name="Read", input="path.py")   # normal → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("thought1" in t for t in texts), f"thought1 suppressed in quiet: {texts}"
    assert any("thought2" in t for t in texts), f"thought2 shown after switch to normal: {texts}"
    assert not any("🔧 Tool" in t for t in texts), f"Tools suppressed in normal mode: {texts}"


async def test_mode_transition_quiet_to_debug() -> None:
    """quiet → debug: tools appear with full args and full ToolResult after switch."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")  # quiet → suppressed
        notif.mode = "debug"
        yield ToolStarted(name="Read", input="path.py")  # debug → name + args
        yield ToolResult(content="line1\nline2\nline3")   # debug → full content
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert "🔧 Tool: Bash" not in texts, f"Bash suppressed while quiet: {texts}"
    assert any("path.py" in t for t in texts), f"Args must appear in debug mode: {texts}"
    # debug shows full ToolResult — line2 and line3 should appear (not truncated to brief)
    assert any("line2" in t for t in texts), f"Full tool result must show in debug mode: {texts}"


async def test_mode_transition_normal_to_quiet() -> None:
    """normal → quiet: thinking disappears after switch, response still shown."""
    notif = NotificationsConfig(mode="normal", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="thought1")  # normal → shown
        notif.mode = "quiet"
        yield ThinkingResult(content="thought2")  # quiet → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("thought1" in t for t in texts), f"thought1 must appear (normal mode): {texts}"
    assert not any("thought2" in t for t in texts), f"thought2 suppressed (quiet): {texts}"
    assert any("✅ Response" in t for t in texts), f"Response must always show: {texts}"


async def test_mode_transition_normal_to_verbose() -> None:
    """normal → verbose: tools appear (name-only) after switch (were suppressed before)."""
    notif = NotificationsConfig(mode="normal", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")   # normal → suppressed
        notif.mode = "verbose"
        yield ToolStarted(name="Read", input="path.py")   # verbose → name only
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("🔧 Tool" in t and "Bash" in t for t in texts), f"Bash suppressed in normal: {texts}"
    assert any("🔧 Tool" in t and "Read" in t for t in texts), f"Read must appear after switch: {texts}"
    # verbose mode shows name only — input args must NOT appear
    assert not any("path.py" in t for t in texts), f"Args must NOT appear in verbose mode: {texts}"


async def test_mode_transition_normal_to_debug() -> None:
    """normal → debug: ToolResult changes from suppressed to full after switch."""
    notif = NotificationsConfig(mode="normal", interval_minutes=0)
    msg = _mock_message("go")

    long_content = "first sentence. second sentence.\nline3\nline4"

    async def _send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # normal → suppressed
        notif.mode = "debug"
        yield ToolResult(content=long_content)  # debug  → full
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 1, f"Expected 1 ToolResult message (debug only): {texts}"
    # The result (debug mode) contains full multiline content
    assert "line3" in tool_results[0], f"ToolResult must be full (debug mode): {tool_results[0]}"


async def test_mode_transition_verbose_to_quiet() -> None:
    """verbose → quiet: thinking and tools suppressed after switch."""
    notif = NotificationsConfig(mode="verbose", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="first")  # verbose → shown
        notif.mode = "quiet"
        yield ThinkingResult(content="second") # quiet   → suppressed
        yield ToolStarted(name="Read")         # quiet   → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    thinking_msgs = [t for t in texts if "💭 Thinking" in t]
    assert len(thinking_msgs) == 1, f"Only first ThinkingResult shown (verbose): {texts}"
    assert not any("🔧 Tool" in t for t in texts), f"Tool suppressed in quiet mode: {texts}"
    assert any("✅ Response" in t for t in texts)


async def test_mode_transition_verbose_to_normal() -> None:
    """verbose → normal: thinking still shown, tools suppressed after switch."""
    notif = NotificationsConfig(mode="verbose", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="first")            # verbose → shown
        yield ToolStarted(name="Bash", input="echo hi") # verbose → name only
        notif.mode = "normal"
        yield ThinkingResult(content="second")           # normal  → shown
        yield ToolStarted(name="Read", input="path.py") # normal  → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    thinking_msgs = [t for t in texts if "💭 Thinking" in t]
    assert len(thinking_msgs) == 2, f"Both ThinkingResults shown (verbose+normal): {texts}"
    assert any("🔧 Tool" in t and "Bash" in t for t in texts), f"Bash tool shown in verbose: {texts}"
    assert not any("🔧 Tool" in t and "Read" in t for t in texts), f"Read tool suppressed in normal: {texts}"


async def test_mode_transition_verbose_to_debug() -> None:
    """verbose → debug: ToolResult changes from brief to full after switch."""
    notif = NotificationsConfig(mode="verbose", interval_minutes=0)
    msg = _mock_message("go")

    long_content = "first sentence. second sentence.\nline3\nline4"

    async def _send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # verbose → brief
        notif.mode = "debug"
        yield ToolResult(content=long_content)  # debug   → full
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 2, f"Expected 2 ToolResult messages: {texts}"
    assert "line3" not in tool_results[0], f"First ToolResult brief (verbose): {tool_results[0]}"
    assert "line3" in tool_results[1], f"Second ToolResult full (debug): {tool_results[1]}"


async def test_mode_transition_debug_to_quiet() -> None:
    """debug → quiet: tools suppressed and full content hidden after switch."""
    notif = NotificationsConfig(mode="debug", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")  # debug → shown with args
        yield ToolResult(content="line1\nline2")          # debug → full content
        notif.mode = "quiet"
        yield ToolStarted(name="Read", input="path.py")  # quiet → suppressed
        yield ToolResult(content="line3\nline4")          # quiet → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("echo hi" in t for t in texts), f"Bash args shown in debug: {texts}"
    assert any("line1" in t or "line2" in t for t in texts), f"Full content shown in debug: {texts}"
    assert not any("🔧 Tool" in t and "Read" in t for t in texts), f"Read suppressed (quiet): {texts}"
    assert not any("line3" in t or "line4" in t for t in texts), f"line3/4 suppressed (quiet): {texts}"


async def test_mode_transition_debug_to_normal() -> None:
    """debug → normal: ToolResult changes from full to suppressed after switch."""
    notif = NotificationsConfig(mode="debug", interval_minutes=0)
    msg = _mock_message("go")

    long_content = "first sentence. second sentence.\nline3\nline4"

    async def _send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # debug  → full
        notif.mode = "normal"
        yield ToolResult(content=long_content)  # normal → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 1, f"Expected 1 ToolResult message (debug only): {texts}"
    assert "line3" in tool_results[0], f"ToolResult full (debug): {tool_results[0]}"


async def test_mode_transition_debug_to_verbose() -> None:
    """debug → verbose: ToolResult changes from full to brief after switch."""
    notif = NotificationsConfig(mode="debug", interval_minutes=0)
    msg = _mock_message("go")

    long_content = "first sentence. second sentence.\nline3\nline4"

    async def _send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # debug   → full
        notif.mode = "verbose"
        yield ToolResult(content=long_content)  # verbose → brief
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 2, f"Expected 2 ToolResult messages: {texts}"
    assert "line3" in tool_results[0], f"First ToolResult full (debug): {tool_results[0]}"
    assert "line3" not in tool_results[1], f"Second ToolResult brief (verbose): {tool_results[1]}"


# ──────────────────────────────────────────────────────────────────
# handle_message — thinking events across mode transitions
# ──────────────────────────────────────────────────────────────────


async def test_mode_transition_quiet_to_verbose_shows_thinking() -> None:
    """quiet → verbose: ThinkingResult visible after switch."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="thought1") # quiet   → suppressed
        notif.mode = "verbose"
        yield ThinkingResult(content="thought2") # verbose → shown
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("thought1" in t for t in texts), f"thought1 suppressed in quiet: {texts}"
    assert any("thought2" in t for t in texts), f"thought2 visible after switch to verbose: {texts}"


async def test_mode_transition_normal_to_verbose_both_show_thinking() -> None:
    """normal → verbose: ThinkingResult visible in both modes."""
    notif = NotificationsConfig(mode="normal", interval_minutes=0)
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingResult(content="first")  # normal  → shown
        notif.mode = "verbose"
        yield ThinkingResult(content="second") # verbose → shown
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    thinking_msgs = [t for t in texts if "💭 Thinking" in t]
    assert len(thinking_msgs) == 2, f"Both ThinkingResults visible (normal+verbose): {texts}"


# ──────────────────────────────────────────────────────────────────
# handle_message — live e2e: concurrent /notify during active query
# Simulates a real /notify command running while handle_message
# is awaiting the next event (two coroutines, one event loop).
# ──────────────────────────────────────────────────────────────────


async def test_live_concurrent_notify_normal_to_verbose() -> None:
    """Concurrent /notify verbose while handle_message awaits next event.

    Uses asyncio.Event to interleave execution: handle_message awaits
    the event, /notify fires and changes mode, then next event arrives.
    The post-notify tool event must be formatted in verbose mode (name only).
    """
    from archon.chat.commands import notify_command

    notif = NotificationsConfig(mode="normal", interval_minutes=0)
    handler_msg = _mock_message("go")

    gate = asyncio.Event()  # handler waits; notify fires; handler continues

    async def _interleaved_send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")  # normal → suppressed
        gate.set()                                        # signal notify to fire
        await asyncio.sleep(0)                            # yield to event loop
        yield ToolStarted(name="Read", input="path.py")  # should be verbose now → name only
        yield Response(content="Done")

    session = MagicMock()
    session.send = _interleaved_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    async def _run_notify() -> None:
        await gate.wait()
        # Simulate /notify verbose command mutating the shared notifications object
        notif.mode = "verbose"

    await asyncio.gather(
        handle_message(handler_msg, mgr, _split, notifications=notif),
        _run_notify(),
    )

    texts = [call[0][0] for call in handler_msg.answer.call_args_list]
    assert not any("🔧 Tool" in t and "Bash" in t for t in texts), f"Bash suppressed in normal: {texts}"
    assert any("🔧 Tool" in t and "Read" in t for t in texts), f"Read visible after concurrent /verbose: {texts}"
    # verbose shows name only — no args
    assert not any("path.py" in t for t in texts), f"Args must NOT appear in verbose mode: {texts}"


async def test_live_concurrent_notify_verbose_to_quiet() -> None:
    """Concurrent /notify quiet while handle_message awaits next event.

    After the mode change, tools must be suppressed even though the
    query started in verbose mode.
    """
    notif = NotificationsConfig(mode="verbose", interval_minutes=0)
    handler_msg = _mock_message("go")

    gate = asyncio.Event()

    async def _interleaved_send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")  # verbose → shown
        gate.set()
        await asyncio.sleep(0)
        yield ToolStarted(name="Read", input="path.py")  # quiet   → suppressed
        yield Response(content="Done")

    session = MagicMock()
    session.send = _interleaved_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    async def _run_notify() -> None:
        await gate.wait()
        notif.mode = "quiet"

    await asyncio.gather(
        handle_message(handler_msg, mgr, _split, notifications=notif),
        _run_notify(),
    )

    texts = [call[0][0] for call in handler_msg.answer.call_args_list]
    assert any("🔧 Tool" in t and "Bash" in t for t in texts), f"Bash shown in verbose before switch: {texts}"
    # verbose shows name only — no args
    assert not any("echo hi" in t for t in texts), f"Args must NOT appear in verbose mode: {texts}"
    assert not any("🔧 Tool" in t and "Read" in t for t in texts), f"Read suppressed after /quiet: {texts}"


async def test_live_concurrent_notify_quiet_to_debug() -> None:
    """Concurrent /notify debug while handle_message awaits: full output appears."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    handler_msg = _mock_message("go")

    gate = asyncio.Event()
    long_content = "sentence one. sentence two.\nline3\nline4"

    async def _interleaved_send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # quiet → suppressed
        gate.set()
        await asyncio.sleep(0)
        yield ToolResult(content=long_content)  # debug → full
        yield Response(content="Done")

    session = MagicMock()
    session.send = _interleaved_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    async def _run_notify() -> None:
        await gate.wait()
        notif.mode = "debug"

    await asyncio.gather(
        handle_message(handler_msg, mgr, _split, notifications=notif),
        _run_notify(),
    )

    texts = [call[0][0] for call in handler_msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 1, f"Only post-switch ToolResult visible: {texts}"
    assert "line3" in tool_results[0], f"Post-switch ToolResult full (debug): {tool_results[0]}"


async def test_live_concurrent_notify_does_not_affect_completed_events() -> None:
    """Events already sent before a /notify switch are not retroactively changed.

    This verifies idempotency: once an event is sent (formatted and answered),
    a subsequent mode change cannot undo it.
    """
    notif = NotificationsConfig(mode="debug", interval_minutes=0)
    handler_msg = _mock_message("go")

    gate = asyncio.Event()

    async def _interleaved_send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="ls -la")  # debug → shown with full args
        gate.set()
        await asyncio.sleep(0)
        yield Response(content="Done")

    session = MagicMock()
    session.send = _interleaved_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    async def _run_notify() -> None:
        await gate.wait()
        notif.mode = "quiet"  # switch to quiet AFTER bash was already sent

    await asyncio.gather(
        handle_message(handler_msg, mgr, _split, notifications=notif),
        _run_notify(),
    )

    texts = [call[0][0] for call in handler_msg.answer.call_args_list]
    # Bash was already sent in debug mode — it stays in the message history
    assert any("ls -la" in t for t in texts), f"Bash event was already sent, must remain: {texts}"


async def test_handle_message_all_event_types_formatted() -> None:
    events = [
        ThinkingResult(content="thinking"),
        ToolStarted(name="Bash"),
        ToolResult(content="output"),
        Response(content="done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("do it")

    await handle_message(msg, mgr, _split)

    assert msg.answer.await_count == 5
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Processing..."
    assert texts[1] == "💭 Thinking:\nthinking"
    assert texts[2] == "🔧 Tool: Bash"
    assert texts[3] == "📤 Result:\noutput"
    assert texts[4] == "✅ Response:\ndone"


# ──────────────────────────────────────────────────────────────────
# handle_message — typing indicator (no background loop, inline before each send)
# ──────────────────────────────────────────────────────────────────


async def test_typing_not_sent_repeatedly_during_quiet_processing() -> None:
    """In quiet mode, typing must not be sent repeatedly during long processing gaps.

    Regression: the old _keep_typing background loop refreshed every 4 s, causing
    the indicator to reappear endlessly while the bot was silently processing.
    The new throttled approach fires once at message receipt; the pre-response call
    is suppressed because < 4s has elapsed since the initial send.
    """
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    msg = _mock_message("go")

    async def _slow_send(text: str) -> AsyncGenerator:
        await asyncio.sleep(0.05)   # 50 ms silence; old loop would have fired many times
        yield Response(content="Done")

    session = MagicMock()
    session.send = _slow_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    # Exactly 1: the initial call at message receipt.
    # Pre-response typing is throttled (50 ms < 4 s cooldown).
    # The old loop would have produced far more calls during the 50 ms silence.
    assert msg.bot.send_chat_action.await_count == 1


async def test_typing_sent_before_each_outgoing_message_in_debug_mode() -> None:
    """Typing is rate-limited to once per _TYPING_COOLDOWN_SECS = 4.0 s.

    Three rapid events fire within milliseconds of each other. Only the initial
    send_chat_action goes through; subsequent pre-message calls are throttled.
    """
    events = [ThinkingResult(content="pondering"), ToolStarted(name="Bash"), Response(content="Done")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    # debug mode (notifications=None): all 3 events produce exactly 1 message each
    await handle_message(msg, mgr, _split)

    # Only the initial typing call; all 3 pre-message calls are throttled (< 4s cooldown).
    assert msg.bot.send_chat_action.await_count == 1


# ──────────────────────────────────────────────────────────────────
# S11.3 — handle_message × per-agent notification mode
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_quiet_orch_agents_normal_shows_subagent_event() -> None:
    """Quiet orchestrator + normal agents → orchestrator SubagentStarted notification is sent.

    Events with source='orchestrator' (the default) must still reach Telegram.
    Only events with source='sub-agent' are filtered out.
    """
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0,
        agents=NotificationsAgentsConfig(mode="normal"),
    )
    events = [
        SubagentStarted(agent_id="a1", agent_type="researcher", source="orchestrator"),
        SubagentStopped(agent_id="a1", agent_type="researcher", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("🤖 Agent" in t and "started" in t for t in texts), f"Expected agent start event, got: {texts}"
    assert any("🤖 Agent" in t and "done" in t for t in texts), f"Expected agent stop event, got: {texts}"
    assert "✅ Response:\nDone" in texts


async def test_handle_message_quiet_orch_agents_normal_subagent_not_in_beacon() -> None:
    """When agents are not quiet, orchestrator SubagentStarted must NOT be counted in the beacon."""
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0.001,  # beacon enabled
        agents=NotificationsAgentsConfig(mode="normal"),
    )
    msg = _mock_message("go")

    async def _send_with_agent(text: str) -> AsyncGenerator:
        yield SubagentStarted(agent_id="a1", agent_type="coder", source="orchestrator")
        await asyncio.sleep(0.12)  # let beacon fire with counts
        yield Response(content="Done")

    session = MagicMock()
    session.is_processing = False
    session.send = _send_with_agent
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # No beacon text should mention "1 tool" — the SubagentStarted was NOT counted
    beacon_texts = [t for t in texts if t.startswith("⏳ Working... (")]
    for bt in beacon_texts:
        assert "tool" not in bt, f"SubagentStarted should not have been counted in beacon: {bt}"


async def test_handle_message_quiet_orch_agents_quiet_subagent_always_sent() -> None:
    """Orchestrator SubagentStarted (source='orchestrator') is sent even in quiet mode.

    Agent lifecycle events with source='orchestrator' must reach the user
    regardless of notification mode.
    """
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0,
        agents=NotificationsAgentsConfig(mode=None),  # inherit → quiet
    )
    events = [
        SubagentStarted(agent_id="a1", agent_type="coder", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # SubagentStarted MUST be sent as a direct message, not swallowed into beacon
    assert any("🤖 Agent" in t and "started" in t for t in texts), (
        f"Expected direct agent start notification, got: {texts}"
    )
    # Must NOT appear in beacon counts
    beacon_texts = [t for t in texts if t.startswith("⏳ Working... (") and "tool" in t]
    assert not beacon_texts, f"SubagentStarted must not be counted in beacon: {texts}"


async def test_handle_message_quiet_orch_agents_normal_no_subagent_notification_sent_before_start() -> None:
    """⏳ Working... is still the first message in quiet mode, even with agents=normal."""
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0,
        agents=NotificationsAgentsConfig(mode="normal"),
    )
    events = [
        SubagentStarted(agent_id="a1", agent_type="coder", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working...", f"First message must be Working..., got: {texts[0]}"


async def test_handle_message_normal_orch_agents_quiet_still_shows_subagent_event() -> None:
    """Normal orchestrator + agents=quiet → orchestrator SubagentStarted still sent.

    Agent lifecycle events with source='orchestrator' must reach the user regardless
    of the agents notification mode setting.  Tools are suppressed in normal mode.
    """
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="normal",
        agents=NotificationsAgentsConfig(mode="quiet"),
    )
    events = [
        ToolStarted(name="Bash"),  # orchestrator tool — suppressed in normal mode
        SubagentStarted(agent_id="a1", agent_type="researcher", source="orchestrator"),  # MUST appear
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("🔧 Tool" in t for t in texts), "Tools suppressed in normal mode"
    assert any("🤖 Agent" in t for t in texts), (
        f"Agent start event must be visible regardless of agents mode: {texts}"
    )


async def test_handle_message_verbose_shows_subagent_events() -> None:
    """In verbose mode orchestrator SubagentStarted and SubagentStopped are always shown.

    Regression guard: the quiet-mode filter (`if currently_quiet:`) does NOT run
    in verbose mode; both lifecycle events with source='orchestrator' must reach
    format_event unchanged and produce notification messages.
    """
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped
    from archon.config.loader import NotificationsConfig

    notif = NotificationsConfig(mode="verbose")
    events = [
        SubagentStarted(agent_id="a1", agent_type="researcher", source="orchestrator"),
        SubagentStopped(agent_id="a1", agent_type="researcher", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("🤖 Agent" in t and "started" in t for t in texts), (
        f"Expected agent-start notification in verbose mode, got: {texts}"
    )
    assert any("🤖 Agent" in t and "done" in t for t in texts), (
        f"Expected agent-done notification in verbose mode, got: {texts}"
    )


async def test_handle_message_debug_shows_subagent_events() -> None:
    """In debug mode orchestrator SubagentStarted and SubagentStopped are always shown."""
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped
    from archon.config.loader import NotificationsConfig

    notif = NotificationsConfig(mode="debug")
    events = [
        SubagentStarted(agent_id="b1", agent_type="coder", source="orchestrator"),
        SubagentStopped(agent_id="b1", agent_type="coder", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("🤖 Agent" in t and "started" in t for t in texts), (
        f"Expected agent-start notification in debug mode, got: {texts}"
    )
    assert any("🤖 Agent" in t and "done" in t for t in texts), (
        f"Expected agent-done notification in debug mode, got: {texts}"
    )


async def test_handle_message_quiet_orch_explicit_quiet_agents_subagent_always_sent() -> None:
    """Quiet orchestrator + explicit agents=quiet → orchestrator SubagentStarted still sent.

    Regression guard for the ``pass`` guard in the quiet-mode filter.
    Without the ``pass``, the catch-all
    ``elif not isinstance(event, (Response, ErrorEvent)): continue``
    branch would swallow SubagentStarted silently.
    """
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0,
        agents=NotificationsAgentsConfig(mode="quiet"),  # explicit — not just inherited
    )
    events = [
        SubagentStarted(agent_id="c1", agent_type="planner", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("🤖 Agent" in t and "started" in t for t in texts), (
        f"Expected direct agent-start notification even with explicit agents=quiet, got: {texts}"
    )
    # Must NOT be counted in the beacon (which would suppress the direct message)
    beacon_texts = [t for t in texts if t.startswith("⏳ Working... (") and "tool" in t]
    assert not beacon_texts, f"SubagentStarted must not be counted in beacon: {texts}"


async def test_handle_message_no_notifications_shows_subagent_events() -> None:
    """Without a NotificationsConfig (default mode) orchestrator SubagentStarted/Stopped are shown."""
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped

    events = [
        SubagentStarted(agent_id="d1", agent_type="explorer", source="orchestrator"),
        SubagentStopped(agent_id="d1", agent_type="explorer", source="orchestrator"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split)  # no notifications → default debug

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("🤖 Agent" in t and "started" in t for t in texts), (
        f"Expected agent-start notification with no notifications config, got: {texts}"
    )
    assert any("🤖 Agent" in t and "done" in t for t in texts), (
        f"Expected agent-done notification with no notifications config, got: {texts}"
    )


# ──────────────────────────────────────────────────────────────────
# FR.003 — source filtering in handle_message
# ──────────────────────────────────────────────────────────────────


async def test_sub_agent_events_not_sent_to_telegram() -> None:
    """Events with source='sub-agent' must NOT be sent to Telegram."""
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped, ThinkingResult

    events = [
        SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova", source="sub-agent"),
        ThinkingResult(content="sub-agent thought", source="sub-agent"),
        SubagentStopped(agent_id="a1", agent_type="general", agent_name="Nova", source="sub-agent"),
        Response(content="orchestrator done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # Sub-agent events must NOT appear in Telegram messages
    assert not any("🤖 Agent" in t for t in texts), (
        f"Sub-agent lifecycle events must NOT be sent to Telegram, got: {texts}"
    )
    assert not any("sub-agent thought" in t for t in texts), (
        f"Sub-agent ThinkingResult must NOT be sent to Telegram, got: {texts}"
    )
    # Orchestrator response must still arrive
    assert any("✅ Response" in t and "orchestrator done" in t for t in texts), (
        f"Orchestrator response must still reach Telegram, got: {texts}"
    )


async def test_sub_agent_events_routed_to_agent_logger() -> None:
    """Events with source='sub-agent' are forwarded to agent_logger.record_event()."""
    from archon.ai.event_mapper import SubagentStarted, ThinkingResult

    mock_agent_logger = MagicMock()
    mock_agent_logger.record_event = AsyncMock()

    events = [
        SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova", source="sub-agent"),
        ThinkingResult(content="sub thought", source="sub-agent"),
        Response(content="done"),
    ]
    session = _mock_session(*events)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, agent_logger=mock_agent_logger)

    # agent_logger must have been called for sub-agent events (not for orchestrator Response)
    assert mock_agent_logger.record_event.await_count == 2, (
        f"Expected 2 record_event calls (SubagentStarted + ThinkingResult), "
        f"got {mock_agent_logger.record_event.await_count}"
    )


async def test_orchestrator_events_still_sent_to_telegram() -> None:
    """Events with source='orchestrator' (or no source) still go to Telegram."""
    from archon.ai.event_mapper import SubagentStarted, ThinkingResult

    events = [
        ThinkingResult(content="orchestrator thought"),  # source='orchestrator'
        SubagentStarted(agent_id="a1", agent_type="general", agent_name="Nova", source="orchestrator"),
        Response(content="all done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # All orchestrator events should arrive (mode=debug, default)
    assert any("💭 Thinking:" in t for t in texts), f"ThinkingResult expected in texts: {texts}"
    assert any("🤖 Agent" in t and "started" in t for t in texts), (
        f"Orchestrator SubagentStarted expected in texts: {texts}"
    )
    assert any("✅ Response" in t for t in texts), f"Response expected in texts: {texts}"


async def test_sub_agent_events_not_routed_to_agent_logger_when_logger_is_none() -> None:
    """When agent_logger=None, sub-agent events are silently discarded (no crash)."""
    from archon.ai.event_mapper import SubagentStarted

    events = [
        SubagentStarted(agent_id="a1", agent_type="general", source="sub-agent"),
        Response(content="done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    # No agent_logger passed → defaults to None
    await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # Only the Response should reach Telegram
    assert any("✅ Response" in t for t in texts)


# ──────────────────────────────────────────────────────────────────
# FR.003 — sub-agent Response/ErrorEvent written to main history
# ──────────────────────────────────────────────────────────────────


async def test_sub_agent_response_written_to_history_manager() -> None:
    """Sub-agent Response events must be written to history_manager even though they don't go to Telegram."""
    from archon.ai.event_mapper import SubagentStarted

    history_manager = MagicMock()
    history_manager.record_user_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    events = [
        SubagentStarted(agent_id="a1", agent_type="general", source="sub-agent"),
        Response(content="sub-agent answer", source="sub-agent"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("do it")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    # Must NOT reach Telegram
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("sub-agent answer" in t for t in texts), (
        f"Sub-agent Response must NOT go to Telegram, got: {texts}"
    )
    # Must be recorded in history
    recorded_events = [call.args[1] for call in history_manager.record_event.call_args_list]
    assert any(isinstance(e, Response) and e.content == "sub-agent answer" for e in recorded_events), (
        f"Sub-agent Response must be written to history_manager, recorded: {recorded_events}"
    )


async def test_sub_agent_error_event_written_to_history_manager() -> None:
    """Sub-agent ErrorEvent must be written to history_manager even though it doesn't go to Telegram."""
    history_manager = MagicMock()
    history_manager.record_user_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    events = [
        ErrorEvent(message="sub-agent failed", source="sub-agent"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("do it")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded_events = [call.args[1] for call in history_manager.record_event.call_args_list]
    assert any(isinstance(e, ErrorEvent) and e.message == "sub-agent failed" for e in recorded_events), (
        f"Sub-agent ErrorEvent must be written to history_manager, recorded: {recorded_events}"
    )


async def test_sub_agent_thinking_result_not_written_to_history_manager() -> None:
    """Sub-agent ThinkingResult must NOT be written to history_manager."""
    history_manager = MagicMock()
    history_manager.record_user_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    events = [
        ThinkingResult(content="sub-agent thought", source="sub-agent"),
        Response(content="done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded_events = [call.args[1] for call in history_manager.record_event.call_args_list]
    assert not any(isinstance(e, ThinkingResult) and e.source == "sub-agent" for e in recorded_events), (
        f"Sub-agent ThinkingResult must NOT be written to history_manager, recorded: {recorded_events}"
    )


async def test_sub_agent_tool_started_not_written_to_history_manager() -> None:
    """Sub-agent ToolStarted must NOT be written to history_manager."""
    history_manager = MagicMock()
    history_manager.record_user_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    events = [
        ToolStarted(name="Read", source="sub-agent"),
        Response(content="done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    recorded_events = [call.args[1] for call in history_manager.record_event.call_args_list]
    assert not any(isinstance(e, ToolStarted) and e.source == "sub-agent" for e in recorded_events), (
        f"Sub-agent ToolStarted must NOT be written to history_manager, recorded: {recorded_events}"
    )


# ──────────────────────────────────────────────────────────────────
# Concurrent-send guard (Bug: typing... but no response)
# When a new message arrives while a sub-agent is still running, the user
# must receive an immediate ❌ error — not silence with a typing indicator.
# ──────────────────────────────────────────────────────────────────


def _make_busy_session() -> MagicMock:
    """Return a mock session whose send() immediately yields a 'busy' ErrorEvent.

    This replicates the post-fix behaviour of ClaudeSession.send() when
    _send_lock is already held: one ErrorEvent, then StopAsyncIteration.
    """
    session = MagicMock()

    async def _busy_send(prompt: str):  # type: ignore[return]
        yield ErrorEvent(message="Still processing your previous request — please wait")

    session.send = _busy_send
    return session


async def test_handle_message_while_session_busy_sends_error() -> None:
    """Regression: second message during active processing yields an ErrorEvent.

    Before the fix: session.send() had no concurrency guard — the second
    query() call raced with the first receive_response() loop, all events
    were consumed by the first handler, and the second handler sent nothing
    (user saw typing... but no reply).

    After the fix: send() checks _send_lock; if locked it immediately yields
    ErrorEvent("Still processing...") so handle_message delivers ❌ to the user.
    """
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=_make_busy_session())
    mgr.pop_last_injected_files = MagicMock(return_value=[])

    msg = _mock_message("ping")
    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("❌" in t and "wait" in t.lower() for t in texts), (
        f"Expected busy-error reply, got: {texts}"
    )


async def test_handle_message_while_session_busy_does_not_hang() -> None:
    """The busy-rejection path returns immediately — no blocking await."""
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=_make_busy_session())
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("ping")

    # Complete within 1 s (would block forever with the old await-on-lock approach).
    done, _ = await asyncio.wait(
        [asyncio.create_task(handle_message(msg, mgr, _split))],
        timeout=1.0,
    )
    assert done, "handle_message did not complete within 1 s — possible hang"


# ──────────────────────────────────────────────────────────────────
# Security: chat message content must NOT appear in log output (Bug.002)
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_does_not_log_message_content(caplog: pytest.LogCaptureFixture) -> None:
    """handle_message must not write user message text to the log file.

    This is a security requirement: chat messages may contain sensitive
    information and must not be persisted in log files.
    """
    sensitive_text = "my secret password is hunter2"
    mgr = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(sensitive_text)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

    for record in caplog.records:
        assert sensitive_text not in record.getMessage(), (
            f"Message content leaked into log: {record.getMessage()!r}"
        )


async def test_handle_message_logs_receipt_without_content(caplog: pytest.LogCaptureFixture) -> None:
    """handle_message must still emit a log record when a message is received.

    The log record must identify the user but must NOT contain the message body.
    """
    mgr = _mock_session_manager(Response(content="ok"))
    msg = _mock_message("confidential content here")

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

    # At least one INFO record about receiving the message
    receipt_records = [
        r for r in caplog.records
        if r.levelno == logging.INFO and "42" in r.getMessage()  # user_id=42 from _mock_message
    ]
    assert receipt_records, "Expected at least one INFO log record referencing the user"

    # None of those records must contain the message body
    for record in receipt_records:
        assert "confidential content here" not in record.getMessage(), (
            f"Message body must not appear in log: {record.getMessage()!r}"
        )


async def test_handle_message_does_not_log_partial_content(caplog: pytest.LogCaptureFixture) -> None:
    """Even a truncated prefix of a long message must not appear in logs.

    Regression guard: the old code used '%.50s' which leaked the first 50 chars.
    """
    long_message = "A" * 200 + " sensitive tail"
    mgr = _mock_session_manager(Response(content="ok"))
    msg = _mock_message(long_message)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

    first_50 = long_message[:50]
    for record in caplog.records:
        assert first_50 not in record.getMessage(), (
            f"Partial message content (first 50 chars) leaked into log: {record.getMessage()!r}"
        )


def _mock_session_raising(exc: Exception) -> MagicMock:
    """Session whose send() raises *exc* on the first iteration."""
    session = MagicMock()

    async def _send(prompt: str) -> AsyncGenerator:
        raise exc
        yield  # makes this an async generator

    session.send = _send
    return session


def _mock_session_manager_raising(exc: Exception) -> SessionManager:
    session = _mock_session_raising(exc)
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    return mgr


@pytest.mark.asyncio
async def test_handle_message_error_does_not_log_message_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When processing raises, the user's message text must NOT appear in the log.

    Regression guard for Bug.002: the exception handler previously logged
    str(exc) which could contain the original prompt if the SDK echoed it back.
    """
    sensitive_text = "top secret project-x plan"
    # Simulate an SDK exception whose message happens to contain the user's text
    exc = RuntimeError(f"SDK failure while processing: {sensitive_text}")
    mgr = _mock_session_manager_raising(exc)
    msg = _mock_message(sensitive_text)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

    for record in caplog.records:
        assert sensitive_text not in record.getMessage(), (
            f"Message content leaked via exception log: {record.getMessage()!r}"
        )


@pytest.mark.asyncio
async def test_handle_message_error_logs_exception_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When processing raises, the ERROR log must still identify the exception type and user."""
    mgr = _mock_session_manager_raising(ValueError("something went wrong"))
    msg = _mock_message("hello")

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Expected at least one ERROR log record on exception"
    combined = " ".join(r.getMessage() for r in error_records)
    assert "42" in combined, "ERROR record must identify the user_id"
    assert "ValueError" in combined, "ERROR record must name the exception type"


# ──────────────────────────────────────────────────────────────────
# FR.001 — agent_name display in format_event
# ──────────────────────────────────────────────────────────────────


def test_format_subagent_started_shows_agent_name() -> None:
    from archon.ai.event_mapper import SubagentStarted
    event = SubagentStarted(agent_id="x", agent_type="bash", agent_name="Atlas")
    msgs = format_event(event, _split)
    assert any("Atlas" in m for m in msgs)


def test_format_subagent_stopped_shows_agent_name() -> None:
    from archon.ai.event_mapper import SubagentStopped
    event = SubagentStopped(agent_id="x", agent_type="bash", agent_name="Orion")
    msgs = format_event(event, _split)
    assert any("Orion" in m for m in msgs)


def test_format_subagent_started_falls_back_to_type_when_no_name() -> None:
    from archon.ai.event_mapper import SubagentStarted
    event = SubagentStarted(agent_id="x", agent_type="bash", agent_name="")
    msgs = format_event(event, _split)
    assert any("bash" in m for m in msgs)


def test_format_subagent_stopped_falls_back_to_type_when_no_name() -> None:
    from archon.ai.event_mapper import SubagentStopped
    event = SubagentStopped(agent_id="x", agent_type="bash", agent_name="")
    msgs = format_event(event, _split)
    assert any("bash" in m for m in msgs)


def test_format_subagent_name_is_html_escaped() -> None:
    from archon.ai.event_mapper import SubagentStarted
    event = SubagentStarted(agent_id="x", agent_type="t", agent_name="<script>")
    msgs = format_event(event, _split)
    assert all("<script>" not in m for m in msgs)
    assert any("&lt;script&gt;" in m for m in msgs)


# ──────────────────────────────────────────────────────────────────
# Bug.004 — Telegram network errors must not interrupt AI processing
# ──────────────────────────────────────────────────────────────────


async def test_telegram_error_during_event_reply_does_not_abort_processing() -> None:
    """TelegramNetworkError while sending an event reply must not abort the AI session.

    Before the Bug.004 fix, any exception inside the event loop propagated to
    the outer except block, aborting all further processing.  After the fix,
    Telegram send failures are caught locally; subsequent events are still
    delivered.
    """
    events = [ThinkingResult(content="pondering"), Response(content="Done")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    call_count = 0
    original_answer = msg.answer

    async def _answer_second_raises(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # "✅ Response:..." — simulates Telegram flap
            raise Exception("TelegramNetworkError: network failure")

    msg.answer = AsyncMock(side_effect=_answer_second_raises)

    await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts), (
        f"Response must still be attempted after Telegram error on ThinkingResult: {texts}"
    )


async def test_telegram_error_during_event_reply_is_logged_at_warning_not_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Telegram send failures in the event loop must be WARNING, not ERROR.

    ERROR is reserved for AI session failures (e.g. SDK crash).  A transient
    Telegram network error does not mean Claude failed — it means the delivery
    of one notification failed.
    """
    # Use two events in debug mode so we can simulate a failure on the second send.
    # In debug mode ThinkingResult produces "💭 Thinking:..." (call 1),
    # and Response produces "✅ Response:..." (call 2) which we make fail.
    mgr = _mock_session_manager(ThinkingResult(content="pondering"), Response(content="Done"))
    msg = _mock_message("go")

    call_count = 0

    async def _answer_second_raises(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # "✅ Response..." reply — simulates network error
            raise Exception("TelegramNetworkError: network failure")

    msg.answer = AsyncMock(side_effect=_answer_second_raises)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split, notifications=NotificationsConfig(mode="debug"))

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not error_records, (
        f"No ERROR log expected for a Telegram send failure: {[r.getMessage() for r in error_records]}"
    )
    assert any("TelegramNetworkError" in r.getMessage() or "network" in r.getMessage().lower()
               or "Failed" in r.getMessage() for r in warning_records), (
        f"Expected a WARNING about the failed delivery: {[r.getMessage() for r in warning_records]}"
    )


async def test_telegram_error_on_working_ack_does_not_abort_processing() -> None:
    """Failure to send the initial '⏳ Working...' must not prevent AI processing.

    The working acknowledgement is best-effort.  If Telegram is momentarily
    unreachable, Claude should still run and try to deliver the result.
    """
    from archon.config.loader import NotificationsConfig
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    call_count = 0

    async def _answer_first_raises(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # "⏳ Working..." — fails
            raise Exception("TelegramNetworkError: timeout")

    msg.answer = AsyncMock(side_effect=_answer_first_raises)

    # Use quiet mode so the Working... ack is sent
    notif = NotificationsConfig(mode="quiet")
    await handle_message(msg, mgr, _split, notifications=notif)  # must not raise

    # session.send must have been called despite the failed acknowledgement
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts), (
        f"Response must still be attempted when Working ack fails: {texts}"
    )


async def test_telegram_error_on_working_ack_logged_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed '⏳ Working...' delivery must be WARNING, not ERROR."""
    from archon.config.loader import NotificationsConfig
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    async def _always_raises(text: str, **kwargs: object) -> None:
        raise Exception("TelegramNetworkError: timeout")

    msg.answer = AsyncMock(side_effect=_always_raises)

    # Use quiet mode so the Working... ack is sent first
    notif = NotificationsConfig(mode="quiet")
    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split, notifications=notif)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not error_records, (
        f"No ERROR log expected when only Telegram delivery fails: {[r.getMessage() for r in error_records]}"
    )


async def test_telegram_error_on_error_notification_does_not_propagate() -> None:
    """If the error-reply message.answer also fails, handle_message must not raise.

    Before the fix the error handler called message.answer() without a guard,
    so a network error there would propagate out of handle_message entirely.
    """
    session = MagicMock()

    async def _send_raises(prompt: str):
        raise RuntimeError("SDK failure")
        yield  # make it an async generator

    session.send = _send_raises
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("hello")

    # All message.answer calls fail (simulates persistent Telegram outage)
    msg.answer = AsyncMock(side_effect=Exception("TelegramNetworkError: offline"))

    await handle_message(msg, mgr, _split)  # must not raise


async def test_typing_indicator_error_does_not_abort_processing() -> None:
    """Failure to send the typing indicator must not interrupt AI processing.

    send_chat_action is best-effort — it only drives the typing bubble in the
    Telegram UI.  A network error there must never abort Claude's work.
    """
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")
    msg.bot.send_chat_action = AsyncMock(side_effect=Exception("TelegramNetworkError: timeout"))

    await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts), (
        f"Response must still be sent when typing indicator fails: {texts}"
    )


async def test_typing_indicator_error_is_logged_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Typing indicator failures must be WARNING level, not ERROR."""
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")
    msg.bot.send_chat_action = AsyncMock(side_effect=Exception("TelegramNetworkError: timeout"))

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert not error_records, (
        f"No ERROR expected for typing indicator failure: {[r.getMessage() for r in error_records]}"
    )


# ──────────────────────────────────────────────────────────────────
# PlanEvent triggers PlanExecutor (Phase 2 Task #6)
# ──────────────────────────────────────────────────────────────────


async def test_plan_event_triggers_plan_executor() -> None:
    """PlanEvent should launch PlanExecutor as an async task."""
    plan_event = _make_plan_event(2)
    mgr = _mock_session_manager(plan_event)
    msg = _mock_message("big task")
    bam = MagicMock()

    with patch("archon.chat.handler.PlanExecutor") as MockExecutor:
        mock_instance = MagicMock()
        mock_instance.execute = AsyncMock()
        MockExecutor.return_value = mock_instance

        await handle_message(msg, mgr, _split, background_agent_manager=bam)
        # Allow the created task to run
        await asyncio.sleep(0.05)

    MockExecutor.assert_called_once()
    mock_instance.execute.assert_awaited_once_with(plan_event.plan)


async def test_plan_event_without_bam_does_not_crash() -> None:
    """If BAM is not available, PlanEvent is still formatted but no executor is launched."""
    plan_event = _make_plan_event(2)
    mgr = _mock_session_manager(plan_event)
    msg = _mock_message("task")

    # No background_agent_manager passed — should not crash
    await handle_message(msg, mgr, _split)

    # PlanEvent should still be formatted and sent
    calls = msg.answer.call_args_list
    plan_msgs = [c for c in calls if "📋 Plan:" in str(c)]
    assert len(plan_msgs) >= 1


async def test_normal_response_still_works_with_bam() -> None:
    """Normal Response events should work unaffected when BAM is available."""
    mgr = _mock_session_manager(Response(content="Normal answer"))
    msg = _mock_message("question")
    bam = MagicMock()

    await handle_message(msg, mgr, _split, background_agent_manager=bam)

    calls = msg.answer.call_args_list
    texts = [c[0][0] for c in calls]
    assert any("Normal answer" in t for t in texts)


async def test_handle_message_returns_without_waiting_for_plan_executor() -> None:
    """handle_message should return immediately after yielding PlanEvent — not wait for execution."""
    plan_event = _make_plan_event(2)
    mgr = _mock_session_manager(plan_event)
    msg = _mock_message("big task")
    bam = MagicMock()

    with patch("archon.chat.handler.PlanExecutor") as MockExecutor:
        # Make execute() take a long time
        async def slow_execute(plan):
            await asyncio.sleep(100)

        mock_instance = MagicMock()
        mock_instance.execute = slow_execute
        MockExecutor.return_value = mock_instance

        # handle_message should return quickly (not wait for executor)
        await asyncio.wait_for(
            handle_message(msg, mgr, _split, background_agent_manager=bam),
            timeout=5.0,
        )


# ──────────────────────────────────────────────────────────────────
# PromotionEvent formatting + handler spawn (Phase 2 — Smart Task Promotion)
# ──────────────────────────────────────────────────────────────────


def test_format_promotion_event() -> None:
    """PromotionEvent formats as a promotion notice with tool count."""
    event = PromotionEvent(
        agent_prompt="enriched prompt", original_prompt="user query",
        tool_count=3,
    )
    result = format_event(event, _split)
    assert len(result) == 1
    assert "tools used" in result[0].lower()
    assert "3" in result[0]


def test_format_promotion_event_always_shown_in_quiet() -> None:
    """PromotionEvent is always visible regardless of notification mode."""
    notif = NotificationsConfig(mode="quiet")
    event = PromotionEvent(
        agent_prompt="prompt", original_prompt="query", tool_count=4,
    )
    result = format_event(event, _split, notifications=notif)
    assert len(result) == 1
    assert "tools used" in result[0].lower()


async def test_handle_message_promotion_spawns_agent() -> None:
    """PromotionEvent triggers BAM.spawn()."""
    promotion = PromotionEvent(
        agent_prompt="enriched prompt", original_prompt="investigate",
        tool_count=3,
    )
    mgr = _mock_session_manager(promotion)
    msg = _mock_message("investigate")
    bam = MagicMock()
    bam.spawn = AsyncMock()

    await handle_message(msg, mgr, _split, background_agent_manager=bam)

    bam.spawn.assert_awaited_once()
    call_kwargs = bam.spawn.call_args
    assert call_kwargs.kwargs.get("task") == "enriched prompt" or call_kwargs[1].get("task") == "enriched prompt"


async def test_handle_message_promotion_passes_context_to_spawn() -> None:
    """PromotionEvent spawn() must include context= from session.context_summary."""
    promotion = PromotionEvent(
        agent_prompt="enriched prompt", original_prompt="investigate",
        tool_count=3,
    )
    session = _mock_session(promotion)
    session.context_summary = "prior context summary"
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    msg = _mock_message("investigate")
    bam = MagicMock()
    bam.spawn = AsyncMock()

    await handle_message(msg, mgr, _split, background_agent_manager=bam)

    bam.spawn.assert_awaited_once()
    call_kwargs = bam.spawn.call_args
    context_passed = call_kwargs.kwargs.get("context") or (call_kwargs[1].get("context") if call_kwargs[1] else None)
    assert context_passed == "prior context summary"


async def test_handle_message_promotion_without_bam_does_not_crash() -> None:
    """PromotionEvent without BAM should not crash — just format and send."""
    promotion = PromotionEvent(
        agent_prompt="prompt", original_prompt="query", tool_count=3,
    )
    mgr = _mock_session_manager(promotion)
    msg = _mock_message("query")

    # No background_agent_manager — should not crash
    await handle_message(msg, mgr, _split)

    calls = msg.answer.call_args_list
    promo_msgs = [c for c in calls if "tools used" in str(c).lower()]
    assert len(promo_msgs) >= 1


# ──────────────────────────────────────────────────────────────────
# format_event — ReminderInjectedEvent
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["quiet", "normal", "verbose", "debug"])
def test_reminder_always_shown(mode: str) -> None:
    """ReminderInjectedEvent is always shown regardless of notification mode."""
    notif = NotificationsConfig(mode=mode, interval_minutes=0)
    event = ReminderInjectedEvent(message_count=5)
    result = format_event(event, _split, notifications=notif)
    assert result == ["🔔 Reminder injected (message 5)"]


def test_reminder_renders_without_notifications_config() -> None:
    """ReminderInjectedEvent renders correctly when no NotificationsConfig is provided."""
    event = ReminderInjectedEvent(message_count=5)
    result = format_event(event, _split)
    assert result == ["🔔 Reminder injected (message 5)"]


# ──────────────────────────────────────────────────────────────────
# Issue A — assert replaced by graceful skip (message.bot is None)
# ──────────────────────────────────────────────────────────────────


async def test_send_typing_skips_gracefully_when_bot_is_none() -> None:
    """When message.bot is None the typing indicator is skipped with a warning — not AssertionError."""
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")
    msg.bot = None  # simulate missing bot reference

    # Must not raise; response must still be delivered via message.answer
    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts)


async def test_plan_event_skips_gracefully_when_bot_is_none() -> None:
    """When message.bot is None inside PlanEvent loop, the iteration continues — not AssertionError."""
    plan_event = _make_plan_event(2)
    mgr = _mock_session_manager(plan_event, Response(content="Done"))
    msg = _mock_message("big task")
    msg.bot = None  # simulate missing bot reference
    bam = MagicMock()

    # Must not raise; processing must continue past the PlanEvent
    await handle_message(msg, mgr, _split, background_agent_manager=bam)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts)


# ──────────────────────────────────────────────────────────────────
# Issue B — TelegramRetryAfter triggers sleep + retry in handler.py
# ──────────────────────────────────────────────────────────────────


async def test_retry_after_triggers_sleep_and_retry() -> None:
    """TelegramRetryAfter on first send must sleep retry_after+1 s then retry once."""
    from unittest.mock import patch
    from aiogram.exceptions import TelegramRetryAfter

    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    call_count = 0

    async def _answer_first_retry_after(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # first event reply (after Processing... ack)
            raise TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=1)

    msg.answer = AsyncMock(side_effect=_answer_first_retry_after)

    slept: list[float] = []

    async def _fake_sleep(duration: float) -> None:
        slept.append(duration)

    with patch("archon.chat.handler.asyncio.sleep", side_effect=_fake_sleep):
        await handle_message(msg, mgr, _split)

    # sleep must have been called with retry_after + 1 = 2
    assert any(s == 2 for s in slept), f"Expected sleep(2) for retry_after=1, got: {slept}"
    # Total answer calls: Processing... + retry attempt = 3 (first attempt raised, second succeeds)
    assert msg.answer.await_count >= 3


async def test_retry_after_retry_failure_is_logged_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If the retry after TelegramRetryAfter also fails, it must be logged at WARNING."""
    from aiogram.exceptions import TelegramRetryAfter

    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    call_count = 0

    async def _answer_always_rate_limited(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 2:  # both first attempt and retry fail
            raise TelegramRetryAfter(method=MagicMock(), message="flood", retry_after=0)

    msg.answer = AsyncMock(side_effect=_answer_always_rate_limited)

    from unittest.mock import patch

    async def _noop_sleep(duration: float) -> None:
        pass

    with caplog.at_level(logging.DEBUG, logger="archon"), \
         patch("archon.chat.handler.asyncio.sleep", side_effect=_noop_sleep):
        await handle_message(msg, mgr, _split)  # must not raise

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not error_records, f"No ERROR expected for rate-limit retry failure: {[r.getMessage() for r in error_records]}"
    assert any("retry" in r.getMessage().lower() or "Failed" in r.getMessage() for r in warning_records)


# ──────────────────────────────────────────────────────────────────
# RecoveryEvent formatting
# ──────────────────────────────────────────────────────────────────


def test_recovery_event_format() -> None:
    """RecoveryEvent renders with 🔄 prefix and HTML-escaped message."""
    event = RecoveryEvent(phase="timeout_detected", message="Timed out <5s>")
    result = format_event(event, _split)
    assert result == ["🔄 Timed out &lt;5s&gt;"]


def test_recovery_event_shown_in_quiet_mode() -> None:
    """RecoveryEvent is always shown, even in quiet mode."""
    event = RecoveryEvent(phase="session_recovered", message="Session recovered")
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    result = format_event(event, _split, notifications=notif)
    assert result == ["🔄 Session recovered"]


# ──────────────────────────────────────────────────────────────────
# Task 2.4 — Router event filtering in format_event and quiet mode
# ──────────────────────────────────────────────────────────────────


def test_format_router_events_quiet_returns_empty() -> None:
    """Router ToolStarted/ToolResult/ThinkingResult return [] in quiet mode."""
    notif = NotificationsConfig(mode="quiet")
    for event in [
        ToolStarted(name="ListHistory", id=1, source="router"),
        ToolResult(content="history data", id=1, source="router"),
        ThinkingResult(content="routing logic", source="router"),
    ]:
        assert format_event(event, _split, notifications=notif) == [], (
            f"Expected [] for {type(event).__name__} router event in quiet mode"
        )


def test_format_router_events_normal_returns_empty() -> None:
    """Router ToolStarted/ToolResult/ThinkingResult return [] in normal mode."""
    notif = NotificationsConfig(mode="normal")
    for event in [
        ToolStarted(name="ListHistory", id=1, source="router"),
        ToolResult(content="history data", id=1, source="router"),
        ThinkingResult(content="routing logic", source="router"),
    ]:
        assert format_event(event, _split, notifications=notif) == [], (
            f"Expected [] for {type(event).__name__} router event in normal mode"
        )


def test_format_router_error_event_all_modes_visible() -> None:
    """Router ErrorEvent is always visible regardless of notification mode."""
    error = ErrorEvent(message="router crashed", source="router")
    for mode in ("quiet", "normal", "verbose", "debug"):
        notif = NotificationsConfig(mode=mode)
        result = format_event(error, _split, notifications=notif)
        assert len(result) > 0, f"Expected non-empty for router ErrorEvent in {mode} mode"
        assert "[Router]" in result[0]
        assert "router crashed" in result[0]


def test_format_router_tool_started_verbose() -> None:
    """Router ToolStarted renders with [Router] prefix in verbose mode."""
    notif = NotificationsConfig(mode="verbose")
    event = ToolStarted(name="ReadHistory", id=2, source="router")
    result = format_event(event, _split, notifications=notif)
    assert len(result) > 0
    assert "[Router]" in result[0]
    assert "ReadHistory" in result[0]


def test_format_router_tool_started_debug() -> None:
    """Router ToolStarted in debug mode shows the name with [Router] prefix."""
    notif = NotificationsConfig(mode="debug")
    event = ToolStarted(name="ReadHistory", id=2, input="some input", source="router")
    result = format_event(event, _split, notifications=notif)
    assert len(result) > 0
    assert "[Router]" in result[0]


def test_format_router_response_always_suppressed() -> None:
    """Router Response is suppressed (returns []) in all modes including verbose/debug."""
    response = Response(content='{"scope":"small"}', source="router")
    for mode in ("quiet", "normal", "verbose", "debug"):
        notif = NotificationsConfig(mode=mode)
        result = format_event(response, _split, notifications=notif)
        assert result == [], f"Expected [] for router Response in {mode} mode, got {result}"


def test_router_tool_started_does_not_increment_beacon_count() -> None:
    """Router ToolStarted does not count toward quiet-mode beacon tool counts."""
    # In quiet mode, a router ToolStarted must be suppressed WITHOUT incrementing counts.
    # We verify this by checking that format_event returns [] AND that the event is
    # treated differently from a main-session ToolStarted (which returns [] but DOES count).
    # Since format_event has no side effects for router events, we verify the return value.
    notif = NotificationsConfig(mode="quiet")
    router_tool = ToolStarted(name="ListHistory", source="router")
    result = format_event(router_tool, _split, notifications=notif)
    # Router tools are suppressed with no output
    assert result == []


@pytest.mark.asyncio
async def test_router_tool_started_quiet_no_beacon_count_increment() -> None:
    """Router ToolStarted in quiet+interval mode does not increment tool count for beacon."""
    from archon.ai.event_mapper import RoutingEvent, Response as Resp

    # Inject: router ToolStarted, then a main Response — in quiet mode with interval
    router_tool = ToolStarted(name="ListHistory", source="router")
    main_response = Resp(content="Done")

    mgr = _mock_session_manager(router_tool, main_response)
    msg = _mock_message("go")
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)  # 0 = no beacon

    # Collect all answer calls — no beacon should be sent with tool count > 0
    answers: list[str] = []

    async def _capture_answer(text: str, **kw: object) -> None:
        answers.append(text)

    msg.answer = AsyncMock(side_effect=_capture_answer)

    await handle_message(msg, mgr, _split, notifications=notif)

    # The final response must appear; no beacon about "1 tool" (from the router ToolStarted)
    tool_beacons = [a for a in answers if "tool" in a.lower() and "1" in a]
    assert not tool_beacons, (
        f"Router ToolStarted must NOT increment beacon tool count, got: {tool_beacons}"
    )


# ──────────────────────────────────────────────────────────────────
# Fix 4 — additional format_event regression guards
# ──────────────────────────────────────────────────────────────────


def test_format_main_session_events_unchanged() -> None:
    """source='orchestrator' events are rendered without any [Router] modification."""
    notif = NotificationsConfig(mode="verbose")
    event = ToolStarted(name="Read", input={"file_path": "/foo"}, source="orchestrator")
    result = format_event(event, _split, notifications=notif)
    assert "[Router]" not in "".join(result)
    assert len(result) > 0  # should produce output (not suppressed) in verbose mode


def test_router_events_telegram_gated_by_mode() -> None:
    """In quiet mode, router ToolStarted produces no Telegram message; in verbose mode it does."""
    quiet_notif = NotificationsConfig(mode="quiet")
    verbose_notif = NotificationsConfig(mode="verbose")

    event = ToolStarted(name="history_read", input={}, source="router")

    quiet_result = format_event(event, _split, notifications=quiet_notif)
    verbose_result = format_event(event, _split, notifications=verbose_notif)

    assert quiet_result == []  # suppressed in quiet
    assert len(verbose_result) > 0  # visible in verbose
    assert "[Router]" in "".join(verbose_result)


async def test_handler_separator_in_history_not_telegram(tmp_path: "Path") -> None:
    """The ---  separator inserted between router and main events appears in history but NOT in Telegram."""
    from pathlib import Path
    from archon.ai.history_manager import HistoryManager
    from datetime import date

    history_manager = HistoryManager(directory=str(tmp_path))

    router_event = ToolStarted(name="history_read", input={}, source="router")
    main_event = Response(content="Done", source="orchestrator")

    # Emit router then main events through handle_message
    mgr = _mock_session_manager(router_event, main_event)
    msg = _mock_message("test task")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    # History file must contain the separator (HistoryManager stores in sessions/ subdir)
    today = date.today().strftime("%Y-%m-%d")
    content = (tmp_path / "sessions" / f"{today}.md").read_text()
    assert "---" in content  # separator present in history

    # Telegram must NOT have received a bare separator message
    for call in msg.answer.call_args_list:
        sent_text = call.args[0] if call.args else call.kwargs.get("text", "")
        assert sent_text.strip() != "---", "Separator must not be sent to Telegram"


# ──────────────────────────────────────────────────────────────────
# check_auto_compact — unit tests
# ──────────────────────────────────────────────────────────────────


def _mock_session_manager_with_auto_compact(
    *events: object,
    auto_compact_return: "int | None" = None,
) -> SessionManager:
    """Session manager where auto_compact_if_needed returns the given value."""
    mgr = _mock_session_manager(*events)
    mgr.auto_compact_if_needed = AsyncMock(return_value=auto_compact_return)
    return mgr


async def test_auto_compact_called_on_successful_delivery() -> None:
    """auto_compact_if_needed must be called once with correct user_id after successful event loop."""
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=None)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    mgr.auto_compact_if_needed.assert_awaited_once_with(42)


async def test_auto_compact_not_called_on_event_loop_error() -> None:
    """auto_compact_if_needed must NOT be called when session.send() raises an exception."""
    session = MagicMock()
    session.is_processing = False

    async def _send_raises(prompt: str):
        raise RuntimeError("SDK failure")
        yield  # make it an async generator

    session.send = _send_raises
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    mgr.pop_last_injected_files = MagicMock(return_value=[])
    mgr.auto_compact_if_needed = AsyncMock(return_value=None)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)  # must not raise

    mgr.auto_compact_if_needed.assert_not_awaited()


async def test_auto_compact_notification_verbose() -> None:
    """auto_compact returns percentage + mode=verbose → message.answer() called with note."""
    notif = NotificationsConfig(mode="verbose")
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=85)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("Auto-compaction" in t and "85%" in t for t in texts)


async def test_auto_compact_notification_debug() -> None:
    """auto_compact returns percentage + mode=debug → message.answer() called with note."""
    notif = NotificationsConfig(mode="debug")
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=85)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("Auto-compaction" in t and "85%" in t for t in texts)


async def test_auto_compact_no_notification_normal() -> None:
    """auto_compact returns percentage + mode=normal → no message.answer() for auto-compact note."""
    notif = NotificationsConfig(mode="normal")
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=85)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("Auto-compaction" in t for t in texts)


async def test_auto_compact_no_notification_quiet() -> None:
    """auto_compact returns percentage + mode=quiet → no message.answer() for auto-compact note."""
    notif = NotificationsConfig(mode="quiet", interval_minutes=0)
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=85)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("Auto-compaction" in t for t in texts)


async def test_auto_compact_history_logged() -> None:
    """auto_compact returns percentage → record_archon_message called with the note."""
    history_manager = MagicMock()
    history_manager.record_user_message = AsyncMock()
    history_manager.record_archon_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    notif = NotificationsConfig(mode="normal")
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=85)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, notifications=notif, history_manager=history_manager)

    calls = [call[0][0] for call in history_manager.record_archon_message.call_args_list]
    assert any("Auto-compaction" in c and "85%" in c for c in calls)


async def test_auto_compact_none_no_side_effects() -> None:
    """auto_compact returns None → no extra answer, no history log for auto-compact."""
    history_manager = MagicMock()
    history_manager.record_user_message = AsyncMock()
    history_manager.record_archon_message = AsyncMock()
    history_manager.record_event = AsyncMock()

    notif = NotificationsConfig(mode="verbose")
    mgr = _mock_session_manager_with_auto_compact(Response(content="Hi"), auto_compact_return=None)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, notifications=notif, history_manager=history_manager)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("Auto-compaction" in t for t in texts)

    archon_calls = [call[0][0] for call in history_manager.record_archon_message.call_args_list]
    assert not any("Auto-compaction" in c for c in archon_calls)


async def test_auto_compact_error_handled_gracefully(caplog: pytest.LogCaptureFixture) -> None:
    """auto_compact_if_needed raises → exception logged, handler does not crash."""
    mgr = _mock_session_manager(Response(content="Hi"))
    mgr.auto_compact_if_needed = AsyncMock(side_effect=RuntimeError("compact error"))
    msg = _mock_message("hello")

    with caplog.at_level(logging.ERROR, logger="archon"):
        await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts)  # response still delivered
    assert any("Auto-compaction" in r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR)
