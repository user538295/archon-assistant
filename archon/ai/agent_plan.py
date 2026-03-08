"""Agent plan schema, parsing, validation, and topological sort."""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("archon")


@dataclass(frozen=True, slots=True)
class AgentTask:
    id: str
    task: str
    depends_on: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class AgentPlan:
    scope: str
    summary: str
    agents: list[AgentTask]


def parse_agent_plan(raw: str) -> AgentPlan | None:
    """Parse a raw string into an AgentPlan if it matches the schema.

    Accepts scope "large" or "small". Returns None if the string is not valid.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        return None

    scope = data.get("scope")
    if scope not in ("large", "small"):
        return None

    if not isinstance(data.get("summary"), str):
        return None

    agents_raw = data.get("agents")
    if not isinstance(agents_raw, list) or len(agents_raw) == 0:
        return None

    agents: list[AgentTask] = []
    for entry in agents_raw:
        if not isinstance(entry, dict):
            return None
        agent_id = entry.get("id")
        task = entry.get("task")
        if not isinstance(agent_id, str) or not isinstance(task, str):
            return None
        depends_on = entry.get("depends_on", [])
        if not isinstance(depends_on, list):
            return None
        agents.append(AgentTask(id=agent_id, task=task, depends_on=tuple(depends_on)))

    return AgentPlan(scope=scope, summary=data["summary"], agents=agents)


def validate_dependency_graph(plan: AgentPlan) -> bool:
    """Check that all depends_on IDs exist and there are no cycles."""
    known_ids = {a.id for a in plan.agents}

    # Check for unknown references
    for agent in plan.agents:
        for dep in agent.depends_on:
            if dep not in known_ids:
                logger.warning("Unknown depends_on ID %r in agent %r", dep, agent.id)
                return False

    # Cycle detection via topological sort (Kahn's algorithm)
    in_degree: dict[str, int] = {a.id: 0 for a in plan.agents}
    adjacency: dict[str, list[str]] = {a.id: [] for a in plan.agents}
    for agent in plan.agents:
        for dep in agent.depends_on:
            adjacency[dep].append(agent.id)
            in_degree[agent.id] += 1

    queue: deque[str] = deque(aid for aid, deg in in_degree.items() if deg == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if visited != len(plan.agents):
        logger.warning("Dependency cycle detected in agent plan")
        return False

    return True


def topological_sort(plan: AgentPlan) -> list[list[AgentTask]]:
    """Return execution waves: agents in the same wave can run in parallel.

    Raises ValueError if the graph contains a cycle.
    """
    task_map = {a.id: a for a in plan.agents}
    in_degree: dict[str, int] = {a.id: 0 for a in plan.agents}
    adjacency: dict[str, list[str]] = {a.id: [] for a in plan.agents}

    for agent in plan.agents:
        for dep in agent.depends_on:
            adjacency[dep].append(agent.id)
            in_degree[agent.id] += 1

    waves: list[list[AgentTask]] = []
    queue = [aid for aid, deg in in_degree.items() if deg == 0]

    while queue:
        wave = [task_map[aid] for aid in queue]
        waves.append(wave)
        next_queue: list[str] = []
        for aid in queue:
            for neighbor in adjacency[aid]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_queue.append(neighbor)
        queue = next_queue

    total_sorted = sum(len(w) for w in waves)
    if total_sorted != len(plan.agents):
        raise ValueError("Dependency cycle detected in agent plan")

    return waves
