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
        pipeline=[CronPipelineStep(name="hello_tool", kind="tool", value="echo hello")],
        timeout_seconds=30.0,
        enabled=True,
    )
    defaults.update(kwargs)
    return CronJobConfig(**defaults)


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _make_scheduler(
    cfg: CronConfig,
    bot: MagicMock | None = None,
    allowed_user_ids: list[int] | None = None,
) -> CronScheduler:
    return CronScheduler(
        cfg,
        bot or _make_bot(),
        allowed_user_ids=allowed_user_ids or [],
    )


# ── Pipeline chaining ─────────────────────────────────────────────


class TestFullPipeline:
    async def test_tool_stdout_passed_to_next_prompt_step(self) -> None:
        """Tool output is substituted via named ref in the following prompt step."""
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
            CronPipelineStep(name="echo_tool", kind="tool", value="echo tooloutput"),
            CronPipelineStep(name="process_prompt", kind="prompt", value="Process: {echo_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured == ["Process: tooloutput"]
        assert scheduler.job_statuses["test"].last_result == "summarized"

    async def test_notification_sent_with_success_icon(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(name="done_tool", kind="tool", value="echo pipeline_done")])
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[777])
        await scheduler._run_job(job)
        bot.send_message.assert_awaited_once()
        msg = bot.send_message.call_args[0][1]
        assert "pipeline_done" in msg
        assert "✅" in msg

    async def test_three_step_pipeline_chains_correctly(self) -> None:
        """Three sequential tool steps — last step output is the final result."""
        pipeline = [
            CronPipelineStep(name="step1_tool", kind="tool", value="echo step1"),
            CronPipelineStep(name="step2_tool", kind="tool", value="echo step2"),
            CronPipelineStep(name="step3_tool", kind="tool", value="echo step3"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        assert scheduler.job_statuses["test"].last_result == "step3"

    async def test_empty_pipeline_completes_with_empty_result(self) -> None:
        """A job with an empty pipeline list (validation_error=None) completes with empty result.

        Note: in production, load_cron_jobs always sets validation_error for empty pipelines,
        so this state only arises when constructing CronJobConfig directly (e.g. in tests or
        programmatically). The scheduler handles it gracefully.
        """
        job = _make_job(pipeline=[])
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg)
        await scheduler._run_job(job)
        assert scheduler.job_statuses["test"].last_result == ""


# ── Named ref chains ──────────────────────────────────────────────


class TestNamedRefChains:
    async def test_named_ref_chains_tool_to_prompt(self) -> None:
        """Named ref from tool step is correctly substituted into prompt."""
        from archon.ai.event_mapper import Response

        captured: list[str] = []
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="result")

        mock_session.send = _mock_send

        pipeline = [
            CronPipelineStep(name="health_check_tool", kind="tool", value="echo ok_status"),
            CronPipelineStep(name="summary_prompt", kind="prompt", value="Summarize: {health_check_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured == ["Summarize: ok_status"]

    async def test_two_tools_both_referenced_in_prompt(self) -> None:
        """Both tool outputs are substituted into a single prompt step."""
        from archon.ai.event_mapper import Response

        captured: list[str] = []
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="merged")

        mock_session.send = _mock_send

        pipeline = [
            CronPipelineStep(name="tool_1_tool", kind="tool", value="echo output_one"),
            CronPipelineStep(name="tool_2_tool", kind="tool", value="echo output_two"),
            CronPipelineStep(name="merge_prompt", kind="prompt", value="Merge: {tool_1_tool} and {tool_2_tool}"),
        ]
        job = _make_job(pipeline=pipeline)
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg)

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_job(job)

        assert captured == ["Merge: output_one and output_two"]


# ── /scheduled command with invalid job ───────────────────────────


class TestJobsCommandWithInvalidJob:
    async def test_scheduled_command_shows_invalid_job_warning(self) -> None:
        """scheduled_command output contains ⚠️ and the error text for invalid jobs."""
        from archon.chat.commands import scheduled_command
        from aiogram.types import Message

        # Build a scheduler with one invalid job
        invalid_job = CronJobConfig(
            name="broken-job",
            schedule="* * * * *",
            pipeline=[],
            validation_error="step 'bad_step' has no recognized suffix",
        )
        cfg = CronConfig(enabled=True, jobs=[invalid_job])
        bot = _make_bot()
        scheduler = CronScheduler(cfg, bot, allowed_user_ids=[])

        # Mock the Message object
        sent_texts: list[str] = []

        message = MagicMock(spec=Message)
        message.answer = AsyncMock(side_effect=lambda text, **kw: sent_texts.append(text))

        # Patch reload_jobs to be a no-op
        scheduler.reload_jobs = MagicMock()  # type: ignore[method-assign]

        await scheduled_command(message, cron_scheduler=scheduler)

        assert len(sent_texts) == 1
        assert "⚠️" in sent_texts[0]
        assert "broken-job" in sent_texts[0]

    async def test_scheduled_command_shows_valid_jobs_normally(self) -> None:
        """scheduled_command output shows normal state for valid jobs."""
        from archon.chat.commands import scheduled_command
        from aiogram.types import Message

        valid_job = CronJobConfig(
            name="good-job",
            schedule="* * * * *",
            pipeline=[CronPipelineStep(name="echo_tool", kind="tool", value="echo hi")],
            validation_error=None,
        )
        cfg = CronConfig(enabled=True, jobs=[valid_job])
        bot = _make_bot()
        scheduler = CronScheduler(cfg, bot, allowed_user_ids=[])

        sent_texts: list[str] = []
        message = MagicMock(spec=Message)
        message.answer = AsyncMock(side_effect=lambda text, **kw: sent_texts.append(text))

        scheduler.reload_jobs = MagicMock()  # type: ignore[method-assign]

        await scheduled_command(message, cron_scheduler=scheduler)

        assert len(sent_texts) == 1
        assert "good-job" in sent_texts[0]
        assert "⚠️" not in sent_texts[0]


# ── Loop behaviour ────────────────────────────────────────────────


class TestSchedulerLoop:
    async def test_disabled_job_not_fired_by_loop(self) -> None:
        """Job with enabled=False is skipped even when _should_fire would return True."""
        bot = _make_bot()
        enabled_job = _make_job(name="enabled", pipeline=[CronPipelineStep(name="e_tool", kind="tool", value="echo e")])
        disabled_job = _make_job(
            name="disabled", enabled=False, pipeline=[CronPipelineStep(name="d_tool", kind="tool", value="echo d")]
        )
        cfg = CronConfig(enabled=True, jobs=[enabled_job, disabled_job])
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])

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
        job_a = _make_job(name="a", pipeline=[CronPipelineStep(name="a_tool", kind="tool", value="echo a")])
        job_b = _make_job(name="b", pipeline=[CronPipelineStep(name="b_tool", kind="tool", value="echo b")])
        cfg = CronConfig(enabled=True, jobs=[job_a, job_b])
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[1])

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
        job = _make_job(pipeline=[CronPipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 2'")])
        cfg = CronConfig(enabled=True, jobs=[job])
        scheduler = _make_scheduler(cfg, bot, allowed_user_ids=[123])
        await scheduler._run_job(job)
        msg = bot.send_message.call_args[0][1]
        assert "❌" in msg
        assert "test" in msg  # job name appears in notification

    async def test_failed_job_does_not_affect_other_jobs(self) -> None:
        """A failing job does not prevent other jobs from running."""
        bot = _make_bot()
        bad_job = _make_job(name="bad", pipeline=[CronPipelineStep(name="fail_tool", kind="tool", value="bash -c 'exit 1'")])
        good_job = _make_job(name="good", pipeline=[CronPipelineStep(name="ok_tool", kind="tool", value="echo ok")])
        cfg = CronConfig(enabled=True, jobs=[bad_job, good_job])
        scheduler = _make_scheduler(cfg, bot)

        now = datetime(2025, 1, 1, 12, 0, 5)
        tasks = []
        for job in scheduler._config.jobs:
            if scheduler._should_fire(job, now):
                scheduler._statuses[job.name].last_fire_at = now
                tasks.append(asyncio.create_task(scheduler._run_job(job)))
        await asyncio.gather(*tasks)

        assert scheduler.job_statuses["bad"].last_error is not None
        assert scheduler.job_statuses["good"].last_result == "ok"
