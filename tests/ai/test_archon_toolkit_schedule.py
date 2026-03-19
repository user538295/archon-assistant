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


# ──────────────────────────────────────────────────────────────────
# Task 5.5 — update_scheduled_task() tests
# ──────────────────────────────────────────────────────────────────


def _create_job_file(jobs_dir: Path, name: str, cron: str, prompt: str, enabled: bool = False, timeout_seconds: float = 60.0) -> Path:
    """Helper: write a minimal job.toml in jobs_dir/name/."""
    import tomli_w
    job_dir = jobs_dir / name
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "name": name,
        "cron": cron,
        "enabled": enabled,
        "timeout_seconds": timeout_seconds,
        "pipeline": {"run_prompt": prompt},
    }
    job_file = job_dir / "job.toml"
    job_file.write_text(tomli_w.dumps(doc))
    return job_file


class TestUpdateScheduledTask:
    async def test_update_scheduled_task_cron(self, tmp_path: Path) -> None:
        """update cron field — TOML should reflect the new cron."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "my-job", "*/5 * * * *", "Do stuff")
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "my-job", "cron": "0 */6 * * *"},
            user_id=42,
        )

        assert "my-job" in result or "updated" in result.lower()
        parsed = tomllib.loads((jobs_dir / "my-job" / "job.toml").read_text())
        assert parsed["cron"] == "0 */6 * * *"
        # Other fields unchanged
        assert parsed["pipeline"]["run_prompt"] == "Do stuff"
        scheduler.reload_jobs.assert_called_once()

    async def test_update_scheduled_task_enabled_false(self, tmp_path: Path) -> None:
        """Disable a job — enabled should be false in TOML."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "active-job", "*/5 * * * *", "Run", enabled=True)
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "active-job", "enabled": False},
            user_id=42,
        )

        assert "active-job" in result or "updated" in result.lower()
        parsed = tomllib.loads((jobs_dir / "active-job" / "job.toml").read_text())
        assert parsed["enabled"] is False
        scheduler.reload_jobs.assert_called_once()

    async def test_update_scheduled_task_not_found(self, tmp_path: Path) -> None:
        """Updating a nonexistent job returns an error."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "ghost-job", "cron": "*/5 * * * *"},
            user_id=42,
        )

        assert "not found" in result.lower() or "does not exist" in result.lower() or "ghost-job" in result
        scheduler.reload_jobs.assert_not_called()

    async def test_update_scheduled_task_partial(self, tmp_path: Path) -> None:
        """Update only prompt — cron must remain unchanged."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "partial-job", "0 9 * * *", "Original prompt")
        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "partial-job", "prompt": "New prompt"},
            user_id=42,
        )

        parsed = tomllib.loads((jobs_dir / "partial-job" / "job.toml").read_text())
        assert parsed["cron"] == "0 9 * * *"  # unchanged
        assert parsed["pipeline"]["run_prompt"] == "New prompt"

    async def test_update_scheduled_task_too_frequent_cron(self, tmp_path: Path) -> None:
        """Updating cron to '* * * * *' (every minute) is rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "rate-job", "*/5 * * * *", "Run me")
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "rate-job", "cron": "* * * * *"},
            user_id=42,
        )

        assert "5 minute" in result.lower() or "minimum" in result.lower() or "frequent" in result.lower()
        # TOML should be unchanged
        parsed = tomllib.loads((jobs_dir / "rate-job" / "job.toml").read_text())
        assert parsed["cron"] == "*/5 * * * *"
        scheduler.reload_jobs.assert_not_called()

    async def test_update_scheduled_task_via_mcp(self, tmp_path: Path) -> None:
        """update_scheduled_task is callable via the background ArchonMCPServer."""
        from aiohttp.test_utils import TestClient, TestServer
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "mcp-update-job", "*/5 * * * *", "Old prompt")

        bot = _make_bot()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        scheduler = _make_scheduler(jobs_dir)
        toolkit = ArchonToolkit(job_scheduler=scheduler, bot=bot)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18321, toolkit=toolkit,
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
                        "name": "update_scheduled_task",
                        "arguments": {
                            "name": "mcp-update-job",
                            "prompt": "Updated prompt via MCP",
                        },
                    },
                },
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            text = data["result"]["content"][0]["text"]
            assert "mcp-update-job" in text or "updated" in text.lower()

            parsed = tomllib.loads((jobs_dir / "mcp-update-job" / "job.toml").read_text())
            assert parsed["pipeline"]["run_prompt"] == "Updated prompt via MCP"
        finally:
            await client.close()

    async def test_update_scheduled_task_timeout_seconds(self, tmp_path: Path) -> None:
        """Update only timeout_seconds — cron must remain unchanged, reload called once."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "timeout-job", "*/5 * * * *", "Run something", timeout_seconds=60.0)
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "timeout-job", "timeout_seconds": 120.0},
            user_id=42,
        )

        assert "timeout-job" in result or "updated" in result.lower()
        parsed = tomllib.loads((jobs_dir / "timeout-job" / "job.toml").read_text())
        assert parsed["timeout_seconds"] == 120.0
        assert parsed["cron"] == "*/5 * * * *"  # unchanged
        scheduler.reload_jobs.assert_called_once()

    async def test_update_scheduled_task_no_fields_changed(self, tmp_path: Path) -> None:
        """Calling update with name only returns 'no fields changed' and does NOT reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "noop-job", "*/5 * * * *", "Same prompt")
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "noop-job"},
            user_id=42,
        )

        assert "no fields changed" in result.lower()
        scheduler.reload_jobs.assert_not_called()

    async def test_add_update_list_scheduled_task(self, tmp_path: Path) -> None:
        """E2E: add a job, update its cron, list shows updated cron."""
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
        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "e2e-update-job", "cron": "*/10 * * * *", "prompt": "Initial"},
            user_id=42,
        )

        # Update cron
        update_result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "e2e-update-job", "cron": "0 8 * * *"},
            user_id=42,
        )
        assert "e2e-update-job" in update_result or "updated" in update_result.lower()

        # List jobs — should show updated cron
        import json as _json
        list_result = await toolkit.call_tool("list_scheduled_tasks", {})
        jobs = _json.loads(list_result)
        job = next((j for j in jobs if j["name"] == "e2e-update-job"), None)
        assert job is not None, f"e2e-update-job not found in {jobs}"
        assert job["cron"] == "0 8 * * *"


# ──────────────────────────────────────────────────────────────────
# update_scheduled_task — invalid name validation
# ──────────────────────────────────────────────────────────────────


class TestUpdateScheduledTaskInvalidName:
    async def test_update_scheduled_task_path_traversal_rejected(self, tmp_path: Path) -> None:
        """Name containing '../' is rejected — no reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "../traversal", "cron": "*/5 * * * *"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()
        scheduler.reload_jobs.assert_not_called()

    async def test_update_scheduled_task_empty_name_rejected(self, tmp_path: Path) -> None:
        """Empty name is rejected — no reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "", "cron": "*/5 * * * *"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()
        scheduler.reload_jobs.assert_not_called()

    async def test_update_scheduled_task_too_long_name_rejected(self, tmp_path: Path) -> None:
        """51-char name is rejected — no reload."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        toolkit, scheduler = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool(
            "update_scheduled_task",
            {"name": "a" * 51, "cron": "*/5 * * * *"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()
        scheduler.reload_jobs.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Task 5.6 — remove_scheduled_task() tests
# ──────────────────────────────────────────────────────────────────


def _make_scheduler_with_statuses(
    jobs_dir: Path,
    statuses: dict | None = None,
) -> MagicMock:
    """Make a scheduler mock with optional job_statuses dict."""
    scheduler = _make_scheduler(jobs_dir)
    type(scheduler).job_statuses = PropertyMock(return_value=statuses or {})
    return scheduler


class TestRemoveScheduledTaskSuccess:
    async def test_remove_scheduled_task_success(self, tmp_path: Path) -> None:
        """remove_scheduled_task removes the job directory and calls reload_jobs."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "del-job", "*/5 * * * *", "Delete me")

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "del-job"},
            user_id=42,
        )

        assert "del-job" in result
        assert "removed" in result.lower()
        assert not (jobs_dir / "del-job").exists()
        scheduler.reload_jobs.assert_called_once()

    async def test_remove_scheduled_task_not_found(self, tmp_path: Path) -> None:
        """Removing a nonexistent job returns an error."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "ghost-job"},
            user_id=42,
        )

        assert "not found" in result.lower() or "ghost-job" in result
        scheduler.reload_jobs.assert_not_called()

    async def test_remove_scheduled_task_currently_running(self, tmp_path: Path) -> None:
        """Removing a running job is refused."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "running-job", "*/5 * * * *", "I am running")

        running_status = MagicMock()
        running_status.is_running = True
        scheduler = _make_scheduler_with_statuses(jobs_dir, {"running-job": running_status})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "running-job"},
            user_id=42,
        )

        assert "running" in result.lower() or "refused" in result.lower() or "cannot" in result.lower()
        # Directory should still exist
        assert (jobs_dir / "running-job").exists()
        scheduler.reload_jobs.assert_not_called()

    async def test_remove_scheduled_task_path_traversal(self, tmp_path: Path) -> None:
        """Name with path traversal chars fails name regex validation."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "../../etc"},
            user_id=42,
        )

        assert "invalid" in result.lower() or "name" in result.lower() or "error" in result.lower()
        scheduler.reload_jobs.assert_not_called()

    async def test_remove_scheduled_task_symlink_blocked(self, tmp_path: Path) -> None:
        """A symlink in jobs_dir is rejected — directory is not removed."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        # Create a real directory outside jobs_dir and symlink it in
        real_dir = tmp_path / "real-job-dir"
        real_dir.mkdir()
        (real_dir / "job.toml").write_text("[stub]")
        symlink = jobs_dir / "sym-job"
        symlink.symlink_to(real_dir)

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "sym-job"},
            user_id=42,
        )

        assert "symlink" in result.lower() or "rejected" in result.lower() or "invalid" in result.lower()
        # Real dir should still exist
        assert real_dir.exists()
        scheduler.reload_jobs.assert_not_called()

    async def test_remove_scheduled_task_nested_symlink_blocked(self, tmp_path: Path) -> None:
        """A symlink nested inside a subdirectory of the job dir is rejected."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        # Create a legitimate job directory with a subdirectory
        job_dir = jobs_dir / "nested-sym-job"
        job_dir.mkdir()
        subdir = job_dir / "subdir"
        subdir.mkdir()
        (job_dir / "job.toml").write_text("[job]\ncron = '*/5 * * * *'\nprompt = 'test'")

        # Put a symlink inside the subdirectory
        real_target = tmp_path / "secret"
        real_target.write_text("sensitive data")
        nested_symlink = subdir / "link_to_secret"
        nested_symlink.symlink_to(real_target)

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "nested-sym-job"},
            user_id=42,
        )

        assert "symlink" in result.lower() or "rejected" in result.lower() or "invalid" in result.lower()
        # Job directory should still exist
        assert job_dir.exists()
        # Real target should still exist
        assert real_target.exists()
        scheduler.reload_jobs.assert_not_called()

    async def test_remove_scheduled_task_rmtree_failure_still_reloads(self, tmp_path: Path) -> None:
        """reload_jobs() is called even when shutil.rmtree raises an exception."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "perm-job", "*/5 * * * *", "Permission denied job")

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        with patch("archon.ai.archon_toolkit.shutil.rmtree", side_effect=PermissionError("Permission denied")):
            result = await toolkit.call_tool(
                "remove_scheduled_task",
                {"name": "perm-job"},
                user_id=42,
            )

        assert "error" in result.lower() or "permission" in result.lower()
        # reload_jobs must still be called despite the failure
        scheduler.reload_jobs.assert_called_once()


class TestRemoveScheduledTaskViaMcp:
    async def test_remove_scheduled_task_via_mcp(self, tmp_path: Path) -> None:
        """remove_scheduled_task is callable via the background ArchonMCPServer."""
        from aiohttp.test_utils import TestClient, TestServer
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "mcp-del-job", "*/5 * * * *", "Delete via MCP")

        bot = _make_bot()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler, bot=bot)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18322, toolkit=toolkit,
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
                        "name": "remove_scheduled_task",
                        "arguments": {"name": "mcp-del-job"},
                    },
                },
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            text = data["result"]["content"][0]["text"]
            assert "mcp-del-job" in text or "removed" in text.lower()
            assert not (jobs_dir / "mcp-del-job").exists()
        finally:
            await client.close()


class TestAddRemoveListScheduledTask:
    async def test_add_remove_list_scheduled_task(self, tmp_path: Path) -> None:
        """E2E: add a job, remove it, list returns empty."""
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
        await toolkit.call_tool(
            "add_scheduled_task",
            {"name": "e2e-remove-job", "cron": "*/10 * * * *", "prompt": "E2E remove"},
            user_id=42,
        )

        # Remove the job
        remove_result = await toolkit.call_tool(
            "remove_scheduled_task",
            {"name": "e2e-remove-job"},
            user_id=42,
        )
        assert "e2e-remove-job" in remove_result or "removed" in remove_result.lower()
        assert not (jobs_dir / "e2e-remove-job").exists()

        # List jobs — should be empty
        list_result = await toolkit.call_tool("list_scheduled_tasks", {})
        assert list_result == "No scheduled jobs."


# ──────────────────────────────────────────────────────────────────
# Task 7.4 — get_job_config() tests
# ──────────────────────────────────────────────────────────────────


class TestGetJobConfigReturnsJson:
    async def test_get_job_config_returns_json(self, tmp_path: Path) -> None:
        """get_job_config returns all fields from a valid job.toml as JSON."""
        import json

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "my-job", "*/5 * * * *", "Daily report", enabled=False, timeout_seconds=90.0)

        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool("get_job_config", {"name": "my-job"})

        data = json.loads(result)
        assert data["name"] == "my-job"
        assert data["cron"] == "*/5 * * * *"
        assert data["enabled"] is False
        assert data["timeout_seconds"] == 90.0
        assert "pipeline" in data
        assert data["pipeline"]["run_prompt"] == "Daily report"


class TestGetJobConfigNotFound:
    async def test_get_job_config_not_found(self, tmp_path: Path) -> None:
        """get_job_config returns error message for a nonexistent job."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool("get_job_config", {"name": "ghost-job"})

        assert "not found" in result.lower() or "ghost-job" in result


class TestGetJobConfigInvalidName:
    async def test_get_job_config_invalid_name(self, tmp_path: Path) -> None:
        """get_job_config rejects names that fail the name regex."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        toolkit, _ = _make_toolkit(jobs_dir=jobs_dir)

        result = await toolkit.call_tool("get_job_config", {"name": "../etc"})

        assert "invalid" in result.lower() or "name" in result.lower()


class TestGetJobConfigPathTraversalBlocked:
    async def test_get_job_config_path_traversal_blocked(self, tmp_path: Path) -> None:
        """get_job_config rejects a job.toml that resolves outside jobs_dir."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        # Create a valid-named job dir but point job.toml to an outside file via monkeypatching
        # We simulate this by making the job dir a valid dir, then overriding Path.resolve
        # to return a path outside jobs_dir. Simpler: use a symlink for the job.toml itself.
        job_dir = jobs_dir / "safe-job"
        job_dir.mkdir()

        # Create an outside file and symlink job.toml to it
        outside_file = tmp_path / "outside.toml"
        outside_file.write_text('[job]\nname = "outside"')
        (job_dir / "job.toml").symlink_to(outside_file)

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("get_job_config", {"name": "safe-job"})

        # Either symlink rejection or path traversal rejection
        assert "symlink" in result.lower() or "invalid" in result.lower() or "rejected" in result.lower()


class TestGetJobConfigSymlinkBlocked:
    async def test_get_job_config_symlink_blocked(self, tmp_path: Path) -> None:
        """get_job_config rejects a job directory that is a symlink."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()

        # Create a real dir outside jobs_dir and symlink it in
        real_dir = tmp_path / "real-job-dir"
        real_dir.mkdir()
        (real_dir / "job.toml").write_text(
            'name = "sym-job"\ncron = "*/5 * * * *"\nenabled = false\n'
            '[pipeline]\nrun_prompt = "test"'
        )
        symlink = jobs_dir / "sym-job"
        symlink.symlink_to(real_dir)

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("get_job_config", {"name": "sym-job"})

        assert "symlink" in result.lower() or "rejected" in result.lower() or "invalid" in result.lower()


class TestGetJobConfigMissingScheduler:
    async def test_get_job_config_missing_scheduler(self, tmp_path: Path) -> None:
        """get_job_config raises RuntimeError when job_scheduler is not available."""
        toolkit = ArchonToolkit(job_scheduler=None)

        with pytest.raises(RuntimeError, match="job_scheduler not available"):
            await toolkit.call_tool("get_job_config", {"name": "any-job"})


class TestGetJobConfigInvalidToml:
    async def test_get_job_config_invalid_toml(self, tmp_path: Path) -> None:
        """get_job_config returns error message when job.toml contains invalid TOML."""
        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        job_dir = jobs_dir / "bad-job"
        job_dir.mkdir()
        (job_dir / "job.toml").write_text("this is not [ valid toml !!!")

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("get_job_config", {"name": "bad-job"})

        assert "failed to read" in result.lower() or "invalid" in result.lower()


class TestGetJobConfigViaMcp:
    async def test_get_job_config_via_mcp(self, tmp_path: Path) -> None:
        """get_job_config is callable via the background ArchonMCPServer and returns JSON."""
        import json
        from aiohttp.test_utils import TestClient, TestServer
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        jobs_dir = tmp_path / "schedules"
        jobs_dir.mkdir()
        _create_job_file(jobs_dir, "mcp-get-job", "*/5 * * * *", "MCP get test")

        bot = _make_bot()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        scheduler = _make_scheduler_with_statuses(jobs_dir, {})
        toolkit = ArchonToolkit(job_scheduler=scheduler, bot=bot)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18323, toolkit=toolkit,
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
                        "name": "get_job_config",
                        "arguments": {"name": "mcp-get-job"},
                    },
                },
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            text = data["result"]["content"][0]["text"]
            parsed = json.loads(text)
            assert parsed["name"] == "mcp-get-job"
            assert parsed["cron"] == "*/5 * * * *"
        finally:
            await client.close()


class TestAddThenGetJobConfig:
    async def test_add_then_get_job_config(self, tmp_path: Path) -> None:
        """E2E: add a job via add_scheduled_task, then get_job_config returns enabled=false and prompt."""
        import json
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
            {"name": "e2e-get-job", "cron": "*/10 * * * *", "prompt": "E2E get config test"},
            user_id=42,
        )
        assert "e2e-get-job" in add_result or "created" in add_result.lower()

        # get_job_config — should return enabled=false and matching prompt
        get_result = await toolkit.call_tool("get_job_config", {"name": "e2e-get-job"})
        data = json.loads(get_result)
        assert data["enabled"] is False
        assert data["pipeline"]["run_prompt"] == "E2E get config test"
