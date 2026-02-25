"""Tests for message handler and event formatter — S2.3."""
import asyncio
import logging
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from aiogram.types import Message

from archon.ai.event_mapper import (
    ErrorEvent,
    Response,
    ThinkingResult,
    ThinkingStarted,
    ToolResult,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import DEFAULT_MAX_LEN, format_event, handle_message
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


def _mock_session(*events: object) -> MagicMock:
    """Session whose send() yields the given events."""
    session = MagicMock()

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


# ──────────────────────────────────────────────────────────────────
# format_event — each event type
# ──────────────────────────────────────────────────────────────────


def test_format_thinking_started() -> None:
    assert format_event(ThinkingStarted(), _split) == ["💭 Thinking..."]


def test_format_thinking_result() -> None:
    result = format_event(ThinkingResult(content="pondering"), _split)
    assert result == ["💭 Thought:\npondering"]


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
    # 100 chars, max_len=40: label_w=6, content_max=34, ceil(100/34)=3 chunks
    long_text = "x" * 100
    result = format_event(Response(content=long_text), _split, max_len=40)
    assert len(result) == 3
    assert all(r.startswith("✅ Response:\n") for r in result)


def test_format_thinking_result_splits_long_content() -> None:
    long_text = "a" * 100
    result = format_event(ThinkingResult(content=long_text), _split, max_len=40)
    assert len(result) == 3
    assert all(r.startswith("💭 Thought:\n") for r in result)


def test_format_tool_result_splits_long_content() -> None:
    long_text = "b" * 100
    result = format_event(ToolResult(content=long_text), _split, max_len=40)
    assert len(result) == 3
    assert all(r.startswith("📤 Result:\n") for r in result)


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
    mgr = _mock_session_manager(ThinkingStarted(), Response(content="Hi"))
    msg = _mock_message("Say hi")

    await handle_message(msg, mgr, _split)

    assert msg.answer.await_count == 3
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working..."
    assert texts[1] == "💭 Thinking..."
    assert texts[2] == "✅ Response:\nHi"


async def test_handle_message_gets_or_creates_session_for_user() -> None:
    mgr = _mock_session_manager()
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_awaited_once_with(42)


async def test_handle_message_sends_multi_chunk_event() -> None:
    # 100 chars, max_len=40: label_w=6, content_max=34, ceil(100/34)=3 chunks; +1 for Working...
    long_text = "y" * 100
    mgr = _mock_session_manager(Response(content=long_text))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, max_len=40)

    assert msg.answer.await_count == 4


async def test_handle_message_no_text_is_noop() -> None:
    mgr = MagicMock(spec=SessionManager)
    msg = _mock_message()
    msg.text = None

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_not_called()


async def test_handle_message_no_from_user_is_noop() -> None:
    mgr = MagicMock(spec=SessionManager)
    msg = _mock_message()
    msg.from_user = None

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_not_called()


async def test_handle_message_sends_error_on_session_exception() -> None:
    """If session.send() raises, the handler sends an error message and does not propagate."""
    session = MagicMock()

    async def _send_raises(prompt: str):
        raise RuntimeError("SDK failure")
        yield  # make it an async generator

    session.send = _send_raises
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any(t.startswith("❌ Error:") for t in texts)


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
    assert result == ["💭 Thought:\n&lt;think&gt;idea&lt;/think&gt;"]


def test_format_thinking_result_escapes_ampersand() -> None:
    result = format_event(ThinkingResult(content="cats & dogs"), _split)
    assert result == ["💭 Thought:\ncats &amp; dogs"]


def test_format_thinking_result_escapes_double_quote() -> None:
    result = format_event(ThinkingResult(content='he said "yes"'), _split)
    assert result == ["💭 Thought:\nhe said &quot;yes&quot;"]


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


# ToolResult — brief mode (normal/verbose)
def test_format_tool_result_brief_escapes_angle_brackets() -> None:
    """Brief tool output containing HTML tags must be escaped (fixes <tool_use_error> crash)."""
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolResult(content="<tool_use_error>fail</tool_use_error>"), _split, notifications=notif)
    assert result == ["📤 ✓ &lt;tool_use_error&gt;fail&lt;/tool_use_error&gt;"]
    assert "<tool_use_error>" not in result[0]


def test_format_tool_result_brief_escapes_ampersand() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolResult(content="ok & done"), _split, notifications=notif)
    assert result == ["📤 ✓ ok &amp; done"]


def test_format_tool_result_brief_escapes_double_quote() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolResult(content='say "hi"'), _split, notifications=notif)
    assert result == ["📤 ✓ say &quot;hi&quot;"]


def test_format_tool_result_brief_escapes_html_with_id() -> None:
    """Brief tool output with an ID tag: HTML in content must still be escaped."""
    notif = NotificationsConfig(mode="normal")
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
    no background task is needed.
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

    with patch("asyncio.create_task", side_effect=_capturing_create_task):
        await handle_message(msg, mgr, _split)  # default (debug) mode — no beacon

    assert created_tasks == [], (
        f"Expected no background tasks, got {len(created_tasks)}"
    )



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

# ThinkingStarted: hidden in normal, shown in verbose/debug
def test_format_thinking_started_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == []


def test_format_thinking_started_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == ["💭 Thinking..."]


def test_format_thinking_started_shown_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == ["💭 Thinking..."]


# ThinkingResult: hidden in normal, shown in verbose/debug
def test_format_thinking_result_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    assert format_event(ThinkingResult(content="secret"), _split, notifications=notif) == []


def test_format_thinking_result_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ThinkingResult(content="pondering"), _split, notifications=notif)
    assert result == ["💭 Thought:\npondering"]


def test_format_thinking_result_shown_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(ThinkingResult(content="pondering"), _split, notifications=notif)
    assert result == ["💭 Thought:\npondering"]


# ToolStarted: name-only in normal; name+args in verbose/debug
def test_format_tool_started_name_only_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolStarted(name="Bash", input="ls -la"), _split, notifications=notif)
    assert result == ["🔧 Tool: Bash"]  # no input shown


def test_format_tool_started_with_args_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolStarted(name="Bash", input="ls -la"), _split, notifications=notif)
    assert result == ["🔧 Tool: Bash\nls -la"]


def test_format_tool_started_with_args_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(ToolStarted(name="Bash", input="ls -la"), _split, notifications=notif)
    assert result == ["🔧 Tool: Bash\nls -la"]


def test_format_tool_started_no_input_shown_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolStarted(name="Read"), _split, notifications=notif)
    assert result == ["🔧 Tool: Read"]


# ToolResult: brief in normal/verbose; full in debug
def test_format_tool_result_brief_empty_content() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolResult(content=""), _split, notifications=notif)
    assert result == ["📤 ✓ ok"]


def test_format_tool_result_brief_single_line() -> None:
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolResult(content="exit 0\nsome other output"), _split, notifications=notif)
    assert result == ["📤 ✓ exit 0"]


def test_format_tool_result_brief_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    result = format_event(ToolResult(content="exit 0\nmore"), _split, notifications=notif)
    assert result == ["📤 ✓ exit 0"]


def test_format_tool_result_brief_truncates_long_first_line() -> None:
    # No period, no newline — hard cut at 160 chars
    notif = NotificationsConfig(mode="normal")
    long_line = "x" * 200
    result = format_event(ToolResult(content=long_line), _split, notifications=notif)
    assert result == [f"📤 ✓ {'x' * 160}"]


def test_format_tool_result_brief_cuts_after_second_period_no_newline() -> None:
    # Content has multiple periods but no newline — must cut after the second period
    notif = NotificationsConfig(mode="normal")
    content = "First sentence. Second sentence. Third sentence continues on and on."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ First sentence. Second sentence."]


def test_format_tool_result_brief_period_beats_160_char_fallback() -> None:
    # Single period well within 160 chars — must not fall back to the 160-char hard cut
    notif = NotificationsConfig(mode="normal")
    content = "Summary: done. " + "x" * 100
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ Summary: done."]


def test_format_tool_result_brief_second_period_beats_newline_when_earlier() -> None:
    # 2nd period comes before the newline — cut after 2nd period
    notif = NotificationsConfig(mode="normal")
    content = "Sentence one. Sentence two. \nMore content here."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ Sentence one. Sentence two."]


def test_format_tool_result_brief_newline_beats_period_when_earlier() -> None:
    # Newline comes before any period — cut before newline
    notif = NotificationsConfig(mode="normal")
    content = "First line\nhas a period."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ First line"]


def test_format_tool_result_full_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    result = format_event(ToolResult(content="full output"), _split, notifications=notif)
    assert result == ["📤 Result:\nfull output"]


def test_format_tool_result_markdown_bold_in_normal_mode() -> None:
    """Markdown bold in tool result brief is rendered as HTML <b>."""
    notif = NotificationsConfig(mode="normal")
    result = format_event(ToolResult(content="Result: **success**. All done."), _split, notifications=notif)
    assert "<b>success</b>" in result[0]


def test_format_tool_result_markdown_code_in_normal_mode() -> None:
    """Markdown inline code in tool result brief is rendered as HTML <code>."""
    notif = NotificationsConfig(mode="normal")
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


# Response and ErrorEvent: always shown in all modes (tested in Bug.003 section below)


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
    notif = NotificationsConfig(mode="normal")
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
    assert format_event(ThinkingStarted(), _split) == ["💭 Thinking..."]
    assert format_event(ThinkingResult(content="thought"), _split) == ["💭 Thought:\nthought"]
    assert format_event(ToolResult(content="data"), _split) == ["📤 Result:\ndata"]




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
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)  # must not raise

    text: str = msg.answer.call_args[0][0]
    assert "&lt;tool_use_error&gt;" in text
    assert "<tool_use_error>" not in text


# ──────────────────────────────────────────────────────────────────
# handle_message — history_manager integration
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_records_user_message_when_history_manager_set() -> None:
    from unittest.mock import MagicMock as MM
    history_manager = MM()
    history_manager.record_user_message = MM()
    history_manager.record_event = MM()

    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager, cwd="/tmp")

    history_manager.record_user_message.assert_called_once_with(42, "hello", cwd="/tmp")


async def test_handle_message_records_each_event_when_history_manager_set() -> None:
    from unittest.mock import MagicMock as MM
    history_manager = MM()
    history_manager.record_user_message = MM()
    history_manager.record_event = MM()

    events = [ThinkingStarted(), Response(content="Hi")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split, history_manager=history_manager)

    assert history_manager.record_event.call_count == 2


async def test_handle_message_no_crash_without_history_manager() -> None:
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("hello")

    # Must not raise — history_manager defaults to None
    await handle_message(msg, mgr, _split)

    msg.answer.assert_awaited()


# ──────────────────────────────────────────────────────────────────
# handle_message — mid-query mode change (S8.3)
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# handle_message — complete mode transition matrix (Bug.001)
# Non-quiet FROM→TO transitions verified: before-switch uses old mode,
# after-switch uses new mode immediately.
# ──────────────────────────────────────────────────────────────────


async def test_mode_transition_normal_to_verbose() -> None:
    """normal → verbose: tool args appear after switch (were name-only before)."""
    notif = NotificationsConfig(mode="normal")
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")   # normal → name only
        notif.mode = "verbose"
        yield ToolStarted(name="Read", input="path.py")   # verbose → name + args
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert not any("echo hi" in t for t in texts), f"Args must NOT show in normal mode: {texts}"
    assert any("path.py" in t for t in texts), f"Args must show after switch to verbose: {texts}"


async def test_mode_transition_normal_to_debug() -> None:
    """normal → debug: ToolResult changes from brief to full after switch."""
    notif = NotificationsConfig(mode="normal")
    msg = _mock_message("go")

    long_content = "first sentence. second sentence.\nline3\nline4"

    async def _send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # normal → brief
        notif.mode = "debug"
        yield ToolResult(content=long_content)  # debug  → full
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 2, f"Expected 2 ToolResult messages: {texts}"
    # First result (normal mode) is brief — no multiline
    assert "\n" not in tool_results[0] or "line3" not in tool_results[0], \
        f"First ToolResult should be brief (normal mode): {tool_results[0]}"
    # Second result (debug mode) contains full multiline content
    assert "line3" in tool_results[1], f"Second ToolResult must be full (debug mode): {tool_results[1]}"


async def test_mode_transition_verbose_to_normal() -> None:
    """verbose → normal: thinking hidden, tool args hidden after switch."""
    notif = NotificationsConfig(mode="verbose")
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingStarted()                          # verbose → shown
        yield ToolStarted(name="Bash", input="echo hi") # verbose → name + args
        notif.mode = "normal"
        yield ThinkingStarted()                          # normal  → suppressed
        yield ToolStarted(name="Read", input="path.py") # normal  → name only
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    thinking_msgs = [t for t in texts if "💭 Thinking" in t]
    assert len(thinking_msgs) == 1, f"Only first ThinkingStarted shown (verbose): {texts}"
    assert any("echo hi" in t for t in texts), f"Bash args shown in verbose: {texts}"
    assert not any("path.py" in t for t in texts), f"Read args hidden in normal: {texts}"


async def test_mode_transition_verbose_to_debug() -> None:
    """verbose → debug: ToolResult changes from brief to full after switch."""
    notif = NotificationsConfig(mode="verbose")
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

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 2, f"Expected 2 ToolResult messages: {texts}"
    assert "line3" not in tool_results[0], f"First ToolResult brief (verbose): {tool_results[0]}"
    assert "line3" in tool_results[1], f"Second ToolResult full (debug): {tool_results[1]}"


async def test_mode_transition_debug_to_normal() -> None:
    """debug → normal: ToolResult changes from full to brief after switch."""
    notif = NotificationsConfig(mode="debug")
    msg = _mock_message("go")

    long_content = "first sentence. second sentence.\nline3\nline4"

    async def _send(text: str) -> AsyncGenerator:
        yield ToolResult(content=long_content)  # debug  → full
        notif.mode = "normal"
        yield ToolResult(content=long_content)  # normal → brief
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 2, f"Expected 2 ToolResult messages: {texts}"
    assert "line3" in tool_results[0], f"First ToolResult full (debug): {tool_results[0]}"
    assert "line3" not in tool_results[1], f"Second ToolResult brief (normal): {tool_results[1]}"


async def test_mode_transition_debug_to_verbose() -> None:
    """debug → verbose: ToolResult changes from full to brief after switch."""
    notif = NotificationsConfig(mode="debug")
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

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    tool_results = [t for t in texts if "📤" in t]
    assert len(tool_results) == 2, f"Expected 2 ToolResult messages: {texts}"
    assert "line3" in tool_results[0], f"First ToolResult full (debug): {tool_results[0]}"
    assert "line3" not in tool_results[1], f"Second ToolResult brief (verbose): {tool_results[1]}"


# ──────────────────────────────────────────────────────────────────
# handle_message — thinking events across mode transitions
# ──────────────────────────────────────────────────────────────────


async def test_mode_transition_normal_to_verbose_shows_thinking() -> None:
    """normal → verbose: ThinkingStarted becomes visible after switch."""
    notif = NotificationsConfig(mode="normal")
    msg = _mock_message("go")

    async def _send(text: str) -> AsyncGenerator:
        yield ThinkingStarted()                  # normal  → suppressed
        notif.mode = "verbose"
        yield ThinkingStarted()                  # verbose → shown
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    thinking_msgs = [t for t in texts if "💭 Thinking" in t]
    assert len(thinking_msgs) == 1, f"Only second ThinkingStarted visible (verbose): {texts}"


# ──────────────────────────────────────────────────────────────────
# handle_message — live e2e: concurrent /notify during active query
# Simulates a real /notify command running while handle_message
# is awaiting the next event (two coroutines, one event loop).
# ──────────────────────────────────────────────────────────────────


async def test_live_concurrent_notify_normal_to_verbose() -> None:
    """Concurrent /notify verbose while handle_message awaits next event.

    Uses asyncio.Event to interleave execution: handle_message awaits
    the event, /notify fires and changes mode, then next event arrives.
    The post-notify event must be formatted in verbose mode.
    """
    from archon.chat.commands import notify_command

    notif = NotificationsConfig(mode="normal")
    handler_msg = _mock_message("go")

    gate = asyncio.Event()  # handler waits; notify fires; handler continues

    async def _interleaved_send(text: str) -> AsyncGenerator:
        yield ToolStarted(name="Bash", input="echo hi")  # normal → name only
        gate.set()                                        # signal notify to fire
        await asyncio.sleep(0)                            # yield to event loop
        yield ToolStarted(name="Read", input="path.py")  # should be verbose now
        yield Response(content="Done")

    session = MagicMock()
    session.send = _interleaved_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    async def _run_notify() -> None:
        await gate.wait()
        # Simulate /notify verbose command mutating the shared notifications object
        notif.mode = "verbose"

    await asyncio.gather(
        handle_message(handler_msg, mgr, _split, notifications=notif),
        _run_notify(),
    )

    texts = [call[0][0] for call in handler_msg.answer.call_args_list]
    assert not any("echo hi" in t for t in texts), f"Bash args hidden in normal: {texts}"
    assert any("path.py" in t for t in texts), f"Read args visible after concurrent /verbose: {texts}"




async def test_handle_message_all_event_types_formatted() -> None:
    events = [
        ThinkingStarted(),
        ThinkingResult(content="thinking"),
        ToolStarted(name="Bash"),
        ToolResult(content="output"),
        Response(content="done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("do it")

    await handle_message(msg, mgr, _split)

    assert msg.answer.await_count == 6
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working..."
    assert texts[1] == "💭 Thinking..."
    assert texts[2] == "💭 Thought:\nthinking"
    assert texts[3] == "🔧 Tool: Bash"
    assert texts[4] == "📤 Result:\noutput"
    assert texts[5] == "✅ Response:\ndone"


# ──────────────────────────────────────────────────────────────────
# handle_message — typing indicator (no background loop, inline before each send)
# ──────────────────────────────────────────────────────────────────



async def test_typing_sent_before_each_outgoing_message_in_debug_mode() -> None:
    """Typing is rate-limited to once per _TYPING_COOLDOWN_SECS = 4.0 s.

    Three rapid events fire within milliseconds of each other. Only the initial
    send_chat_action goes through; subsequent pre-message calls are throttled.
    """
    events = [ThinkingStarted(), ToolStarted(name="Bash"), Response(content="Done")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    # debug mode (notifications=None): all 3 events produce exactly 1 message each
    await handle_message(msg, mgr, _split)

    # Only the initial typing call; all 3 pre-message calls are throttled (< 4s cooldown).
    assert msg.bot.send_chat_action.await_count == 1




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
# Bug.003 — agent start/stop always notified; "⏳ Working..." always first
# ──────────────────────────────────────────────────────────────────


async def test_handle_message_always_sends_working_in_normal_mode() -> None:
    """Normal mode must always send ⏳ Working... before any event."""
    notif = NotificationsConfig(mode="normal")
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working...", f"First message must be Working..., got: {texts[0]}"


async def test_handle_message_always_sends_working_in_verbose_mode() -> None:
    """Verbose mode must always send ⏳ Working... before any event."""
    notif = NotificationsConfig(mode="verbose")
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working...", f"First message must be Working..., got: {texts[0]}"


async def test_handle_message_always_sends_working_in_debug_mode() -> None:
    """Debug mode must always send ⏳ Working... before any event."""
    notif = NotificationsConfig(mode="debug")
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working...", f"First message must be Working..., got: {texts[0]}"


async def test_handle_message_always_sends_working_no_notifications() -> None:
    """notifications=None must still send ⏳ Working... before any event."""
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working...", f"First message must be Working..., got: {texts[0]}"


async def test_handle_message_working_always_precedes_response() -> None:
    """⏳ Working... must appear before ✅ Response in all modes."""
    for mode in ("normal", "verbose", "debug"):
        notif = NotificationsConfig(mode=mode)
        mgr = _mock_session_manager(Response(content="Done"))
        msg = _mock_message("go")

        await handle_message(msg, mgr, _split, notifications=notif)

        texts = [call[0][0] for call in msg.answer.call_args_list]
        working_idx = texts.index("⏳ Working...")
        response_idx = next(i for i, t in enumerate(texts) if "✅ Response" in t)
        assert working_idx < response_idx, f"Working must precede Response in {mode} mode: {texts}"


def test_format_subagent_started_shown_in_normal() -> None:
    """SubagentStarted is always shown regardless of mode."""
    from archon.ai.event_mapper import SubagentStarted
    notif = NotificationsConfig(mode="normal")
    result = format_event(SubagentStarted(agent_id="a1", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> started"]


def test_format_subagent_started_shown_in_verbose() -> None:
    """SubagentStarted is always shown regardless of mode."""
    from archon.ai.event_mapper import SubagentStarted
    notif = NotificationsConfig(mode="verbose")
    result = format_event(SubagentStarted(agent_id="a1", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> started"]


def test_format_subagent_started_shown_in_debug() -> None:
    """SubagentStarted is always shown regardless of mode."""
    from archon.ai.event_mapper import SubagentStarted
    notif = NotificationsConfig(mode="debug")
    result = format_event(SubagentStarted(agent_id="a1", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> started"]


def test_format_subagent_stopped_shown_in_normal() -> None:
    """SubagentStopped is always shown regardless of mode."""
    from archon.ai.event_mapper import SubagentStopped
    notif = NotificationsConfig(mode="normal")
    result = format_event(SubagentStopped(agent_id="a1", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> done"]


def test_format_subagent_stopped_shown_in_verbose() -> None:
    """SubagentStopped is always shown regardless of mode."""
    from archon.ai.event_mapper import SubagentStopped
    notif = NotificationsConfig(mode="verbose")
    result = format_event(SubagentStopped(agent_id="a1", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> done"]


def test_format_subagent_stopped_shown_in_debug() -> None:
    """SubagentStopped is always shown regardless of mode."""
    from archon.ai.event_mapper import SubagentStopped
    notif = NotificationsConfig(mode="debug")
    result = format_event(SubagentStopped(agent_id="a1", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> done"]


def test_format_response_shown_in_normal() -> None:
    """Response is always shown in normal mode."""
    notif = NotificationsConfig(mode="normal")
    assert format_event(Response(content="Done"), _split, notifications=notif) == ["✅ Response:\nDone"]


def test_format_response_shown_in_verbose() -> None:
    """Response is always shown in verbose mode."""
    notif = NotificationsConfig(mode="verbose")
    assert format_event(Response(content="Done"), _split, notifications=notif) == ["✅ Response:\nDone"]


def test_format_response_shown_in_debug() -> None:
    """Response is always shown in debug mode."""
    notif = NotificationsConfig(mode="debug")
    assert format_event(Response(content="Done"), _split, notifications=notif) == ["✅ Response:\nDone"]


def test_format_error_shown_in_normal() -> None:
    """ErrorEvent is always shown in normal mode."""
    notif = NotificationsConfig(mode="normal")
    assert format_event(ErrorEvent(message="oops"), _split, notifications=notif) == ["❌ Error: oops"]


def test_format_error_shown_in_verbose() -> None:
    """ErrorEvent is always shown in verbose mode."""
    notif = NotificationsConfig(mode="verbose")
    assert format_event(ErrorEvent(message="oops"), _split, notifications=notif) == ["❌ Error: oops"]


def test_format_error_shown_in_debug() -> None:
    """ErrorEvent is always shown in debug mode."""
    notif = NotificationsConfig(mode="debug")
    assert format_event(ErrorEvent(message="oops"), _split, notifications=notif) == ["❌ Error: oops"]


async def test_handle_message_subagent_events_shown_in_all_modes() -> None:
    """SubagentStarted and SubagentStopped are always delivered in all modes."""
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped

    for mode in ("normal", "verbose", "debug"):
        notif = NotificationsConfig(mode=mode)
        events = [
            SubagentStarted(agent_id="a1", agent_type="coder"),
            SubagentStopped(agent_id="a1", agent_type="coder"),
            Response(content="Done"),
        ]
        mgr = _mock_session_manager(*events)
        msg = _mock_message("go")

        await handle_message(msg, mgr, _split, notifications=notif)

        texts = [call[0][0] for call in msg.answer.call_args_list]
        assert any("🤖 Agent" in t and "started" in t for t in texts), (
            f"SubagentStarted not shown in {mode} mode: {texts}"
        )
        assert any("🤖 Agent" in t and "done" in t for t in texts), (
            f"SubagentStopped not shown in {mode} mode: {texts}"
        )


async def test_handle_message_response_always_shown_in_all_modes() -> None:
    """Response is always delivered regardless of mode."""
    for mode in ("normal", "verbose", "debug"):
        notif = NotificationsConfig(mode=mode)
        mgr = _mock_session_manager(Response(content="Done"))
        msg = _mock_message("go")

        await handle_message(msg, mgr, _split, notifications=notif)

        texts = [call[0][0] for call in msg.answer.call_args_list]
        assert any("✅ Response" in t for t in texts), (
            f"Response not shown in {mode} mode: {texts}"
        )


async def test_handle_message_error_always_shown_in_all_modes() -> None:
    """ErrorEvent is always delivered regardless of mode."""
    for mode in ("normal", "verbose", "debug"):
        notif = NotificationsConfig(mode=mode)
        mgr = _mock_session_manager(ErrorEvent(message="oops"))
        msg = _mock_message("go")

        await handle_message(msg, mgr, _split, notifications=notif)

        texts = [call[0][0] for call in msg.answer.call_args_list]
        assert any("❌ Error" in t for t in texts), (
            f"ErrorEvent not shown in {mode} mode: {texts}"
        )


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
    events = [ThinkingStarted(), Response(content="Done")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    call_count = 0
    original_answer = msg.answer

    async def _answer_second_raises(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # "💭 Thinking..." — simulates Telegram flap
            raise Exception("TelegramNetworkError: network failure")

    msg.answer = AsyncMock(side_effect=_answer_second_raises)

    await handle_message(msg, mgr, _split)  # must not raise

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts), (
        f"Response must still be attempted after Telegram error on ThinkingStarted: {texts}"
    )


async def test_telegram_error_during_event_reply_is_logged_at_warning_not_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Telegram send failures in the event loop must be WARNING, not ERROR.

    ERROR is reserved for AI session failures (e.g. SDK crash).  A transient
    Telegram network error does not mean Claude failed — it means the delivery
    of one notification failed.
    """
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    call_count = 0

    async def _answer_second_raises(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # "✅ Response..." reply — simulates network error
            raise Exception("TelegramNetworkError: network failure")

    msg.answer = AsyncMock(side_effect=_answer_second_raises)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

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
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    call_count = 0

    async def _answer_first_raises(text: str, **kwargs: object) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # "⏳ Working..." — fails
            raise Exception("TelegramNetworkError: timeout")

    msg.answer = AsyncMock(side_effect=_answer_first_raises)

    await handle_message(msg, mgr, _split)  # must not raise

    # session.send must have been called despite the failed acknowledgement
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("✅ Response" in t for t in texts), (
        f"Response must still be attempted when Working ack fails: {texts}"
    )


async def test_telegram_error_on_working_ack_logged_at_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failed '⏳ Working...' delivery must be WARNING, not ERROR."""
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    async def _always_raises(text: str, **kwargs: object) -> None:
        raise Exception("TelegramNetworkError: timeout")

    msg.answer = AsyncMock(side_effect=_always_raises)

    with caplog.at_level(logging.DEBUG, logger="archon"):
        await handle_message(msg, mgr, _split)

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
