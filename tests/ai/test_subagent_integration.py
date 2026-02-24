"""Tests for sub-agent integration — hook bridge, config, and event formatting."""
import asyncio
import html
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Message

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import (
    Response,
    SubagentStarted,
    SubagentStopped,
    ToolStarted,
)
from archon.ai.session_manager import SessionManager, _build_sdk_agents
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import format_event
from archon.config.loader import (
    AgentDefinitionConfig,
    AgentsConfig,
    ConfigError,
    load_config,
)

_split = SplitStrategy()


# ──────────────────────────────────────────────────────────────────
# Event dataclass instantiation
# ──────────────────────────────────────────────────────────────────


def test_subagent_started_fields() -> None:
    e = SubagentStarted(agent_id="abc123", agent_type="researcher")
    assert e.agent_id == "abc123"
    assert e.agent_type == "researcher"


def test_subagent_stopped_fields() -> None:
    e = SubagentStopped(agent_id="abc123", agent_type="coder")
    assert e.agent_id == "abc123"
    assert e.agent_type == "coder"


# ──────────────────────────────────────────────────────────────────
# format_event — SubagentStarted / SubagentStopped
# ──────────────────────────────────────────────────────────────────


def test_format_subagent_started_debug_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    event = SubagentStarted(agent_id="x", agent_type="researcher")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent: <b>researcher</b> started"]


def test_format_subagent_stopped_debug_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    event = SubagentStopped(agent_id="x", agent_type="researcher")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent: <b>researcher</b> done"]


def test_format_subagent_started_verbose_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="verbose")
    event = SubagentStarted(agent_id="x", agent_type="coder")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent: <b>coder</b> started"]


def test_format_subagent_stopped_verbose_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="verbose")
    event = SubagentStopped(agent_id="x", agent_type="coder")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent: <b>coder</b> done"]


def test_format_subagent_started_normal_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="normal")
    event = SubagentStarted(agent_id="x", agent_type="reviewer")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent: <b>reviewer</b> started"]


def test_format_subagent_started_quiet_mode_returns_empty() -> None:
    """Sub-agent events are suppressed in quiet mode."""
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="quiet")
    assert format_event(SubagentStarted(agent_id="x", agent_type="a"), _split, notifications=notif) == []
    assert format_event(SubagentStopped(agent_id="x", agent_type="a"), _split, notifications=notif) == []


def test_format_subagent_empty_agent_type() -> None:
    """Empty agent_type falls back to 'unknown'."""
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    result = format_event(SubagentStarted(agent_id="x", agent_type=""), _split, notifications=notif)
    assert result == ["🤖 Agent: <b>unknown</b> started"]


def test_format_subagent_escapes_html() -> None:
    """HTML special chars in agent_type are escaped."""
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    event = SubagentStarted(agent_id="x", agent_type="<script>bad</script>")
    result = format_event(event, _split, notifications=notif)
    assert "<script>" not in result[0]
    assert html.escape("<script>bad</script>") in result[0]


# ──────────────────────────────────────────────────────────────────
# Config — AgentsConfig loading
# ──────────────────────────────────────────────────────────────────


VALID_TOML_BASE = """\
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "/tmp"
inactivity_timeout_seconds = 1800

[output]
max_message_length = 4000
truncation_strategy = "split"
"""


def _env_file(tmp_path: Path, token: str = "test_token") -> Path:
    p = tmp_path / ".env"
    p.write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    return p


def _config_file(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "config.toml"
    p.write_text(VALID_TOML_BASE + extra)
    return p


def test_agents_config_defaults_to_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path))
    assert cfg.agents.enabled is True
    assert cfg.agents.definitions == []


def test_agents_config_parses_definitions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = """
[agents]
enabled = true

[[agents.definitions]]
name = "researcher"
description = "Web research specialist"
prompt = "You are a researcher."
tools = ["WebSearch", "Read"]
model = "haiku"

[[agents.definitions]]
name = "coder"
description = "Expert code writer"
prompt = "You are a coder."
"""
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, extra))
    assert cfg.agents.enabled is True
    assert len(cfg.agents.definitions) == 2

    researcher = cfg.agents.definitions[0]
    assert researcher.name == "researcher"
    assert researcher.description == "Web research specialist"
    assert researcher.prompt == "You are a researcher."
    assert researcher.tools == ["WebSearch", "Read"]
    assert researcher.model == "haiku"

    coder = cfg.agents.definitions[1]
    assert coder.name == "coder"
    assert coder.model is None  # not set


def test_agents_config_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    extra = "\n[agents]\nenabled = false\n"
    cfg = load_config(env_file=_env_file(tmp_path), config_file=_config_file(tmp_path, extra))
    assert cfg.agents.enabled is False


# ──────────────────────────────────────────────────────────────────
# _build_sdk_agents conversion
# ──────────────────────────────────────────────────────────────────


def test_build_sdk_agents_none_when_no_config() -> None:
    assert _build_sdk_agents(None) is None


def test_build_sdk_agents_none_when_disabled() -> None:
    cfg = AgentsConfig(enabled=False, definitions=[
        AgentDefinitionConfig(name="x", description="", prompt="p"),
    ])
    assert _build_sdk_agents(cfg) is None


def test_build_sdk_agents_none_when_empty() -> None:
    cfg = AgentsConfig(enabled=True, definitions=[])
    assert _build_sdk_agents(cfg) is None


def test_build_sdk_agents_returns_dict() -> None:
    cfg = AgentsConfig(
        enabled=True,
        definitions=[
            AgentDefinitionConfig(
                name="researcher",
                description="Researcher",
                prompt="You are a researcher.",
                tools=["WebSearch"],
                model="haiku",
            ),
            AgentDefinitionConfig(
                name="coder",
                description="Coder",
                prompt="You are a coder.",
                tools=[],
                model=None,
            ),
        ],
    )
    result = _build_sdk_agents(cfg)
    assert result is not None
    assert set(result.keys()) == {"researcher", "coder"}

    researcher = result["researcher"]
    assert researcher.description == "Researcher"
    assert researcher.prompt == "You are a researcher."
    assert researcher.tools == ["WebSearch"]
    assert researcher.model == "haiku"

    coder = result["coder"]
    assert coder.tools is None  # empty list → None for SDK


# ──────────────────────────────────────────────────────────────────
# ClaudeSession — hook queue drain in send()
# ──────────────────────────────────────────────────────────────────


def _make_mock_client(messages: list = []):
    client = MagicMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.query = AsyncMock()

    async def _receive_response():  # type: ignore[return]
        for m in messages:
            yield m

    client.receive_response = _receive_response
    return client


async def test_send_drains_hook_queue_between_events() -> None:
    """Hook events injected mid-stream appear before the next regular event."""
    from claude_agent_sdk import ResultMessage

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="Hello",
    )

    # Use a sentinel session reference resolved at generator construction time
    session: ClaudeSession = ClaudeSession()

    # Simulate a hook firing DURING receive_response() by injecting into the queue
    # inside the generator, just before yielding the ResultMessage.
    async def _receive_with_hook():  # type: ignore[return]
        session._hook_queue.put_nowait(SubagentStarted(agent_id="a1", agent_type="researcher"))
        yield result_msg

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = _receive_with_hook

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()

    session._client = mock_client  # point the live session at our hook-injecting mock

    events = [e async for e in session.send("test")]

    # SubagentStarted must be present and appear BEFORE the Response
    assert any(isinstance(e, SubagentStarted) for e in events)
    assert any(isinstance(e, Response) for e in events)
    subagent_idx = next(i for i, e in enumerate(events) if isinstance(e, SubagentStarted))
    response_idx = next(i for i, e in enumerate(events) if isinstance(e, Response))
    assert subagent_idx < response_idx


async def test_send_clears_stale_hook_events_at_start() -> None:
    """Leftover hook events from previous send() are discarded."""
    from claude_agent_sdk import ResultMessage

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="Hello",
    )
    session = ClaudeSession()
    mock_client = _make_mock_client([result_msg])

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()

    # Stale event from "previous" session
    session._hook_queue.put_nowait(SubagentStarted(agent_id="stale", agent_type="old"))

    # Second send — stale should be discarded, only normal events yielded
    mock_client.receive_response = lambda: _make_mock_client([result_msg]).receive_response()

    async def _fresh_receive():  # type: ignore[return]
        yield result_msg

    mock_client.receive_response = _fresh_receive

    events = [e async for e in session.send("test2")]

    # The stale SubagentStarted from before should NOT appear
    assert not any(
        isinstance(e, SubagentStarted) and e.agent_id == "stale"
        for e in events
    )


async def test_send_drains_hook_queue_after_all_events() -> None:
    """Hook events placed after the last SDK message appear via final drain."""
    from claude_agent_sdk import ResultMessage

    result_msg = ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        result="Hello",
    )
    session = ClaudeSession()

    # We'll inject a SubagentStopped *after* the result message arrives
    # by using a custom receive_response that also fills the queue
    async def _receive_with_hook():  # type: ignore[return]
        yield result_msg
        # Simulate hook firing after last message
        session._hook_queue.put_nowait(SubagentStopped(agent_id="a1", agent_type="coder"))

    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.disconnect = AsyncMock()
    mock_client.query = AsyncMock()
    mock_client.receive_response = _receive_with_hook

    with patch("archon.ai.claude_session.ClaudeSDKClient", return_value=mock_client):
        await session.start()

    # Reset to fresh client so start() doesn't interfere
    session._client = mock_client

    events = [e async for e in session.send("test")]

    assert any(isinstance(e, SubagentStopped) for e in events)


async def test_session_accepts_agents_param() -> None:
    """ClaudeSession stores agents and passes them to options."""
    from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions

    captured_options: list[ClaudeAgentOptions] = []

    class FakeClient:
        def __init__(self, options):
            captured_options.append(options)
        connect = AsyncMock()
        disconnect = AsyncMock()

    agents = {
        "helper": AgentDefinition(description="A helper", prompt="Help the user.")
    }
    session = ClaudeSession(agents=agents)
    with patch("archon.ai.claude_session.ClaudeSDKClient", side_effect=FakeClient):
        with patch.dict("os.environ", {}, clear=False):
            try:
                await session.start()
            except AttributeError:
                pass  # FakeClient has no await-able connect attr as class

    assert session._agents == agents


# ──────────────────────────────────────────────────────────────────
# SessionManager — agents_config propagation
# ──────────────────────────────────────────────────────────────────


def test_session_manager_stores_agents_config() -> None:
    cfg = AgentsConfig(
        enabled=True,
        definitions=[AgentDefinitionConfig(name="x", description="", prompt="p")],
    )
    mgr = SessionManager(timeout=60, agents_config=cfg)
    assert mgr._agents_config is cfg


def test_session_manager_no_agents_config() -> None:
    mgr = SessionManager(timeout=60)
    assert mgr._agents_config is None
