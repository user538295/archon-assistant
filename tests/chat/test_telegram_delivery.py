"""Tests for shared Telegram-safe message rendering helpers."""

import html

from archon.ai.truncation import SplitStrategy
from archon.chat.md_formatter import md_to_html
from archon.chat.telegram_delivery import render_split_messages


def test_render_split_messages_returns_single_message_when_it_fits() -> None:
    result = render_split_messages(
        text="hello",
        prefix="✅ Response:\n",
        truncation=SplitStrategy(),
        max_len=40,
        renderer=md_to_html,
    )

    assert result == ["✅ Response:\nhello"]


def test_render_split_messages_respects_final_max_len_after_html_escaping() -> None:
    result = render_split_messages(
        text="<" * 30,
        prefix="✅ Response:\n",
        truncation=SplitStrategy(),
        max_len=40,
        renderer=md_to_html,
    )

    assert len(result) > 1
    assert all(message.startswith("✅ Response:\n") for message in result)
    assert all(len(message) <= 40 for message in result)
    assert all("&lt;" in message for message in result)


def test_render_split_messages_keeps_html_tags_balanced_per_chunk() -> None:
    result = render_split_messages(
        text="**w** " * 50,
        prefix="✅ Response:\n",
        truncation=SplitStrategy(),
        max_len=80,
        renderer=md_to_html,
    )

    assert len(result) > 1
    for message in result:
        assert message.count("<b>") == message.count("</b>")


def test_render_split_messages_supports_plain_html_escape_renderer() -> None:
    result = render_split_messages(
        text='echo "<tag>"',
        prefix="🔧 Tool: Bash\n",
        truncation=SplitStrategy(),
        max_len=40,
        renderer=html.escape,
    )

    assert len(result) > 1
    assert all(message.startswith("🔧 Tool: Bash\n") for message in result)
    assert all(len(message) <= 40 for message in result)
    assert any("&quot;" in message for message in result)


def test_render_split_messages_emits_truncation_notice_when_content_cannot_fit() -> None:
    # Use an absurdly small max_len so even a 1-char chunk cannot fit after
    # the prefix is prepended — this drives the binary search to produce no
    # valid split and must trigger the placeholder notice instead of a 1-char
    # message.
    prefix = "✅ Response:\n"
    long_text = "x" * 500
    # max_len smaller than just the prefix — no rendered chunk can ever fit
    max_len = len(prefix) - 1

    result = render_split_messages(
        text=long_text,
        prefix=prefix,
        truncation=SplitStrategy(),
        max_len=max_len,
        renderer=html.escape,
    )

    assert len(result) == 1
    assert "Content too large to display" in result[0]
    assert "500" in result[0]
    assert result[0].startswith(prefix)
