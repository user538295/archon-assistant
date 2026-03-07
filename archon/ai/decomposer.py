"""Decomposer — the brain that evaluates, answers, and plans."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
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

_SUMMARIZER_MODEL = "claude-haiku-4-5-20251001"
_SUMMARIZER_PROMPT = (
    "Summarize the conversation exchanges below in 70-100 words. "
    "Focus on: topics discussed, actions taken or planned, decisions made. "
    "Be factual and brief."
)
_ORCH_RESET_THRESHOLD = 20
_SUMMARY_RESET_THRESHOLD = 30
_SUMMARY_WAIT_TIMEOUT = 3.0


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
        # Separate session for orchestration calls (review, route_task).
        # Prevents JSON-generation instructions from polluting the main
        # conversation context used by answer().
        self._orch_session = ClaudeSession(
            cwd=cwd,
            model=model,
            max_turns=1,
        )
        # Context tracking — Haiku summarization of answer() turns
        self._pending_turns: deque[tuple[str, str]] = deque()
        self._context_summary: str = ""
        self._summary_session = ClaudeSession(
            model=_SUMMARIZER_MODEL,
            system_prompt=_SUMMARIZER_PROMPT,
            tools=[],
            max_turns=1,
        )
        self._summary_task: asyncio.Task[None] | None = None
        self._orch_call_count: int = 0
        self._summary_call_count: int = 0

    async def start(self) -> None:
        await self._session.start()
        await self._orch_session.start()
        await self._summary_session.start()

    async def stop(self) -> None:
        # Cancel in-flight summary task
        if self._summary_task and not self._summary_task.done():
            self._summary_task.cancel()
            try:
                await self._summary_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self._summary_session.stop()
        except Exception:
            logger.error("Summary session stop failed", exc_info=True)
        try:
            await self._orch_session.stop()
        except Exception:
            logger.error("Orchestration session stop failed", exc_info=True)
        await self._session.stop()

    # ── Mode 1: Re-evaluate low-confidence classification ──────────

    async def review(self, prompt: str, classification: Classification) -> ReviewResult:
        """Re-evaluate a low-confidence classification.

        Sends a review instruction to the session and parses the JSON response.
        On failure, falls back to the original classification values.
        """
        await self._await_pending_summary()
        await self._reset_orch_if_needed()

        context = self._build_orch_context()
        context_block = f"\n\n{context}\n\n" if context else "\n\n"

        review_prompt = load_prompt("review")
        instruction = (
            f"[INTERNAL: pipeline orchestration — not a user message]"
            f"{context_block}"
            f"{review_prompt}\n\n"
            f"[Original classification: intent={classification.intent}, "
            f"confidence={classification.confidence}]\n\n"
            f"User message: {prompt}"
        )

        raw_response = ""
        try:
            async for event in self._orch_session.send(instruction):
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
        last_response = ""
        try:
            async for event in self._session.send(prompt):
                if isinstance(event, Response):
                    last_response = event.content
                yield event
        finally:
            if last_response:
                self._pending_turns.append((prompt, last_response))
                self._schedule_summary()

    # ── Mode 3: Route a task (decide scope) ────────────────────────

    async def route_task(self, prompt: str) -> TaskOutput:
        """Route a task — Decomposer decides scope in one call.

        Returns TaskOutput with scope="small" or scope="large".
        On parse failure, falls back to scope="small" with the original prompt.
        """
        await self._await_pending_summary()
        await self._reset_orch_if_needed()

        context = self._build_orch_context()
        context_block = f"\n\n{context}\n\n" if context else "\n\n"

        route_prompt = load_prompt("route_task")
        instruction = (
            f"[INTERNAL: pipeline orchestration — not a user message]"
            f"{context_block}"
            f"{route_prompt}\n\nUser request: {prompt}"
        )

        raw_response = ""
        try:
            async for event in self._orch_session.send(instruction):
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

    # ── Context tracking ───────────────────────────────────────────

    def _schedule_summary(self) -> None:
        """Schedule a Haiku summary task, skipping if one is already running."""
        if self._summary_task and not self._summary_task.done():
            return  # already running; new turns picked up by self-scheduling
        self._summary_task = asyncio.create_task(self._refresh_summary())

    async def _refresh_summary(self) -> None:
        """Summarize pending turns using Haiku (fire-and-forget)."""
        snapshot = list(self._pending_turns)
        if not snapshot:
            return

        # Reset summary session periodically to clear accumulated SDK history.
        self._summary_call_count += 1
        if self._summary_call_count >= _SUMMARY_RESET_THRESHOLD:
            await self._summary_session.stop()
            await self._summary_session.start()
            self._summary_call_count = 0

        parts: list[str] = []
        if self._context_summary:
            parts.append(f"Previous summary:\n{self._context_summary}")
        parts.append("New exchanges:")
        for prompt, response in snapshot:
            parts.append(f"User: {prompt}")
            parts.append(f"Assistant: {response}")

        try:
            summary = ""
            async for event in self._summary_session.send("\n".join(parts)):
                if isinstance(event, Response):
                    summary = event.content
            if summary:
                self._context_summary = summary
                for _ in range(min(len(snapshot), len(self._pending_turns))):
                    self._pending_turns.popleft()
                # Self-schedule if new turns arrived during this run
                if self._pending_turns:
                    self._summary_task = asyncio.create_task(self._refresh_summary())
        except Exception:
            logger.warning(
                "Context summarization failed, keeping turns in buffer",
                exc_info=True,
            )
            # No self-scheduling on failure — prevents infinite retry loops.
            # Next answer() or track_context() call will schedule again.

    async def _await_pending_summary(self) -> None:
        """Wait for in-flight summary to complete (with timeout)."""
        if self._summary_task and not self._summary_task.done():
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._summary_task),
                    timeout=_SUMMARY_WAIT_TIMEOUT,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.debug(
                    "Summary wait timed out after %.1fs", _SUMMARY_WAIT_TIMEOUT
                )

    def _build_orch_context(self) -> str:
        """Build context block from Haiku summary for orchestration prompts."""
        if not self._context_summary:
            return ""
        return (
            "[Main-session context for routing:]\n"
            f"{self._context_summary}\n"
            "[End context]"
        )

    async def _reset_orch_if_needed(self) -> None:
        """Periodically restart the orch session to clear accumulated history."""
        self._orch_call_count += 1
        if self._orch_call_count < _ORCH_RESET_THRESHOLD:
            return
        await self._orch_session.stop()
        await self._orch_session.start()
        self._orch_call_count = 0

    def track_context(self, prompt: str, summary: str) -> None:
        """Record a context entry from an external source (escalation, agent completion)."""
        self._pending_turns.append((prompt, summary))
        self._schedule_summary()

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
        """Return usage stats for the main session with sub-session data attached.

        CONTRACT: ``total_cost_usd`` reflects the main (answer) session cost only.
        It does NOT include orchestration or summary session costs.
        Pipeline.usage_stats adds all sub-session costs on top — never change this
        to aggregate costs here or Pipeline will double-count them.
        """
        main = self._session.usage_stats
        if main is None:
            return None

        def _sub_stats(s: ClaudeSession) -> dict[str, Any]:
            u = s.usage_stats or {}
            return {
                "cost_usd": u.get("total_cost_usd", 0.0),
                "cumulative_cache_creation": u.get("cumulative_cache_creation", 0),
            }

        return {
            **main,
            "sessions": {
                "orchestration": _sub_stats(self._orch_session),
                "summary": _sub_stats(self._summary_session),
            },
        }

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
