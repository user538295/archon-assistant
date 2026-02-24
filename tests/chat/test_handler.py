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
    session.send = _slow_send
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

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
    session.send = _send_with_mode_change
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

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

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    beacon_texts = [t for t in texts if t.startswith("⏳") and t != "⏳ Working..."]
    assert beacon_texts, (
        f"Beacon should have fired after switching to quiet+interval mid-query, got: {texts}"
    )


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
    events = [ThinkingStarted(), ToolStarted(name="Bash"), Response(content="Done")]
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
    """Quiet orchestrator + normal agents → SubagentStarted notification is sent.

    Even though the orchestrator is in quiet mode (only Response shown by default),
    sub-agent lifecycle events must pass through to format_event because the
    resolved agent mode is 'normal', not 'quiet'.
    """
    from archon.ai.event_mapper import SubagentStarted, SubagentStopped
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0,
        agents=NotificationsAgentsConfig(mode="normal"),
    )
    events = [
        SubagentStarted(agent_id="a1", agent_type="researcher"),
        SubagentStopped(agent_id="a1", agent_type="researcher"),
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert "🤖 Agent: <b>researcher</b> started" in texts, f"Expected agent start event, got: {texts}"
    assert "🤖 Agent: <b>researcher</b> done" in texts, f"Expected agent stop event, got: {texts}"
    assert "✅ Response:\nDone" in texts


async def test_handle_message_quiet_orch_agents_normal_subagent_not_in_beacon() -> None:
    """When agents are not quiet, SubagentStarted must NOT be counted in the beacon."""
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0.001,  # beacon enabled
        agents=NotificationsAgentsConfig(mode="normal"),
    )
    msg = _mock_message("go")

    async def _send_with_agent(text: str) -> AsyncGenerator:
        yield SubagentStarted(agent_id="a1", agent_type="coder")
        await asyncio.sleep(0.12)  # let beacon fire with counts
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send_with_agent
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # No beacon text should mention "1 tool" — the SubagentStarted was NOT counted
    beacon_texts = [t for t in texts if t.startswith("⏳ Working... (")]
    for bt in beacon_texts:
        assert "tool" not in bt, f"SubagentStarted should not have been counted in beacon: {bt}"


async def test_handle_message_quiet_orch_agents_quiet_subagent_counted_in_beacon() -> None:
    """When agents are quiet (inherit or explicit), SubagentStarted IS counted in beacon."""
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0.001,  # beacon enabled
        agents=NotificationsAgentsConfig(mode=None),  # inherit → quiet
    )
    msg = _mock_message("go")

    async def _send_with_agent(text: str) -> AsyncGenerator:
        yield SubagentStarted(agent_id="a1", agent_type="coder")
        await asyncio.sleep(0.12)  # let beacon fire
        yield Response(content="Done")

    session = MagicMock()
    session.send = _send_with_agent
    mgr = MagicMock(spec=SessionManager)
    mgr.get_or_create = AsyncMock(return_value=session)

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    # Beacon should mention tool count because SubagentStarted WAS counted
    beacon_texts = [t for t in texts if "tool" in t and t.startswith("⏳")]
    assert beacon_texts, f"Expected beacon with tool count (agent counted), got: {texts}"


async def test_handle_message_quiet_orch_agents_normal_no_subagent_notification_sent_before_start() -> None:
    """⏳ Working... is still the first message in quiet mode, even with agents=normal."""
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="quiet",
        interval_minutes=0,
        agents=NotificationsAgentsConfig(mode="normal"),
    )
    events = [SubagentStarted(agent_id="a1", agent_type="coder"), Response(content="Done")]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert texts[0] == "⏳ Working...", f"First message must be Working..., got: {texts[0]}"


async def test_handle_message_normal_orch_agents_quiet_hides_subagent_event() -> None:
    """Normal orchestrator + quiet agents → SubagentStarted not sent."""
    from archon.ai.event_mapper import SubagentStarted
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig

    notif = NotificationsConfig(
        mode="normal",
        agents=NotificationsAgentsConfig(mode="quiet"),
    )
    events = [
        ToolStarted(name="Bash"),  # orchestrator tool — should appear
        SubagentStarted(agent_id="a1", agent_type="researcher"),  # agent — should be hidden
        Response(content="Done"),
    ]
    mgr = _mock_session_manager(*events)
    msg = _mock_message("go")

    await handle_message(msg, mgr, _split, notifications=notif)

    texts = [call[0][0] for call in msg.answer.call_args_list]
    assert any("🔧 Tool" in t for t in texts), "Orchestrator tool event should be visible"
    assert not any("🤖 Agent" in t for t in texts), f"Agent event should be suppressed: {texts}"
