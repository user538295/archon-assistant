"""Markdown → Telegram HTML formatter (powered by mistune 3.x).

Telegram HTML supports only: <b>, <i>, <s>, <u>, <code>, <pre>, <a>.
Every other construct (lists, blockquotes, tables, images, thematic breaks)
is converted to plain-text equivalents so Telegram never rejects the message
with a BadRequest / entity-parse error.
"""
import html as _html
from typing import Optional

import mistune
from mistune.plugins.formatting import parse_strikethrough

# Private sentinel used by list_item() / list() to reconstruct numbered lists
# without losing the item-index information between calls.
_SENTINEL = "\x00"


def _render_strikethrough_telegram(renderer: mistune.HTMLRenderer, text: str) -> str:
    """Render strikethrough as <s> (Telegram) instead of <del>."""
    return f"<s>{text}</s>"


def _strikethrough_telegram_plugin(md: mistune.Markdown) -> None:
    """Strikethrough plugin that outputs <s> instead of <del> for Telegram."""
    md.inline.register(
        "strikethrough",
        r"~~(?=[^\s~])",
        parse_strikethrough,
        before="link",
    )
    if md.renderer:
        md.renderer.register("strikethrough", _render_strikethrough_telegram)


class _TelegramRenderer(mistune.HTMLRenderer):
    """Render Markdown tokens as Telegram-compatible HTML.

    Telegram supports only: <b>, <i>, <s>, <u>, <code>, <pre>, <a>.
    Unsupported block wrappers are stripped or mapped to supported tags.
    """

    def heading(self, text: str, level: int, **attrs: object) -> str:
        return f"<b>{text}</b>\n"

    def paragraph(self, text: str) -> str:
        return text + "\n"

    def emphasis(self, text: str) -> str:
        return f"<i>{text}</i>"

    def strong(self, text: str) -> str:
        return f"<b>{text}</b>"

    def codespan(self, text: str) -> str:
        return f"<code>{_html.escape(text)}</code>"

    def block_html(self, html: str) -> str:
        """Escape raw HTML blocks — Telegram doesn't allow arbitrary HTML."""
        return _html.escape(html.strip()) + "\n"

    def inline_html(self, html: str) -> str:
        """Escape raw inline HTML for the same reason."""
        return _html.escape(html)

    # ── Lists ────────────────────────────────────────────────────────
    # Telegram has no <ul>/<ol>/<li>; convert to plain bullet / numbered lines.

    def list_item(self, text: str) -> str:
        """Prefix each item with a sentinel so list() can re-number them."""
        return _SENTINEL + text.strip() + "\n"

    def list(self, text: str, ordered: bool, **attrs: object) -> str:
        """Reassemble items as bullet points or 1. 2. 3. numbers."""
        parts = [p.rstrip("\n") for p in text.split(_SENTINEL) if p.strip()]
        if ordered:
            lines = [f"{i + 1}. {item}" for i, item in enumerate(parts)]
        else:
            lines = [f"• {item}" for item in parts]
        return "\n".join(lines) + "\n"

    # ── Block quote ──────────────────────────────────────────────────
    # Telegram has no <blockquote>; prefix each line with a pipe glyph.

    def block_quote(self, text: str) -> str:
        lines = text.strip().split("\n")
        return "\n".join(f"│ {line}" for line in lines) + "\n"

    # ── Image ────────────────────────────────────────────────────────
    # Telegram has no <img>; emit a plain-text placeholder.

    def image(self, text: str, url: str, title: Optional[str] = None) -> str:
        """``text`` is the rendered alt text (may contain inline markup)."""
        return f"[image: {text}]" if text else "[image]"

    # ── Thematic break / hard line break ─────────────────────────────

    def thematic_break(self) -> str:
        return "─" * 20 + "\n"

    def linebreak(self) -> str:
        return "\n"

    # ── Table ────────────────────────────────────────────────────────
    # Telegram has no table tags; render as ASCII-style pipe-delimited rows.
    # Header cells are bolded; a │ glyph frames every cell.
    # These methods shadow the ones the 'table' plugin would register, so
    # parsing is provided by the plugin while rendering stays Telegram-safe.

    def table_cell(self, text: str, align: Optional[str] = None, head: bool = False) -> str:
        content = f"<b>{text}</b>" if head else text
        return content + " │ "

    def table_row(self, text: str) -> str:
        # text = "cell1 │ cell2 │ " — strip trailing separator, add leading one
        return "│ " + text.rstrip("│ ").rstrip() + " │\n"

    def table_head(self, text: str) -> str:
        # table_head children are table_cell tokens directly (no table_row
        # wrapper), so text is the raw concatenation of cell outputs.
        # Add the same │ framing that table_row adds for body rows.
        return "│ " + text.rstrip("│ ").rstrip() + " │\n"

    def table_body(self, text: str) -> str:
        return text

    def table(self, text: str) -> str:
        return text + "\n"

    # ── Code blocks ──────────────────────────────────────────────────

    def block_code(self, code: str, info: Optional[str] = None) -> str:
        lang = (info or "").strip()
        if lang:
            lang = lang.split()[0]
        escaped = _html.escape(code.rstrip("\n"))
        if lang:
            return f'<pre><code class="language-{_html.escape(lang)}">{escaped}</code></pre>\n'
        return f"<pre>{escaped}</pre>\n"


_md = mistune.create_markdown(
    renderer=_TelegramRenderer(),
    plugins=[_strikethrough_telegram_plugin, "table"],
)


def md_to_html(text: str) -> str:
    """Convert markdown-formatted text to Telegram HTML using mistune 3.x."""
    if not text:
        return ""
    result = _md(text)
    if isinstance(result, str):
        return result.rstrip("\n") if result else ""
    return ""
