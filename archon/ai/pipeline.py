"""Pipeline — multi-agent routing: Classifier → Decomposer with clean routing algorithm."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

from archon.ai.agent_plan import AgentPlan, topological_sort
from archon.ai.classifier import Classifier
from archon.ai.decomposer import Decomposer, TaskOutput
from archon.ai.event_mapper import (
    ClassificationEvent,
    ErrorEvent,
    Event,
    FallbackNoticeEvent,
    PlanEvent,
    PromotionEvent,
    RoutingEvent,
    ToolResult,
    ToolStarted,
)

if TYPE_CHECKING:
    from claude_agent_sdk import AgentDefinition

    from archon.ai.context_provider import ContextProvider
    from archon.ai.reminder import ContextReminder
    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")

_CONFIDENCE_THRESHOLD = 0.8
_TOOL_PROMOTION_THRESHOLD = 10
_PROMOTION_RESULT_MAX_CHARS = 500
_TASK_DIRECT_TIMEOUT_S: float = 300.0
_ACLOSE_TIMEOUT_S: float = 10.0


def _build_promotion_prompt(
    tool_pairs: list[tuple[ToolStarted, ToolResult | None]], original_prompt: str
) -> str:
    """Build an enriched prompt for the promoted background agent."""
    lines = [
        "[CONTINUATION: This task was started inline but required more investigation.",
        "The following partial results were already collected:]",
        "",
    ]
    for i, (started, result) in enumerate(tool_pairs, 1):
        input_part = f"({started.input})" if started.input else ""
        lines.append(f"Tool {i}: {started.name}{input_part}")
        if result is not None:
            content = result.content
            if len(content) > _PROMOTION_RESULT_MAX_CHARS:
                content = content[:_PROMOTION_RESULT_MAX_CHARS] + "..."
            lines.append(f"Result: {content}")
        else:
            lines.append("Result: [not yet available — task was promoted before this tool completed]")
        lines.append("")
    lines.append("[END PARTIAL RESULTS]")
    lines.append("")
    lines.append(f"Original request: {original_prompt}")
    return "\n".join(lines)


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
        reminder: "ContextReminder | None" = None,
        tool_promotion_threshold: int = _TOOL_PROMOTION_THRESHOLD,
        context_provider: "ContextProvider | None" = None,
        orch_mcp_url: str | None = None,
        orch_mcp_headers: dict[str, str] | None = None,
    ) -> None:
        self._tool_promotion_threshold = tool_promotion_threshold
        self._lock = asyncio.Lock()
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
            reminder=reminder,
            context_provider=context_provider,
            orch_mcp_url=orch_mcp_url,
            orch_mcp_headers=orch_mcp_headers,
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
        """Route a user message through the classification → routing algorithm.

        Serializes concurrent calls via an asyncio.Lock so two messages from
        the same user never run through the pipeline simultaneously.  The lock
        is held for the full duration of the generator (including streaming);
        it is released automatically on exhaustion or aclose().
        """
        async with self._lock:
            # ── Step 1: Classify ──────────────────────────────────────
            result = await self._classifier.classify(prompt)

            if result.error:
                yield ErrorEvent(message=result.error, source="pipeline")
                return

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

            # ── Step 2: Route ─────────────────────────────────────────
            if intent == "chat" and confidence >= _CONFIDENCE_THRESHOLD:
                yield self._routing_event("chat")
                async for event in self._task_direct_monitored(prompt):
                    yield event
                return

            # All other cases (task, or chat below confidence threshold) → orch decides
            task_output = await self._decomposer.route_task(prompt)
            logger.info(
                "route_task scope=%s fallback=%s for prompt: %.80s",
                task_output.scope, task_output.is_fallback, prompt,
            )

            # Defensive: is_fallback should never occur with scope="large" (parse_task_output
            # always returns scope="small" on failure), but guard against future regressions.
            if task_output.is_fallback and task_output.scope == "large":
                logger.warning(
                    "route_task returned is_fallback=True with scope='large' — demoting to inline"
                )
                task_output = TaskOutput(
                    scope="small",
                    prompt=task_output.prompt,
                    is_fallback=True,
                    fallback_reason=task_output.fallback_reason,
                )

            # Only emit FallbackNoticeEvent when there is a user-visible reason.
            # Timeout-based fallbacks (fallback_reason="") are silent internal routing
            # decisions — the system continues working fine, so don't alarm the user.
            if task_output.is_fallback and task_output.fallback_reason:
                yield FallbackNoticeEvent(reason=task_output.fallback_reason)

            if task_output.scope == "large":
                # Flush pending context before spawning background agents — prevents
                # stale injections from appearing in a session that won't process them.
                self._decomposer.flush_pending_context()
                for event in self._yield_plan(task_output):
                    yield event
            else:
                # trivial or small → inline with tool promotion as safety net
                yield self._routing_event("task_direct")
                resolved = task_output.prompt or prompt
                if resolved != prompt:
                    resolved = (
                        f"[Original user request]: {prompt}\n"
                        f"[Resolved context]: {resolved}"
                    )
                async for event in self._task_direct_monitored(resolved):
                    yield event

    async def _task_direct_monitored(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Stream decomposer events, promoting to background agent if tool count exceeds threshold.

        A wall-clock timeout of _TASK_DIRECT_TIMEOUT_S guards against SDK hangs
        (Bug 03: 19-minute silence for a trivial response).
        """
        tool_count = 0
        tool_pairs: list[tuple[ToolStarted, ToolResult | None]] = []
        current_started: ToolStarted | None = None

        gen = self._decomposer.answer(prompt)
        try:
            try:
                async with asyncio.timeout(_TASK_DIRECT_TIMEOUT_S):
                    async for event in gen:
                        if isinstance(event, ToolStarted):
                            # Save any previous started without a result
                            if current_started is not None:
                                tool_pairs.append((current_started, None))
                            current_started = event
                            # tool_count tracks ToolStarted events (not completions); parallel tool calls
                            # increment the counter before results arrive, which may cause earlier-than-expected
                            # promotion in sessions with high parallelism. This is a known design tradeoff.
                            tool_count += 1
                            yield event  # always let the user see the tool start

                            if self._tool_promotion_threshold > 0 and tool_count >= self._tool_promotion_threshold:
                                # Promote: build enriched prompt and yield PromotionEvent
                                tool_pairs.append((current_started, None))
                                agent_prompt = _build_promotion_prompt(tool_pairs, prompt)
                                yield PromotionEvent(
                                    agent_prompt=agent_prompt,
                                    original_prompt=prompt,
                                    tool_count=tool_count,
                                )
                                self._decomposer.track_context(
                                    prompt,
                                    f"[Task escalated to background agent after {tool_count} tool calls]",
                                )
                                self._decomposer.flush_pending_context()
                                await gen.aclose()
                                return
                        elif isinstance(event, ToolResult) and current_started is not None:
                            tool_pairs.append((current_started, event))
                            current_started = None
                            yield event
                        else:
                            yield event
            except TimeoutError:
                logger.error(
                    "_task_direct_monitored timed out after %.0fs for prompt: %.100s",
                    _TASK_DIRECT_TIMEOUT_S,
                    prompt,
                )
                yield ErrorEvent(
                    message=f"Response timed out after {_TASK_DIRECT_TIMEOUT_S:.0f}s. The task may still be running.",
                    source="pipeline",
                )
        finally:
            try:
                await asyncio.wait_for(gen.aclose(), timeout=_ACLOSE_TIMEOUT_S)
            except Exception:
                logger.warning(
                    "_task_direct_monitored: gen.aclose() timed out or failed for prompt: %.100s",
                    prompt,
                    exc_info=True,
                )

    def _yield_plan(self, task_output: Any) -> list[Event]:
        """Convert a large-scope TaskOutput into plan events. Only called when scope == 'large'."""
        plan = AgentPlan(
            scope="large",
            summary=task_output.summary,
            agents=task_output.agents,
        )
        events: list[Event] = [PlanEvent(plan=plan, summary=plan.summary)]
        agent_count = len(plan.agents)
        try:
            wave_count = len(topological_sort(plan))
        except ValueError:
            wave_count = 0
        events.append(self._routing_event("agent_plan", agent_count, wave_count))
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
        return self._lock.locked() or self._decomposer.is_processing

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
        """Return aggregated usage stats across all sessions.

        ``total_cost_usd`` = main session (from Decomposer) + classifier + orchestration + summary.
        This relies on Decomposer.usage_stats["total_cost_usd"] being main-session cost only
        (see Decomposer.usage_stats docstring — do not change that contract).
        ``cumulative_cache_creation`` is intentionally kept as main-session-only for the
        context window progress bar; sub-session cache values live in the ``sessions`` dict.
        """
        stats = self._decomposer.usage_stats
        if stats is None:
            return None

        clf = self._classifier.usage_stats or {}
        clf_cost = clf.get("total_cost_usd", 0.0)
        clf_cache = clf.get("cumulative_cache_creation", 0)

        existing_sessions: dict[str, Any] = stats.get("sessions") or {}
        sub_total = sum(s.get("cost_usd", 0.0) for s in existing_sessions.values()) + clf_cost

        return {
            **stats,
            "total_cost_usd": stats.get("total_cost_usd", 0.0) + sub_total,
            "sessions": {
                **existing_sessions,
                "classifier": {"cost_usd": clf_cost, "cumulative_cache_creation": clf_cache},
            },
        }

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

    def flush_pending_context(self) -> None:
        self._decomposer.flush_pending_context()

    def track_context(self, prompt: str, summary: str) -> None:
        self._decomposer.track_context(prompt, summary)

    @property
    def reminder(self) -> "ContextReminder | None":
        return self._decomposer.reminder

    @property
    def context_summary(self) -> str:
        """Return the current conversation summary from the Decomposer."""
        return self._decomposer.context_summary
