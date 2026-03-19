"""Tests for add_scheduled_task() tool — Task 5.4."""
import tomllib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from archon.ai.archon_toolkit import ArchonToolkit


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _make_scheduler(
    jobs_dir: Path,
    num_jobs: int = 0,
) -> MagicMock:
    scheduler = MagicMock()
    scheduler.reload_jobs = MagicMock()
    type(scheduler).jobs_dir = PropertyMock(return_value=str(jobs_dir))

    # job_configs returns a list of mock configs
    fake_jobs = [MagicMock() for _ in range(num_jobs)]
    type(scheduler).job_configs = PropertyMock(return_value=fake_jobs)

    return scheduler


def _make_toolkit(
    *,
    jobs_dir: Path,
    num_jobs: int = 0,
    bot: MagicMock | None = None,
    user_id: int = 42,
) -> tuple[ArchonToolkit, MagicMock]:
    scheduler = _make_scheduler(jobs_dir, num_jobs)
    if bot is None:
        bot = _make_bot()
    toolkit = ArchonToolkit(job_scheduler=scheduler, bot=bot)
    return toolkit, scheduler


# ──────────────────────────────────────────────────────────────────
# Happy path
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskCreatesToml:
    async def test_add_scheduled_task_creates_toml(self, tmp_path: Path) -> None:
        """add_scheduled_task creates a TOML file in jobs_dir/name/job.toml."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {
                "name": "my-job",
                "cron": "*/5 * * * *",
                "prompt": "Run the daily report",
                "timeout_seconds": 90.0,
            },
            user_id=42,
        )

        job_file = jobs_dir / "my-job" / "job.toml"
        assert job_file.exists(), f"Expected {job_file} to exist"

        parsed = tomllib.loads(job_file.read_text())
        assert parsed["name"] == "my-job"
        assert parsed["cron"] == "*/5 * * * *"
        assert parsed["enabled"] is False
        assert parsed["timeout_seconds"] == 90.0
        # Pipeline section exists with a prompt step
        assert "pipeline" in parsed
        pipeline = parsed["pipeline"]
        assert isinstance(pipeline, dict)
        # Exactly one prompt step — value must match the prompt
        prompt_keys = [k for k in pipeline if k.endswith("_prompt")]
        assert len(prompt_keys) == 1
        assert pipeline[prompt_keys[0]] == "Run the daily report"

        assert "created" in result.lower() or "my-job" in result

    async def test_add_scheduled_task_enabled_false_by_default(self, tmp_path: Path) -> None:
        """Jobs are created as disabled (enabled = false)."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "check-job", "cron": "0 */6 * * *", "prompt": "Check status"},
            user_id=42,
        )

        job_file = jobs_dir / "check-job" / "job.toml"
        parsed = tomllib.loads(job_file.read_text())
        assert parsed["enabled"] is False

    async def test_add_scheduled_task_default_timeout(self, tmp_path: Path) -> None:
        """Default timeout_seconds is 60.0 when not specified."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "quick-job", "cron": "0 9 * * *", "prompt": "Quick task"},
            user_id=42,
        )

        job_file = jobs_dir / "quick-job" / "job.toml"
        parsed = tomllib.loads(job_file.read_text())
        assert parsed["timeout_seconds"] == 60.0


# ──────────────────────────────────────────────────────────────────
# Validation — cron
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskInvalidCron:
    async def test_add_scheduled_task_invalid_cron(self, tmp_path: Path) -> None:
        """Invalid cron expression is rejected with an error message."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "bad-job", "cron": "not a cron", "prompt": "Do stuff"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "error" in result.lower() or "cron" in result.lower()
        # No file should be created
        assert not (jobs_dir / "bad-job").exists()

    async def test_add_scheduled_task_too_frequent_cron(self, tmp_path: Path) -> None:
        """Cron expression more frequent than every 5 minutes is rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "freq-job", "cron": "* * * * *", "prompt": "Too fast"},
            user_id=42,
        )

        assert "5 minute" in result.lower() or "minimum" in result.lower() or "frequent" in result.lower()
        assert not (jobs_dir / "freq-job").exists()

    async def test_add_scheduled_task_2_minute_cron_rejected(self, tmp_path: Path) -> None:
        """Cron running every 2 minutes is also rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "two-min-job", "cron": "*/2 * * * *", "prompt": "Too frequent"},
            user_id=42,
        )

        assert "5 minute" in result.lower() or "minimum" in result.lower() or "frequent" in result.lower()
        assert not (jobs_dir / "two-min-job").exists()

    async def test_add_scheduled_task_exactly_5_minute_accepted(self, tmp_path: Path) -> None:
        """Cron running every 5 minutes is accepted."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "five-min-job", "cron": "*/5 * * * *", "prompt": "Five mins"},
            user_id=42,
        )

        assert (jobs_dir / "five-min-job" / "job.toml").exists()


# ──────────────────────────────────────────────────────────────────
# Validation — name
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskInvalidName:
    async def test_add_scheduled_task_invalid_name_path_traversal(self, tmp_path: Path) -> None:
        """Name containing path separators is rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "../etc", "cron": "*/5 * * * *", "prompt": "Do stuff"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()
        # Ensure no file was created outside the jobs dir
        assert not (tmp_path / "etc").exists()

    async def test_add_scheduled_task_invalid_name_spaces(self, tmp_path: Path) -> None:
        """Name with spaces is rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "my job", "cron": "*/5 * * * *", "prompt": "Do stuff"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()

    async def test_add_scheduled_task_invalid_name_too_long(self, tmp_path: Path) -> None:
        """Name exceeding 50 chars is rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        long_name = "a" * 51
        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": long_name, "cron": "*/5 * * * *", "prompt": "Do stuff"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()

    async def test_add_scheduled_task_valid_name_chars(self, tmp_path: Path) -> None:
        """Name with alphanumeric, underscores, hyphens is accepted."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "my_job-123", "cron": "*/5 * * * *", "prompt": "Valid name"},
            user_id=42,
        )

        assert (jobs_dir / "my_job-123" / "job.toml").exists()


# ──────────────────────────────────────────────────────────────────
# Duplicate detection
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskDuplicate:
    async def test_add_scheduled_task_duplicate_name(self, tmp_path: Path) -> None:
        """Creating a job with an existing name returns an error (no overwrite)."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        # Create first time
        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "dup-job", "cron": "*/5 * * * *", "prompt": "First"},
            user_id=42,
        )

        # Attempt second creation
        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "dup-job", "cron": "0 9 * * *", "prompt": "Second"},
            user_id=42,
        )

        assert "already exists" in result.lower() or "exists" in result.lower() or "duplicate" in result.lower()

        # Original file should be unchanged (still has "First" prompt)
        job_file = jobs_dir / "dup-job" / "job.toml"
        parsed = tomllib.loads(job_file.read_text())
        pipeline = parsed["pipeline"]
        prompt_values = list(pipeline.values())
        assert "First" in prompt_values[0]


# ──────────────────────────────────────────────────────────────────
# Max jobs limit
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskMaxJobs:
    async def test_add_scheduled_task_max_jobs_exceeded(self, tmp_path: Path) -> None:
        """Rejected when 20 or more jobs already exist."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir, num_jobs=20)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "overflow-job", "cron": "*/5 * * * *", "prompt": "Too many"},
            user_id=42,
        )

        assert "limit" in result.lower() or "maximum" in result.lower() or "max" in result.lower()
        assert not (jobs_dir / "overflow-job").exists()

    async def test_add_scheduled_task_19_jobs_allowed(self, tmp_path: Path) -> None:
        """With 19 existing jobs, a 20th can still be added."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir, num_jobs=19)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "job-20", "cron": "*/5 * * * *", "prompt": "19 existing"},
            user_id=42,
        )

        assert (jobs_dir / "job-20" / "job.toml").exists()


# ──────────────────────────────────────────────────────────────────
# Reload and notification
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskTriggersReload:
    async def test_add_scheduled_task_triggers_reload(self, tmp_path: Path) -> None:
        """reload_jobs() is called after successfully creating the job file."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "reload-job", "cron": "*/5 * * * *", "prompt": "Reload test"},
            user_id=42,
        )

        scheduler.reload_jobs.assert_called_once()

    async def test_add_scheduled_task_no_reload_on_error(self, tmp_path: Path) -> None:
        """reload_jobs() is NOT called when validation fails."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "bad name!", "cron": "not a cron", "prompt": "Fail"},
            user_id=42,
        )

        scheduler.reload_jobs.assert_not_called()


class TestAddScheduledTaskSendsNotification:
    async def test_add_scheduled_task_sends_notification(self, tmp_path: Path) -> None:
        """A Telegram notification is sent to the user after job creation."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        bot = _make_bot()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir, bot=bot)

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "notify-job", "cron": "*/5 * * * *", "prompt": "Notify test"},
            user_id=99,
        )

        bot.send_message.assert_called_once()
        call_kwargs = bot.send_message.call_args
        # The chat_id should match the user_id
        assert call_kwargs.kwargs.get("chat_id") == 99 or call_kwargs.args[0] == 99
        # Message should mention the job name
        msg = call_kwargs.kwargs.get("text", "") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else "")
        assert "notify-job" in msg

    async def test_add_scheduled_task_no_notification_on_error(self, tmp_path: Path) -> None:
        """No Telegram notification is sent when the tool returns an error."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        bot = _make_bot()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir, bot=bot)

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "bad name!", "cron": "not a cron", "prompt": "Fail"},
            user_id=42,
        )

        bot.send_message.assert_not_called()

    async def test_add_scheduled_task_sends_notification_no_bot(self, tmp_path: Path) -> None:
        """Tool succeeds even if bot is None (no notification sent)."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        scheduler = _make_scheduler(jobs_dir)
        toolkit = ArchonToolkit(job_scheduler=scheduler, bot=None)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "no-bot-job", "cron": "*/5 * * * *", "prompt": "No bot"},
            user_id=42,
        )

        assert (jobs_dir / "no-bot-job" / "job.toml").exists()
        assert "created" in result.lower() or "no-bot-job" in result


# ──────────────────────────────────────────────────────────────────
# TOML injection safety
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskTomlInjectionSafe:
    async def test_add_scheduled_task_toml_injection_safe(self, tmp_path: Path) -> None:
        """Prompt containing TOML special chars is safely serialized."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        evil_prompt = 'Say hello\n[injected]\nkey = "value"\nmalicious = true'

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "inject-job", "cron": "*/5 * * * *", "prompt": evil_prompt},
            user_id=42,
        )

        job_file = jobs_dir / "inject-job" / "job.toml"
        assert job_file.exists()
        # Must parse correctly — no injected sections
        parsed = tomllib.loads(job_file.read_text())
        assert "injected" not in parsed
        assert "malicious" not in parsed
        # The prompt value must round-trip correctly
        pipeline = parsed["pipeline"]
        prompt_values = list(pipeline.values())
        assert prompt_values[0] == evil_prompt

    async def test_add_scheduled_task_triple_quote_injection_safe(self, tmp_path: Path) -> None:
        """Prompt with triple quotes is safely serialized."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        tricky_prompt = 'Hello """ world """ and [fake_section]'

        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "quote-job", "cron": "*/5 * * * *", "prompt": tricky_prompt},
            user_id=42,
        )

        job_file = jobs_dir / "quote-job" / "job.toml"
        assert job_file.exists()
        parsed = tomllib.loads(job_file.read_text())
        pipeline = parsed["pipeline"]
        prompt_values = list(pipeline.values())
        assert prompt_values[0] == tricky_prompt


# ──────────────────────────────────────────────────────────────────
# MCP integration
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskViaMcp:
    async def test_add_scheduled_task_via_mcp(self, tmp_path: Path) -> None:
        """add_scheduled_task is callable via the background ArchonMCPServer."""
        from aiohttp.test_utils import TestClient, TestServer
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        bot = _make_bot()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        scheduler = _make_scheduler(jobs_dir)
        toolkit = ArchonToolkit(job_scheduler=scheduler, bot=bot)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18320, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "add_scheduled_task",
                        "arguments": {
                            "name": "mcp-job",
                            "cron": "*/5 * * * *",
                            "prompt": "MCP test prompt",
                        },
                    },
                },
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            text = data["result"]["content"][0]["text"]
            assert "mcp-job" in text or "created" in text.lower()

            # File should exist
            assert (jobs_dir / "mcp-job" / "job.toml").exists()
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E — add then list with real JobScheduler
# ──────────────────────────────────────────────────────────────────


class TestAddThenListScheduledTask:
    async def test_add_then_list_scheduled_task(self, tmp_path: Path) -> None:
        """E2E: add a job with real JobScheduler, list shows it with enabled=False."""
        from archon.ai.job_scheduler import JobScheduler
        from archon.config.loader import ScheduleConfig

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        bot = _make_bot()
        schedule_config = ScheduleConfig(enabled=True, jobs=[], jobs_dir=str(jobs_dir))
        scheduler = JobScheduler(
            config=schedule_config,
            bot=bot,
            allowed_user_ids=[],
            jobs_dir_base=tmp_path,
        )

        toolkit = ArchonToolkit(job_scheduler=scheduler, bot=bot)

        # Add the job
        add_result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "e2e-job", "cron": "*/10 * * * *", "prompt": "E2E test"},
            user_id=42,
        )

        assert "e2e-job" in add_result or "created" in add_result.lower()

        # List jobs — should include the new job
        list_result = await toolkit.call_tool("list_scheduled_tasks", {})

        import json
        jobs = json.loads(list_result)
        assert isinstance(jobs, list)
        job = next((j for j in jobs if j["name"] == "e2e-job"), None)
        assert job is not None, f"e2e-job not found in {jobs}"
        assert job["enabled"] is False
        assert job["cron"] == "*/10 * * * *"


# ──────────────────────────────────────────────────────────────────
# Security fix: non-uniform cron schedule bypass
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskNonUniformCron:
    async def test_add_scheduled_task_too_frequent_cron_nonuniform(self, tmp_path: Path) -> None:
        """Non-uniform cron '0,3 * * * *' (3-min minimum gap) is rejected.

        A 2-point interval check would incorrectly pass this because the
        interval between the 1st and 2nd upcoming runs can be 57 minutes
        when sampled near :00. The fix checks the minimum across 10 pairs.
        """
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "nonuniform-job", "cron": "0,3 * * * *", "prompt": "Too fast"},
            user_id=42,
        )

        assert "5 minute" in result.lower() or "minimum" in result.lower() or "frequent" in result.lower()
        assert not (jobs_dir / "nonuniform-job").exists()


# ──────────────────────────────────────────────────────────────────
# Security fix: empty directory cleanup on write failure
# ──────────────────────────────────────────────────────────────────


class TestAddScheduledTaskWriteFailureCleanup:
    async def test_add_scheduled_task_write_failure_cleanup(self, tmp_path: Path) -> None:
        """When atomic_write raises, the job directory is cleaned up.

        Without this fix an OSError during write would leave an empty
        {jobs_dir}/{name}/ directory, causing subsequent calls with the
        same name to fail with 'already exists' — blocking recovery.
        """
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        # call_tool re-raises handler exceptions after logging them
        with patch("archon.ai.archon_toolkit.atomic_write", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                await toolkit.call_tool(
                    "add_scheduled_task",
                    {"name": "fail-job", "cron": "*/5 * * * *", "prompt": "Will fail"},
                    user_id=42,
                )

        # The directory must have been removed by the cleanup code in the handler
        job_dir = jobs_dir / "fail-job"
        assert not job_dir.exists(), (
            f"Expected {job_dir} to be cleaned up after write failure, but it still exists"
        )
