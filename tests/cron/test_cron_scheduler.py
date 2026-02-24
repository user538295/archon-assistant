"""Unit tests for CronScheduler — mocked subprocess and Claude session."""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from archon.ai.cron_scheduler import CronScheduler, JobStatus
from archon.config.loader import CronConfig, CronJobConfig, CronPipelineStep


# ── Helpers ───────────────────────────────────────────────────────


def _make_job(
    name: str = "test_job",
    schedule: str = "* * * * *",
    pipeline: list | None = None,
    notify_user_id: int | None = None,
    timeout_seconds: float = 30.0,
    enabled: bool = True,
) -> CronJobConfig:
    if pipeline is None:
        pipeline = [CronPipelineStep(tool="echo hello")]
    return CronJobConfig(
        name=name,
        schedule=schedule,
        pipeline=pipeline,
        notify_user_id=notify_user_id,
        timeout_seconds=timeout_seconds,
        enabled=enabled,
    )


def _make_config(*jobs: CronJobConfig, enabled: bool = True) -> CronConfig:
    return CronConfig(enabled=enabled, jobs=list(jobs))


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


# ── Start / Stop ──────────────────────────────────────────────────


class TestCronSchedulerLifecycle:
    async def test_start_disabled_creates_no_task(self) -> None:
        cfg = _make_config(_make_job(), enabled=False)
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler.start()
        assert scheduler._task is None

    async def test_start_enabled_creates_task(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())

        async def _noop_loop() -> None:
            await asyncio.sleep(999)

        with patch.object(scheduler, "_loop", side_effect=_noop_loop):
            await scheduler.start()
        assert scheduler._task is not None
        scheduler._task.cancel()
        try:
            await scheduler._task
        except asyncio.CancelledError:
            pass

    async def test_stop_cancels_running_task(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())

        async def _slow_loop() -> None:
            await asyncio.sleep(999)

        with patch.object(scheduler, "_loop", side_effect=_slow_loop):
            await scheduler.start()
        await scheduler.stop()
        assert scheduler._task is not None and scheduler._task.done()

    async def test_stop_when_not_started_is_safe(self) -> None:
        cfg = _make_config(_make_job(), enabled=False)
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler.start()  # no-op (disabled)
        await scheduler.stop()  # should not raise


# ── _should_fire ──────────────────────────────────────────────────


class TestShouldFire:
    def test_should_fire_true_when_cron_matches_now(self) -> None:
        """'* * * * *' fires every minute — should be True within 60s of prev tick."""
        cfg = _make_config(_make_job(schedule="* * * * *"))
        scheduler = CronScheduler(cfg, _make_bot())
        test_time = datetime(2025, 1, 1, 12, 0, 5)  # 5 seconds after minute boundary
        assert scheduler._should_fire(cfg.jobs[0], test_time) is True

    def test_should_fire_false_for_non_matching_expression(self) -> None:
        """'0 0 1 1 *' (Jan 1 midnight) should not fire on a random summer day."""
        cfg = _make_config(_make_job(schedule="0 0 1 1 *"))
        scheduler = CronScheduler(cfg, _make_bot())
        test_time = datetime(2025, 6, 15, 12, 30, 0)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is False

    def test_should_fire_false_when_already_fired_this_slot(self) -> None:
        """Should not fire again if last_fire_at >= previous cron slot."""
        cfg = _make_config(_make_job(schedule="* * * * *"))
        scheduler = CronScheduler(cfg, _make_bot())
        test_time = datetime(2025, 1, 1, 12, 0, 5)
        # Simulate already having fired at 12:00:03
        scheduler._statuses["test_job"].last_fire_at = datetime(2025, 1, 1, 12, 0, 3)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is False

    def test_should_fire_invalid_expression_returns_false(self) -> None:
        """Bad cron expressions must not crash — return False."""
        cfg = _make_config(_make_job(schedule="not_a_cron"))
        scheduler = CronScheduler(cfg, _make_bot())
        assert scheduler._should_fire(cfg.jobs[0], datetime.now()) is False

    def test_should_fire_true_after_different_slot(self) -> None:
        """Fire again in the next minute even if already fired in the previous minute."""
        cfg = _make_config(_make_job(schedule="* * * * *"))
        scheduler = CronScheduler(cfg, _make_bot())
        # last_fire_at was in the previous minute (12:01:xx), now it's 12:02:05
        scheduler._statuses["test_job"].last_fire_at = datetime(2025, 1, 1, 12, 1, 30)
        test_time = datetime(2025, 1, 1, 12, 2, 5)
        assert scheduler._should_fire(cfg.jobs[0], test_time) is True


# ── _run_tool ─────────────────────────────────────────────────────


class TestRunTool:
    async def test_run_tool_returns_stdout(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(tool="echo hello")
        result = await scheduler._run_tool(step, "", timeout=10.0)
        assert result == "hello"

    async def test_run_tool_passes_stdin_to_process(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(tool="cat")
        result = await scheduler._run_tool(step, "piped_input", timeout=10.0)
        assert result == "piped_input"

    async def test_run_tool_timeout_raises_runtime_error(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(tool="sleep 999")
        with pytest.raises(RuntimeError, match="timed out"):
            await scheduler._run_tool(step, "", timeout=0.05)

    async def test_run_tool_nonzero_exit_raises_runtime_error(self) -> None:
        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(tool="bash -c 'exit 1'")
        with pytest.raises(RuntimeError, match="exit 1"):
            await scheduler._run_tool(step, "", timeout=10.0)


# ── _run_prompt ───────────────────────────────────────────────────


class TestRunPrompt:
    async def test_run_prompt_returns_response_text(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(prompt="Say: {input}")

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield Response(content="mocked response")

        mock_session.send = _mock_send

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            result = await scheduler._run_prompt(step, "world", timeout=30.0)
        assert result == "mocked response"

    async def test_run_prompt_replaces_input_placeholder(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(prompt="Summarize: {input}")
        captured: list[str] = []

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            captured.append(prompt)
            yield Response(content="summary")

        mock_session.send = _mock_send

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_prompt(step, "hello world", timeout=30.0)
        assert captured == ["Summarize: hello world"]

    async def test_run_prompt_stops_session_on_completion(self) -> None:
        from archon.ai.event_mapper import Response

        cfg = _make_config(_make_job())
        scheduler = CronScheduler(cfg, _make_bot())
        step = CronPipelineStep(prompt="test")

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()

        async def _mock_send(prompt: str):  # type: ignore[return]
            yield Response(content="ok")

        mock_session.send = _mock_send

        with patch("archon.ai.cron_scheduler.ClaudeSession", return_value=mock_session):
            await scheduler._run_prompt(step, "", timeout=30.0)
        mock_session.stop.assert_awaited_once()


# ── _run_job ──────────────────────────────────────────────────────


class TestRunJob:
    async def test_run_job_success_notifies_user(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(tool="echo done")], notify_user_id=99)
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        bot.send_message.assert_awaited_once()
        # First positional arg is chat_id
        assert bot.send_message.call_args[0][0] == 99
        # Message contains the output
        msg = bot.send_message.call_args[0][1]
        assert "done" in msg

    async def test_run_job_no_notify_user_skips_send(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(tool="echo test")], notify_user_id=None)
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        bot.send_message.assert_not_awaited()

    async def test_run_job_updates_status(self) -> None:
        job = _make_job(pipeline=[CronPipelineStep(tool="echo result")])
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.run_count == 1
        assert status.last_result == "result"
        assert status.last_error is None
        assert status.last_run is not None
        assert status.is_running is False

    async def test_run_job_failure_sets_last_error(self) -> None:
        bot = _make_bot()
        job = _make_job(
            pipeline=[CronPipelineStep(tool="bash -c 'exit 1'")],
            notify_user_id=99,
        )
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        status = scheduler.job_statuses[job.name]
        assert status.last_error is not None
        assert status.last_result is None

    async def test_run_job_failure_sends_error_notification(self) -> None:
        bot = _make_bot()
        job = _make_job(
            pipeline=[CronPipelineStep(tool="bash -c 'exit 1'")],
            notify_user_id=99,
        )
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        bot.send_message.assert_awaited_once()
        msg = bot.send_message.call_args[0][1]
        assert "❌" in msg

    async def test_run_job_skips_when_already_running(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(tool="echo test")])
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, bot)
        scheduler._statuses[job.name].is_running = True
        await scheduler._run_job(job)
        # run_count must still be 0 (skipped)
        assert scheduler.job_statuses[job.name].run_count == 0

    async def test_run_job_resets_is_running_after_completion(self) -> None:
        job = _make_job(pipeline=[CronPipelineStep(tool="echo ok")])
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].is_running is False

    async def test_run_job_run_count_increments_on_each_call(self) -> None:
        job = _make_job(pipeline=[CronPipelineStep(tool="echo run")])
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, _make_bot())
        await scheduler._run_job(job)
        await scheduler._run_job(job)
        assert scheduler.job_statuses[job.name].run_count == 2

    async def test_run_job_notification_contains_check_mark(self) -> None:
        bot = _make_bot()
        job = _make_job(pipeline=[CronPipelineStep(tool="echo success")], notify_user_id=1)
        cfg = _make_config(job)
        scheduler = CronScheduler(cfg, bot)
        await scheduler._run_job(job)
        msg = bot.send_message.call_args[0][1]
        assert "✅" in msg


# ── job_statuses ──────────────────────────────────────────────────


class TestJobStatuses:
    def test_job_statuses_returns_copy(self) -> None:
        cfg = _make_config(_make_job(name="j1"))
        scheduler = CronScheduler(cfg, _make_bot())
        statuses = scheduler.job_statuses
        statuses["j1"] = JobStatus(name="mutated")
        # Internal dict unchanged
        assert scheduler._statuses["j1"].name == "j1"

    def test_initial_statuses_all_zeroed(self) -> None:
        cfg = _make_config(_make_job(name="alpha"), _make_job(name="beta"))
        scheduler = CronScheduler(cfg, _make_bot())
        for name in ("alpha", "beta"):
            s = scheduler.job_statuses[name]
            assert s.run_count == 0
            assert s.last_run is None
            assert s.is_running is False
            assert s.last_fire_at is None


# ── reload_jobs ────────────────────────────────────────────────────


class TestReloadJobs:
    """Tests for CronScheduler.reload_jobs()."""

    def test_reload_noop_when_no_jobs_dir_base(self) -> None:
        """reload_jobs() is a no-op when jobs_dir_base is not set."""
        cfg = _make_config(_make_job(name="original"))
        scheduler = CronScheduler(cfg, _make_bot())  # no jobs_dir_base
        # Mark the status so we can detect if it survived the call
        scheduler._statuses["original"].run_count = 7
        scheduler.reload_jobs()
        assert "original" in scheduler._statuses
        assert scheduler._statuses["original"].run_count == 7

    def test_reload_picks_up_new_job_from_disk(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A new .toml file written after construction is discovered on reload."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        # Write a new job file
        (jobs_dir / "new-job.toml").write_text(
            'schedule = "0 9 * * *"\n[[pipeline]]\ntool = "echo hi"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = CronScheduler(cfg, _make_bot(), jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert len(scheduler._config.jobs) == 1
        assert scheduler._config.jobs[0].name == "new-job"
        assert "new-job" in scheduler._statuses

    def test_reload_preserves_runtime_status_for_existing_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """Runtime status (last_run, run_count) survives a reload for jobs still on disk."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        (jobs_dir / "stable.toml").write_text(
            'schedule = "* * * * *"\n[[pipeline]]\ntool = "echo ok"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = CronScheduler(cfg, _make_bot(), jobs_dir_base=tmp_path)
        scheduler.reload_jobs()  # first load
        # Simulate some runtime state
        scheduler._statuses["stable"].run_count = 5
        scheduler._statuses["stable"].last_run = datetime(2025, 1, 1, 8, 0, 0)
        scheduler.reload_jobs()  # second reload — same file on disk
        s = scheduler._statuses["stable"]
        assert s.run_count == 5
        assert s.last_run == datetime(2025, 1, 1, 8, 0, 0)

    def test_reload_removes_deleted_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """A job whose .toml file is deleted is removed from statuses on reload."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "gone.toml"
        toml_file.write_text(
            'schedule = "0 6 * * *"\n[[pipeline]]\ntool = "echo bye"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = CronScheduler(cfg, _make_bot(), jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert "gone" in scheduler._statuses
        # Now remove the file and reload again
        toml_file.unlink()
        scheduler.reload_jobs()
        assert "gone" not in scheduler._statuses
        assert scheduler._config.jobs == []

    def test_reload_updates_schedule_for_existing_job(self, tmp_path: "pytest.TempPathFactory") -> None:  # type: ignore[name-defined]
        """Changing the schedule in a .toml file is reflected after reload."""
        jobs_dir = tmp_path / "cron.d"
        jobs_dir.mkdir()
        toml_file = jobs_dir / "daily.toml"
        toml_file.write_text(
            'schedule = "0 8 * * *"\n[[pipeline]]\ntool = "echo morning"\n'
        )
        cfg = CronConfig(enabled=True, jobs=[], jobs_dir="cron.d")
        scheduler = CronScheduler(cfg, _make_bot(), jobs_dir_base=tmp_path)
        scheduler.reload_jobs()
        assert scheduler._config.jobs[0].schedule == "0 8 * * *"
        # Change the schedule on disk
        toml_file.write_text(
            'schedule = "0 20 * * *"\n[[pipeline]]\ntool = "echo evening"\n'
        )
        scheduler.reload_jobs()
        assert scheduler._config.jobs[0].schedule == "0 20 * * *"
