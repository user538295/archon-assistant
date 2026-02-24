"""Unit tests for ClaudeSession QMD / MCP server wiring — FR.002.

Verifies that:
  - qmd_url=None → no 'qmd' key in mcp_servers dict passed to SDK options
  - qmd_url set → mcp_servers["qmd"] is the expected HTTP dict
  - The full ClaudeAgentOptions block is built correctly

No real SDK / subprocess is spawned; ClaudeSDKClient.connect() is mocked.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.claude_session import ClaudeSession


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_client(mcp_servers_capture: list) -> MagicMock:
    """Return a mock ClaudeSDKClient that records the options it receives."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    return client


# ── qmd_url=None (QMD disabled) ───────────────────────────────────────────────


async def test_no_qmd_url_produces_empty_mcp_servers() -> None:
    """When qmd_url is None the mcp_servers dict passed to ClaudeAgentOptions must be empty."""
    captured_options: list = []

    def _fake_options_cls(**kwargs):
        captured_options.append(kwargs)
        return MagicMock()

    session = ClaudeSession(qmd_url=None)

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch("archon.ai.claude_session.ClaudeAgentOptions", side_effect=_fake_options_cls),
    ):
        await session.start()

    assert len(captured_options) == 1
    mcp_servers = captured_options[0].get("mcp_servers", {})
    assert "qmd" not in mcp_servers


async def test_no_qmd_url_mcp_servers_is_empty_dict() -> None:
    captured: list = []

    session = ClaudeSession(qmd_url=None)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch("archon.ai.claude_session.ClaudeAgentOptions", side_effect=lambda **kw: captured.append(kw) or MagicMock()),
    ):
        await session.start()

    assert captured[0].get("mcp_servers", {}) == {}


# ── qmd_url set (QMD enabled) ─────────────────────────────────────────────────


async def test_qmd_url_produces_correct_mcp_servers_entry() -> None:
    """qmd_url must appear as mcp_servers['qmd'] = {'type': 'http', 'url': <url>}."""
    captured: list = []
    url = "http://localhost:8181/mcp"

    session = ClaudeSession(qmd_url=url)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch("archon.ai.claude_session.ClaudeAgentOptions", side_effect=lambda **kw: captured.append(kw) or MagicMock()),
    ):
        await session.start()

    mcp_servers = captured[0]["mcp_servers"]
    assert "qmd" in mcp_servers
    assert mcp_servers["qmd"] == {"type": "http", "url": url}


async def test_qmd_url_type_field_is_http() -> None:
    captured: list = []
    session = ClaudeSession(qmd_url="http://remote.host:9090/mcp")
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch("archon.ai.claude_session.ClaudeAgentOptions", side_effect=lambda **kw: captured.append(kw) or MagicMock()),
    ):
        await session.start()

    assert captured[0]["mcp_servers"]["qmd"]["type"] == "http"


async def test_qmd_url_value_preserved_exactly() -> None:
    url = "http://192.168.1.50:41823/mcp"
    captured: list = []
    session = ClaudeSession(qmd_url=url)
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch("archon.ai.claude_session.ClaudeAgentOptions", side_effect=lambda **kw: captured.append(kw) or MagicMock()),
    ):
        await session.start()

    assert captured[0]["mcp_servers"]["qmd"]["url"] == url


# ── mcp_servers is the only entry when no other servers are configured ────────


async def test_only_qmd_in_mcp_servers_no_extras() -> None:
    captured: list = []
    session = ClaudeSession(qmd_url="http://localhost:8181/mcp")
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()

    with (
        patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client),
        patch("archon.ai.claude_session.ClaudeAgentOptions", side_effect=lambda **kw: captured.append(kw) or MagicMock()),
    ):
        await session.start()

    mcp_servers = captured[0]["mcp_servers"]
    assert set(mcp_servers.keys()) == {"qmd"}


# ── qmd_url stored correctly in __init__ ─────────────────────────────────────


def test_qmd_url_stored_on_init() -> None:
    url = "http://localhost:8181/mcp"
    session = ClaudeSession(qmd_url=url)
    assert session._qmd_url == url


def test_qmd_url_none_stored_on_init() -> None:
    session = ClaudeSession(qmd_url=None)
    assert session._qmd_url is None


def test_qmd_url_default_is_none() -> None:
    session = ClaudeSession()
    assert session._qmd_url is None
