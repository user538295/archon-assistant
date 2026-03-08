"""PlanExecutor — resolves dependency graph and spawns workers via BackgroundAgentManager."""

from __future__ import annotations

import asyncio
import html
import logging
from typing import TYPE_CHECKING

from archon.ai.agent_plan import AgentPlan, AgentTask, topological_sort
from archon.ai.background_agent_manager import AgentRun
from archon.ai.event_mapper import WaveCompleted, WaveStarted

if TYPE_CHECKING:
    from aiogram import Bot

    from archon.ai.background_agent_manager import BackgroundAgentManager
    from archon.ai.history_manager import HistoryManager

logger = logging.getLogger("archon")

# Maximum seconds to wait for all agents in a single wave to complete.
# Guards against agents whose _run_agent finally-block panics without setting done.
MAX_WAVE_TIMEOUT: float = 3600.0


class PlanExecutor:
    """Execute an agent plan by resolving dependencies and spawning workers.

    Runs as an asyncio task — Pipeline.send() returns immediately after
    yielding PlanEvent. This class manages the full lifecycle:
    1. Send plan summary notification
    2. Compute execution waves via topological sort
    3. For each wave: spawn agents, wait for completion
    4. Pass upstream log file paths to dependent agents
    5. Send final summary notification
    """

    def __init__(
        self,
        bam: BackgroundAgentManager,
        bot: Bot,
        user_id: int,
        cwd: str,
        history_manager: HistoryManager | None = None,
        context_summary: str = "",
    ) -> None:
        self._bam = bam
        self._bot = bot
        self._user_id = user_id
        self._cwd = cwd
        self._history = history_manager
        self._context_summary = context_summary

    async def execute(self, plan: AgentPlan) -> None:
        """Main entry point — run as an async task."""
        try:
            await self._execute_plan(plan)
        except Exception:
            logger.exception("PlanExecutor crashed")
            await self._notify("❌ Plan execution failed unexpectedly.")

    async def _execute_plan(self, plan: AgentPlan) -> None:
        n = len(plan.agents)
        await self._notify(f"📋 Executing plan: {html.escape(plan.summary)} ({n} agent{'s' if n != 1 else ''})")

        try:
            waves = topological_sort(plan)
        except ValueError:
            await self._notify("❌ Plan has a dependency cycle. Aborting.")
            return

        # Track agent runs keyed by agent task id (dependency graph key)
        runs: dict[str, AgentRun] = {}
        failed_ids: set[str] = set()
        skipped_ids: set[str] = set()

        for wave_idx, wave in enumerate(waves, start=1):
            wave_runs: list[tuple[AgentTask, AgentRun]] = []

            # Determine which agents in this wave will be attempted (not skipped)
            runnable = [t for t in wave if not self._should_skip(t, failed_ids, skipped_ids)]
            skipped_now = [t for t in wave if self._should_skip(t, failed_ids, skipped_ids)]
            for agent_task in skipped_now:
                skipped_ids.add(agent_task.id)
                logger.info("Skipping agent %s: dependency failed", agent_task.id)

            # Record WaveStarted before spawning so timeline is correct (Issue C fix)
            # Use task IDs as placeholder names; BAM replaces with pool names after spawn
            if runnable:
                await self._record_event(WaveStarted(wave_number=wave_idx, agent_names=[t.id for t in runnable]))

            for agent_task in runnable:
                # Build task prompt with upstream context
                task_prompt = self._build_task_prompt(agent_task, runs)

                try:
                    run = await self._bam.spawn(
                        user_id=self._user_id,
                        task=task_prompt,
                        context=self._context_summary,
                        user_request=plan.summary,
                    )
                except RuntimeError as exc:
                    logger.warning("spawn() failed for agent %s: %s", agent_task.id, exc)
                    failed_ids.add(agent_task.id)
                    continue

                runs[agent_task.id] = run
                wave_runs.append((agent_task, run))

            wave_pool_names = [run.name for _, run in wave_runs]


            # Fix A: wrap gather in wait_for so a hung agent doesn't wait forever.
            # On timeout, notify the user and abort the plan.
            if wave_runs:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*[run.done.wait() for _, run in wave_runs]),
                        timeout=MAX_WAVE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Wave %d timed out after %.0fs", wave_idx, MAX_WAVE_TIMEOUT)
                    await self._notify(
                        f"❌ Wave {wave_idx} timed out after {int(MAX_WAVE_TIMEOUT)}s. Aborting plan."
                    )
                    return

            # Check for failures in this wave
            wave_failed_names: list[str] = []
            for agent_task, run in wave_runs:
                if run.status == "failed":
                    failed_ids.add(agent_task.id)
                    wave_failed_names.append(run.name)  # pool name

            if wave_pool_names:
                await self._record_event(WaveCompleted(
                    wave_number=wave_idx,
                    agent_names=wave_pool_names,
                    failed_names=wave_failed_names,
                ))

        # Send final summary
        succeeded = sum(1 for r in runs.values() if r.status == "completed")
        cancelled = sum(1 for r in runs.values() if r.status == "cancelled")
        total = len(plan.agents)
        skipped = len(skipped_ids)
        failed = len(failed_ids)

        parts = [f"✅ Plan completed: {succeeded}/{total} agents succeeded"]
        if failed > 0:
            parts.append(f"❌ {failed} failed")
        if cancelled > 0:
            parts.append(f"🚫 {cancelled} cancelled")
        if skipped > 0:
            parts.append(f"⏭ {skipped} skipped")

        await self._notify("\n".join(parts))

    def _should_skip(
        self,
        agent_task: AgentTask,
        failed_ids: set[str],
        skipped_ids: set[str],
    ) -> bool:
        """Check if any dependency of this agent has failed or been skipped."""
        for dep in agent_task.depends_on:
            if dep in failed_ids or dep in skipped_ids:
                return True
        return False

    def _build_task_prompt(
        self,
        agent_task: AgentTask,
        runs: dict[str, AgentRun],
    ) -> str:
        """Build the task prompt, prepending upstream log paths for dependencies."""
        if not agent_task.depends_on:
            return agent_task.task

        upstream_lines: list[str] = []
        for dep_id in agent_task.depends_on:
            dep_run = runs.get(dep_id)
            if dep_run and dep_run.log_path:
                upstream_lines.append(f"Agent {dep_run.name} output: {dep_run.log_path}")

        if not upstream_lines:
            return agent_task.task

        upstream_block = "\n".join(upstream_lines)
        return f"[Upstream agent outputs]\n{upstream_block}\n[End upstream outputs]\n\n{agent_task.task}"

    async def _record_event(self, event: WaveStarted | WaveCompleted) -> None:
        """Record an event to history if a HistoryManager is available."""
        if self._history is not None:
            await self._history.record_event(self._user_id, event)

    async def _notify(self, text: str) -> None:
        """Send a notification to the user via Telegram."""
        try:
            await self._bot.send_message(self._user_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("PlanExecutor notification failed: %s", exc)
