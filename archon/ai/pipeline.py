"""Pipeline — multi-agent routing: Classifier (Haiku) → Decomposer (Sonnet)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

import json

from archon.ai.classification import Classification, parse_classification
from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import ClassificationEvent, Event, Response
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
        """Stop both sessions."""
        await self._classifier.stop()
        await self._decomposer.stop()

    async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Classify the prompt, yield ClassificationEvent, then route to Decomposer."""
        # Step 1: Classify (graceful degradation on any failure)
        classifier_response = ""
        try:
            async for event in self._classifier.send(prompt):
                if isinstance(event, Response):
                    classifier_response = event.content
        except Exception:
            logger.error("Classifier failed — defaulting to task intent", exc_info=True)

        # Step 2: Parse classification
        classification = parse_classification(classifier_response)
        yield ClassificationEvent(
            intent=classification.intent,
            confidence=classification.confidence,
        )

        # Step 3: Route to Decomposer with classification context
        decomposer_prompt = _build_decomposer_prompt(classification, prompt)
        async for event in self._decomposer.send(decomposer_prompt):
            yield event

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
