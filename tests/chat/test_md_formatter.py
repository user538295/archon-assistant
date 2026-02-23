"""Tests for markdown → Telegram HTML formatter."""
import pytest

from archon.chat.md_formatter import md_to_html


# ──────────────────────────────────────────────────────────────────
# Happy paths — basic markdown conversions
# ──────────────────────────────────────────────────────────────────


def test_bold_asterisks() -> None:
    assert md_to_html("**bold**") == "<b>bold</b>"


def test_bold_underscores() -> None:
    assert md_to_html("__bold__") == "<b>bold</b>"


def test_italic_asterisk() -> None:
    assert md_to_html("*italic*") == "<i>italic</i>"


def test_italic_underscore() -> None:
    assert md_to_html("_italic_") == "<i>italic</i>"


def test_strikethrough() -> None:
    assert md_to_html("~~strike~~") == "<s>strike</s>"


def test_inline_code() -> None:
    assert md_to_html("`code`") == "<code>code</code>"


def test_fenced_code_block_no_lang() -> None:
    assert md_to_html("```\nsome code\n```") == "<pre>some code</pre>"


def test_fenced_code_block_with_lang() -> None:
    result = md_to_html("```python\nreturn x + 1\n```")
    assert result == '<pre><code class="language-python">return x + 1</code></pre>'


def test_heading_h1() -> None:
    assert md_to_html("# Title") == "<b>Title</b>"


def test_heading_h2() -> None:
    assert md_to_html("## Subtitle") == "<b>Subtitle</b>"


def test_heading_h3() -> None:
    assert md_to_html("### Section") == "<b>Section</b>"


def test_mixed_inline() -> None:
    assert md_to_html("**bold** and *italic*") == "<b>bold</b> and <i>italic</i>"


def test_plain_text_passthrough() -> None:
    assert md_to_html("hello world") == "hello world"


def test_empty_string() -> None:
    assert md_to_html("") == ""


def test_multiline_code_block() -> None:
    result = md_to_html("```python\nline1\nline2\n```")
    assert result == '<pre><code class="language-python">line1\nline2</code></pre>'


def test_mixed_code_block_and_inline() -> None:
    text = "Here is **bold** and:\n```python\ncode\n```\nmore text"
    result = md_to_html(text)
    assert "<b>bold</b>" in result
    assert '<pre><code class="language-python">code</code></pre>' in result
    assert result.endswith("more text")


def test_response_with_sections_and_code() -> None:
    text = "## Result\n\nHere is the answer:\n\n```bash\nls -la\n```\n\nDone."
    result = md_to_html(text)
    assert "<b>Result</b>" in result
    assert '<pre><code class="language-bash">ls -la</code></pre>' in result
    assert "Done." in result


# ──────────────────────────────────────────────────────────────────
# HTML safety — special characters must still be escaped
# ──────────────────────────────────────────────────────────────────


def test_angle_brackets_in_plain_text() -> None:
    assert md_to_html("<script>") == "&lt;script&gt;"


def test_ampersand_in_plain_text() -> None:
    assert md_to_html("foo & bar") == "foo &amp; bar"


def test_double_quote_in_plain_text() -> None:
    assert md_to_html('say "hi"') == 'say &quot;hi&quot;'


def test_html_chars_in_fenced_code_block() -> None:
    assert md_to_html("```\n<div class=\"x\">\n```") == "<pre>&lt;div class=&quot;x&quot;&gt;</pre>"


def test_html_chars_in_inline_code() -> None:
    assert md_to_html("`<tag>`") == "<code>&lt;tag&gt;</code>"


def test_ampersand_in_inline_code() -> None:
    assert md_to_html("`foo & bar`") == "<code>foo &amp; bar</code>"


def test_html_chars_in_code_block_not_interpreted_as_tags() -> None:
    result = md_to_html("```\n<b>not bold</b>\n```")
    assert "<b>not bold</b>" not in result
    assert "&lt;b&gt;not bold&lt;/b&gt;" in result


def test_heading_with_ampersand() -> None:
    assert md_to_html("## Hello & World") == "<b>Hello &amp; World</b>"


# ──────────────────────────────────────────────────────────────────
# Non-matching patterns — should NOT apply formatting
# ──────────────────────────────────────────────────────────────────


def test_markdown_inside_code_block_not_processed() -> None:
    result = md_to_html("```\n**not bold**\n```")
    assert "<b>" not in result
    assert "**not bold**" in result


def test_markdown_inside_inline_code_not_processed() -> None:
    result = md_to_html("`**not bold**`")
    assert result == "<code>**not bold**</code>"


def test_underscore_in_identifier_not_italic() -> None:
    assert md_to_html("variable_name") == "variable_name"


def test_multiple_underscores_in_identifier_not_italic() -> None:
    assert md_to_html("some_variable_name") == "some_variable_name"


def test_double_asterisk_not_single_italic() -> None:
    # **text** should be bold, not two italic markers
    assert md_to_html("**text**") == "<b>text</b>"


def test_unmatched_asterisk_not_processed() -> None:
    assert md_to_html("price is $5*") == "price is $5*"


def test_hash_mid_line_not_heading() -> None:
    result = md_to_html("foo # bar")
    assert "<b>" not in result
    assert result == "foo # bar"
