"""Integration tests: QMD URL propagation from SessionManager → ClaudeSession.

Verifies that the qmd_url configured on SessionManager is forwarded
to every ClaudeSession it creates via the default factory.

No real SDK / subprocess / network I/O occurs.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.session_manager import SessionManager


# ── helpers ───────────────────────────────────────────────────────────────────


def _mock_session() -> ClaudeSession:
    s = MagicMock(spec=ClaudeSession)
    s.start = AsyncMock()
    s.stop = AsyncMock()
    s.is_alive = True
    return s


# ── qmd_url flows through default factory ────────────────────────────────────


async def test_default_factory_passes_qmd_url_to_claude_session() -> None:
    """Default factory must pass qmd_url from SessionManager to ClaudeSession."""
    url = "http://localhost:8181/mcp"
    mgr = SessionManager(timeout=60, qmd_url=url)

    mock_session = _mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        await mgr.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("qmd_url") == url


async def test_default_factory_passes_none_when_qmd_disabled() -> None:
    """qmd_url=None must be forwarded as None (QMD disabled path)."""
    mgr = SessionManager(timeout=60, qmd_url=None)

    mock_session = _mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        await mgr.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("qmd_url") is None


async def test_qmd_url_same_for_all_new_sessions() -> None:
    """All sessions created by the same manager must receive the same qmd_url."""
    url = "http://qmd.internal:9090/mcp"
    mgr = SessionManager(timeout=60, qmd_url=url)

    received_urls: list = []

    def _factory(_cwd):
        s = _mock_session()
        return s

    # Use a custom factory that captures qmd_url via ClaudeSession constructor.
    created: list = []
    mock_s1 = _mock_session()
    mock_s2 = _mock_session()
    call_idx = {"n": 0}

    with patch("archon.ai.session_manager.Pipeline") as MockPipeline:
        MockPipeline.side_effect = [mock_s1, mock_s2]
        await mgr.get_or_create(user_id=1)
        await mgr.get_or_create(user_id=2)

    calls = MockPipeline.call_args_list
    assert len(calls) == 2
    for call in calls:
        _, kw = call
        assert kw.get("qmd_url") == url


# ── SessionManager stores qmd_url internally ──────────────────────────────────


def test_session_manager_stores_qmd_url() -> None:
    url = "http://localhost:8181/mcp"
    mgr = SessionManager(timeout=60, qmd_url=url)
    assert mgr._qmd_url == url


def test_session_manager_qmd_url_defaults_to_none() -> None:
    mgr = SessionManager(timeout=60)
    assert mgr._qmd_url is None


# ── custom factory ignores qmd_url (manager doesn't inject into custom factories) ──


async def test_custom_factory_is_not_overridden_by_qmd_url() -> None:
    """When a custom session_factory is supplied, SessionManager must not interfere."""
    custom_session = _mock_session()
    factory_calls: list = []

    def _custom_factory(cwd):
        factory_calls.append(cwd)
        return custom_session

    mgr = SessionManager(timeout=60, qmd_url="http://localhost:8181/mcp", session_factory=_custom_factory)
    session = await mgr.get_or_create(user_id=1)

    assert session is custom_session
    assert len(factory_calls) == 1


# ── qmd_url forwarded alongside other session params ─────────────────────────


async def test_qmd_url_and_model_both_forwarded() -> None:
    """qmd_url and model are independent; both must reach ClaudeSession."""
    url = "http://localhost:8181/mcp"
    mgr = SessionManager(timeout=60, qmd_url=url)
    mgr.set_model("claude-sonnet-4-5")

    mock_session = _mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        await mgr.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("qmd_url") == url
    assert kwargs.get("model") == "claude-sonnet-4-5"


async def test_qmd_url_forwarded_with_skill_loader() -> None:
    """qmd_url must be forwarded even when a skill_loader is present."""
    url = "http://localhost:8181/mcp"

    skill_loader = MagicMock()
    skill_loader.load_all.return_value = []

    mgr = SessionManager(timeout=60, qmd_url=url, skill_loader=skill_loader)

    mock_session = _mock_session()
    with patch("archon.ai.session_manager.Pipeline", return_value=mock_session) as MockPipeline:
        await mgr.get_or_create(user_id=1)

    _, kwargs = MockPipeline.call_args
    assert kwargs.get("qmd_url") == url
