"""Pipeline — multi-agent routing: Classifier (Haiku) → Decomposer (Sonnet)."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, AsyncGenerator

from archon.ai.agent_plan import parse_agent_plan, topological_sort
from archon.ai.classification import Classification, parse_classification
from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import ClassificationEvent, ErrorEvent, Event, PlanEvent, Response, RoutingEvent
from archon.ai.prompts import load_prompt

if TYPE_CHECKING:
    from claude_agent_sdk import AgentDefinition
    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")

_CLASSIFIER_MODEL = "claude-haiku-4-5-20251001"


def _build_decomposer_prompt(classification: Classification, user_prompt: str) -> str:
    """Prepend classification JSON to the user prompt for the Decomposer."""
    classification_json = json.dumps(
        {"intent": classification.intent, "confidence": classification.confidence},
    )
    return f"[Classification: {classification_json}]\n\n{user_prompt}"


class Pipeline:
    """Routes user messages through Classifier → Decomposer.

    Duck-types as ClaudeSession so handler.py and SessionManager work unchanged.
    """

    def __init__(
        self,
        cwd: str | None = None,
        skills: list[Skill] | None = None,
        model: str | None = None,
        plugins: list[dict] | None = None,
        agents: dict[str, AgentDefinition] | None = None,
        qmd_url: str | None = None,
        background_agent_mcp_url: str | None = None,
        spawn_rule: str | None = None,
    ) -> None:
        classifier_prompt = load_prompt("classifier")
        decomposer_prompt = load_prompt("decomposer")

        self._classifier = ClaudeSession(
            cwd=cwd,
            model=_CLASSIFIER_MODEL,
            system_prompt=classifier_prompt,
            qmd_url=qmd_url,
            tools=[],
            max_turns=1,
        )
        self._decomposer = ClaudeSession(
            cwd=cwd,
            skills=skills,
            model=model,
            plugins=plugins,
            agents=agents,
            qmd_url=qmd_url,
            background_agent_mcp_url=background_agent_mcp_url,
            spawn_rule=spawn_rule,
            system_prompt=decomposer_prompt,
        )

    async def start(self) -> None:
        """Start both Classifier and Decomposer sessions."""
        await self._classifier.start()
        await self._decomposer.start()

    async def stop(self) -> None:
        """Stop both sessions.  Decomposer is always stopped even if Classifier fails."""
        try:
            await self._classifier.stop()
        except Exception:
            logger.error("Classifier stop failed", exc_info=True)
        await self._decomposer.stop()

    async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Classify the prompt, yield ClassificationEvent, then route to Decomposer."""
        # Step 1: Classify (graceful degradation on any failure)
        classifier_response = ""
        classifier_error = ""
        t0 = time.monotonic()
        try:
            async for event in self._classifier.send(prompt):
                if isinstance(event, Response):
                    classifier_response = event.content
        except Exception as exc:
            classifier_error = f"Classifier failed: {exc}"
            logger.error("Classifier failed — defaulting to task intent", exc_info=True)
        duration_s = round(time.monotonic() - t0, 1)

        if classifier_error:
            yield ErrorEvent(message=classifier_error, source="pipeline")

        # Step 2: Parse classification
        result = parse_classification(classifier_response)
        classification = result.classification
        parse_error = result.error or ""
        logger.info(
            "Classification: intent=%s confidence=%.2f duration=%.1fs",
            classification.intent, classification.confidence, duration_s,
        )
        yield ClassificationEvent(
            intent=classification.intent,
            confidence=classification.confidence,
            raw_response=classifier_response,
            model=_CLASSIFIER_MODEL,
            duration_s=duration_s,
            parse_error=parse_error,
        )

        # Step 3: Route to Decomposer with classification context
        decomposer_prompt = _build_decomposer_prompt(classification, prompt)
        plan_detected = False
        plan = None
        async for event in self._decomposer.send(decomposer_prompt):
            # Intercept the final Response to check for an agent plan
            if isinstance(event, Response):
                plan = parse_agent_plan(event.content)
                if plan is not None:
                    yield PlanEvent(plan=plan, summary=plan.summary)
                    plan_detected = True
                    continue
            yield event

        # Step 4: Yield routing decision for history logging
        agent_count = 0
        wave_count = 0
        if plan_detected and plan is not None:
            agent_count = len(plan.agents)
            try:
                wave_count = len(topological_sort(plan))
            except ValueError:
                wave_count = 0
        yield RoutingEvent(
            routing="agent_plan" if plan_detected else "direct",
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
