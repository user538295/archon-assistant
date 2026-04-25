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


# ──────────────────────────────────────────────────────────────────
# Task 3.2 — Classifier ThinkingResult gating
# ──────────────────────────────────────────────────────────────────


def test_format_event_suppresses_classifier_thinking_in_normal_mode() -> None:
    """classifier ThinkingResult must be suppressed in normal mode."""
    from archon.ai.event_mapper import ThinkingResult
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = ThinkingResult(content="pondering", source="classifier")
    notif = NotificationsConfig(mode="normal")
    result = format_event(event, truncation, notifications=notif)
    assert result == [], f"Expected [] in normal mode, got: {result}"


def test_format_event_suppresses_classifier_thinking_in_verbose_mode() -> None:
    """classifier ThinkingResult must be suppressed in verbose mode."""
    from archon.ai.event_mapper import ThinkingResult
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = ThinkingResult(content="pondering", source="classifier")
    notif = NotificationsConfig(mode="verbose")
    result = format_event(event, truncation, notifications=notif)
    assert result == [], f"Expected [] in verbose mode, got: {result}"


def test_format_event_delivers_classifier_thinking_in_debug_mode() -> None:
    """classifier ThinkingResult must be delivered in debug mode."""
    from archon.ai.event_mapper import ThinkingResult
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = ThinkingResult(content="clf deep thought", source="classifier")
    notif = NotificationsConfig(mode="debug")
    result = format_event(event, truncation, notifications=notif)
    assert len(result) > 0, f"Expected output in debug mode, got: {result}"
    assert "clf deep thought" in result[0]


def test_format_event_regular_thinking_unchanged() -> None:
    """Non-classifier ThinkingResult must render normally in debug mode (regression guard)."""
    from archon.ai.event_mapper import ThinkingResult
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = ThinkingResult(content="main session thought")
    notif = NotificationsConfig(mode="debug")
    result = format_event(event, truncation, notifications=notif)
    assert len(result) > 0
    assert "main session thought" in result[0]


# ──────────────────────────────────────────────────────────────────
# Task 2.1 — size_display field on ContextInjectedEvent / SkillInjectedEvent
# ──────────────────────────────────────────────────────────────────


def test_context_injected_chars_unit() -> None:
    """ContextInjectedEvent with size_display='5 chars' renders (5 chars) in verbose mode."""
    from archon.ai.event_mapper import ContextInjectedEvent
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = ContextInjectedEvent(injection_type="history", size_display="5 chars")
    notif = NotificationsConfig(mode="verbose")
    result = format_event(event, truncation, notifications=notif)
    assert len(result) > 0, f"Expected output in verbose mode, got: {result}"
    assert "(5 chars)" in result[0], f"Expected '(5 chars)' in output, got: {result[0]}"


def test_context_injected_tokens_unit() -> None:
    """ContextInjectedEvent with size_display='2 tokens' renders (2 tokens) in verbose mode."""
    from archon.ai.event_mapper import ContextInjectedEvent
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = ContextInjectedEvent(injection_type="history", size_display="2 tokens")
    notif = NotificationsConfig(mode="verbose")
    result = format_event(event, truncation, notifications=notif)
    assert len(result) > 0, f"Expected output in verbose mode, got: {result}"
    assert "(2 tokens)" in result[0], f"Expected '(2 tokens)' in output, got: {result[0]}"


def test_skill_injected_chars_unit() -> None:
    """SkillInjectedEvent with size_display='3 chars' renders (3 chars) in verbose mode."""
    from archon.ai.event_mapper import SkillInjectedEvent
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = SkillInjectedEvent(skill_name="my-skill", size_display="3 chars")
    notif = NotificationsConfig(mode="verbose")
    result = format_event(event, truncation, notifications=notif)
    assert len(result) > 0, f"Expected output in verbose mode, got: {result}"
    assert "(3 chars)" in result[0], f"Expected '(3 chars)' in output, got: {result[0]}"


def test_skill_injected_tokens_unit() -> None:
    """SkillInjectedEvent with size_display='1 tokens' renders (1 tokens) in verbose mode."""
    from archon.ai.event_mapper import SkillInjectedEvent
    from archon.chat.telegram_formatter import format_event
    from archon.config.loader import NotificationsConfig

    truncation = SplitStrategy()
    event = SkillInjectedEvent(skill_name="my-skill", size_display="1 tokens")
    notif = NotificationsConfig(mode="verbose")
    result = format_event(event, truncation, notifications=notif)
    assert len(result) > 0, f"Expected output in verbose mode, got: {result}"
    assert "(1 tokens)" in result[0], f"Expected '(1 tokens)' in output, got: {result[0]}"
