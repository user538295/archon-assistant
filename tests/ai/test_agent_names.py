"""Tests for the _AGENT_NAMES pool and SubagentStarted/SubagentStopped event fields.

The hook-based name-assignment mechanism (_assign_agent_name, _release_agent_name,
_active_agent_names, _build_hooks) was removed when the SDK's Task tool was
unconditionally disabled.  Only the pool itself and the event dataclass fields
are tested here.
"""
import pytest

from archon.ai.claude_session import _AGENT_NAMES
from archon.ai.event_mapper import SubagentStarted, SubagentStopped


# ──────────────────────────────────────────────────────────────────
# Name pool
# ──────────────────────────────────────────────────────────────────


def test_pool_has_exactly_30_names() -> None:
    assert len(_AGENT_NAMES) == 30


def test_pool_names_are_unique() -> None:
    assert len(set(_AGENT_NAMES)) == 30


def test_pool_names_are_nonempty_strings() -> None:
    assert all(isinstance(n, str) and n for n in _AGENT_NAMES)


# ──────────────────────────────────────────────────────────────────
# SubagentStarted / SubagentStopped — event dataclass fields
# ──────────────────────────────────────────────────────────────────


def test_subagent_started_has_agent_name_field() -> None:
    e = SubagentStarted(agent_id="x", agent_type="t", agent_name="Atlas")
    assert e.agent_name == "Atlas"


def test_subagent_started_agent_name_defaults_to_empty_string() -> None:
    e = SubagentStarted(agent_id="x", agent_type="t")
    assert e.agent_name == ""


def test_subagent_stopped_has_agent_name_field() -> None:
    e = SubagentStopped(agent_id="x", agent_type="t", agent_name="Orion")
    assert e.agent_name == "Orion"


def test_subagent_stopped_agent_name_defaults_to_empty_string() -> None:
    e = SubagentStopped(agent_id="x", agent_type="t")
    assert e.agent_name == ""
