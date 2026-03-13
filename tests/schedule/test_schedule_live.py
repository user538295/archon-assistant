"""Live integration tests for JobScheduler — real subprocess, real asyncio.

Run manually with:
  uv run pytest tests/schedule/test_schedule_live.py -m live -v -s

These tests are excluded from the default CI run (marker: live).
The 'test_live_schedule_fires_within_90_seconds' test schedules a '* * * * *' job,
starts the scheduler, and waits for the job to fire (first tick is immediate).
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from archon.ai.job_scheduler import JobScheduler
from archon.config.loader import ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.live
async def test_live_tool_step_echo() -> None:
    """A simple echo tool step returns the correct output."""
    cfg = ScheduleConfig(
        enabled=True,
        jobs=[ScheduledJobConfig(name="echo_test", cron="* * * * *", pipeline=[])],
    )
    scheduler = JobScheduler(cfg, _make_bot(), allowed_user_ids=[])
    result = await scheduler._run_tool("echo hello from schedule", timeout=10.0)
    assert result == "hello from schedule"


@pytest.mark.live
async def test_live_tool_step_empty_stdin() -> None:
    """Tool step uses empty stdin in the new model."""
    cfg = ScheduleConfig(
        enabled=True,
        jobs=[ScheduledJobConfig(name="echo_test", cron="* * * * *", pipeline=[])],
    )
    scheduler = JobScheduler(cfg, _make_bot(), allowed_user_ids=[])
    result = await scheduler._run_tool("echo no_stdin_here", timeout=10.0)
    assert result == "no_stdin_here"


@pytest.mark.live
async def test_live_pipeline_tool_chaining() -> None:
    """Multi-step pipeline: two echo steps, last output is final result."""
    bot = _make_bot()
    job = ScheduledJobConfig(
        name="chain_test",
        cron="* * * * *",
        pipeline=[
            SchedulePipelineStep(name="step1_tool", kind="tool", value="echo step_one_output"),
            SchedulePipelineStep(name="step2_tool", kind="tool", value="echo step_two"),
        ],
    )
    cfg = ScheduleConfig(enabled=True, jobs=[job])
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[])
    await scheduler._run_job(job)
    status = scheduler.job_statuses["chain_test"]
    assert status.last_result == "step_two"
    assert status.run_count == 1
    assert status.is_running is False


@pytest.mark.live
async def test_live_pipeline_with_ref_substitution() -> None:
    """Named ref in tool command is substituted from earlier step output."""
    bot = _make_bot()
    job = ScheduledJobConfig(
        name="ref_test",
        cron="* * * * *",
        pipeline=[
            SchedulePipelineStep(name="word_tool", kind="tool", value="echo hello"),
            SchedulePipelineStep(name="echo_tool", kind="tool", value="echo {word_tool}"),
        ],
    )
    cfg = ScheduleConfig(enabled=True, jobs=[job])
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[])
    await scheduler._run_job(job)
    assert scheduler.job_statuses["ref_test"].last_result == "hello"


@pytest.mark.live
async def test_live_prompt_step_executes() -> None:
    """A real Claude session returns a non-empty response for a prompt step."""
    cfg = ScheduleConfig(
        enabled=True,
        jobs=[ScheduledJobConfig(name="prompt_test", cron="* * * * *", pipeline=[])],
    )
    scheduler = JobScheduler(cfg, _make_bot(), allowed_user_ids=[])
    result = await scheduler._run_prompt("Reply with exactly one word: hello", timeout=60.0)
    assert len(result) > 0
    assert "hello" in result.lower()


@pytest.mark.live
async def test_live_schedule_fires_within_90_seconds() -> None:
    """Full scheduler with '* * * * *' fires on first tick and notifies.

    The scheduler loop checks immediately on start. For '* * * * *', the
    previous cron slot is always within the last 60 seconds, so the job fires
    on the FIRST loop tick (within a second of start). Timeout is 90s for safety.
    """
    fired = asyncio.Event()
    received_messages: list[str] = []

    bot = MagicMock()

    async def _capture_notify(chat_id: int, text: str, **kwargs: object) -> None:
        received_messages.append(text)
        fired.set()

    bot.send_message = _capture_notify

    cfg = ScheduleConfig(
        enabled=True,
        jobs=[
            ScheduledJobConfig(
                name="live_schedule_test",
                cron="* * * * *",
                pipeline=[SchedulePipelineStep(name="hello_tool", kind="tool", value="echo hello from schedule")],
                timeout_seconds=10.0,
            )
        ],
    )
    scheduler = JobScheduler(cfg, bot, allowed_user_ids=[])
    await scheduler.start()
    try:
        await asyncio.wait_for(fired.wait(), timeout=90.0)
    finally:
        await scheduler.stop()

    assert len(received_messages) >= 1
    assert "hello from schedule" in received_messages[0]
    status = scheduler.job_statuses["live_schedule_test"]
    assert status.run_count >= 1
    assert status.last_result == "hello from schedule"
    assert status.is_running is False


@pytest.mark.live
async def test_live_job_status_fully_populated_after_run() -> None:
    """After a job runs, all status fields are set correctly."""
    job = ScheduledJobConfig(
        name="status_test",
        cron="* * * * *",
        pipeline=[SchedulePipelineStep(name="status_tool", kind="tool", value="echo status_ok")],
    )
    cfg = ScheduleConfig(enabled=True, jobs=[job])
    scheduler = JobScheduler(cfg, _make_bot(), allowed_user_ids=[])
    await scheduler._run_job(job)
    status = scheduler.job_statuses["status_test"]
    assert status.run_count == 1
    assert status.last_run is not None
    assert status.last_result == "status_ok"
    assert status.last_error is None
    assert status.is_running is False
