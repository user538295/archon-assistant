"""Tests for get_model and set_model tools — Task 5.1."""
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_toolkit import ArchonToolkit


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_session_manager(model: str = "claude-sonnet-4-6") -> MagicMock:
    sm = MagicMock()
    sm.get_model.return_value = model
    sm.set_model = MagicMock()
    return sm


def _make_config(
    default: str = "claude-sonnet-4-6",
    available: list[str] | None = None,
) -> MagicMock:
    if available is None:
        available = ["claude-sonnet-4-6", "claude-haiku-4-5"]
    config = MagicMock()
    config.models.default = default
    config.models.available = available
    return config


def _make_toolkit(
    *,
    session_manager: object | None = None,
    config: object | None = None,
) -> ArchonToolkit:
    return ArchonToolkit(session_manager=session_manager, config=config)


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# ──────────────────────────────────────────────────────────────────
# Unit tests — get_model
# ──────────────────────────────────────────────────────────────────


class TestGetModelReturnsCurrent:
    async def test_get_model_returns_current(self) -> None:
        """get_model returns the model from session_manager."""
        sm = _make_session_manager(model="claude-haiku-4-5")
        toolkit = _make_toolkit(session_manager=sm)

        result = await toolkit.call_tool("get_model", {})

        sm.get_model.assert_called_once()
        assert result == "claude-haiku-4-5"

    async def test_get_model_falls_back_to_config_when_no_session_manager(self) -> None:
        """get_model falls back to config.models.default when session_manager is None."""
        config = _make_config(default="claude-sonnet-4-6")
        toolkit = _make_toolkit(session_manager=None, config=config)

        result = await toolkit.call_tool("get_model", {})

        assert result == "claude-sonnet-4-6"

    async def test_get_model_falls_back_to_config_when_session_returns_none(self) -> None:
        """get_model falls back to config.models.default when session_manager.get_model() returns None."""
        sm = MagicMock()
        sm.get_model.return_value = None
        config = _make_config(default="claude-sonnet-4-6")
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("get_model", {})

        assert result == "claude-sonnet-4-6"

    async def test_get_model_raises_when_neither_available(self) -> None:
        """get_model raises RuntimeError when both session_manager and config are None."""
        toolkit = _make_toolkit(session_manager=None, config=None)

        with pytest.raises(RuntimeError):
            await toolkit.call_tool("get_model", {})


# ──────────────────────────────────────────────────────────────────
# Unit tests — set_model
# ──────────────────────────────────────────────────────────────────


class TestSetModelValid:
    async def test_set_model_valid(self) -> None:
        """set_model with a known model calls session_manager.set_model and returns success."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("set_model", {"model": "claude-haiku-4-5"})

        sm.set_model.assert_called_once_with("claude-haiku-4-5")
        assert result == "Model set to claude-haiku-4-5."

    async def test_set_model_returns_confirmation_message(self) -> None:
        """set_model returns 'Model set to <model>.' on success."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("set_model", {"model": "claude-sonnet-4-6"})

        assert result == "Model set to claude-sonnet-4-6."


class TestSetModelInvalid:
    async def test_set_model_invalid(self) -> None:
        """set_model with unknown model returns error listing available models."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        result = await toolkit.call_tool("set_model", {"model": "gpt-4"})

        # Should NOT call set_model on session_manager
        sm.set_model.assert_not_called()
        assert "gpt-4" in result or "invalid" in result.lower()
        assert "claude-sonnet-4-6" in result
        assert "claude-haiku-4-5" in result

    async def test_set_model_invalid_does_not_change_model(self) -> None:
        """set_model with invalid model does not mutate session_manager."""
        sm = _make_session_manager(model="claude-sonnet-4-6")
        config = _make_config(available=["claude-sonnet-4-6"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        await toolkit.call_tool("set_model", {"model": "unknown-model"})

        sm.set_model.assert_not_called()


class TestSetModelMissingDeps:
    async def test_set_model_raises_without_config(self) -> None:
        """set_model raises RuntimeError when config is not available."""
        sm = _make_session_manager()
        toolkit = _make_toolkit(session_manager=sm, config=None)

        with pytest.raises(RuntimeError):
            await toolkit.call_tool("set_model", {"model": "claude-sonnet-4-6"})

    async def test_set_model_raises_without_session_manager(self) -> None:
        """set_model raises RuntimeError when session_manager is not available."""
        config = _make_config()
        toolkit = _make_toolkit(session_manager=None, config=config)

        with pytest.raises(RuntimeError):
            await toolkit.call_tool("set_model", {"model": "claude-sonnet-4-6"})


# ──────────────────────────────────────────────────────────────────
# Integration — both MCP servers expose get_model
# ──────────────────────────────────────────────────────────────────


class TestGetModelViaBothMcp:
    async def test_get_model_via_background_mcp(self) -> None:
        """get_model is callable via the background ArchonMCPServer."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        sm = _make_session_manager(model="claude-haiku-4-5")
        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18310, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/call", {"name": "get_model", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "claude-haiku-4-5" in data["result"]["content"][0]["text"]
        finally:
            await client.close()

    async def test_get_model_via_router_mcp(self, tmp_path) -> None:
        """get_model is callable via ArchonRouterMCPServer."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        sm = _make_session_manager(model="claude-sonnet-4-6")
        toolkit = _make_toolkit(session_manager=sm)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
            allowed_tools=frozenset(toolkit.tool_names),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "get_model", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "claude-sonnet-4-6" in data["result"]["content"][0]["text"]
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# Integration — set_model via MCP server
# ──────────────────────────────────────────────────────────────────


class TestSetModelViaMcp:
    async def test_set_model_via_mcp(self) -> None:
        """set_model is callable via the background MCP server and changes model."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        sm = _make_session_manager(model="claude-sonnet-4-6")
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18311, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "set_model", "arguments": {"model": "claude-haiku-4-5"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "claude-haiku-4-5" in data["result"]["content"][0]["text"]
            sm.set_model.assert_called_once_with("claude-haiku-4-5")
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E — roundtrip: set_model then get_model, verify archon_status
# ──────────────────────────────────────────────────────────────────


class TestSetModelThenGetModelRoundtrip:
    async def test_set_model_then_get_model_roundtrip(self) -> None:
        """E2E: set model, get model, assert match; archon_status also reflects change."""
        sm = _make_session_manager(model="claude-sonnet-4-6")
        config = _make_config(
            default="claude-sonnet-4-6",
            available=["claude-sonnet-4-6", "claude-haiku-4-5"],
        )
        # archon_status also reads notifications.mode — ensure it's JSON-serializable
        config.notifications.mode = "normal"

        # Simulate set_model mutating the return value of get_model
        new_model: list[str] = ["claude-sonnet-4-6"]

        def _get_model() -> str:
            return new_model[0]

        def _set_model(model: str) -> None:
            new_model[0] = model

        sm.get_model.side_effect = _get_model
        sm.set_model.side_effect = _set_model

        toolkit = _make_toolkit(session_manager=sm, config=config)

        # Set model
        set_result = await toolkit.call_tool("set_model", {"model": "claude-haiku-4-5"})
        assert set_result == "Model set to claude-haiku-4-5."

        # Get model — should reflect the change
        get_result = await toolkit.call_tool("get_model", {})
        assert get_result == "claude-haiku-4-5"

        # archon_status should also reflect the new model
        status_json = await toolkit.call_tool("archon_status", {})
        status = json.loads(status_json)
        assert status["model"] == "claude-haiku-4-5"

    async def test_set_model_audit_logged_at_warning(self, caplog) -> None:
        """set_model emits a WARNING-level audit log entry."""
        sm = _make_session_manager()
        config = _make_config(available=["claude-sonnet-4-6", "claude-haiku-4-5"])
        toolkit = _make_toolkit(session_manager=sm, config=config)

        with caplog.at_level(logging.WARNING, logger="archon"):
            await toolkit.call_tool("set_model", {"model": "claude-haiku-4-5"})

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("set_model" in m and "claude-haiku-4-5" in m for m in warning_messages)


# ──────────────────────────────────────────────────────────────────
# Unit tests — list_skills
# ──────────────────────────────────────────────────────────────────


def _make_skill(name: str, description: str) -> MagicMock:
    skill = MagicMock()
    skill.name = name
    skill.description = description
    return skill


def _make_skill_loader(skills: list) -> MagicMock:
    loader = MagicMock()
    type(loader).skills = PropertyMock(return_value=skills)
    return loader


class TestListSkillsWithSkills:
    async def test_list_skills_with_skills(self) -> None:
        """list_skills returns JSON array with name and description for each skill."""
        skills = [
            _make_skill("playwright-cli", "Browser automation via Playwright CLI"),
            _make_skill("commit", "Create git commits with smart messages"),
        ]
        loader = _make_skill_loader(skills)
        toolkit = ArchonToolkit(skill_loader=loader)

        result = await toolkit.call_tool("list_skills", {})

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["name"] == "playwright-cli"
        assert data[0]["description"] == "Browser automation via Playwright CLI"
        assert data[1]["name"] == "commit"
        assert data[1]["description"] == "Create git commits with smart messages"

    async def test_list_skills_only_name_and_description_fields(self) -> None:
        """list_skills includes only name and description — no content or other fields."""
        skills = [_make_skill("my-skill", "My skill description")]
        loader = _make_skill_loader(skills)
        toolkit = ArchonToolkit(skill_loader=loader)

        result = await toolkit.call_tool("list_skills", {})

        data = json.loads(result)
        assert set(data[0].keys()) == {"name", "description"}


class TestListSkillsEmpty:
    async def test_list_skills_empty(self) -> None:
        """list_skills returns plain message when no skills are available."""
        loader = _make_skill_loader([])
        toolkit = ArchonToolkit(skill_loader=loader)

        result = await toolkit.call_tool("list_skills", {})

        assert result == "No skills available."

    async def test_list_skills_no_loader(self) -> None:
        """list_skills returns plain message when skill_loader is not set."""
        toolkit = ArchonToolkit(skill_loader=None)

        result = await toolkit.call_tool("list_skills", {})

        assert result == "No skills available."


class TestListSkillsError:
    async def test_list_skills_loader_raises_exception(self) -> None:
        """list_skills propagates an exception from the loader through call_tool."""
        loader = MagicMock()
        type(loader).skills = PropertyMock(side_effect=OSError("skills dir not found"))
        toolkit = ArchonToolkit(skill_loader=loader)

        with pytest.raises(OSError, match="skills dir not found"):
            await toolkit.call_tool("list_skills", {})


# ──────────────────────────────────────────────────────────────────
# Integration — list_skills via MCP server
# ──────────────────────────────────────────────────────────────────


class TestListSkillsViaMcp:
    async def test_list_skills_via_mcp(self) -> None:
        """list_skills is callable via the background ArchonMCPServer HTTP endpoint."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        skills = [
            _make_skill("deploy", "Deploy the application"),
            _make_skill("review-pr", "Review a pull request"),
        ]
        loader = _make_skill_loader(skills)
        toolkit = ArchonToolkit(skill_loader=loader)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18312, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/call", {"name": "list_skills", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            text = data["result"]["content"][0]["text"]
            parsed = json.loads(text)
            assert len(parsed) == 2
            assert parsed[0]["name"] == "deploy"
            assert parsed[1]["name"] == "review-pr"
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E — list_skills with real SkillLoader reading from tmp_path
# ──────────────────────────────────────────────────────────────────


class TestListSkillsWithRealSkillLoader:
    async def test_list_skills_with_real_skill_loader(self, tmp_path) -> None:
        """E2E: two SKILL.md files on disk → both returned in JSON array."""
        from archon.ai.skill_loader import SkillLoader

        # Create two skill directories with valid SKILL.md files
        for name, description in [
            ("alpha", "Alpha skill for testing"),
            ("beta", "Beta skill for testing"),
        ]:
            skill_dir = tmp_path / name
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\n\nSkill body.\n",
                encoding="utf-8",
            )

        loader = SkillLoader(skills_dir=tmp_path)
        toolkit = ArchonToolkit(skill_loader=loader)

        result = await toolkit.call_tool("list_skills", {})

        data = json.loads(result)
        assert len(data) == 2
        names = {s["name"] for s in data}
        assert names == {"alpha", "beta"}
        descriptions = {s["description"] for s in data}
        assert "Alpha skill for testing" in descriptions
        assert "Beta skill for testing" in descriptions


# ──────────────────────────────────────────────────────────────────
# Unit tests — list_scheduled_tasks
# ──────────────────────────────────────────────────────────────────


def _make_job_status(
    name: str,
    last_run: str | None = None,
    last_result: str | None = None,
    last_error: str | None = None,
    run_count: int = 0,
) -> MagicMock:
    from datetime import datetime, timezone

    status = MagicMock()
    status.name = name
    status.last_run = datetime.fromisoformat(last_run) if last_run else None
    status.last_result = last_result
    status.last_error = last_error
    status.run_count = run_count
    return status


def _make_job_config(
    name: str,
    cron: str = "0 * * * *",
    enabled: bool = True,
) -> MagicMock:
    cfg = MagicMock()
    cfg.name = name
    cfg.cron = cron
    cfg.enabled = enabled
    return cfg


def _make_job_scheduler(
    statuses: dict,
    next_runs: dict,
    job_configs: list | None = None,
) -> MagicMock:
    scheduler = MagicMock()
    type(scheduler).job_statuses = PropertyMock(return_value=statuses)
    scheduler.next_run_times.return_value = next_runs
    if job_configs is not None:
        type(scheduler).job_configs = PropertyMock(return_value=job_configs)
    return scheduler


class TestListScheduledTasksWithJobs:
    async def test_list_scheduled_tasks_with_jobs(self) -> None:
        """list_scheduled_tasks returns JSON array with all fields for each job."""
        from datetime import datetime, timezone

        last_run_dt = datetime(2026, 3, 18, 10, 0, 0, tzinfo=timezone.utc)
        next_run_dt = datetime(2026, 3, 18, 11, 0, 0, tzinfo=timezone.utc)

        statuses = {
            "job_a": _make_job_status(
                "job_a",
                last_run=last_run_dt.isoformat(),
                last_result="done",
                run_count=3,
            ),
            "job_b": _make_job_status(
                "job_b",
                last_error="oops",
                run_count=1,
            ),
        }
        job_configs = [
            _make_job_config("job_a", cron="0 * * * *", enabled=True),
            _make_job_config("job_b", cron="*/5 * * * *", enabled=False),
        ]
        next_runs = {
            "job_a": next_run_dt,
            "job_b": None,
        }

        scheduler = _make_job_scheduler(statuses, next_runs, job_configs)
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("list_scheduled_tasks", {})

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 2

        job_a = next(d for d in data if d["name"] == "job_a")
        assert job_a["cron"] == "0 * * * *"
        assert job_a["enabled"] is True
        assert job_a["last_result"] == "done"
        assert job_a["last_error"] is None
        assert job_a["run_count"] == 3
        assert job_a["next_run"] is not None
        assert "last_run" in job_a

        job_b = next(d for d in data if d["name"] == "job_b")
        assert job_b["cron"] == "*/5 * * * *"
        assert job_b["enabled"] is False
        assert job_b["last_error"] == "oops"
        assert job_b["last_result"] is None
        assert job_b["run_count"] == 1
        assert job_b["next_run"] is None

    async def test_list_scheduled_tasks_all_fields_present(self) -> None:
        """list_scheduled_tasks returns all required fields for each job."""
        statuses = {
            "my_job": _make_job_status("my_job", run_count=0),
        }
        job_configs = [_make_job_config("my_job", cron="0 9 * * *")]
        next_runs = {"my_job": None}

        scheduler = _make_job_scheduler(statuses, next_runs, job_configs)
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("list_scheduled_tasks", {})
        data = json.loads(result)

        assert len(data) == 1
        entry = data[0]
        required_fields = {"name", "enabled", "cron", "last_run", "last_result", "last_error", "next_run", "run_count"}
        assert required_fields.issubset(set(entry.keys()))


class TestListScheduledTasksEmpty:
    async def test_list_scheduled_tasks_empty(self) -> None:
        """list_scheduled_tasks returns plain message when no jobs configured."""
        scheduler = _make_job_scheduler({}, {}, [])
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("list_scheduled_tasks", {})

        assert result == "No scheduled jobs."

    async def test_list_scheduled_tasks_no_scheduler(self) -> None:
        """list_scheduled_tasks returns plain message when job_scheduler is not set."""
        toolkit = ArchonToolkit(job_scheduler=None)

        result = await toolkit.call_tool("list_scheduled_tasks", {})

        assert result == "No scheduled jobs."

    async def test_list_scheduled_tasks_skips_stale_status_entries(self) -> None:
        """list_scheduled_tasks skips jobs present in statuses but missing from job_configs."""
        statuses = {
            "live_job": _make_job_status("live_job", run_count=2),
            "orphan_job": _make_job_status("orphan_job", run_count=5),
        }
        job_configs = [_make_job_config("live_job", cron="0 * * * *")]
        next_runs = {"live_job": None, "orphan_job": None}

        scheduler = _make_job_scheduler(statuses, next_runs, job_configs)
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("list_scheduled_tasks", {})
        data = json.loads(result)

        names = [d["name"] for d in data]
        assert "live_job" in names
        assert "orphan_job" not in names
        assert len(data) == 1


# ──────────────────────────────────────────────────────────────────
# Integration — list_scheduled_tasks via MCP server
# ──────────────────────────────────────────────────────────────────


class TestListScheduledTasksViaMcp:
    async def test_list_scheduled_tasks_via_mcp(self) -> None:
        """list_scheduled_tasks is callable via the background ArchonMCPServer HTTP endpoint."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        statuses = {
            "nightly": _make_job_status("nightly", run_count=5, last_result="ok"),
        }
        job_configs = [_make_job_config("nightly", cron="0 2 * * *")]
        next_runs = {"nightly": None}

        scheduler = _make_job_scheduler(statuses, next_runs, job_configs)
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18316, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/call", {"name": "list_scheduled_tasks", "arguments": {}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            text = data["result"]["content"][0]["text"]
            parsed = json.loads(text)
            assert len(parsed) == 1
            assert parsed[0]["name"] == "nightly"
            assert parsed[0]["run_count"] == 5
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# E2E — list_scheduled_tasks with real JobScheduler from tmp_path
# ──────────────────────────────────────────────────────────────────


class TestListScheduledTasksWithRealScheduler:
    async def test_list_scheduled_tasks_with_real_scheduler(self, tmp_path) -> None:
        """E2E: one job TOML on disk → job appears in list_scheduled_tasks result."""
        from unittest.mock import AsyncMock, MagicMock
        from archon.ai.job_scheduler import JobScheduler
        from archon.config.loader import ScheduleConfig, ScheduledJobConfig, SchedulePipelineStep

        # Create a real job config in memory (no file system needed for basic scheduler)
        job = ScheduledJobConfig(
            name="daily_report",
            cron="0 8 * * *",
            pipeline=[SchedulePipelineStep(name="echo_tool", kind="tool", value="echo hi")],
            enabled=True,
        )
        schedule_config = ScheduleConfig(enabled=True, jobs=[job])

        bot = MagicMock()
        bot.send_message = AsyncMock()

        scheduler = JobScheduler(
            config=schedule_config,
            bot=bot,
            allowed_user_ids=[],
        )
        toolkit = ArchonToolkit(job_scheduler=scheduler)

        result = await toolkit.call_tool("list_scheduled_tasks", {})

        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) == 1
        entry = data[0]
        assert entry["name"] == "daily_report"
        assert entry["cron"] == "0 8 * * *"
        assert entry["enabled"] is True
        assert entry["run_count"] == 0
        assert entry["last_run"] is None
        assert entry["last_result"] is None
        assert entry["last_error"] is None
        # next_run should be a non-None ISO string (job is enabled with valid cron)
        assert entry["next_run"] is not None


# ──────────────────────────────────────────────────────────────────
# Unit tests — get_config
# ──────────────────────────────────────────────────────────────────


def _write_config(tmp_path, content: str):
    cfg = tmp_path / "config.toml"
    cfg.write_text(content, encoding="utf-8")
    return cfg


class TestGetConfigReturnsValue:
    async def test_get_config_returns_value(self, tmp_path) -> None:
        """get_config returns JSON-serialized value for a simple string field."""
        cfg = _write_config(tmp_path, '[session]\nworking_directory = "/home/user"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "session.working_directory"})

        assert result == json.dumps("/home/user")


class TestGetConfigNestedPath:
    async def test_get_config_nested_path(self, tmp_path) -> None:
        """get_config navigates dot-notation path correctly."""
        cfg = _write_config(tmp_path, '[notifications]\nmode = "verbose"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "notifications.mode"})

        assert result == json.dumps("verbose")


class TestGetConfigNotFound:
    async def test_get_config_not_found(self, tmp_path) -> None:
        """get_config returns error message (not exception) when path is missing."""
        cfg = _write_config(tmp_path, "[session]\nworking_directory = \"/tmp\"\n")
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "nonexistent.field"})

        assert result == "Config key 'nonexistent.field' not found."


class TestGetConfigRedactsSensitive:
    async def test_get_config_redacts_sensitive(self, tmp_path) -> None:
        """get_config returns '***' when any path component matches sensitive keywords."""
        cfg = _write_config(tmp_path, '[telegram]\nbot_token = "secret-abc"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "telegram.bot_token"})

        assert result == '"***"'

    async def test_get_config_redacts_password(self, tmp_path) -> None:
        """get_config redacts paths containing 'password'."""
        cfg = _write_config(tmp_path, '[db]\npassword = "hunter2"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "db.password"})

        assert result == '"***"'

    async def test_get_config_redacts_secret(self, tmp_path) -> None:
        """get_config redacts paths containing 'secret'."""
        cfg = _write_config(tmp_path, '[auth]\napi_secret = "mysecret"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "auth.api_secret"})

        assert result == '"***"'

    async def test_get_config_redacts_key(self, tmp_path) -> None:
        """get_config redacts paths containing 'key'."""
        cfg = _write_config(tmp_path, '[encryption]\nkey = "aes256key"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "encryption.key"})

        assert result == '"***"'

    async def test_get_config_redacts_case_insensitive(self, tmp_path) -> None:
        """get_config redacts sensitive paths regardless of case."""
        cfg = _write_config(tmp_path, '[auth]\nAPI_KEY = "somekey"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "auth.API_KEY"})

        assert result == '"***"'


class TestGetConfigMissingFile:
    async def test_get_config_missing_config_file(self, tmp_path) -> None:
        """get_config returns error message (not crash) when config file is missing."""
        missing = tmp_path / "nonexistent.toml"
        toolkit = ArchonToolkit(config_file=missing)

        result = await toolkit.call_tool("get_config", {"path": "session.working_directory"})

        assert result == "Config file not found."


class TestGetConfigTableRedaction:
    async def test_get_config_table_redacts_sensitive_keys_in_dict(self, tmp_path) -> None:
        """get_config redacts sensitive keys inside a returned table dict."""
        cfg = _write_config(tmp_path, '[telegram]\nbot_token = "secret"\nother = "safe"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "telegram"})

        data = json.loads(result)
        assert isinstance(data, dict)
        assert data["bot_token"] == "***"
        assert data["other"] == "safe"

    async def test_get_config_table_redacts_nested_sensitive_keys(self, tmp_path) -> None:
        """get_config recursively redacts sensitive keys in nested dicts."""
        cfg = _write_config(
            tmp_path,
            "[outer]\n[outer.inner]\napi_key = \"should_be_redacted\"\nname = \"visible\"\n",
        )
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "outer"})

        data = json.loads(result)
        assert data["inner"]["api_key"] == "***"
        assert data["inner"]["name"] == "visible"


class TestGetConfigInvalidToml:
    async def test_get_config_invalid_toml_returns_error(self, tmp_path) -> None:
        """get_config returns clean error message when config file has invalid TOML."""
        cfg = tmp_path / "config.toml"
        cfg.write_text("this is not = valid toml [\n", encoding="utf-8")
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "session.working_directory"})

        assert result == "Config file is invalid TOML."


class TestGetConfigListOfDictsRedaction:
    async def test_get_config_redacts_sensitive_keys_in_list_of_dicts(self, tmp_path) -> None:
        """get_config redacts sensitive keys inside dicts contained in a list value."""
        cfg = _write_config(
            tmp_path,
            (
                "[[services.endpoints]]\n"
                'api_key = "secret123"\n'
                'url = "https://example.com"\n'
            ),
        )
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("get_config", {"path": "services"})

        data = json.loads(result)
        assert isinstance(data, dict)
        endpoints = data["endpoints"]
        assert isinstance(endpoints, list)
        assert len(endpoints) == 1
        assert endpoints[0]["api_key"] == "***"
        assert endpoints[0]["url"] == "https://example.com"


class TestGetConfigPermissionError:
    async def test_get_config_permission_error_returns_error(self, tmp_path) -> None:
        """get_config returns clean error message when config file is not readable."""
        from unittest.mock import patch, mock_open

        cfg = _write_config(tmp_path, '[session]\nworking_directory = "/tmp"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            result = await toolkit.call_tool("get_config", {"path": "session.working_directory"})

        assert result == "Config file not readable."


# ──────────────────────────────────────────────────────────────────
# Integration — get_config via both MCP servers
# ──────────────────────────────────────────────────────────────────


class TestGetConfigViaBgMcp:
    async def test_get_config_via_bg_mcp(self, tmp_path) -> None:
        """get_config is callable via the background ArchonMCPServer HTTP endpoint."""
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        cfg = _write_config(tmp_path, '[notifications]\nmode = "normal"\n')

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        toolkit = ArchonToolkit(config_file=cfg)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18320, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc("tools/call", {"name": "get_config", "arguments": {"path": "notifications.mode"}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "normal" in data["result"]["content"][0]["text"]
        finally:
            await client.close()


class TestGetConfigViaOrchMcp:
    async def test_get_config_via_router_mcp(self, tmp_path) -> None:
        """get_config is callable via ArchonRouterMCPServer."""
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        cfg = _write_config(tmp_path, '[notifications]\nmode = "quiet"\n')

        toolkit = ArchonToolkit(config_file=cfg)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
            allowed_tools=frozenset(toolkit.tool_names),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": "get_config", "arguments": {"path": "notifications.mode"}}),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
            assert "quiet" in data["result"]["content"][0]["text"]
        finally:
            await client.close()


# ──────────────────────────────────────────────────────────────────
# Unit tests — set_config
# ──────────────────────────────────────────────────────────────────


class TestSetConfigWritesValue:
    async def test_set_config_writes_value(self, tmp_path) -> None:
        """set_config writes a string value to the config file."""
        import tomllib

        cfg = _write_config(tmp_path, '[session]\nworking_directory = "/old"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("set_config", {"path": "session.working_directory", "value": "/new"})

        assert "session.working_directory" in result
        assert "/new" in result

        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        assert data["session"]["working_directory"] == "/new"


class TestSetConfigIntCoercion:
    async def test_set_config_int_coercion(self, tmp_path) -> None:
        """set_config coerces '42' to int when stored in the config file."""
        import tomllib

        cfg = _write_config(tmp_path, "[session]\ninactivity_timeout_seconds = 300\n")
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("set_config", {"path": "session.inactivity_timeout_seconds", "value": "42"})

        assert "42" in result

        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        assert data["session"]["inactivity_timeout_seconds"] == 42
        assert isinstance(data["session"]["inactivity_timeout_seconds"], int)


class TestSetConfigBoolCoercion:
    async def test_set_config_bool_coercion(self, tmp_path) -> None:
        """set_config coerces 'false' to bool when stored in the config file."""
        import tomllib

        cfg = _write_config(tmp_path, "[schedule]\nenabled = true\n")
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool("set_config", {"path": "schedule.enabled", "value": "false"})

        assert "false" in result.lower()

        with open(cfg, "rb") as f:
            data = tomllib.load(f)
        assert data["schedule"]["enabled"] is False


class TestSetConfigInvalidValue:
    async def test_set_config_invalid_value(self, tmp_path) -> None:
        """set_config returns error message when set_config_value raises ValueError."""
        from unittest.mock import patch

        cfg = _write_config(tmp_path, "[session]\nworking_directory = \"/tmp\"\n")
        toolkit = ArchonToolkit(config_file=cfg)

        with patch(
            "archon.ai.archon_toolkit.set_config_value",
            side_effect=ValueError("Round-trip validation failed"),
        ):
            result = await toolkit.call_tool("set_config", {"path": "session.working_directory", "value": "bad"})

        assert "Round-trip validation failed" in result or "error" in result.lower()
        # File should be unchanged
        with open(cfg, "rb") as f:
            import tomllib
            data = tomllib.load(f)
        assert data["session"]["working_directory"] == "/tmp"


class TestSetConfigAuditLogged:
    async def test_set_config_audit_logged(self, tmp_path, caplog) -> None:
        """set_config emits a WARNING-level audit log containing path and value."""
        cfg = _write_config(tmp_path, '[notifications]\nmode = "normal"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        with caplog.at_level(logging.WARNING, logger="archon"):
            await toolkit.call_tool("set_config", {"path": "notifications.mode", "value": "quiet"})

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "notifications.mode" in m and "quiet" in m
            for m in warning_messages
        )


class TestSetConfigMissingConfigFile:
    async def test_set_config_missing_config_file(self, tmp_path) -> None:
        """set_config returns error message (not crash) when config file does not exist."""
        missing = tmp_path / "nonexistent.toml"
        toolkit = ArchonToolkit(config_file=missing)

        result = await toolkit.call_tool("set_config", {"path": "session.working_directory", "value": "/new"})

        assert result == "Config file not found."


class TestSetConfigRedactsSensitiveInAuditLog:
    async def test_set_config_redacts_sensitive_in_audit_log(self, tmp_path, caplog) -> None:
        """set_config redacts sensitive values (token, password, secret, key) in audit log."""
        cfg = _write_config(tmp_path, '[api]\ntoken = "old-secret"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        with caplog.at_level(logging.WARNING, logger="archon"):
            await toolkit.call_tool("set_config", {"path": "api.token", "value": "super-secret-token-value"})

        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any("api.token" in m for m in warning_messages), "audit log must contain path"
        assert all(
            "super-secret-token-value" not in m for m in warning_messages
        ), "audit log must NOT contain plaintext sensitive value"
        assert any("***" in m for m in warning_messages), "audit log must show *** for sensitive value"


class TestSetConfigPermissionError:
    async def test_set_config_permission_error(self, tmp_path) -> None:
        """set_config returns error message (not crash) when set_config_value raises PermissionError."""
        cfg = _write_config(tmp_path, '[notifications]\nmode = "normal"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        with patch(
            "archon.ai.archon_toolkit.set_config_value",
            side_effect=PermissionError("read-only"),
        ):
            result = await toolkit.call_tool(
                "set_config", {"path": "notifications.mode", "value": "quiet"}
            )

        assert result == "Permission denied reading config file."


class TestSetConfigRedactsSensitiveInReturnValue:
    async def test_set_config_redacts_sensitive_in_return_value(self, tmp_path) -> None:
        """set_config return string must not contain plaintext value for sensitive paths."""
        cfg = _write_config(tmp_path, '[api]\ntoken = "old-secret"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        result = await toolkit.call_tool(
            "set_config", {"path": "api.token", "value": "super-secret-token-value"}
        )

        assert "super-secret-token-value" not in result, "return value must NOT contain plaintext sensitive value"
        assert "***" in result, "return value must contain *** for sensitive path"


class TestSetConfigReadbackFailureReturnsCaveat:
    async def test_set_config_readback_failure_returns_success_with_caveat(self, tmp_path) -> None:
        """set_config returns success-with-caveat message when read-back raises any exception."""
        cfg = _write_config(tmp_path, '[notifications]\nmode = "normal"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        with (
            patch("archon.ai.archon_toolkit.set_config_value"),
            patch(
                "archon.ai.archon_toolkit.get_config_value",
                side_effect=Exception("read error"),
            ),
        ):
            result = await toolkit.call_tool(
                "set_config", {"path": "notifications.mode", "value": "quiet"}
            )

        assert "write succeeded, value not verified" in result


# ──────────────────────────────────────────────────────────────────
# Integration — set_config via both MCP servers
# ──────────────────────────────────────────────────────────────────


class TestSetConfigViaBgMcp:
    async def test_set_config_via_bg_mcp(self, tmp_path) -> None:
        """set_config is callable via the background ArchonMCPServer and updates config file."""
        import tomllib
        from archon.ai.archon_mcp_server import ArchonMCPServer
        from archon.ai.background_agent_manager import BackgroundAgentManager

        cfg = _write_config(tmp_path, '[notifications]\nmode = "normal"\n')

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm_for_bam = MagicMock()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm_for_bam)

        toolkit = ArchonToolkit(config_file=cfg)

        server = ArchonMCPServer(
            manager=manager, host="127.0.0.1", port=18330, toolkit=toolkit,
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp/42",
                json=_rpc(
                    "tools/call",
                    {"name": "set_config", "arguments": {"path": "notifications.mode", "value": "quiet"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
        finally:
            await client.close()

        with open(cfg, "rb") as f:
            stored = tomllib.load(f)
        assert stored["notifications"]["mode"] == "quiet"


class TestSetConfigViaOrchMcp:
    async def test_set_config_via_router_mcp(self, tmp_path) -> None:
        """set_config is callable via ArchonRouterMCPServer and updates config file."""
        import tomllib
        from archon.ai.archon_router_mcp_server import ArchonRouterMCPServer

        cfg = _write_config(tmp_path, '[notifications]\nmode = "verbose"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        server = ArchonRouterMCPServer(
            history_root=str(tmp_path), toolkit=toolkit,
            allowed_tools=frozenset(toolkit.tool_names),
        )
        client = TestClient(TestServer(server._app))
        await client.start_server()

        try:
            resp = await client.post(
                "/mcp",
                json=_rpc(
                    "tools/call",
                    {"name": "set_config", "arguments": {"path": "notifications.mode", "value": "normal"}},
                ),
                headers={"Authorization": f"Bearer {server.token}"},
            )
            data = await resp.json()
            assert data["result"]["isError"] is False
        finally:
            await client.close()

        with open(cfg, "rb") as f:
            stored = tomllib.load(f)
        assert stored["notifications"]["mode"] == "normal"


# ──────────────────────────────────────────────────────────────────
# E2E — set_config then get_config roundtrip
# ──────────────────────────────────────────────────────────────────


class TestSetThenGetConfigRoundtrip:
    async def test_set_then_get_config_roundtrip(self, tmp_path) -> None:
        """E2E: set a value via set_config, then get_config returns the same value."""
        cfg = _write_config(tmp_path, '[notifications]\nmode = "normal"\n')
        toolkit = ArchonToolkit(config_file=cfg)

        await toolkit.call_tool("set_config", {"path": "notifications.mode", "value": "quiet"})

        result = await toolkit.call_tool("get_config", {"path": "notifications.mode"})

        assert result == json.dumps("quiet")
