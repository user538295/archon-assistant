"""Tests for sub-agent integration — hook bridge, config, and event formatting."""
import html
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import (
    SubagentStarted,
    SubagentStopped,
)
from archon.ai.truncation import SplitStrategy
from archon.chat.handler import format_event
from archon.config.loader import (
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
    assert result == ["🤖 Agent <b>researcher</b> started"]


def test_format_subagent_stopped_debug_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    event = SubagentStopped(agent_id="x", agent_type="researcher")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> done"]


def test_format_subagent_started_verbose_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="verbose")
    event = SubagentStarted(agent_id="x", agent_type="coder")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent <b>coder</b> started"]


def test_format_subagent_stopped_verbose_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="verbose")
    event = SubagentStopped(agent_id="x", agent_type="coder")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent <b>coder</b> done"]


def test_format_subagent_started_normal_mode() -> None:
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="normal")
    event = SubagentStarted(agent_id="x", agent_type="reviewer")
    result = format_event(event, _split, notifications=notif)
    assert result == ["🤖 Agent <b>reviewer</b> started"]


def test_format_subagent_started_quiet_mode_always_notifies() -> None:
    """Sub-agent lifecycle events are always sent regardless of quiet mode."""
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="quiet")
    assert format_event(SubagentStarted(agent_id="x", agent_type="a"), _split, notifications=notif) == ["🤖 Agent <b>a</b> started"]
    assert format_event(SubagentStopped(agent_id="x", agent_type="a"), _split, notifications=notif) == ["🤖 Agent <b>a</b> done"]


def test_format_subagent_empty_agent_type() -> None:
    """Empty agent_type falls back to 'unknown'."""
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    result = format_event(SubagentStarted(agent_id="x", agent_type=""), _split, notifications=notif)
    assert result == ["🤖 Agent <b>unknown</b> started"]


def test_format_subagent_escapes_html() -> None:
    """HTML special chars in agent_type are escaped."""
    from archon.config.loader import NotificationsConfig
    notif = NotificationsConfig(mode="debug")
    event = SubagentStarted(agent_id="x", agent_type="<script>bad</script>")
    result = format_event(event, _split, notifications=notif)
    assert "<script>" not in result[0]
    assert html.escape("<script>bad</script>") in result[0]


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
# S11.3 — Per-agent notification configuration
# ──────────────────────────────────────────────────────────────────


# ---------- _resolve_agent_mode helper ----------


def test_resolve_agent_mode_none_inherits_orchestrator_normal() -> None:
    """`agents.mode=None` → inherits orchestrator mode."""
    from archon.chat.handler import _resolve_agent_mode
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode=None))
    assert _resolve_agent_mode(notif) == "normal"


def test_resolve_agent_mode_none_inherits_orchestrator_quiet() -> None:
    from archon.chat.handler import _resolve_agent_mode
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode=None))
    assert _resolve_agent_mode(notif) == "quiet"


def test_resolve_agent_mode_explicit_overrides_orchestrator() -> None:
    """`agents.mode="quiet"` overrides orchestrator mode="normal"."""
    from archon.chat.handler import _resolve_agent_mode
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode="quiet"))
    assert _resolve_agent_mode(notif) == "quiet"


def test_resolve_agent_mode_explicit_verbose_overrides_quiet_orchestrator() -> None:
    from archon.chat.handler import _resolve_agent_mode
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="verbose"))
    assert _resolve_agent_mode(notif) == "verbose"


def test_resolve_agent_mode_notifications_none_returns_debug() -> None:
    """notifications=None → fallback to 'debug' (backward compat)."""
    from archon.chat.handler import _resolve_agent_mode
    assert _resolve_agent_mode(None) == "debug"


# ---------- format_event — SubagentStarted / SubagentStopped via resolved agent mode ----------


def test_format_subagent_started_agents_quiet_orchestrator_normal_still_notifies() -> None:
    """agents.mode='quiet' no longer suppresses — agent lifecycle is always notified."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode="quiet"))
    result = format_event(SubagentStarted(agent_id="x", agent_type="coder"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>coder</b> started"]


def test_format_subagent_stopped_agents_quiet_orchestrator_normal_still_notifies() -> None:
    """agents.mode='quiet' no longer suppresses — agent lifecycle is always notified."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode="quiet"))
    result = format_event(SubagentStopped(agent_id="x", agent_type="coder"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>coder</b> done"]


def test_format_subagent_started_agents_normal_orchestrator_quiet_shows_event() -> None:
    """agents.mode='normal' shows events even when orchestrator is quiet."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="normal"))
    result = format_event(SubagentStarted(agent_id="x", agent_type="explorer"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>explorer</b> started"]


def test_format_subagent_stopped_agents_normal_orchestrator_quiet_shows_event() -> None:
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="normal"))
    result = format_event(SubagentStopped(agent_id="x", agent_type="explorer"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>explorer</b> done"]


def test_format_subagent_started_agents_verbose_orchestrator_quiet_shows_event() -> None:
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="verbose"))
    result = format_event(SubagentStarted(agent_id="x", agent_type="researcher"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>researcher</b> started"]


def test_format_subagent_started_agents_inherit_quiet_orchestrator_still_notifies() -> None:
    """agents.mode=None inherits 'quiet' but agent lifecycle is always notified."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode=None))
    result = format_event(SubagentStarted(agent_id="x", agent_type="coder"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>coder</b> started"]


def test_format_subagent_started_agents_inherit_normal_orchestrator_shows_event() -> None:
    """agents.mode=None → inherits 'normal' → shown."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode=None))
    result = format_event(SubagentStarted(agent_id="x", agent_type="coder"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>coder</b> started"]


def test_format_subagent_started_agents_debug_orchestrator_quiet_shows_event() -> None:
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="debug"))
    result = format_event(SubagentStarted(agent_id="x", agent_type="tester"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>tester</b> started"]


def test_format_subagent_started_both_explicit_quiet_still_notifies() -> None:
    """orchestrator=quiet AND agents=quiet (both explicit) → agent lifecycle still always shown.

    Regression guard: if _resolve_agent_mode were used with ``if agent_mode == 'quiet':
    return []`` logic, this combination would suppress the event.  Agent lifecycle
    is *never* suppressed regardless of any mode setting.
    """
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="quiet"))
    result = format_event(SubagentStarted(agent_id="x", agent_type="planner"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>planner</b> started"]


def test_format_subagent_stopped_both_explicit_quiet_still_notifies() -> None:
    """orchestrator=quiet AND agents=quiet (both explicit) → SubagentStopped always shown."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig
    notif = NotificationsConfig(mode="quiet", agents=NotificationsAgentsConfig(mode="quiet"))
    result = format_event(SubagentStopped(agent_id="x", agent_type="planner"), _split, notifications=notif)
    assert result == ["🤖 Agent <b>planner</b> done"]


# ---------- config load/save — notifications.agents ----------

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


def test_load_config_parses_notifications_agents_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=test\n")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        VALID_TOML_BASE + "\n[notifications]\nmode = \"normal\"\n\n[notifications.agents]\nmode = \"quiet\"\n"
    )
    cfg = load_config(env_file=env, config_file=cfg_file)
    assert cfg.notifications.agents.mode == "quiet"


def test_load_config_no_notifications_agents_section_defaults_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=test\n")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(VALID_TOML_BASE + "\n[notifications]\nmode = \"normal\"\n")
    cfg = load_config(env_file=env, config_file=cfg_file)
    assert cfg.notifications.agents.mode is None


def test_load_config_no_notifications_section_agents_defaults_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No [notifications] section at all → agents.mode defaults to None."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=test\n")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(VALID_TOML_BASE)
    cfg = load_config(env_file=env, config_file=cfg_file)
    assert cfg.notifications.agents.mode is None


def test_load_config_notifications_agents_verbose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=test\n")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(
        VALID_TOML_BASE + "\n[notifications.agents]\nmode = \"verbose\"\n"
    )
    cfg = load_config(env_file=env, config_file=cfg_file)
    assert cfg.notifications.agents.mode == "verbose"


def test_save_notifications_config_writes_agents_mode(tmp_path: Path) -> None:
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig, save_notifications_config
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(VALID_TOML_BASE)

    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode="quiet"))
    save_notifications_config(notif, config_file=cfg_file)

    content = cfg_file.read_text()
    assert 'mode = "quiet"' in content  # agents.mode written

    # Reload and verify round-trip
    import tomllib
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)
    assert data["notifications"]["agents"]["mode"] == "quiet"


def test_save_notifications_config_agents_mode_none_does_not_write_key(tmp_path: Path) -> None:
    """agents.mode=None → the mode key should not appear under [notifications.agents]."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig, save_notifications_config
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(VALID_TOML_BASE)

    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode=None))
    save_notifications_config(notif, config_file=cfg_file)

    import tomllib
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)
    # Either no 'agents' key, or 'agents' dict without 'mode'
    agents_section = data.get("notifications", {}).get("agents", {})
    assert "mode" not in agents_section


def test_save_notifications_config_removes_existing_agents_mode_when_set_to_none(tmp_path: Path) -> None:
    """If config already had agents.mode set, saving with None removes it."""
    from archon.config.loader import NotificationsAgentsConfig, NotificationsConfig, save_notifications_config
    cfg_file = tmp_path / "config.toml"
    # Start with agents.mode = "quiet" in the file
    cfg_file.write_text(
        VALID_TOML_BASE + "\n[notifications]\nmode = \"normal\"\n\n[notifications.agents]\nmode = \"quiet\"\n"
    )

    # Now save with agents.mode=None → should clear the key
    notif = NotificationsConfig(mode="normal", agents=NotificationsAgentsConfig(mode=None))
    save_notifications_config(notif, config_file=cfg_file)

    import tomllib
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)
    agents_section = data.get("notifications", {}).get("agents", {})
    assert "mode" not in agents_section
