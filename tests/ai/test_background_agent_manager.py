"""Tests for BackgroundAgentManager — S15.2."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from archon.ai.background_agent_manager import AgentRun, BackgroundAgentManager
from archon.ai.claude_session import _AGENT_NAMES


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


def _make_session_manager() -> MagicMock:
    sm = MagicMock()
    sm.get_or_create = AsyncMock()
    return sm


def _make_mock_claude_session(result: str = "agent result") -> MagicMock:
    """Return a mock ClaudeSession that completes successfully."""
    from archon.ai.event_mapper import Response

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.inject_context = MagicMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        yield Response(content=result)

    session.send = _send
    return session


def _make_failing_claude_session(error: str = "boom") -> MagicMock:
    """Return a mock ClaudeSession whose send() raises an exception."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.inject_context = MagicMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        raise RuntimeError(error)
        yield  # make it an async generator

    session.send = _send
    return session


def _make_slow_claude_session(delay: float = 10.0) -> MagicMock:
    """Return a mock ClaudeSession that sleeps for `delay` seconds."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.inject_context = MagicMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        await asyncio.sleep(delay)
        from archon.ai.event_mapper import Response
        yield Response(content="slow result")

    session.send = _send
    return session


def _make_manager(
    max_parallel: int = 5,
    result: str = "agent result",
) -> tuple[BackgroundAgentManager, MagicMock, MagicMock]:
    """Return (manager, bot, main_session) with a mock that completes immediately."""
    bot = _make_bot()
    sm = _make_session_manager()
    mock_agent_session = _make_mock_claude_session(result)
    sm.get_or_create.return_value = MagicMock(inject_context=MagicMock())

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_agent_session):
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=sm,
            max_parallel=max_parallel,
        )
    return manager, bot, sm


# ──────────────────────────────────────────────────────────────────
# AgentRun dataclass
# ──────────────────────────────────────────────────────────────────


class TestAgentRun:
    def test_agent_run_defaults(self) -> None:
        run = AgentRun(
            run_id="abc123",
            name="Atlas",
            task="do something",
            context="",
            user_id=42,
            started_at=1.0,
        )
        assert run.run_id == "abc123"
        assert run.name == "Atlas"
        assert run.task == "do something"
        assert run.context == ""
        assert run.user_id == 42
        assert run.started_at == 1.0
        assert run.status == "running"
        assert run.result is None
        assert run.error is None

    def test_agent_run_with_all_fields(self) -> None:
        run = AgentRun(
            run_id="xyz",
            name="Nova",
            task="summarize",
            context="some context",
            user_id=99,
            started_at=2.0,
            status="completed",
            result="done",
        )
        assert run.status == "completed"
        assert run.result == "done"


# ──────────────────────────────────────────────────────────────────
# spawn() — basic fire-and-forget
# ──────────────────────────────────────────────────────────────────


class TestSpawn:
    async def test_spawn_returns_agent_run_immediately(self) -> None:
        """spawn() returns before the agent completes."""
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock()
        main_session_mock.inject_context = MagicMock()
        main_session_mock.is_alive = True
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        slow_session = _make_slow_claude_session(delay=10.0)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="slow task")

        assert run.status == "running"
        assert run.run_id != ""
        assert run.name in _AGENT_NAMES

        # Cleanup
        await manager.stop_all()

    async def test_spawn_creates_asyncio_task(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        slow_session = _make_slow_claude_session(delay=10.0)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="task")

        assert run._task_ref is not None
        assert isinstance(run._task_ref, asyncio.Task)

        await manager.stop_all()

    async def test_spawn_assigns_name_from_pool(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="task")

        assert run.name in _AGENT_NAMES
        await manager.stop_all()

    async def test_spawn_uses_preferred_name_if_available(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="task", name="Sage")

        assert run.name == "Sage"
        await manager.stop_all()

    async def test_spawn_no_duplicate_names_for_concurrent_agents(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=10)
            run1 = await manager.spawn(user_id=1, task="t1")
            run2 = await manager.spawn(user_id=1, task="t2")

        assert run1.name != run2.name

        await manager.stop_all()


# ──────────────────────────────────────────────────────────────────
# list_running / list_all
# ──────────────────────────────────────────────────────────────────


class TestListAgents:
    async def test_list_running_returns_running_agents(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=5)
            await manager.spawn(user_id=1, task="t1")
            await manager.spawn(user_id=1, task="t2")
            running = manager.list_running(user_id=1)

        assert len(running) == 2
        assert all(r.status == "running" for r in running)

        await manager.stop_all()

    async def test_list_running_filters_by_user_id(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=5)
            await manager.spawn(user_id=1, task="for user 1")
            await manager.spawn(user_id=2, task="for user 2")

        assert len(manager.list_running(user_id=1)) == 1
        assert len(manager.list_running(user_id=2)) == 1
        assert len(manager.list_running(user_id=99)) == 0

        await manager.stop_all()

    async def test_list_running_empty_when_none(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        assert manager.list_running(user_id=1) == []

    async def test_list_all_includes_completed_agents(self) -> None:
        """list_all() returns completed agents too."""
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        fast_session = _make_mock_claude_session(result="done")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="quick task")
            # Wait for task to complete
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # Even after completion, list_all should include it
        all_runs = manager.list_all(user_id=1)
        assert len(all_runs) >= 1
        assert any(r.run_id == run.run_id for r in all_runs)


# ──────────────────────────────────────────────────────────────────
# cancel()
# ──────────────────────────────────────────────────────────────────


class TestCancel:
    async def test_cancel_running_agent_returns_true(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="long task")
            # Give the task a chance to start running before we cancel it.
            # Without this the task may not have entered _run_agent yet, so
            # cancellation would not call our except handler.
            await asyncio.sleep(0.05)
            result = await manager.cancel(run.run_id)
            assert result is True
            await asyncio.sleep(0.05)  # let cancellation propagate

        assert run.status == "cancelled"

    async def test_cancel_unknown_run_id_returns_false(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        result = await manager.cancel("nonexistent-id")
        assert result is False

    async def test_cancel_sets_status_cancelled(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="task")
            await asyncio.sleep(0.05)  # let task start before cancelling
            await manager.cancel(run.run_id)
            await asyncio.sleep(0.05)  # let cancellation propagate

        assert run.status == "cancelled"


# ──────────────────────────────────────────────────────────────────
# Max parallel limit
# ──────────────────────────────────────────────────────────────────


class TestMaxParallel:
    async def test_exceeding_max_parallel_raises_runtime_error(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=2)
            await manager.spawn(user_id=1, task="t1")
            await manager.spawn(user_id=1, task="t2")
            with pytest.raises(RuntimeError, match="[Mm]ax"):
                await manager.spawn(user_id=1, task="t3 should fail")

        await manager.stop_all()

    async def test_max_parallel_applies_per_user(self) -> None:
        """Different users have independent limits."""
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=1)
            await manager.spawn(user_id=1, task="user1 task")
            # user2 should NOT be blocked by user1's agent
            await manager.spawn(user_id=2, task="user2 task")

        await manager.stop_all()


# ──────────────────────────────────────────────────────────────────
# Agent completion — result, notification, inject_context
# ──────────────────────────────────────────────────────────────────


class TestAgentCompletion:
    async def test_successful_run_sets_status_completed(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        fast_session = _make_mock_claude_session(result="great result")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="quick task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "completed"
        assert run.result == "great result"
        assert run.error is None

    async def test_successful_run_sends_telegram_notification(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        fast_session = _make_mock_claude_session(result="agent output")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=42, task="do work")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        bot.send_message.assert_awaited_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == 42  # user_id
        msg = call_args[0][1]
        assert "✅" in msg
        assert run.name in msg

    async def test_successful_run_calls_inject_context(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        fast_session = _make_mock_claude_session(result="injected result")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="inject test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        main_session_mock.inject_context.assert_called_once()
        injected_text: str = main_session_mock.inject_context.call_args[0][0]
        assert run.name in injected_text
        assert "injected result" in injected_text

    async def test_inject_context_format(self) -> None:
        """Result injection uses the canonical [Background agent X] format."""
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        fast_session = _make_mock_claude_session(result="the answer")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="the question")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        injected: str = main_session_mock.inject_context.call_args[0][0]
        assert f"[Background agent {run.name} completed]" in injected
        assert "the question" in injected
        assert "the answer" in injected
        assert f"[End agent {run.name}]" in injected

    async def test_failed_run_sets_status_failed(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        failing_session = _make_failing_claude_session(error="something exploded")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="doomed task")
            if run._task_ref:
                # _run_agent catches the exception internally and returns normally;
                # the task itself does NOT raise — just wait for it to complete.
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed"
        assert run.error is not None
        assert "exploded" in run.error

    async def test_failed_run_sends_error_notification(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        failing_session = _make_failing_claude_session(error="network error")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=7, task="fail task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        bot.send_message.assert_awaited_once()
        msg = bot.send_message.call_args[0][1]
        assert "❌" in msg

    async def test_failed_run_does_not_call_inject_context(self) -> None:
        """inject_context should NOT be called when the agent fails."""
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        failing_session = _make_failing_claude_session()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="fail")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        main_session_mock.inject_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# stop_all()
# ──────────────────────────────────────────────────────────────────


class TestStopAll:
    async def test_stop_all_cancels_running_tasks(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session(delay=30.0)):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=5)
            run1 = await manager.spawn(user_id=1, task="t1")
            run2 = await manager.spawn(user_id=2, task="t2")
            # Give tasks a chance to start before stop_all cancels them
            await asyncio.sleep(0.05)
            await manager.stop_all()
            await asyncio.sleep(0.05)

        assert run1.status == "cancelled"
        assert run2.status == "cancelled"

    async def test_stop_all_with_no_agents_is_noop(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        await manager.stop_all()  # must not raise


# ──────────────────────────────────────────────────────────────────
# get_run()
# ──────────────────────────────────────────────────────────────────


class TestGetRun:
    async def test_get_run_returns_run_by_id(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(inject_context=MagicMock(), is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="findme")

        assert manager.get_run(run.run_id) is run
        await manager.stop_all()

    async def test_get_run_returns_none_for_unknown_id(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        assert manager.get_run("no-such-id") is None
