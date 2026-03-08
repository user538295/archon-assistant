"""Tests for CronScheduler — task observability fixes."""
import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.cron_scheduler import CronScheduler, _log_task_exception
from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_job(name: str = "test_job", schedule: str = "* * * * *") -> CronJobConfig:
    return CronJobConfig(
        name=name,
        schedule=schedule,
        pipeline=[CronPipelineStep(name="step_tool", kind="tool", value="echo hi")],
    )


def _make_scheduler(jobs: list[CronJobConfig] | None = None) -> CronScheduler:
    config = CronConfig(enabled=True, jobs=jobs or [])
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return CronScheduler(config=config, bot=bot, allowed_user_ids=[1])


# ──────────────────────────────────────────────────────────────────
# _log_task_exception helper
# ──────────────────────────────────────────────────────────────────


async def test_log_task_exception_logs_error_on_failure(caplog: pytest.LogCaptureFixture) -> None:
    """An exception inside a cron task must be logged at ERROR level."""
    async def _fail() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(_fail())
    with pytest.raises(Exception):
        await asyncio.shield(task)

    with caplog.at_level(logging.ERROR, logger="archon"):
        _log_task_exception(task, "my_job")

    assert any("my_job" in r.message and r.levelno == logging.ERROR for r in caplog.records)


async def test_log_task_exception_silent_on_cancelled() -> None:
    """A cancelled task must NOT produce an error log."""
    async def _wait() -> None:
        await asyncio.sleep(100)

    task = asyncio.create_task(_wait())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    records: list[logging.LogRecord] = []
    handler = logging.handlers_mock = MagicMock()

    import logging as _logging
    archon_logger = _logging.getLogger("archon")
    original_handlers = archon_logger.handlers[:]
    captured: list[logging.LogRecord] = []

    class _Cap(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    cap = _Cap()
    archon_logger.addHandler(cap)
    try:
        _log_task_exception(task, "my_job")
    finally:
        archon_logger.removeHandler(cap)

    assert not any(r.levelno >= logging.ERROR for r in captured)


# ──────────────────────────────────────────────────────────────────
# Task tracking: _tasks set
# ──────────────────────────────────────────────────────────────────


async def test_task_added_to_tasks_set_and_removed_on_completion() -> None:
    """Tasks spawned by _loop are tracked in _tasks and auto-removed when done."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    # Patch _run_job to a fast coroutine
    async def _fast_job(_job: CronJobConfig) -> None:
        return

    scheduler._run_job = _fast_job  # type: ignore[method-assign]

    # Manually simulate what _loop does
    task = asyncio.create_task(scheduler._run_job(job), name="cron-test")
    scheduler._tasks.add(task)
    task.add_done_callback(scheduler._tasks.discard)
    task.add_done_callback(
        lambda t, name=job.name: _log_task_exception(t, name)
    )

    assert task in scheduler._tasks
    await task
    # discard callback fires synchronously after task completes
    await asyncio.sleep(0)  # allow callbacks to run
    assert task not in scheduler._tasks


# ──────────────────────────────────────────────────────────────────
# Integration: exception in _run_job surfaces via done-callback log
# ──────────────────────────────────────────────────────────────────


async def test_exception_in_run_job_is_logged_via_callback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If _run_job raises (bypassing internal try/except), the callback logs it."""
    job = _make_job()
    scheduler = _make_scheduler([job])

    async def _exploding_job(_job: CronJobConfig) -> None:
        raise RuntimeError("unexpected crash")

    scheduler._run_job = _exploding_job  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="archon"):
        task = asyncio.create_task(scheduler._run_job(job))
        scheduler._tasks.add(task)
        task.add_done_callback(scheduler._tasks.discard)
        task.add_done_callback(
            lambda t, name=job.name: _log_task_exception(t, name)
        )
        # Await without raising so callback fires
        try:
            await task
        except RuntimeError:
            pass
        await asyncio.sleep(0)

    assert any(
        "test_job" in r.message and r.levelno == logging.ERROR
        for r in caplog.records
    )


# ──────────────────────────────────────────────────────────────────
# asyncio.get_running_loop() — no DeprecationWarning
# ──────────────────────────────────────────────────────────────────


async def test_disable_invalid_job_uses_running_loop(tmp_path: pytest.TempPathFactory) -> None:
    """_disable_invalid_job must not raise when called from a running event loop."""
    job = _make_job()
    job.validation_error = "bad config"
    scheduler = _make_scheduler([job])
    scheduler._jobs_dir_base = tmp_path  # type: ignore[assignment]

    # No TOML file on disk — should be a safe no-op without warnings
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        await scheduler._disable_invalid_job(job)  # must not raise DeprecationWarning
