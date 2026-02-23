"""Markdown → Telegram HTML formatter."""
import html
import re


def md_to_html(text: str) -> str:
    """Convert markdown-formatted text to Telegram HTML.

    Handles: **bold**, __bold__, *italic*, _italic_, ~~strike~~,
    `inline code`, fenced code blocks (with optional language), # headings.
    HTML special characters are always escaped.
    """
    result: list[str] = []
    last = 0
    for m in re.finditer(r"```(\w*)\n?([\s\S]*?)```", text):
        result.append(_inline(text[last : m.start()]))
        lang = m.group(1).strip()
        code = html.escape(m.group(2).rstrip("\n"))
        if lang:
            result.append(f'<pre><code class="language-{html.escape(lang)}">{code}</code></pre>')
        else:
            result.append(f"<pre>{code}</pre>")
        last = m.end()
    result.append(_inline(text[last:]))
    return "".join(result)


def _inline(text: str) -> str:
    """Apply inline markdown → HTML on a segment containing no fenced code blocks."""
    # Step 1: extract inline code spans before HTML-escaping so their
    # content is protected from both escaping interference and bold/italic.
    codes: list[str] = []

    def _save(m: re.Match) -> str:  # type: ignore[type-arg]
        codes.append(f"<code>{html.escape(m.group(1))}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r"`([^`\n]+)`", _save, text)

    # Step 2: HTML-escape everything else.
    text = html.escape(text)

    # Step 3: inline markdown patterns (delimiters are not HTML-special).
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<b>\1</b>", text)
    # Italic (single * or _ not adjacent to another * or word char)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(?!_)(.+?)(?<!_)_(?!\w)", r"<i>\1</i>", text)
    # Strikethrough
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)
    # Headings h1–h3 at line start → bold
    text = re.sub(r"^#{1,3} +(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)

    # Step 4: restore inline code spans.
    for i, code_html in enumerate(codes):
        text = text.replace(f"\x00{i}\x00", code_html)

    return text
