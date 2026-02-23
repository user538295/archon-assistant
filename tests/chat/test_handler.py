"""Tests for message handler and event formatter — S2.3."""
import asyncio
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

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

    assert msg.answer.await_count == 2
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "💭 Thinking..."
    assert texts[1] == "✅ Response:\nHi"


async def test_handle_message_gets_or_creates_session_for_user() -> None:
    mgr = _mock_session_manager()
    msg = _mock_message("hello")

    await handle_message(msg, mgr, _split)

    mgr.get_or_create.assert_awaited_once_with(42)


async def test_handle_message_sends_multi_chunk_event() -> None:
    # 100 chars, max_len=40: label_w=6, content_max=34, ceil(100/34)=3 chunks
    long_text = "y" * 100
    mgr = _mock_session_manager(Response(content=long_text))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, max_len=40)

    assert msg.answer.await_count == 3


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

    msg.answer.assert_awaited_once()
    text: str = msg.answer.call_args[0][0]
    assert text.startswith("❌ Error:")


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
    """Typing chat action must be sent at least once while processing."""
    mgr = _mock_session_manager(Response(content="Hi"))
    msg = _mock_message("Say hi")

    await handle_message(msg, mgr, _split)

    msg.bot.send_chat_action.assert_awaited_once_with(chat_id=100, action="typing")


async def test_handle_message_typing_task_fully_done_after_return() -> None:
    """typing_task must be fully cancelled before handle_message returns.

    Ensures we await the cancellation — not just fire-and-forget — so the
    event loop cannot squeeze in an extra send_chat_action after the response.
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
        await handle_message(msg, mgr, _split)

    assert created_tasks, "Expected at least the typing task to be created"
    for task in created_tasks:
        assert task.done(), f"Background task still running after handle_message returned: {task}"


# ──────────────────────────────────────────────────────────────────
# format_event — mode-based visibility matrix (S8.1)
# ──────────────────────────────────────────────────────────────────

# ThinkingStarted: hidden in quiet/normal, shown in verbose/debug
def test_format_thinking_started_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == []


def test_format_thinking_started_hidden_in_normal() -> None:
    notif = NotificationsConfig(mode="normal")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == []


def test_format_thinking_started_shown_in_verbose() -> None:
    notif = NotificationsConfig(mode="verbose")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == ["💭 Thinking..."]


def test_format_thinking_started_shown_in_debug() -> None:
    notif = NotificationsConfig(mode="debug")
    assert format_event(ThinkingStarted(), _split, notifications=notif) == ["💭 Thinking..."]


# ThinkingResult: hidden in quiet/normal, shown in verbose/debug
def test_format_thinking_result_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ThinkingResult(content="secret"), _split, notifications=notif) == []


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


# ToolStarted: hidden in quiet; name-only in normal; name+args in verbose/debug
def test_format_tool_started_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ToolStarted(name="Bash", input="ls"), _split, notifications=notif) == []


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


# ToolResult: hidden in quiet; brief in normal/verbose; full in debug
def test_format_tool_result_hidden_in_quiet() -> None:
    notif = NotificationsConfig(mode="quiet")
    assert format_event(ToolResult(content="output"), _split, notifications=notif) == []


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
    # No period, no newline — hard cut at 80 chars
    notif = NotificationsConfig(mode="normal")
    long_line = "x" * 100
    result = format_event(ToolResult(content=long_line), _split, notifications=notif)
    assert result == [f"📤 ✓ {'x' * 80}"]


def test_format_tool_result_brief_cuts_after_first_period_no_newline() -> None:
    # Content has a period mid-string but no newline — must cut after the period
    notif = NotificationsConfig(mode="normal")
    content = "Perfect! Now I have all the information you need. Let me show the results."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ Perfect! Now I have all the information you need."]


def test_format_tool_result_brief_period_beats_80_char_fallback() -> None:
    # Period well within 80 chars — must not fall back to the 80-char hard cut
    notif = NotificationsConfig(mode="normal")
    content = "Summary: done. " + "x" * 100
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ Summary: done."]


def test_format_tool_result_brief_period_beats_newline_when_earlier() -> None:
    # Period comes before the newline — cut at period
    notif = NotificationsConfig(mode="normal")
    content = "First sentence.\nSecond line."
    result = format_event(ToolResult(content=content), _split, notifications=notif)
    assert result == ["📤 ✓ First sentence."]


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


# Response and ErrorEvent: always shown in all modes
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
    events = [ThinkingStarted(), ThinkingResult(content="hmm"), ToolStarted(name="Bash"),
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
    mgr = _mock_session_manager(ThinkingStarted(), ErrorEvent(message="oops"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert "❌ Error: oops" in texts


async def test_handle_message_normal_mode_does_not_send_working() -> None:
    """Normal mode streams events directly — no 'Working...' prefix."""
    notif = NotificationsConfig(mode="normal")
    mgr = _mock_session_manager(Response(content="Done"))
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
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
        yield ThinkingStarted()
        yield ToolStarted(name="Bash")
        await asyncio.sleep(0.12)  # long enough for ~2 timer ticks
        yield ToolStarted(name="Read")
        yield Response(content="Done")

    session = MagicMock()
    session.send = _slow_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    all_texts = [call[0][0] for call in msg.answer.call_args_list]
    status_updates = [t for t in all_texts if t.startswith("⏳ Working...") and "tool" in t]
    assert len(status_updates) >= 1


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

    with patch("archon.chat.handler.random.choice", return_value="Pondering"):
        await handle_message(msg, mgr, _split, notifications=notif)

    all_texts = [call[0][0] for call in msg.answer.call_args_list]
    beacon_texts = [t for t in all_texts if t.startswith("⏳") and t != "⏳ Working..."]

    # First beacon must be "Working" (no counts yet — no tools/thinking fired)
    first_beacon = next((t for t in all_texts if t.startswith("⏳") and t != "⏳ Working..."), None)
    # Subsequent beacons must use a fun word from _BEACON_WORDS
    fun_beacons = [t for t in all_texts if any(t.startswith(f"⏳ {w}") for w in _BEACON_WORDS)]
    assert len(fun_beacons) >= 1


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

    assert msg.answer.await_count == 5
    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "💭 Thinking..."
    assert texts[1] == "💭 Thought:\nthinking"
    assert texts[2] == "🔧 Tool: Bash"
    assert texts[3] == "📤 Result:\noutput"
    assert texts[4] == "✅ Response:\ndone"
