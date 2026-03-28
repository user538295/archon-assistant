"""Tests for archon.chat.telegram_formatter — Task 0 of FEAT-023."""

from archon.ai.event_mapper import Response
from archon.ai.truncation import SplitStrategy


def test_format_event_importable_from_telegram_formatter() -> None:
    """format_event can be imported directly from archon.chat.telegram_formatter."""
    from archon.chat.telegram_formatter import format_event  # noqa: F401

    assert callable(format_event)


def test_handler_still_works_after_format_event_extraction() -> None:
    """format_event is still importable from archon.chat.handler (re-export)."""
    from archon.chat.handler import format_event  # noqa: F401

    assert callable(format_event)


def test_format_event_from_telegram_formatter_behaves_correctly() -> None:
    """format_event imported from telegram_formatter produces correct output."""
    from archon.chat.telegram_formatter import format_event

    truncation = SplitStrategy()
    event = Response(content="Hello world")
    result = format_event(event, truncation)
    assert isinstance(result, list)
    assert len(result) == 1
    assert "Hello world" in result[0]
