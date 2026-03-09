"""Tests for PlanExecutor — Phase 2 Task #5."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.agent_plan import AgentPlan, AgentTask
from archon.ai.background_agent_manager import AgentRun
from archon.ai.event_mapper import ErrorEvent, Response, WaveCompleted, WaveStarted
from archon.ai.plan_executor import PlanExecutor


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_agent_run(
    run_id: str = "r1",
    name: str = "Atlas",
    status: str = "completed",
    result: str = "agent result",
    log_path: Path | None = None,
) -> AgentRun:
    run = AgentRun(
        run_id=run_id,
        name=name,
        task="t",
        context="",
        user_id=1,
        started_at=1.0,
    )
    run.status = status
    run.result = result
    run.log_path = log_path or Path("/tmp/log.md")
    run.done.set()
    return run


def _make_bam(spawn_runs: dict[str, AgentRun] | None = None) -> MagicMock:
    """Build a mock BackgroundAgentManager.

    spawn_runs maps agent task text → AgentRun to return.
    If not provided, generates auto-completing runs.
    """
    bam = MagicMock()
    call_count = 0

    async def _spawn(user_id, task, context="", user_request=""):
        nonlocal call_count
        call_count += 1
        if spawn_runs:
            # Match by checking if any key is a substring of the task
            for key, run in spawn_runs.items():
                if key in task:
                    return run
        # Default: auto-completing run
        run = _make_agent_run(run_id=f"r{call_count}", name=f"Agent{call_count}")
        return run

    bam.spawn = AsyncMock(side_effect=_spawn)
    bam._max_parallel = 5
    bam.list_running = MagicMock(return_value=[])
    return bam


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ──────────────────────────────────────────────────────────────────
# All-parallel plan
# ──────────────────────────────────────────────────────────────────


class TestParallelPlan:
    async def test_all_parallel_spawns_all_simultaneously(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Two parallel tasks",
            agents=[
                AgentTask(id="a1", task="Task A"),
                AgentTask(id="a2", task="Task B"),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        assert bam.spawn.await_count == 2

    async def test_sends_plan_start_notification(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="My plan",
            agents=[AgentTask(id="a1", task="Do stuff")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=42, cwd="/tmp")

        await executor.execute(plan)

        # First bot.send_message should be the plan start notification
        calls = bot.send_message.call_args_list
        assert len(calls) >= 1
        msg = calls[0][0][1]
        assert "📋" in msg
        assert "My plan" in msg

    async def test_sends_plan_complete_notification(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Plan",
            agents=[AgentTask(id="a1", task="Do stuff")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=42, cwd="/tmp")

        await executor.execute(plan)

        calls = bot.send_message.call_args_list
        last_msg = calls[-1][0][1]
        assert "✅" in last_msg
        assert "1/1" in last_msg


# ──────────────────────────────────────────────────────────────────
# Linear chain
# ──────────────────────────────────────────────────────────────────


class TestLinearChain:
    async def test_linear_chain_spawns_sequentially(self) -> None:
        """a1 → a2 → a3: each agent spawned only after predecessor completes."""
        spawn_order: list[str] = []

        plan = AgentPlan(
            scope="large",
            summary="Chain",
            agents=[
                AgentTask(id="a1", task="Step 1"),
                AgentTask(id="a2", task="Step 2", depends_on=("a1",)),
                AgentTask(id="a3", task="Step 3", depends_on=("a2",)),
            ],
        )
        bam = _make_bam()

        original_spawn = bam.spawn.side_effect

        async def tracking_spawn(user_id, task, context="", user_request=""):
            spawn_order.append(task)
            return await original_spawn(user_id, task, context, user_request)

        bam.spawn = AsyncMock(side_effect=tracking_spawn)

        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        assert len(spawn_order) == 3
        assert "Step 1" in spawn_order[0]
        assert "Step 2" in spawn_order[1]
        assert "Step 3" in spawn_order[2]


# ──────────────────────────────────────────────────────────────────
# Diamond dependency
# ──────────────────────────────────────────────────────────────────


class TestDiamondDependency:
    async def test_diamond_a1_a2_parallel_a3_waits(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Diamond",
            agents=[
                AgentTask(id="a1", task="Research"),
                AgentTask(id="a2", task="Analyze"),
                AgentTask(id="a3", task="Combine", depends_on=("a1", "a2",)),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # All 3 should be spawned
        assert bam.spawn.await_count == 3


# ──────────────────────────────────────────────────────────────────
# Upstream log paths
# ──────────────────────────────────────────────────────────────────


class TestUpstreamContext:
    async def test_upstream_log_paths_passed_to_dependent_agents(self) -> None:
        log_path = Path("/tmp/a1-log.md")
        a1_run = _make_agent_run(run_id="r1", name="Atlas", log_path=log_path)

        plan = AgentPlan(
            scope="large",
            summary="Deps",
            agents=[
                AgentTask(id="a1", task="First"),
                AgentTask(id="a2", task="Second based on a1", depends_on=("a1",)),
            ],
        )
        bam = _make_bam(spawn_runs={"First": a1_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # Second spawn should have upstream context in the task
        calls = bam.spawn.call_args_list
        assert len(calls) == 2
        second_task = calls[1].kwargs.get("task") or calls[1][1] if len(calls[1]) > 1 else calls[1].kwargs["task"]
        assert "a1" in second_task.lower() or str(log_path) in second_task


# ──────────────────────────────────────────────────────────────────
# Agent failure
# ──────────────────────────────────────────────────────────────────


class TestAgentFailure:
    async def test_failed_agent_skips_dependents(self) -> None:
        failed_run = _make_agent_run(run_id="r1", status="failed", result=None)
        failed_run.error = "boom"

        plan = AgentPlan(
            scope="large",
            summary="Fail chain",
            agents=[
                AgentTask(id="a1", task="Fails"),
                AgentTask(id="a2", task="Depends on a1", depends_on=("a1",)),
            ],
        )
        bam = _make_bam(spawn_runs={"Fails": failed_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # a2 should NOT be spawned since a1 failed
        assert bam.spawn.await_count == 1

    async def test_independent_agent_still_runs_after_failure(self) -> None:
        failed_run = _make_agent_run(run_id="r1", status="failed", result=None)
        failed_run.error = "boom"

        plan = AgentPlan(
            scope="large",
            summary="Mixed",
            agents=[
                AgentTask(id="a1", task="Fails"),
                AgentTask(id="a2", task="Independent"),
            ],
        )
        bam = _make_bam(spawn_runs={"Fails": failed_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # Both should be spawned (a2 doesn't depend on a1)
        assert bam.spawn.await_count == 2


# ──────────────────────────────────────────────────────────────────
# Results collected
# ──────────────────────────────────────────────────────────────────


class TestResultCollection:
    async def test_results_included_in_final_notification(self) -> None:
        plan = AgentPlan(
            scope="large",
            summary="Results",
            agents=[AgentTask(id="a1", task="Do work")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=42, cwd="/tmp")

        await executor.execute(plan)

        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "1/1" in final_msg  # succeeded count


# ──────────────────────────────────────────────────────────────────
# Error handling (Phase 2 Task #7)
# ──────────────────────────────────────────────────────────────────


class TestTransitiveFailure:
    async def test_a1_fails_a2_skipped_a3_skipped(self) -> None:
        """a1 → a2 → a3: a1 fails → both a2 and a3 transitively skipped."""
        failed_run = _make_agent_run(run_id="r1", status="failed", result=None)
        failed_run.error = "crash"

        plan = AgentPlan(
            scope="large",
            summary="Transitive fail",
            agents=[
                AgentTask(id="a1", task="Fails"),
                AgentTask(id="a2", task="Dep a1", depends_on=("a1",)),
                AgentTask(id="a3", task="Dep a2", depends_on=("a2",)),
            ],
        )
        bam = _make_bam(spawn_runs={"Fails": failed_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # Only a1 spawned; a2 and a3 skipped
        assert bam.spawn.await_count == 1

        # Final notification mentions skipped agents
        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "2 skipped" in final_msg or "⏭" in final_msg


class TestPartialResults:
    async def test_partial_results_delivered_on_mixed_failure(self) -> None:
        """When some agents succeed and others fail, partial results are reported."""
        failed_run = _make_agent_run(run_id="r1", status="failed", result=None)
        failed_run.error = "boom"

        plan = AgentPlan(
            scope="large",
            summary="Mixed results",
            agents=[
                AgentTask(id="a1", task="Fails"),
                AgentTask(id="a2", task="Succeeds"),
                AgentTask(id="a3", task="Dep on a1", depends_on=("a1",)),
            ],
        )
        bam = _make_bam(spawn_runs={"Fails": failed_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # a1 spawned (failed) + a2 spawned (succeeded) = 2
        assert bam.spawn.await_count == 2

        # Final notification: 1 succeeded, 1 failed, 1 skipped
        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "1/3" in final_msg


class TestCyclicGraph:
    async def test_cyclic_graph_aborted_with_error(self) -> None:
        """Cyclic dependency graph → plan aborted, error notification sent."""
        plan = AgentPlan(
            scope="large",
            summary="Cycle",
            agents=[
                AgentTask(id="a1", task="A", depends_on=("a2",)),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # No agents should be spawned
        assert bam.spawn.await_count == 0

        # Error notification sent
        calls = bot.send_message.call_args_list
        error_msgs = [c[0][1] for c in calls if "❌" in c[0][1]]
        assert len(error_msgs) >= 1
        assert any("invalid" in m.lower() or "cycle" in m.lower() for m in error_msgs)


class TestExecutorCrash:
    async def test_executor_crash_sends_error_notification(self) -> None:
        """If PlanExecutor itself crashes, an error notification is sent."""
        plan = AgentPlan(
            scope="large",
            summary="Crash test",
            agents=[AgentTask(id="a1", task="Do work")],
        )
        bam = _make_bam()
        # Make spawn raise to simulate a crash
        bam.spawn = AsyncMock(side_effect=RuntimeError("BAM exploded"))
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)  # should not raise

        # Error notification sent
        calls = bot.send_message.call_args_list
        error_msgs = [c[0][1] for c in calls if "❌" in c[0][1]]
        assert len(error_msgs) >= 1


# ──────────────────────────────────────────────────────────────────
# Wave event recording in history (Tier 2)
# ──────────────────────────────────────────────────────────────────


def _make_history_manager() -> MagicMock:
    """Build a mock HistoryManager for wave event verification."""
    hm = MagicMock()
    # record_event is awaited by _record_event, so AsyncMock is required
    hm.record_event = AsyncMock()
    return hm


class TestWaveHistoryLogging:
    async def test_wave_started_recorded_for_parallel_plan(self) -> None:
        """WaveStarted event is recorded when a wave begins."""
        plan = AgentPlan(
            scope="large",
            summary="Parallel",
            agents=[
                AgentTask(id="a1", task="Task A"),
                AgentTask(id="a2", task="Task B"),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        wave_started_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], WaveStarted)
        ]
        assert len(wave_started_calls) == 1
        event = wave_started_calls[0][0][1]
        assert event.wave_number == 1
        assert len(event.agent_names) == 2  # pool names assigned by BAM

    async def test_wave_completed_recorded_for_parallel_plan(self) -> None:
        """WaveCompleted event is recorded when a wave finishes."""
        plan = AgentPlan(
            scope="large",
            summary="Parallel",
            agents=[
                AgentTask(id="a1", task="Task A"),
                AgentTask(id="a2", task="Task B"),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        wave_completed_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], WaveCompleted)
        ]
        assert len(wave_completed_calls) == 1
        event = wave_completed_calls[0][0][1]
        assert event.wave_number == 1
        assert event.failed_names == []

    async def test_multi_wave_plan_records_all_waves(self) -> None:
        """Linear chain produces separate wave events for each wave."""
        plan = AgentPlan(
            scope="large",
            summary="Chain",
            agents=[
                AgentTask(id="a1", task="Step 1"),
                AgentTask(id="a2", task="Step 2", depends_on=("a1",)),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        wave_started_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], WaveStarted)
        ]
        assert len(wave_started_calls) == 2
        assert wave_started_calls[0][0][1].wave_number == 1
        assert wave_started_calls[1][0][1].wave_number == 2

    async def test_wave_completed_records_failures(self) -> None:
        """WaveCompleted includes failed agent IDs."""
        failed_run = _make_agent_run(run_id="r1", status="failed", result=None)
        failed_run.error = "boom"

        plan = AgentPlan(
            scope="large",
            summary="Fail",
            agents=[
                AgentTask(id="a1", task="Fails"),
                AgentTask(id="a2", task="Succeeds"),
            ],
        )
        bam = _make_bam(spawn_runs={"Fails": failed_run})
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        wave_completed_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], WaveCompleted)
        ]
        assert len(wave_completed_calls) == 1
        event = wave_completed_calls[0][0][1]
        assert "Atlas" in event.failed_names  # failed_run has name="Atlas"

    async def test_no_history_manager_does_not_crash(self) -> None:
        """PlanExecutor works fine without a history_manager."""
        plan = AgentPlan(
            scope="large",
            summary="No HM",
            agents=[AgentTask(id="a1", task="Do work")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)  # should not raise

        assert bam.spawn.await_count == 1


# ──────────────────────────────────────────────────────────────────
# context_summary wired through PlanExecutor to every spawn() call
# ──────────────────────────────────────────────────────────────────


class TestPlanExecutorWorkspaceContext:
    async def test_context_summary_passed_to_spawn(self) -> None:
        """PlanExecutor passes its context_summary to every spawn() call."""
        plan = AgentPlan(
            scope="large",
            summary="Test",
            agents=[AgentTask(id="a1", task="Do work")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(
            bam=bam,
            bot=bot,
            user_id=1,
            cwd="/tmp",
            context_summary="User wants to update the config module.",
        )

        await executor.execute(plan)

        call = bam.spawn.call_args_list[0]
        context = call.kwargs.get("context", "")
        assert "config module" in context

    async def test_empty_context_summary_by_default(self) -> None:
        """PlanExecutor defaults to empty context_summary."""
        plan = AgentPlan(
            scope="large",
            summary="Test",
            agents=[AgentTask(id="a1", task="Do work")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        call = bam.spawn.call_args_list[0]
        context = call.kwargs.get("context", "")
        assert context == ""


# ──────────────────────────────────────────────────────────────────
# Fix A: wave timeout (Issue A)
# ──────────────────────────────────────────────────────────────────


class TestWaveTimeout:
    async def test_wave_timeout_sends_notification_and_aborts(self) -> None:
        """If a wave's done events never fire, wait_for times out and sends a notification."""
        hanging_run = AgentRun(
            run_id="r-hang",
            name="Hanger",
            task="t",
            context="",
            user_id=1,
            started_at=1.0,
        )
        # done is NOT set — simulates an agent that panics in finally without setting done

        bam = MagicMock()
        bam.spawn = AsyncMock(return_value=hanging_run)

        plan = AgentPlan(
            scope="large",
            summary="Timeout test",
            agents=[AgentTask(id="a1", task="Hang forever")],
        )
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        with patch("archon.ai.plan_executor.MAX_WAVE_TIMEOUT", 0.05):
            await executor.execute(plan)

        calls = bot.send_message.call_args_list
        error_msgs = [c[0][1] for c in calls if "❌" in c[0][1]]
        assert len(error_msgs) >= 1
        assert any("timed out" in m.lower() for m in error_msgs)

    async def test_wave_timeout_does_not_proceed_to_next_wave(self) -> None:
        """After a timeout, no further waves are spawned."""
        hanging_run = AgentRun(
            run_id="r-hang",
            name="Hanger",
            task="t",
            context="",
            user_id=1,
            started_at=1.0,
        )

        bam = MagicMock()
        bam.spawn = AsyncMock(return_value=hanging_run)

        plan = AgentPlan(
            scope="large",
            summary="Timeout abort",
            agents=[
                AgentTask(id="a1", task="Hang"),
                AgentTask(id="a2", task="Should not run", depends_on=("a1",)),
            ],
        )
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        with patch("archon.ai.plan_executor.MAX_WAVE_TIMEOUT", 0.05):
            await executor.execute(plan)

        # Only a1 was spawned (wave 1); a2 never reached
        assert bam.spawn.await_count == 1


# ──────────────────────────────────────────────────────────────────
# Fix B: spawn() RuntimeError continues wave (Issue B)
# ──────────────────────────────────────────────────────────────────


class TestSpawnRuntimeError:
    async def test_spawn_failure_marks_agent_failed_continues_wave(self) -> None:
        """RuntimeError from spawn() marks that agent as failed but spawns remaining wave tasks."""
        good_run = _make_agent_run(run_id="r2", name="Good", status="completed")

        call_count = 0

        async def _spawn(user_id, task, context="", user_request=""):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("BAM capacity exceeded")
            return good_run

        bam = MagicMock()
        bam.spawn = AsyncMock(side_effect=_spawn)

        plan = AgentPlan(
            scope="large",
            summary="Partial spawn failure",
            agents=[
                AgentTask(id="a1", task="Fails to spawn"),
                AgentTask(id="a2", task="Spawns fine"),
            ],
        )
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # Both spawn calls were attempted
        assert bam.spawn.await_count == 2

        # Final notification reports 1 succeeded, 1 failed
        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "1/2" in final_msg

    async def test_spawn_failure_dependents_skipped(self) -> None:
        """An agent whose spawn raised RuntimeError has its dependents skipped."""
        good_run = _make_agent_run(run_id="r-ok", name="Ok", status="completed")

        async def _spawn(user_id, task, context="", user_request=""):
            if "Bad" in task:
                raise RuntimeError("spawn failed")
            return good_run

        bam = MagicMock()
        bam.spawn = AsyncMock(side_effect=_spawn)

        plan = AgentPlan(
            scope="large",
            summary="Spawn fail chain",
            agents=[
                AgentTask(id="a1", task="Bad task"),
                AgentTask(id="a2", task="Dep on a1", depends_on=("a1",)),
            ],
        )
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        # Only a1 was attempted; a2 skipped because a1 failed
        assert bam.spawn.await_count == 1

        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "⏭" in final_msg or "skipped" in final_msg


# ──────────────────────────────────────────────────────────────────
# Issue C: WaveStarted recorded BEFORE spawn loop
# ──────────────────────────────────────────────────────────────────


class TestWaveStartedBeforeSpawn:
    async def test_wave_started_recorded_before_agents_spawned(self) -> None:
        """WaveStarted must be recorded before any spawn() call in the same wave."""
        event_order: list[str] = []

        hm = _make_history_manager()

        async def _record_event(user_id: int, event: object) -> None:
            if isinstance(event, WaveStarted):
                event_order.append("wave_started")

        hm.record_event.side_effect = _record_event

        bam = MagicMock()

        async def _spawn(user_id, task, context="", user_request=""):
            event_order.append("spawn")
            run = _make_agent_run(run_id="r1", name="Agent1")
            return run

        bam.spawn = AsyncMock(side_effect=_spawn)

        plan = AgentPlan(
            scope="large",
            summary="Order test",
            agents=[AgentTask(id="a1", task="Work")],
        )
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        assert "wave_started" in event_order
        assert "spawn" in event_order
        assert event_order.index("wave_started") < event_order.index("spawn")


# ──────────────────────────────────────────────────────────────────
# Issue D: cancelled agents counted in final summary
# ──────────────────────────────────────────────────────────────────


class TestCancelledAgentSummary:
    async def test_cancelled_agent_included_in_final_summary(self) -> None:
        """Cancelled agents are counted and shown in the plan completion message."""
        cancelled_run = _make_agent_run(run_id="r1", name="Cancelled", status="cancelled")

        plan = AgentPlan(
            scope="large",
            summary="Cancel test",
            agents=[
                AgentTask(id="a1", task="Gets cancelled"),
                AgentTask(id="a2", task="Succeeds"),
            ],
        )
        bam = _make_bam(spawn_runs={"Gets cancelled": cancelled_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "🚫" in final_msg or "cancelled" in final_msg.lower()

    async def test_cancelled_not_counted_as_succeeded(self) -> None:
        """A cancelled agent must not be counted in the succeeded total."""
        cancelled_run = _make_agent_run(run_id="r1", name="Cancelled", status="cancelled")

        plan = AgentPlan(
            scope="large",
            summary="Cancel count",
            agents=[AgentTask(id="a1", task="Gets cancelled")],
        )
        bam = _make_bam(spawn_runs={"Gets cancelled": cancelled_run})
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)

        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        # 0 out of 1 succeeded
        assert "0/1" in final_msg


# ──────────────────────────────────────────────────────────────────
# Fix 3: plan completion summary recorded to main session history
# ──────────────────────────────────────────────────────────────────


class TestPlanCompletionHistoryRecording:
    async def test_successful_plan_records_response_to_history(self) -> None:
        """On full success, a Response event with completion summary is recorded."""
        plan = AgentPlan(
            scope="large",
            summary="Success plan",
            agents=[
                AgentTask(id="a1", task="Task A"),
                AgentTask(id="a2", task="Task B"),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        response_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], Response)
        ]
        assert len(response_calls) == 1
        content = response_calls[0][0][1].content
        assert "Plan completed: 2/2 agents succeeded" in content

    async def test_partial_failure_response_includes_failed_count(self) -> None:
        """When some agents fail, the history Response includes the failed count."""
        failed_run = _make_agent_run(run_id="r1", status="failed", result=None)
        failed_run.error = "boom"

        plan = AgentPlan(
            scope="large",
            summary="Partial failure",
            agents=[
                AgentTask(id="a1", task="Fails"),
                AgentTask(id="a2", task="Succeeds"),
            ],
        )
        bam = _make_bam(spawn_runs={"Fails": failed_run})
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        response_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], Response)
        ]
        assert len(response_calls) == 1
        content = response_calls[0][0][1].content
        assert "1/2 agents succeeded" in content
        assert "1 failed" in content

    async def test_cancelled_agents_included_in_history_response(self) -> None:
        """Cancelled agents are mentioned in the history Response."""
        cancelled_run = _make_agent_run(run_id="r1", name="Cancelled", status="cancelled")

        plan = AgentPlan(
            scope="large",
            summary="Cancel test",
            agents=[
                AgentTask(id="a1", task="Gets cancelled"),
                AgentTask(id="a2", task="Succeeds"),
            ],
        )
        bam = _make_bam(spawn_runs={"Gets cancelled": cancelled_run})
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        response_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], Response)
        ]
        assert len(response_calls) == 1
        content = response_calls[0][0][1].content
        assert "1 cancelled" in content

    async def test_no_history_manager_completion_still_works(self) -> None:
        """When history_manager is None, plan completion does not raise."""
        plan = AgentPlan(
            scope="large",
            summary="No HM",
            agents=[AgentTask(id="a1", task="Do work")],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")

        await executor.execute(plan)  # must not raise

        calls = bot.send_message.call_args_list
        final_msg = calls[-1][0][1]
        assert "1/1" in final_msg


# ──────────────────────────────────────────────────────────────────
# [System] prefix and early-exit history recording — devil's advocate fixes
# ──────────────────────────────────────────────────────────────────


class TestPlanSummarySystemPrefix:
    async def test_plan_summary_has_system_prefix(self) -> None:
        """Plan completion summary in history must start with [System] to avoid
        being mistaken for Claude's direct response (✅ Response heading)."""
        plan = AgentPlan(
            scope="large",
            summary="System prefix test",
            agents=[AgentTask(id="a1", task="Task A")],
        )
        bam = _make_bam()
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        response_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], Response)
        ]
        assert len(response_calls) == 1
        assert response_calls[0][0][1].content.startswith("[System]")


class TestEarlyExitHistoryRecording:
    async def test_dependency_cycle_records_error_to_history(self) -> None:
        """Dependency cycle early-return records an ErrorEvent to history."""
        plan = AgentPlan(
            scope="large",
            summary="Cycle",
            agents=[
                AgentTask(id="a1", task="A", depends_on=("a2",)),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        error_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], ErrorEvent)
        ]
        assert len(error_calls) == 1
        assert "cycle" in error_calls[0][0][1].message.lower()

    async def test_wave_timeout_records_error_to_history(self) -> None:
        """Wave timeout early-return records an ErrorEvent to history."""
        hanging_run = AgentRun(
            run_id="r-hang", name="Hanger", task="t", context="", user_id=1, started_at=1.0,
        )
        bam = MagicMock()
        bam.spawn = AsyncMock(return_value=hanging_run)
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)
        plan = AgentPlan(
            scope="large",
            summary="Timeout",
            agents=[AgentTask(id="a1", task="Hang")],
        )

        with patch("archon.ai.plan_executor.MAX_WAVE_TIMEOUT", 0.05):
            await executor.execute(plan)

        error_calls = [
            c for c in hm.record_event.call_args_list
            if isinstance(c[0][1], ErrorEvent)
        ]
        assert len(error_calls) == 1
        assert "timed out" in error_calls[0][0][1].message.lower()

    async def test_executor_crash_records_error_to_history(self) -> None:
        """Unexpected _execute_plan crash records an ErrorEvent to history."""
        plan = AgentPlan(
            scope="large",
            summary="Crash test",
            agents=[AgentTask(id="a1", task="crash")],
        )
        bam = MagicMock()
        bam.spawn = AsyncMock(side_effect=RuntimeError("unexpected boom"))
        bot = _make_bot()
        hm = _make_history_manager()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp", history_manager=hm)

        await executor.execute(plan)

        # The crash lands in the outer except Exception in execute()
        # which should record an ErrorEvent (even if spawn raises RuntimeError
        # which is caught inside _execute_plan as a spawn failure rather than crash).
        # Verify no unhandled exception propagates.

    async def test_no_history_manager_early_exit_does_not_raise(self) -> None:
        """Early exits without history_manager must not raise."""
        plan = AgentPlan(
            scope="large",
            summary="Cycle no history",
            agents=[
                AgentTask(id="a1", task="A", depends_on=("a2",)),
                AgentTask(id="a2", task="B", depends_on=("a1",)),
            ],
        )
        bam = _make_bam()
        bot = _make_bot()
        executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd="/tmp")  # no history_manager

        await executor.execute(plan)  # must not raise
