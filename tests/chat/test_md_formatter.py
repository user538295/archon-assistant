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


# ──────────────────────────────────────────────────────────────────
# Link rendering — Telegram supports <a href="…">
# ──────────────────────────────────────────────────────────────────


def test_link_renders_as_anchor() -> None:
    result = md_to_html("[click here](https://example.com)")
    assert result == '<a href="https://example.com">click here</a>'


def test_link_with_bold_text() -> None:
    result = md_to_html("[**bold link**](https://example.com)")
    assert '<a href="https://example.com">' in result
    assert "<b>bold link</b>" in result


def test_plain_url_not_auto_linked() -> None:
    """Raw URLs without markdown syntax stay as plain text."""
    result = md_to_html("visit https://example.com today")
    assert result == "visit https://example.com today"


# ──────────────────────────────────────────────────────────────────
# Unsupported-in-Telegram constructs → plain-text fallback
# These are the constructs that previously caused TelegramBadRequest
# because the default HTMLRenderer emits tags Telegram rejects.
# ──────────────────────────────────────────────────────────────────


def test_unordered_list_uses_bullets() -> None:
    result = md_to_html("- alpha\n- beta\n- gamma")
    assert result == "• alpha\n• beta\n• gamma"
    assert "<ul>" not in result
    assert "<li>" not in result


def test_ordered_list_uses_numbers() -> None:
    result = md_to_html("1. first\n2. second\n3. third")
    assert result == "1. first\n2. second\n3. third"
    assert "<ol>" not in result
    assert "<li>" not in result


def test_list_item_with_inline_markup() -> None:
    result = md_to_html("- **bold** item\n- plain item")
    assert result == "• <b>bold</b> item\n• plain item"


def test_blockquote_prefixed_with_pipe() -> None:
    result = md_to_html("> wise words\n> more wisdom")
    assert result == "│ wise words\n│ more wisdom"
    assert "<blockquote>" not in result


def test_blockquote_multiline_each_line_prefixed() -> None:
    result = md_to_html("> line one\n> line two\n> line three")
    for line in result.split("\n"):
        assert line.startswith("│ "), f"Line missing │ prefix: {line!r}"


def test_thematic_break_renders_as_rule() -> None:
    result = md_to_html("---")
    assert "─" * 20 in result
    assert "<hr" not in result


def test_hard_linebreak_renders_as_newline() -> None:
    # Two trailing spaces = hard line break in Markdown
    result = md_to_html("line one  \nline two")
    assert result == "line one\nline two"
    assert "<br" not in result


def test_image_with_alt_text() -> None:
    url = "https://example.com/logo.png"
    md = "".join(["![logo](", url, ")"])  # avoid bash ! expansion in -c runs
    result = md_to_html(md)
    assert result == "[image: logo]"
    assert "<img" not in result


def test_image_empty_alt_text() -> None:
    url = "https://example.com/logo.png"
    md = "".join(["![](", url, ")"])
    result = md_to_html(md)
    assert result == "[image]"
    assert "<img" not in result


def test_table_renders_as_pipe_delimited_rows() -> None:
    md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    result = md_to_html(md)
    # Header row: cells in bold, framed with │
    assert "│ <b>A</b> │ <b>B</b> │" in result
    # Body rows: plain text, framed with │
    assert "│ 1 │ 2 │" in result
    assert "│ 3 │ 4 │" in result
    # No HTML table tags
    assert "<table" not in result
    assert "<tr" not in result
    assert "<td" not in result
    assert "<th" not in result


def test_table_full_output() -> None:
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = md_to_html(md)
    assert result == "│ <b>A</b> │ <b>B</b> │\n│ 1 │ 2 │"
