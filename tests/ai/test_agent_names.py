"""FR.001 — Named Agents: TDD test suite (written before implementation).

Tests are written in red phase — they import symbols that don't exist yet
(_AGENT_NAMES, _assign_agent_name, _release_agent_name, agent_name field).
Run this file to confirm all fail, then implement to make them green.
"""
import asyncio
import pytest

from archon.ai.claude_session import ClaudeSession, _AGENT_NAMES
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
# _assign_agent_name
# ──────────────────────────────────────────────────────────────────


def test_assign_returns_name_from_pool() -> None:
    session = ClaudeSession()
    name = session._assign_agent_name("agent-001")
    assert name in _AGENT_NAMES


def test_assign_stores_mapping() -> None:
    session = ClaudeSession()
    name = session._assign_agent_name("agent-001")
    assert session._active_agent_names["agent-001"] == name


def test_assign_two_concurrent_agents_get_different_names() -> None:
    session = ClaudeSession()
    n1 = session._assign_agent_name("a1")
    n2 = session._assign_agent_name("a2")
    assert n1 != n2


def test_assign_same_agent_id_is_idempotent() -> None:
    session = ClaudeSession()
    n1 = session._assign_agent_name("a1")
    n2 = session._assign_agent_name("a1")
    assert n1 == n2


def test_assign_exhausted_pool_returns_nonempty_fallback() -> None:
    session = ClaudeSession()
    for i in range(30):
        session._assign_agent_name(f"agent-{i}")
    # 31st agent — pool exhausted, must not raise
    name = session._assign_agent_name("agent-30")
    assert isinstance(name, str) and name


# ──────────────────────────────────────────────────────────────────
# _release_agent_name
# ──────────────────────────────────────────────────────────────────


def test_release_removes_mapping() -> None:
    session = ClaudeSession()
    session._assign_agent_name("a1")
    session._release_agent_name("a1")
    assert "a1" not in session._active_agent_names


def test_release_returns_the_assigned_name() -> None:
    session = ClaudeSession()
    assigned = session._assign_agent_name("a1")
    released = session._release_agent_name("a1")
    assert released == assigned


def test_release_nonexistent_agent_returns_none() -> None:
    session = ClaudeSession()
    result = session._release_agent_name("ghost")
    assert result is None


def test_name_reused_after_release() -> None:
    """After releasing a name it becomes available again."""
    session = ClaudeSession()
    # Manually occupy 29 slots (all names except index 0) so only _AGENT_NAMES[0]
    # is available — this avoids randomness making the test non-deterministic.
    for i, name in enumerate(_AGENT_NAMES[1:]):
        session._active_agent_names[f"b{i}"] = name
    # Only _AGENT_NAMES[0] is free → assign it
    n1 = session._assign_agent_name("target")
    assert n1 == _AGENT_NAMES[0]
    # Release it — 29 slots still occupied, _AGENT_NAMES[0] is now available again
    session._release_agent_name("target")
    # Next assignment must pick _AGENT_NAMES[0] (only option)
    n2 = session._assign_agent_name("new-target")
    assert n2 == _AGENT_NAMES[0]


# ──────────────────────────────────────────────────────────────────
# SubagentStarted / SubagentStopped — agent_name field
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


# ──────────────────────────────────────────────────────────────────
# Hook integration — names assigned / released via _build_hooks
# ──────────────────────────────────────────────────────────────────


async def test_hook_start_puts_subagent_started_with_name() -> None:
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    event = session._hook_queue.get_nowait()
    assert isinstance(event, SubagentStarted)
    assert event.agent_name in _AGENT_NAMES
    assert event.agent_id == "a1"


async def test_hook_stop_puts_subagent_stopped_with_same_name() -> None:
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    stop_fn = hooks["SubagentStop"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    started = session._hook_queue.get_nowait()
    await stop_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    stopped = session._hook_queue.get_nowait()
    assert stopped.agent_name == started.agent_name


async def test_hook_stop_releases_name_from_active_registry() -> None:
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    stop_fn = hooks["SubagentStop"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    session._hook_queue.get_nowait()  # drain
    await stop_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    session._hook_queue.get_nowait()  # drain
    assert "a1" not in session._active_agent_names


async def test_two_concurrent_hooks_assign_different_names() -> None:
    session = ClaudeSession()
    hooks = session._build_hooks()
    start_fn = hooks["SubagentStart"][0].hooks[0]
    await start_fn({"agent_id": "a1", "agent_type": "bash"}, None, None)
    await start_fn({"agent_id": "a2", "agent_type": "bash"}, None, None)
    e1 = session._hook_queue.get_nowait()
    e2 = session._hook_queue.get_nowait()
    assert e1.agent_name != e2.agent_name
