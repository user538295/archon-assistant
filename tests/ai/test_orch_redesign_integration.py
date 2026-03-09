"""Integration tests for the orchestration session redesign.

Covers the "rewrite the script from yesterday" use case:
- _orch_session receives history context from context_provider at session start
- Pipeline routing: chat + confidence >= 0.8 → direct; everything else → route_task()
- Dual-prompt format in Pipeline._yield_plan() when _orch_session enriches the prompt
- Background agents get agents.md injected
- ArchonOrchestratorMCPServer path restriction and tool behaviour
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from archon.ai.archon_orch_mcp_server import ArchonOrchestratorMCPServer
from archon.ai.background_agent_manager import AgentRun, BackgroundAgentManager
from archon.ai.classification import Classification
from archon.ai.classifier import ClassifierResult
from archon.ai.decomposer import TaskOutput
from archon.ai.event_mapper import ClassificationEvent, PlanEvent, Response, RoutingEvent
from archon.ai.pipeline import Pipeline


# ──────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────


def _mock_session(*events, is_processing: bool = False) -> MagicMock:
    """Build a mock ClaudeSession that yields given events from send()."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_processing = is_processing
    session.processing_seconds = None
    session.idle_seconds = 5.0
    session.send_count = 0
    session.usage_stats = None
    session.diagnostics = {"is_alive": True}
    session.model = "claude-sonnet-4-6"
    session.is_alive = True
    session._send_calls: list[str] = []

    async def _send(prompt: str) -> AsyncGenerator:
        session._send_calls.append(prompt)
        for event in events:
            yield event

    session.send = _send
    session.activate_skill = MagicMock()
    session.inject_context = MagicMock()
    session.recent_events = MagicMock(return_value=[])
    session.flush_pending_context = MagicMock()
    return session


def _mock_context_provider(
    startup_prompt: str = "# History\nFiles are in ~/.archon/history/",
    recent_context: str | None = "2026-03-08: User created collect_bins.sh at /Users/manczg/projects/collect_bins.sh",
) -> MagicMock:
    provider = MagicMock()
    provider.startup_context_prompt = MagicMock(return_value=startup_prompt)
    provider.get_recent_context = MagicMock(return_value=recent_context)
    return provider


def _make_decomposer(
    session_events=None,
    orch_events=None,
    summary_events=None,
    **kwargs,
):
    """Build a Decomposer with mocked main, orchestration, and summary sessions."""
    from archon.ai.decomposer import Decomposer

    if session_events is None:
        session_events = [Response(content="Done.")]
    if orch_events is None:
        orch_events = [Response(content='{"scope":"small","summary":"Direct handling","prompt":"do it"}')]
    if summary_events is None:
        summary_events = [Response(content="User discussed topic X.")]

    main_session = _mock_session(*session_events)
    orch_session = _mock_session(*orch_events)
    summary_session = _mock_session(*summary_events)

    with patch(
        "archon.ai.decomposer.ClaudeSession",
        side_effect=[main_session, orch_session, summary_session],
    ):
        with patch("archon.ai.decomposer.load_prompt", return_value="mock prompt"):
            decomposer = Decomposer(**kwargs)

    return decomposer, main_session, orch_session, summary_session


def _mock_classifier(
    intent: str = "task",
    confidence: float = 0.9,
    error: str = "",
    parse_error: str = "",
) -> MagicMock:
    classifier = MagicMock()
    classifier.start = AsyncMock()
    classifier.stop = AsyncMock()
    classifier.model = "claude-haiku-4-5-20251001"
    classifier.usage_stats = None
    classifier.classify = AsyncMock(
        return_value=ClassifierResult(
            classification=Classification(intent=intent, confidence=confidence),
            raw_response="{}",
            duration_s=0.1,
            parse_error=parse_error,
            error=error,
        )
    )
    return classifier


def _mock_decomposer_obj(
    answer_events=None,
    route_task_result=None,
    model: str = "claude-sonnet-4-6",
) -> MagicMock:
    decomposer = MagicMock()
    decomposer.start = AsyncMock()
    decomposer.stop = AsyncMock()
    decomposer.is_processing = False
    decomposer.processing_seconds = None
    decomposer.idle_seconds = 5.0
    decomposer.send_count = 0
    decomposer.usage_stats = None
    decomposer.diagnostics = {"is_alive": True}
    decomposer.model = model
    decomposer.is_alive = True
    decomposer.context_summary = ""
    decomposer.reminder = None
    decomposer.recent_events = MagicMock(return_value=[])
    decomposer.inject_context = MagicMock()
    decomposer.flush_pending_context = MagicMock()
    decomposer.track_context = MagicMock()
    decomposer.activate_skill = MagicMock()

    if answer_events is None:
        answer_events = [Response(content="Done.")]

    async def _answer(prompt: str) -> AsyncGenerator:
        for event in answer_events:
            yield event

    decomposer.answer = _answer
    decomposer.route_task = AsyncMock(
        return_value=route_task_result
        or TaskOutput(scope="small", summary="Quick task", prompt="Do the thing")
    )
    return decomposer


def _make_pipeline(classifier=None, decomposer=None) -> tuple:
    if classifier is None:
        classifier = _mock_classifier()
    if decomposer is None:
        decomposer = _mock_decomposer_obj()

    with patch("archon.ai.pipeline.Classifier", return_value=classifier):
        with patch("archon.ai.pipeline.Decomposer", return_value=decomposer):
            pipeline = Pipeline()

    return pipeline, classifier, decomposer


async def _collect(pipeline: Pipeline, prompt: str = "test") -> list:
    return [e async for e in pipeline.send(prompt)]


# ──────────────────────────────────────────────────────────────────
# Group 1: Context injection into _orch_session (Decomposer integration)
# ──────────────────────────────────────────────────────────────────


class TestOrchSessionContextInjection:
    async def test_orch_session_inject_context_called_with_startup_prompt(self) -> None:
        """After decomposer.start(), _orch_session.inject_context contains startup prompt text."""
        provider = _mock_context_provider(
            startup_prompt="# History\nFiles are in ~/.archon/history/",
            recent_context="2026-03-08: User created collect_bins.sh at /Users/manczg/projects/collect_bins.sh",
        )
        decomposer, _, orch_session, _ = _make_decomposer(context_provider=provider)

        await decomposer.start()

        orch_session.inject_context.assert_called()
        injected_text = orch_session.inject_context.call_args_list[0][0][0]
        assert "History" in injected_text

        await decomposer.stop()

    async def test_orch_session_inject_context_called_with_recent_context(self) -> None:
        """inject_context includes recent context (collect_bins.sh) from context_provider."""
        provider = _mock_context_provider(
            startup_prompt="# History\nFiles are in ~/.archon/history/",
            recent_context="2026-03-08: User created collect_bins.sh at /Users/manczg/projects/collect_bins.sh",
        )
        decomposer, _, orch_session, _ = _make_decomposer(context_provider=provider)

        await decomposer.start()

        orch_session.inject_context.assert_called()
        all_injected = " ".join(
            str(call_args[0][0]) for call_args in orch_session.inject_context.call_args_list
        )
        assert "collect_bins.sh" in all_injected

        await decomposer.stop()

    async def test_orch_session_no_inject_when_context_provider_none(self, tmp_path) -> None:
        """When context_provider is None, _orch_session.inject_context is never called on start.

        Uses a real tmp_path for cwd (no agents.md present) to isolate the
        context_provider=None condition from the cwd=None condition.
        """
        # cwd set to a temp dir with no agents.md — only context_provider path is absent
        decomposer, _, orch_session, _ = _make_decomposer(cwd=str(tmp_path))

        await decomposer.start()

        # inject_context should not be called: no context_provider and no agents.md
        orch_session.inject_context.assert_not_called()

        await decomposer.stop()

    async def test_orch_session_context_reinjected_after_reset(self) -> None:
        """After _orch_session resets (at threshold), inject_context is called again.

        _reset_orch_if_needed() reuses the same _orch_session object (stop + start).
        So we assert inject_context is called at least twice on the same session:
        once at Decomposer.start() and once after the reset inside route_task().
        """
        from archon.ai import decomposer as decomposer_module

        provider = _mock_context_provider()
        orch_response = Response(
            content='{"scope":"small","summary":"Task summary","prompt":"do the thing"}'
        )

        decomposer, _, orch_session, _ = _make_decomposer(
            orch_events=[orch_response],
            context_provider=provider,
        )

        await decomposer.start()

        # Confirm context injected at start
        call_count_after_start = orch_session.inject_context.call_count
        assert call_count_after_start >= 1

        # Force a reset by setting _orch_call_count just below threshold,
        # then calling route_task once more to trigger reset + re-injection.
        decomposer._orch_call_count = decomposer_module._ORCH_RESET_THRESHOLD - 1

        await decomposer.route_task("rewrite the script from yesterday")

        # inject_context must have been called again after the reset
        assert orch_session.inject_context.call_count > call_count_after_start, (
            "Expected inject_context to be called again after orch session reset"
        )

        # Verify the re-injected text contains expected history content from the provider
        last_injected = orch_session.inject_context.call_args_list[-1][0][0]
        assert "collect_bins.sh" in last_injected, (
            f"Expected last inject_context call to contain 'collect_bins.sh'. "
            f"Got: {last_injected!r}"
        )

        await decomposer.stop()


# ──────────────────────────────────────────────────────────────────
# Group 2: Pipeline routing integration
# ──────────────────────────────────────────────────────────────────


class TestPipelineRouting:
    async def test_task_intent_always_routes_to_route_task(self) -> None:
        """task intent always calls route_task(), never answer()."""
        pipeline, _, decomposer = _make_pipeline(
            classifier=_mock_classifier(intent="task", confidence=0.9),
        )
        await _collect(pipeline, "rewrite the script from yesterday")

        decomposer.route_task.assert_awaited_once_with("rewrite the script from yesterday")

    async def test_chat_below_threshold_routes_to_route_task(self) -> None:
        """chat intent below 0.8 confidence → route_task(), not answer()."""
        pipeline, _, decomposer = _make_pipeline(
            classifier=_mock_classifier(intent="chat", confidence=0.7),
        )
        await _collect(pipeline, "make the thing from last week")

        decomposer.route_task.assert_awaited_once()

    async def test_chat_at_threshold_routes_to_answer(self) -> None:
        """chat intent at exactly 0.8 confidence → answer(), not route_task()."""
        pipeline, _, decomposer = _make_pipeline(
            classifier=_mock_classifier(intent="chat", confidence=0.8),
        )
        await _collect(pipeline, "how are you?")

        decomposer.route_task.assert_not_awaited()

    async def test_dual_prompt_applied_when_orch_enriches(self) -> None:
        """When route_task returns a different prompt, dual-prompt format is used."""
        enriched_prompt = "Rewrite /Users/manczg/projects/collect_bins.sh in Python"
        pipeline, _, decomposer = _make_pipeline(
            classifier=_mock_classifier(intent="task", confidence=0.92),
            decomposer=_mock_decomposer_obj(
                route_task_result=TaskOutput(
                    scope="small",
                    summary="Rewrite script",
                    prompt=enriched_prompt,
                )
            ),
        )
        user_prompt = "rewrite the script from yesterday"
        events = await _collect(pipeline, user_prompt)

        plan_events = [e for e in events if isinstance(e, PlanEvent)]
        assert len(plan_events) == 1
        agent_task = plan_events[0].plan.agents[0].task

        assert "[Original user request]: rewrite the script from yesterday" in agent_task
        assert "[Resolved context]: Rewrite /Users/manczg/projects/collect_bins.sh in Python" in agent_task

    async def test_no_dual_prompt_when_orch_returns_same_prompt(self) -> None:
        """When route_task returns the same prompt as user input, no dual-prompt format."""
        user_prompt = "rewrite the script from yesterday"
        pipeline, _, decomposer = _make_pipeline(
            classifier=_mock_classifier(intent="task", confidence=0.9),
            decomposer=_mock_decomposer_obj(
                route_task_result=TaskOutput(
                    scope="small",
                    summary="Direct task",
                    prompt=user_prompt,  # same as user input — no enrichment
                )
            ),
        )
        events = await _collect(pipeline, user_prompt)

        plan_events = [e for e in events if isinstance(e, PlanEvent)]
        assert len(plan_events) == 1
        agent_task = plan_events[0].plan.agents[0].task

        assert agent_task == user_prompt
        assert "[Original user request]" not in agent_task
        assert "[Resolved context]" not in agent_task

    async def test_route_task_sdk_exception_falls_back_to_original_prompt(self) -> None:
        """If _orch_session.send() raises, route_task falls back to original prompt."""
        orch_response = Response(content='{"scope":"small","summary":"ok","prompt":"do it"}')
        decomposer, _, orch_session, _ = _make_decomposer(orch_events=[orch_response])

        # Replace send with a function that raises on every call
        async def _raising_send(prompt: str) -> AsyncGenerator:
            raise RuntimeError("SDK disconnected")
            yield  # make it an async generator

        orch_session.send = _raising_send

        original_prompt = "rewrite the script from yesterday"
        task_output = await decomposer.route_task(original_prompt)

        assert task_output.scope == "small"
        assert task_output.prompt == original_prompt


# ──────────────────────────────────────────────────────────────────
# Group 3: ArchonOrchestratorMCPServer integration
# ──────────────────────────────────────────────────────────────────


def _rpc(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


@pytest.fixture
async def orch_mcp_client(tmp_path, monkeypatch):
    """Provide a TestClient connected to ArchonOrchestratorMCPServer's aiohttp app.

    Monkeypatches _HISTORY_ROOT to point to tmp_path so tests never touch
    the real ~/.archon/history/ directory.

    Yields (server, client, tmp_path) so callers can access server.token for auth.
    """
    import archon.ai.archon_orch_mcp_server as mcp_module

    monkeypatch.setattr(mcp_module, "_HISTORY_ROOT", tmp_path.resolve())

    server = ArchonOrchestratorMCPServer()
    client = TestClient(TestServer(server._app))
    await client.start_server()
    yield server, client, tmp_path
    await client.close()


class TestArchonOrchestratorMCPServer:
    async def _post(self, client: TestClient, body: dict, token: str) -> dict:
        resp = await client.post(
            "/mcp", json=body, headers={"Authorization": f"Bearer {token}"}
        )
        return await resp.json()

    async def test_history_read_returns_file_contents(self, orch_mcp_client) -> None:
        """history_read tool returns the content of a file inside _HISTORY_ROOT (tmp_path)."""
        server, client, tmp_path = orch_mcp_client

        test_file = tmp_path / "_integration_test_read.txt"
        test_file.write_text("integration test content", encoding="utf-8")

        resp = await self._post(
            client,
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": str(test_file)}}),
            server.token,
        )
        result = resp["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "integration test content" in text

    async def test_history_read_rejects_path_outside_history_root(self, orch_mcp_client) -> None:
        """history_read tool rejects paths outside _HISTORY_ROOT (monkeypatched tmp_path)."""
        server, client, _ = orch_mcp_client

        resp = await self._post(
            client,
            _rpc("tools/call", {"name": "history_read", "arguments": {"path": "/etc/passwd"}}),
            server.token,
        )
        result = resp["result"]
        assert result["isError"] is True
        assert "Access denied" in result["content"][0]["text"]

    async def test_history_grep_returns_matching_lines(self, orch_mcp_client) -> None:
        """history_grep tool returns lines matching the pattern from a file in tmp_path."""
        server, client, tmp_path = orch_mcp_client

        test_file = tmp_path / "_integration_test_grep.txt"
        test_file.write_text(
            "2026-03-07: User asked about Python\n"
            "2026-03-08: User created collect_bins.sh at /Users/manczg/projects/collect_bins.sh\n"
            "2026-03-09: User requested rewrite\n",
            encoding="utf-8",
        )

        resp = await self._post(
            client,
            _rpc(
                "tools/call",
                {
                    "name": "history_grep",
                    "arguments": {
                        "pattern": "collect_bins",
                        "path": str(test_file),
                    },
                },
            ),
            server.token,
        )
        result = resp["result"]
        assert result["isError"] is False
        text = result["content"][0]["text"]
        assert "collect_bins.sh" in text


# ──────────────────────────────────────────────────────────────────
# Group 4: Background agent gets agents.md (BackgroundAgentManager integration)
# ──────────────────────────────────────────────────────────────────


def _make_mock_agent_session(result: str = "agent result") -> MagicMock:
    """Return a mock ClaudeSession that completes immediately."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True
    session.inject_context = MagicMock()

    async def _send(prompt: str):  # type: ignore[return]
        yield Response(content=result)

    session.send = _send
    return session


class TestBackgroundAgentAgentsMdInjection:
    async def test_background_agent_receives_agents_md_when_present(self) -> None:
        """Background agent session gets agents.md content injected when file exists in cwd."""
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_md = Path(tmpdir) / "agents.md"
            agents_md.write_text(
                "# Workspace Agents\nFind files in ~/.archon/history/",
                encoding="utf-8",
            )

            bot = MagicMock()
            bot.send_message = AsyncMock()
            sm = MagicMock()
            sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True, inject_context=MagicMock()))
            sm.track_context = MagicMock()
            sm.inject_agent_context = MagicMock()

            mock_agent_session = _make_mock_agent_session()

            with patch(
                "archon.ai.background_agent_manager.ClaudeSession",
                return_value=mock_agent_session,
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=tmpdir,
                )
                run = await manager.spawn(user_id=1, task="find yesterday's script")

                # Wait for agent task to complete using the public done event
                await asyncio.wait_for(run.done.wait(), timeout=5.0)

            # inject_context must have been called with agents.md content
            inject_calls = mock_agent_session.inject_context.call_args_list
            agents_md_injected = any(
                "Workspace Agents" in str(call_args[0][0])
                for call_args in inject_calls
            )
            assert agents_md_injected, (
                f"Expected inject_context to be called with 'Workspace Agents'. "
                f"Actual calls: {inject_calls}"
            )

            await manager.stop_all()

    async def test_background_agent_skips_inject_when_no_agents_md(self) -> None:
        """Background agent session does not get agents.md injected when file is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # No agents.md in tmpdir

            bot = MagicMock()
            bot.send_message = AsyncMock()
            sm = MagicMock()
            sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True, inject_context=MagicMock()))
            sm.track_context = MagicMock()
            sm.inject_agent_context = MagicMock()

            mock_agent_session = _make_mock_agent_session()

            with patch(
                "archon.ai.background_agent_manager.ClaudeSession",
                return_value=mock_agent_session,
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=tmpdir,
                )
                run = await manager.spawn(user_id=1, task="find yesterday's script")

                # Wait for agent task to complete using the public done event
                await asyncio.wait_for(run.done.wait(), timeout=5.0)

            inject_calls = mock_agent_session.inject_context.call_args_list
            agents_md_injected = any(
                "Workspace Agents" in str(call_args[0][0])
                for call_args in inject_calls
            )
            assert not agents_md_injected, (
                f"Expected inject_context NOT to be called with 'Workspace Agents'. "
                f"Actual calls: {inject_calls}"
            )

            await manager.stop_all()


# ──────────────────────────────────────────────────────────────────
# Group 5: get_recent_context() returning None
# ──────────────────────────────────────────────────────────────────


class TestOrchSessionNoneRecentContext:
    async def test_orch_session_inject_only_startup_prompt_when_no_recent_context(self) -> None:
        """When get_recent_context() returns None, only the startup prompt is injected (no separator)."""
        provider = _mock_context_provider(
            startup_prompt="# History",
            recent_context=None,
        )
        decomposer, _, orch_session, _ = _make_decomposer(context_provider=provider)

        await decomposer.start()

        orch_session.inject_context.assert_called_once()
        injected_text = orch_session.inject_context.call_args_list[0][0][0]
        assert injected_text == "# History", (
            f"Expected exactly '# History' (no separator, no context). Got: {injected_text!r}"
        )

        await decomposer.stop()
