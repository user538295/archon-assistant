"""Decomposer — the brain that evaluates, answers, and plans."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncGenerator

from archon.ai.classification import Classification, extract_json_object
from archon.ai.claude_session import ClaudeSession
from archon.ai.event_mapper import Event, Response
from archon.ai.prompts import load_prompt

if TYPE_CHECKING:
    from claude_agent_sdk import AgentDefinition

    from archon.ai.agent_plan import AgentTask
    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")


@dataclass
class ReviewResult:
    """Result from Decomposer.review() — updated classification with tool estimate."""

    intent: str
    confidence: float
    estimated_tools: int = 0
    reasoning: str = ""


@dataclass
class TaskOutput:
    """Result from Decomposer.route_task() — either a small or large task."""

    scope: str  # "small" or "large"
    summary: str = ""
    prompt: str | None = None  # present for scope="small"
    agents: list[AgentTask] | None = None  # present for scope="large"


class Decomposer:
    """The brain that evaluates, answers, and plans.

    Wraps a ClaudeSession (user-selected model, full capabilities).
    Maintains conversation context across calls.
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
        prompt = load_prompt("decomposer")
        self._session = ClaudeSession(
            cwd=cwd,
            skills=skills,
            model=model,
            plugins=plugins,
            agents=agents,
            qmd_url=qmd_url,
            background_agent_mcp_url=background_agent_mcp_url,
            spawn_rule=spawn_rule,
            system_prompt=prompt,
        )

    async def start(self) -> None:
        await self._session.start()

    async def stop(self) -> None:
        await self._session.stop()

    # ── Mode 1: Re-evaluate low-confidence classification ──────────

    async def review(self, prompt: str, classification: Classification) -> ReviewResult:
        """Re-evaluate a low-confidence classification.

        Sends a review instruction to the session and parses the JSON response.
        On failure, falls back to the original classification values.
        """
        review_prompt = load_prompt("review")
        instruction = (
            f"[INTERNAL: pipeline orchestration — not a user message]\n\n"
            f"{review_prompt}\n\n"
            f"[Original classification: intent={classification.intent}, "
            f"confidence={classification.confidence}]\n\n"
            f"User message: {prompt}"
        )

        raw_response = ""
        try:
            async for event in self._session.send(instruction):
                if isinstance(event, Response):
                    raw_response = event.content
        except Exception as exc:
            logger.error("Decomposer review failed: %s", exc, exc_info=True)
            return ReviewResult(
                intent=classification.intent,
                confidence=classification.confidence,
            )

        return self._parse_review(raw_response, classification)

    def _parse_review(self, raw: str, fallback: Classification) -> ReviewResult:
        """Parse review JSON response with graceful fallback."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            extracted = extract_json_object(raw)
            if extracted is None:
                logger.warning("Review parse failed: no JSON found")
                return ReviewResult(
                    intent=fallback.intent,
                    confidence=fallback.confidence,
                    reasoning="fallback",
                )
            try:
                data = json.loads(extracted)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Review parse failed: malformed JSON")
                return ReviewResult(
                    intent=fallback.intent,
                    confidence=fallback.confidence,
                    reasoning="fallback",
                )

        if not isinstance(data, dict):
            return ReviewResult(
                intent=fallback.intent,
                confidence=fallback.confidence,
                reasoning="fallback",
            )

        intent = data.get("intent", fallback.intent)
        if intent not in ("chat", "task"):
            intent = fallback.intent

        confidence = data.get("confidence", fallback.confidence)
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence = fallback.confidence

        estimated_tools = data.get("estimated_tools", 0)
        try:
            estimated_tools = int(estimated_tools)
        except (TypeError, ValueError):
            estimated_tools = 0

        reasoning = data.get("reasoning", "")

        return ReviewResult(
            intent=intent,
            confidence=confidence,
            estimated_tools=estimated_tools,
            reasoning=reasoning,
        )

    # ── Mode 2: Answer directly ────────────────────────────────────

    async def answer(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Answer directly — full streaming with thinking, tools, response."""
        async for event in self._session.send(prompt):
            yield event

    # ── Mode 3: Route a task (decide scope) ────────────────────────

    async def route_task(self, prompt: str) -> TaskOutput:
        """Route a task — Decomposer decides scope in one call.

        Returns TaskOutput with scope="small" or scope="large".
        On parse failure, falls back to scope="small" with the original prompt.
        """
        route_prompt = load_prompt("route_task")
        instruction = (
            f"[INTERNAL: pipeline orchestration — not a user message]\n\n"
            f"{route_prompt}\n\nUser request: {prompt}"
        )

        raw_response = ""
        try:
            async for event in self._session.send(instruction):
                if isinstance(event, Response):
                    raw_response = event.content
        except Exception as exc:
            logger.error("Decomposer route_task failed: %s", exc, exc_info=True)
            return TaskOutput(scope="small", summary="Direct handling", prompt=prompt)

        return self._parse_task_output(raw_response, prompt)

    def _parse_task_output(self, raw: str, original_prompt: str) -> TaskOutput:
        """Parse route_task JSON response with graceful fallback."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            extracted = extract_json_object(raw)
            if extracted is None:
                logger.warning("route_task parse failed: no JSON found")
                return TaskOutput(
                    scope="small", summary="Direct handling", prompt=original_prompt
                )
            try:
                data = json.loads(extracted)
            except (json.JSONDecodeError, TypeError):
                logger.warning("route_task parse failed: malformed JSON")
                return TaskOutput(
                    scope="small", summary="Direct handling", prompt=original_prompt
                )

        if not isinstance(data, dict):
            return TaskOutput(
                scope="small", summary="Direct handling", prompt=original_prompt
            )

        scope = data.get("scope")
        summary = data.get("summary", "")

        if scope == "large":
            agents_raw = data.get("agents", [])
            if isinstance(agents_raw, list) and agents_raw:
                from archon.ai.agent_plan import AgentTask

                agents = []
                for entry in agents_raw:
                    if isinstance(entry, dict):
                        agent_id = entry.get("id", "")
                        task = entry.get("task", "")
                        depends_on = entry.get("depends_on", [])
                        if agent_id and task:
                            agents.append(
                                AgentTask(id=agent_id, task=task, depends_on=depends_on)
                            )
                if agents:
                    return TaskOutput(scope="large", summary=summary, agents=agents)

            # Large scope but invalid agents — fall back
            logger.warning("route_task: scope=large but agents invalid")
            return TaskOutput(
                scope="small",
                summary=summary or "Direct handling",
                prompt=original_prompt,
            )

        if scope == "small":
            prompt_text = data.get("prompt", original_prompt)
            return TaskOutput(scope="small", summary=summary, prompt=prompt_text)

        # Unknown scope — fall back
        return TaskOutput(
            scope="small", summary="Direct handling", prompt=original_prompt
        )

    # ── Context management ─────────────────────────────────────────

    def inject_context(self, text: str) -> None:
        self._session.inject_context(text)

    def activate_skill(self, skill: Skill) -> None:
        self._session.activate_skill(skill)

    # ── Duck-typing surface (delegated to inner session) ───────────

    @property
    def is_processing(self) -> bool:
        return self._session.is_processing

    @property
    def processing_seconds(self) -> float | None:
        return self._session.processing_seconds

    @property
    def idle_seconds(self) -> float | None:
        return self._session.idle_seconds

    @property
    def diagnostics(self) -> dict[str, Any]:
        return self._session.diagnostics

    @property
    def usage_stats(self) -> dict[str, Any] | None:
        return self._session.usage_stats

    @property
    def send_count(self) -> int:
        return self._session.send_count

    @property
    def is_alive(self) -> bool:
        return self._session.is_alive

    @property
    def model(self) -> str | None:
        return self._session.model

    def recent_events(self, n: int = 20) -> list[tuple[float, Event]]:
        return self._session.recent_events(n)
