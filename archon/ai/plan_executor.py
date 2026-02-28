"""PlanExecutor — resolves dependency graph and spawns workers via BackgroundAgentManager."""

from __future__ import annotations

import asyncio
import html
import logging
from typing import TYPE_CHECKING

from archon.ai.agent_plan import AgentPlan, AgentTask, topological_sort, validate_dependency_graph
from archon.ai.background_agent_manager import AgentRun

if TYPE_CHECKING:
    from aiogram import Bot

    from archon.ai.background_agent_manager import BackgroundAgentManager

logger = logging.getLogger("archon")


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
    ) -> None:
        self._bam = bam
        self._bot = bot
        self._user_id = user_id
        self._cwd = cwd

    async def execute(self, plan: AgentPlan) -> None:
        """Main entry point — run as an async task."""
        try:
            await self._execute_plan(plan)
        except Exception:
            logger.exception("PlanExecutor crashed")
            await self._notify(f"❌ Plan execution failed unexpectedly.")

    async def _execute_plan(self, plan: AgentPlan) -> None:
        n = len(plan.agents)
        await self._notify(f"📋 Executing plan: {html.escape(plan.summary)} ({n} agent{'s' if n != 1 else ''})")

        if not validate_dependency_graph(plan):
            await self._notify("❌ Plan has invalid dependencies (cycles or unknown IDs). Aborting.")
            return

        waves = topological_sort(plan)

        # Track agent runs and results keyed by agent plan ID
        runs: dict[str, AgentRun] = {}
        failed_ids: set[str] = set()
        skipped_ids: set[str] = set()

        for wave in waves:
            wave_runs: list[tuple[AgentTask, AgentRun]] = []

            for agent_task in wave:
                # Skip if any dependency failed
                if self._should_skip(agent_task, failed_ids, skipped_ids):
                    skipped_ids.add(agent_task.id)
                    logger.info("Skipping agent %s: dependency failed", agent_task.id)
                    continue

                # Build task prompt with upstream context
                task_prompt = self._build_task_prompt(agent_task, runs)

                run = await self._bam.spawn(
                    user_id=self._user_id,
                    task=task_prompt,
                    context="",
                    user_request=f"Plan agent {agent_task.id}",
                )
                runs[agent_task.id] = run
                wave_runs.append((agent_task, run))

            # Wait for all agents in this wave to complete
            if wave_runs:
                await asyncio.gather(
                    *[run._done.wait() for _, run in wave_runs]
                )

            # Check for failures in this wave
            for agent_task, run in wave_runs:
                if run.status == "failed":
                    failed_ids.add(agent_task.id)

        # Send final summary
        succeeded = sum(1 for r in runs.values() if r.status == "completed")
        total = len(plan.agents)
        skipped = len(skipped_ids)
        failed = len(failed_ids)

        parts = [f"✅ Plan completed: {succeeded}/{total} agents succeeded"]
        if failed > 0:
            parts.append(f"❌ {failed} failed")
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
        for dep_id in agent_task.depends_on:
            if dep_id in failed_ids or dep_id in skipped_ids:
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
                upstream_lines.append(f"Agent {dep_id} output: {dep_run.log_path}")

        if not upstream_lines:
            return agent_task.task

        upstream_block = "\n".join(upstream_lines)
        return f"[Upstream agent outputs]\n{upstream_block}\n[End upstream outputs]\n\n{agent_task.task}"

    async def _notify(self, text: str) -> None:
        """Send a notification to the user via Telegram."""
        try:
            await self._bot.send_message(self._user_id, text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("PlanExecutor notification failed: %s", exc)
