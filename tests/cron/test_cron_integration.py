"""Integration tests for CronScheduler — full pipeline with mocked bot."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.cron_scheduler import CronScheduler
from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep


# ── Helpers ───────────────────────────────────────────────────────


def _make_job(**kwargs) -> CronJobConfig:  # type: ignore[no-untyped-def]
    defaults: dict = dict(
        name="test",
        schedule="* * * * *",
        pipeline=[CronPipelineStep(tool="echo hello")],
        notify_user_id=None,
        timeout_seconds=30.0,
        enabled=True,
    )
    defaults.update(kwargs)
    return CronJobConfig(**defaults)


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── Pipeline chaining ─────────────────────────────────────────────


class TestFullPipeline:
    async def test_tool_stdout_passed_to_next_prompt_step(self) -> None:
        """Tool stdout replaces {input} in the following prompt step."""
        from archon.ai.event_mapper import Response

        captured: list[str] = []
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="summarized")

        mock_session.send = _mock_send

        pipeline = [
            CronPipelineStep(tool="echo tooloutput"),
            CronPipelineStep(prompt="Process: {input}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = CronScheduler(cfg, _make_bot())

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured == ["Process: tooloutput"]
        assert scheduler.job_statuses["test"].last_result == "summarized"

    async def test_notification_sent_with_success_icon(self) -> None:
        bot = _make_bot()
        job = _make_job(
            pipeline=[CronPipelineStep(tool="echo pipeline_done")],
            notify_user_id=777,
        )
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        bot.send_message.assert_awaited_once()
        msg = bot.send_message.call_args[0][1]
        assert "pipeline_done" in msg
        assert "✅" in msg

    async def test_three_step_pipeline_chains_correctly(self) -> None:
        """Three sequential tool steps pass stdout through the chain."""
        pipeline = [
            CronPipelineStep(tool="echo step1"),
            CronPipelineStep(tool="cat"),     # receives "step1", outputs "step1"
            CronPipelineStep(tool="cat"),     # receives "step1", outputs "step1"
        ]
        job = _make_job(pipeline=pipeline)
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler._run_job(job)
        assert scheduler.job_statuses["test"].last_result == "step1"

    async def test_empty_pipeline_completes_with_empty_result(self) -> None:
        job = _make_job(pipeline=[])
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler._run_job(job)
        assert scheduler.job_statuses["test"].last_result == ""


# ── Loop behaviour ────────────────────────────────────────────────


class TestSchedulerLoop:
    async def test_disabled_job_not_fired_by_loop(self) -> None:
        """Job with enabled=False is skipped even when _should_fire would return True."""
        bot = _make_bot()
        enabled_job = _make_job(
            name="enabled",
            pipeline=[CronPipelineStep(tool="echo e")],
            notify_user_id=1,
        )
        disabled_job = _make_job(
            name="disabled",
            enabled=False,
            pipeline=[CronPipelineStep(tool="echo d")],
            notify_user_id=1,
        )
        cfg = CronConfig(enabled=True, jobs=[enabled_job, disabled_job])
        scheduler = CronScheduler(cfg, bot)

        run_calls: list[str] = []
        original_run_job = scheduler._run_job

        async def _tracking_run_job(job: CronJobConfig) -> None:
            run_calls.append(job.name)
            await original_run_job(job)

        scheduler._run_job = _tracking_run_job  # type: ignore[method-assign]

        # Simulate one loop tick manually
        now = datetime.now()
        for job in scheduler._config.jobs:
            if not job.enabled:
                continue
            if scheduler._should_fire(job, now):
                await scheduler._run_job(job)

        assert "enabled" in run_calls
        assert "disabled" not in run_calls

    async def test_multiple_jobs_can_fire_in_same_tick(self) -> None:
        """Two enabled jobs with matching schedule both fire on the same tick."""
        bot = _make_bot()
        job_a = _make_job(name="a", pipeline=[CronPipelineStep(tool="echo a")], notify_user_id=1)
        job_b = _make_job(name="b", pipeline=[CronPipelineStep(tool="echo b")], notify_user_id=1)
        cfg = CronConfig(enabled=True, jobs=[job_a, job_b])
        scheduler = CronScheduler(cfg, bot)

        now = datetime(2025, 1, 1, 12, 0, 5)  # within a "* * * * *" window
        tasks = []
        for job in scheduler._config.jobs:
            if scheduler._should_fire(job, now):
                scheduler._statuses[job.name].last_fire_at = now
                tasks.append(asyncio.create_task(scheduler._run_job(job)))
        await asyncio.gather(*tasks)

        assert scheduler.job_statuses["a"].run_count == 1
        assert scheduler.job_statuses["b"].run_count == 1

    async def test_error_notification_contains_error_icon(self) -> None:
        bot = _make_bot()
        job = _make_job(
            pipeline=[CronPipelineStep(tool="bash -c 'exit 2'")],
            notify_user_id=123,
        )
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        msg = bot.send_message.call_args[0][1]
        assert "❌" in msg
        assert "test" in msg  # job name appears in notification

    async def test_failed_job_does_not_affect_other_jobs(self) -> None:
        """A failing job does not prevent other jobs from running."""
        bot = _make_bot()
        bad_job = _make_job(name="bad", pipeline=[CronPipelineStep(tool="bash -c 'exit 1'")])
        good_job = _make_job(name="good", pipeline=[CronPipelineStep(tool="echo ok")])
        cfg = CronConfig(enabled=True, jobs=[bad_job, good_job])
        scheduler = CronScheduler(cfg, bot)

        now = datetime(2025, 1, 1, 12, 0, 5)
        tasks = []
        for job in scheduler._config.jobs:
            if scheduler._should_fire(job, now):
                scheduler._statuses[job.name].last_fire_at = now
                tasks.append(asyncio.create_task(scheduler._run_job(job)))
        await asyncio.gather(*tasks)

        assert scheduler.job_statuses["bad"].last_error is not None
        assert scheduler.job_statuses["good"].last_result == "ok"
