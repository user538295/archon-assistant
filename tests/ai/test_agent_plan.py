"""Tests for agent plan schema, parsing, validation, and topological sort."""

from __future__ import annotations

import pytest

from archon.ai.agent_plan import (
    AgentPlan,
    AgentTask,
    parse_agent_plan,
    topological_sort,
    validate_dependency_graph,
)


# ── Parse: happy path ──────────────────────────────────────────────


class TestParseAgentPlan:
    def test_valid_plan_all_fields(self) -> None:
        raw = '{"scope":"large","summary":"Do stuff","agents":[{"id":"a1","task":"Do A","depends_on":["a0"]},{"id":"a0","task":"Do B"}]}'
        plan = parse_agent_plan(raw)
        assert plan is not None
        assert plan.scope == "large"
        assert plan.summary == "Do stuff"
        assert len(plan.agents) == 2
        assert plan.agents[0] == AgentTask(id="a1", task="Do A", depends_on=("a0",))
        assert plan.agents[1] == AgentTask(id="a0", task="Do B", depends_on=())

    def test_no_depends_on_defaults_to_empty(self) -> None:
        raw = '{"scope":"large","summary":"All parallel","agents":[{"id":"a1","task":"X"},{"id":"a2","task":"Y"}]}'
        plan = parse_agent_plan(raw)
        assert plan is not None
        for agent in plan.agents:
            assert agent.depends_on == ()

    def test_linear_chain(self) -> None:
        raw = '{"scope":"large","summary":"Chain","agents":[{"id":"a1","task":"A"},{"id":"a2","task":"B","depends_on":["a1"]},{"id":"a3","task":"C","depends_on":["a2"]}]}'
        plan = parse_agent_plan(raw)
        assert plan is not None
        assert plan.agents[2].depends_on == ("a2",)

    def test_diamond_dependency(self) -> None:
        raw = '{"scope":"large","summary":"Diamond","agents":[{"id":"a1","task":"A"},{"id":"a2","task":"B"},{"id":"a3","task":"C","depends_on":["a1","a2"]}]}'
        plan = parse_agent_plan(raw)
        assert plan is not None
        assert plan.agents[2].depends_on == ("a1", "a2")

    # ── Parse: returns None ──────────────────────────────────────

    def test_not_json(self) -> None:
        assert parse_agent_plan("not json at all") is None

    def test_missing_scope(self) -> None:
        raw = '{"summary":"X","agents":[{"id":"a1","task":"T"}]}'
        assert parse_agent_plan(raw) is None

    def test_scope_small_valid(self) -> None:
        raw = '{"scope":"small","summary":"X","agents":[{"id":"a1","task":"T"}]}'
        plan = parse_agent_plan(raw)
        assert plan is not None
        assert plan.scope == "small"
        assert plan.summary == "X"
        assert len(plan.agents) == 1

    def test_scope_small_requires_agents(self) -> None:
        raw = '{"scope":"small","summary":"X","agents":[]}'
        assert parse_agent_plan(raw) is None

    def test_scope_small_requires_summary(self) -> None:
        raw = '{"scope":"small","agents":[{"id":"a1","task":"T"}]}'
        assert parse_agent_plan(raw) is None

    def test_scope_small_requires_valid_agent_fields(self) -> None:
        raw = '{"scope":"small","summary":"X","agents":[{"id":"a1"}]}'
        assert parse_agent_plan(raw) is None

    def test_scope_unknown_returns_none(self) -> None:
        raw = '{"scope":"unknown","summary":"X","agents":[{"id":"a1","task":"T"}]}'
        assert parse_agent_plan(raw) is None

    def test_missing_agents(self) -> None:
        raw = '{"scope":"large","summary":"X"}'
        assert parse_agent_plan(raw) is None

    def test_empty_agents(self) -> None:
        raw = '{"scope":"large","summary":"X","agents":[]}'
        assert parse_agent_plan(raw) is None

    def test_missing_agent_id(self) -> None:
        raw = '{"scope":"large","summary":"X","agents":[{"task":"T"}]}'
        assert parse_agent_plan(raw) is None

    def test_missing_agent_task(self) -> None:
        raw = '{"scope":"large","summary":"X","agents":[{"id":"a1"}]}'
        assert parse_agent_plan(raw) is None

    def test_agents_not_list(self) -> None:
        raw = '{"scope":"large","summary":"X","agents":"not a list"}'
        assert parse_agent_plan(raw) is None

    def test_root_not_dict(self) -> None:
        assert parse_agent_plan("[1,2,3]") is None

    def test_missing_summary(self) -> None:
        raw = '{"scope":"large","agents":[{"id":"a1","task":"T"}]}'
        assert parse_agent_plan(raw) is None

    def test_markdown_wrapped_json(self) -> None:
        raw = '```json\n{"scope":"large","summary":"X","agents":[{"id":"a1","task":"T"}]}\n```'
        assert parse_agent_plan(raw) is None  # strict: raw must be pure JSON


# ── Validate dependency graph ───────────────────────────────────


class TestValidateDependencyGraph:
    def test_valid_linear_chain(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Chain",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
                AgentTask(id="a3", task="C", depends_on=("a2",)),
            ],
        )
        assert validate_dependency_graph(plan) is True

    def test_valid_all_parallel(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Parallel",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B"),
            ],
        )
        assert validate_dependency_graph(plan) is True

    def test_unknown_depends_on_id(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Bad ref",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B", depends_on=("a99",)),
            ],
        )
        assert validate_dependency_graph(plan) is False

    def test_cycle_detection_simple(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Cycle",
            agents=[
                AgentTask(id="a1", task="A", depends_on=("a2",)),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
            ],
        )
        assert validate_dependency_graph(plan) is False

    def test_cycle_detection_self_loop(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Self",
            agents=[
                AgentTask(id="a1", task="A", depends_on=("a1",)),
            ],
        )
        assert validate_dependency_graph(plan) is False

    def test_diamond_valid(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Diamond",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B"),
                AgentTask(id="a3", task="C", depends_on=("a1", "a2",)),
            ],
        )
        assert validate_dependency_graph(plan) is True


# ── Topological sort ────────────────────────────────────────────


class TestTopologicalSort:
    def test_all_parallel_single_wave(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Parallel",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B"),
                AgentTask(id="a3", task="C"),
            ],
        )
        waves = topological_sort(plan)
        assert len(waves) == 1
        ids = {t.id for t in waves[0]}
        assert ids == {"a1", "a2", "a3"}

    def test_linear_chain_n_waves(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Chain",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
                AgentTask(id="a3", task="C", depends_on=("a2",)),
            ],
        )
        waves = topological_sort(plan)
        assert len(waves) == 3
        assert waves[0][0].id == "a1"
        assert waves[1][0].id == "a2"
        assert waves[2][0].id == "a3"

    def test_mixed_parallel_sequential(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Mixed",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B"),
                AgentTask(id="a3", task="C", depends_on=("a1", "a2",)),
                AgentTask(id="a4", task="D", depends_on=("a3",)),
            ],
        )
        waves = topological_sort(plan)
        assert len(waves) == 3
        wave0_ids = {t.id for t in waves[0]}
        assert wave0_ids == {"a1", "a2"}
        assert waves[1][0].id == "a3"
        assert waves[2][0].id == "a4"

    def test_diamond(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Diamond",
            agents=[
                AgentTask(id="a1", task="A"),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
                AgentTask(id="a3", task="C", depends_on=("a1",)),
                AgentTask(id="a4", task="D", depends_on=("a2", "a3",)),
            ],
        )
        waves = topological_sort(plan)
        assert len(waves) == 3
        assert waves[0][0].id == "a1"
        wave1_ids = {t.id for t in waves[1]}
        assert wave1_ids == {"a2", "a3"}
        assert waves[2][0].id == "a4"

    def test_cycle_raises_value_error(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Cycle",
            agents=[
                AgentTask(id="a1", task="A", depends_on=("a2",)),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
            ],
        )
        with pytest.raises(ValueError, match="cycle"):
            topological_sort(plan)
