"""Pipeline — multi-agent routing: Classifier → Decomposer with clean routing algorithm."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from archon.ai.agent_plan import AgentPlan, AgentTask, topological_sort
from archon.ai.classifier import Classifier
from archon.ai.decomposer import Decomposer
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    PlanEvent,
    ReviewEvent,
    RoutingEvent,
)

if TYPE_CHECKING:
    from claude_agent_sdk import AgentDefinition

    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")

_CONFIDENCE_THRESHOLD = 0.8


class Pipeline:
    """Orchestrates the routing algorithm: Classifier → optional Review → Decomposer.

    Duck-types as ClaudeSession so handler.py and SessionManager work unchanged.
    """

    def __init__(
        self,
        cwd: str | None = None,
        skills: list[Skill] | None = None,
        model: str | None = None,
        plugins: list[dict[str, Any]] | None = None,
        agents: dict[str, AgentDefinition] | None = None,
        qmd_url: str | None = None,
        background_agent_mcp_url: str | None = None,
        spawn_rule: str | None = None,
    ) -> None:
        self._classifier = Classifier(cwd=cwd, qmd_url=qmd_url)
        self._decomposer = Decomposer(
            cwd=cwd,
            skills=skills,
            model=model,
            plugins=plugins,
            agents=agents,
            qmd_url=qmd_url,
            background_agent_mcp_url=background_agent_mcp_url,
            spawn_rule=spawn_rule,
        )

    async def start(self) -> None:
        """Start both Classifier and Decomposer."""
        await self._classifier.start()
        await self._decomposer.start()

    async def stop(self) -> None:
        """Stop both. Decomposer is always stopped even if Classifier fails."""
        try:
            await self._classifier.stop()
        except Exception:
            logger.error("Classifier stop failed", exc_info=True)
        await self._decomposer.stop()

    async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Route a user message through the classification → routing algorithm."""

        # ── Step 1: Classify ──────────────────────────────────────
        result = await self._classifier.classify(prompt)

        if result.error:
            yield ErrorEvent(message=result.error, source="pipeline")

        yield ClassificationEvent(
            intent=result.classification.intent,
            confidence=result.classification.confidence,
            raw_response=result.raw_response,
            model=self._classifier.model,
            duration_s=result.duration_s,
            parse_error=result.parse_error,
        )

        intent: str = result.classification.intent
        confidence: float = result.classification.confidence
        estimated_tools = 0

        # ── Step 2: Re-evaluate if low confidence ─────────────────
        if confidence < _CONFIDENCE_THRESHOLD:
            review = await self._decomposer.review(prompt, result.classification)
            yield ReviewEvent(
                original_intent=intent,
                original_confidence=confidence,
                updated_intent=review.intent,
                updated_confidence=review.confidence,
                estimated_tools=review.estimated_tools,
            )
            intent = review.intent
            confidence = review.confidence
            estimated_tools = review.estimated_tools

        # ── Step 3: Route ─────────────────────────────────────────

        if intent == "chat":
            async for event in self._decomposer.answer(prompt):
                yield event
            yield self._routing_event("chat_direct")
            return

        if estimated_tools > 1:
            # Large task — get multi-agent plan
            task_output = await self._decomposer.route_task(prompt)
            for event in self._yield_plan(task_output, prompt):
                yield event
            return

        # Single tool or simple task — decomposer handles directly
        async for event in self._decomposer.answer(prompt):
            yield event
        yield self._routing_event("task_direct")
        return

    def _yield_plan(self, task_output: Any, prompt: str) -> list[Event]:
        """Convert TaskOutput into plan events."""
        events: list[Event] = []

        if task_output.scope == "large" and task_output.agents:
            plan = AgentPlan(
                scope="large",
                summary=task_output.summary,
                agents=task_output.agents,
            )
            events.append(PlanEvent(plan=plan, summary=plan.summary))
            agent_count = len(plan.agents)
            try:
                wave_count = len(topological_sort(plan))
            except ValueError:
                wave_count = 0
            events.append(self._routing_event("agent_plan", agent_count, wave_count))
        else:
            # Small task — wrap as single-agent plan
            agent_prompt = task_output.prompt or prompt
            plan = AgentPlan(
                scope="small",
                summary=task_output.summary or "Single agent task",
                agents=[AgentTask(id="a1", task=agent_prompt)],
            )
            events.append(PlanEvent(plan=plan, summary=plan.summary))
            events.append(self._routing_event("agent_spawn", 1, 1))

        return events

    def _routing_event(
        self, routing: str, agent_count: int = 0, wave_count: int = 0
    ) -> RoutingEvent:
        return RoutingEvent(
            routing=routing,
            model=self.model or "",
            agent_count=agent_count,
            wave_count=wave_count,
        )

    # ── Delegation to Decomposer (duck-typing surface) ──────────

    @property
    def is_processing(self) -> bool:
        return self._decomposer.is_processing

    @property
    def processing_seconds(self) -> float | None:
        return self._decomposer.processing_seconds

    @property
    def idle_seconds(self) -> float | None:
        return self._decomposer.idle_seconds

    @property
    def diagnostics(self) -> dict[str, Any]:
        return self._decomposer.diagnostics

    @property
    def usage_stats(self) -> dict[str, Any] | None:
        return self._decomposer.usage_stats

    @property
    def send_count(self) -> int:
        return self._decomposer.send_count

    @property
    def is_alive(self) -> bool:
        return self._decomposer.is_alive

    @property
    def model(self) -> str | None:
        return self._decomposer.model

    def recent_events(self, n: int = 20) -> list[tuple[float, Event]]:
        return self._decomposer.recent_events(n)

    def activate_skill(self, skill: Skill) -> None:
        self._decomposer.activate_skill(skill)

    def inject_context(self, text: str) -> None:
        self._decomposer.inject_context(text)
