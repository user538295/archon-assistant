"""E2E smoke tests for the multi-agent pipeline — Task #6.

Patches ClaudeSession only, so Pipeline, classification parsing,
prompt building, and SessionManager all run with real code.

Scenarios:
  - Chat flow end-to-end
  - Task flow end-to-end
  - Malformed classifier → fallback → Decomposer responds
  - Two users: independent Pipelines
  - Session lifecycle: create → send → stop → recreate
"""

from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.event_mapper import (
    ClassificationEvent,
    Response,
    RoutingEvent,
    ThinkingResult,
    ToolResult,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _fake_claude_session(events: list):
    """Create a fake ClaudeSession that yields scripted events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = False
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.is_alive = True
    session.model = None
    session.diagnostics = {"is_alive": True}
    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    return session


# ──────────────────────────────────────────────────────────────────
# Chat flow end-to-end
# ──────────────────────────────────────────────────────────────────


async def test_e2e_chat_flow() -> None:
    """User sends a greeting → classified as chat → Decomposer responds conversationally."""
    classifier = _fake_claude_session([Response(content='{"intent": "chat", "confidence": 0.95}')])
    decomposer = _fake_claude_session([Response(content="Hello! How can I help you today?")])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("hi there")]

    # ClassificationEvent must be first
    assert isinstance(events[0], ClassificationEvent)
    assert events[0].intent == "chat"
    assert events[0].confidence == 0.95

    # Decomposer response must follow
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert "Hello" in responses[0].content

    # Classifier received the raw user prompt
    assert "hi there" in classifier._send_calls[0]

    # Decomposer received classification-prefixed prompt
    assert "[Classification:" in decomposer._send_calls[0]
    assert '"intent": "chat"' in decomposer._send_calls[0]
    assert "hi there" in decomposer._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Task flow end-to-end
# ──────────────────────────────────────────────────────────────────


async def test_e2e_task_flow() -> None:
    """User sends a task request → classified as task → Decomposer uses tools and responds."""
    classifier = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.92}')])
    decomposer = _fake_claude_session([
        ThinkingResult(content="I need to write a test."),
        ToolStarted(name="Write", input="test_foo.py"),
        ToolResult(content="File written successfully."),
        Response(content="Done! I wrote the test file."),
    ])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("write a unit test")]

    # Classification
    assert events[0].intent == "task"

    # Full Decomposer event sequence (RoutingEvent is appended after decomposer loop)
    types = [type(e).__name__ for e in events]
    assert types == [
        "ClassificationEvent",
        "ThinkingResult",
        "ToolStarted",
        "ToolResult",
        "Response",
        "RoutingEvent",
    ]

    # Decomposer received task classification
    assert '"intent": "task"' in decomposer._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Malformed classifier → fallback → Decomposer responds
# ──────────────────────────────────────────────────────────────────


async def test_e2e_malformed_classifier_fallback() -> None:
    """Classifier returns garbage → defaults to task → Decomposer still responds."""
    classifier = _fake_claude_session([Response(content="Sorry, I can't classify this.")])
    decomposer = _fake_claude_session([Response(content="I'll help you with that.")])

    with patch("archon.ai.pipeline.ClaudeSession", side_effect=[classifier, decomposer]):
        mgr = SessionManager(timeout=60)
        session = await mgr.get_or_create(user_id=1)
        events = [e async for e in session.send("do something complex")]

    # Default classification: task with 0.0 confidence
    classification = events[0]
    assert isinstance(classification, ClassificationEvent)
    assert classification.intent == "task"
    assert classification.confidence == 0.0

    # Decomposer still responded
    responses = [e for e in events if isinstance(e, Response)]
    assert len(responses) == 1
    assert responses[0].content == "I'll help you with that."

    # Decomposer received default task classification
    assert '"intent": "task"' in decomposer._send_calls[0]
    assert '"confidence": 0.0' in decomposer._send_calls[0]


# ──────────────────────────────────────────────────────────────────
# Two users: independent Pipelines
# ──────────────────────────────────────────────────────────────────


async def test_e2e_two_users_independent_pipelines() -> None:
    """Two users get independent Pipeline instances with separate sessions."""
    classifier_1 = _fake_claude_session([Response(content='{"intent": "chat", "confidence": 0.9}')])
    decomposer_1 = _fake_claude_session([Response(content="Hi user 1!")])
    classifier_2 = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.85}')])
    decomposer_2 = _fake_claude_session([Response(content="Done for user 2.")])

    with patch(
        "archon.ai.pipeline.ClaudeSession",
        side_effect=[classifier_1, decomposer_1, classifier_2, decomposer_2],
    ):
        mgr = SessionManager(timeout=60)
        session_1 = await mgr.get_or_create(user_id=1)
        session_2 = await mgr.get_or_create(user_id=2)

        # Different pipeline instances
        assert session_1 is not session_2

        events_1 = [e async for e in session_1.send("hello")]
        events_2 = [e async for e in session_2.send("build something")]

    # User 1: chat classification
    assert events_1[0].intent == "chat"
    assert any(isinstance(e, Response) and "user 1" in e.content for e in events_1)

    # User 2: task classification
    assert events_2[0].intent == "task"
    assert any(isinstance(e, Response) and "user 2" in e.content for e in events_2)


# ──────────────────────────────────────────────────────────────────
# Session lifecycle: create → send → stop → recreate
# ──────────────────────────────────────────────────────────────────


async def test_e2e_session_lifecycle() -> None:
    """Session can be created, used, stopped, and recreated."""
    classifier_1 = _fake_claude_session([Response(content='{"intent": "chat", "confidence": 0.8}')])
    decomposer_1 = _fake_claude_session([Response(content="First session.")])
    classifier_2 = _fake_claude_session([Response(content='{"intent": "task", "confidence": 0.9}')])
    decomposer_2 = _fake_claude_session([Response(content="Second session.")])

    with patch(
        "archon.ai.pipeline.ClaudeSession",
        side_effect=[classifier_1, decomposer_1, classifier_2, decomposer_2],
    ):
        mgr = SessionManager(timeout=60)

        # Create and use first session
        session_1 = await mgr.get_or_create(user_id=1)
        events_1 = [e async for e in session_1.send("first message")]
        assert any(isinstance(e, Response) and "First" in e.content for e in events_1)

        # Stop the session
        await mgr.stop(user_id=1)
        assert not mgr.has_session(1)

        # Recreate — should get a fresh pipeline
        session_2 = await mgr.get_or_create(user_id=1)
        assert session_2 is not session_1

        events_2 = [e async for e in session_2.send("second message")]

    assert any(isinstance(e, Response) and "Second" in e.content for e in events_2)
    assert events_2[0].intent == "task"  # second classifier returns task
