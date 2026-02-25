"""Tests for BackgroundAgentManager — S15.2 + FR.15 (per-agent working beacon)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from archon.ai.background_agent_manager import (
    AgentRun,
    BackgroundAgentManager,
    _agent_status_text,
    _AGENT_BEACON_WORDS,
)
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
    sm.get_or_create.return_value = MagicMock()

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

    async def test_spawn_sends_spawn_notification(self) -> None:
        """spawn() sends '🤖 Agent [name] spawned.' immediately when the agent is created."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        slow_session = _make_slow_claude_session(delay=10.0)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=55, task="some task")

        bot.send_message.assert_awaited_once()
        call_args = bot.send_message.call_args
        assert call_args[0][0] == 55          # correct user_id
        msg = call_args[0][1]
        assert "🤖" in msg
        assert "spawned" in msg.lower()
        assert run.name in msg

        await manager.stop_all()

    async def test_spawn_notification_uses_pool_name(self) -> None:
        """The name in the spawn notification always comes from _AGENT_NAMES."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        slow_session = _make_slow_claude_session(delay=10.0)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="pool name test")

        assert run.name in _AGENT_NAMES, f"{run.name!r} not in _AGENT_NAMES pool"

        await manager.stop_all()

    async def test_spawn_creates_asyncio_task(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, max_parallel=1)
            await manager.spawn(user_id=1, task="user1 task")
            # user2 should NOT be blocked by user1's agent
            await manager.spawn(user_id=2, task="user2 task")

        await manager.stop_all()


# ──────────────────────────────────────────────────────────────────
# Agent completion — result and notification
# ──────────────────────────────────────────────────────────────────


class TestAgentCompletion:
    async def test_successful_run_sets_status_completed(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        fast_session = _make_mock_claude_session(result="agent output")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=42, task="do work")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # spawn notification + completion notification = 2 calls
        assert bot.send_message.await_count == 2
        calls = bot.send_message.call_args_list
        # First call: spawn notification
        assert calls[0][0][0] == 42
        assert "🤖" in calls[0][0][1]
        assert "spawned" in calls[0][0][1].lower()
        # Second call: completion notification — full result included
        assert calls[1][0][0] == 42
        msg = calls[1][0][1]
        assert "🤖" in msg
        assert "completed" in msg.lower()
        assert run.name in msg
        assert "agent output" in msg  # full result, not truncated

    async def test_successful_run_full_result_not_truncated(self) -> None:
        """The full result must appear in chat — not cut at 800 or any other limit."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        long_result = "x" * 1200  # longer than the old 800-char limit
        fast_session = _make_mock_claude_session(result=long_result)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="long result task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        all_text = " ".join(c[0][1] for c in bot.send_message.call_args_list)
        assert long_result in all_text, "Full result must appear across sent messages"

    async def test_successful_run_splits_result_over_telegram_limit(self) -> None:
        """Results longer than 4000 chars are split into labelled chunks."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        # 9000 chars → header (1) + 3 chunks (ceil(9000/4000) = 3) + spawn (1) = 5 total
        long_result = "a" * 9000
        fast_session = _make_mock_claude_session(result=long_result)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="huge result task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        calls = bot.send_message.call_args_list
        messages = [c[0][1] for c in calls]
        # spawn + header + 3 chunks = 5
        assert len(messages) == 5
        # chunks are labelled
        chunk_msgs = [m for m in messages if m.startswith("[")]
        assert len(chunk_msgs) == 3
        assert "[1/3]" in chunk_msgs[0]
        assert "[2/3]" in chunk_msgs[1]
        assert "[3/3]" in chunk_msgs[2]
        # full content is present
        combined = "".join(m.split("\n", 1)[1] for m in chunk_msgs)
        assert combined == long_result

    async def test_failed_run_sets_status_failed(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
        sm.get_or_create = AsyncMock(return_value=main_session_mock)
        failing_session = _make_failing_claude_session(error="network error")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=7, task="fail task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # spawn notification + failure notification = 2 calls
        assert bot.send_message.await_count == 2
        calls = bot.send_message.call_args_list
        # First call: spawn notification
        assert "🤖" in calls[0][0][1]
        assert "spawned" in calls[0][0][1].lower()
        # Second call: failure notification
        msg = calls[1][0][1]
        assert "❌" in msg


# ──────────────────────────────────────────────────────────────────
# stop_all()
# ──────────────────────────────────────────────────────────────────


class TestStopAll:
    async def test_stop_all_cancels_running_tasks(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        main_session_mock = MagicMock(is_alive=True)
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
        main_session_mock = MagicMock(is_alive=True)
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


# ──────────────────────────────────────────────────────────────────
# FR.003 — agent_logger integration in BackgroundAgentManager
# ──────────────────────────────────────────────────────────────────


def _make_multi_event_session(events: list) -> MagicMock:
    """Return a mock ClaudeSession that yields the given events."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        for event in events:
            yield event

    session.send = _send
    return session


class TestBackgroundAgentManagerAgentLogger:
    def test_background_agent_manager_accepts_agent_logger(self) -> None:
        """BackgroundAgentManager constructor accepts an agent_logger parameter."""
        bot = _make_bot()
        sm = _make_session_manager()
        mock_logger = MagicMock()
        # Must not raise
        manager = BackgroundAgentManager(
            bot=bot,
            session_manager=sm,
            agent_logger=mock_logger,
        )
        assert manager._agent_logger is mock_logger

    def test_background_agent_manager_agent_logger_defaults_to_none(self) -> None:
        """BackgroundAgentManager without agent_logger has _agent_logger=None."""
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        assert manager._agent_logger is None

    async def test_background_agent_events_tagged_sub_agent_source(self) -> None:
        """All events produced by background agents get source='sub-agent'."""
        from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted

        received_sources: list[str] = []
        mock_logger = MagicMock()
        mock_logger.record_event = MagicMock(
            side_effect=lambda ev: received_sources.append(getattr(ev, "source", "MISSING"))
        )

        events = [
            ThinkingResult(content="thinking"),
            ToolStarted(name="Read"),
            Response(content="done"),
        ]
        agent_session = _make_multi_event_session(events)

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=agent_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                agent_logger=mock_logger,
            )
            run = await manager.spawn(user_id=1, task="tagged task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # All sources forwarded to agent_logger must be "sub-agent"
        assert all(s == "sub-agent" for s in received_sources), (
            f"All events must have source='sub-agent', got: {received_sources}"
        )

    async def test_background_agent_events_forwarded_to_agent_logger(self) -> None:
        """All events during agent execution are forwarded to agent_logger.record_event,
        plus a SubagentStarted before and SubagentStopped after the event loop (+2 lifecycle)."""
        from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted

        mock_logger = MagicMock()
        mock_logger.record_event = MagicMock()

        events = [
            ThinkingResult(content="thinking"),
            ToolStarted(name="Bash"),
            Response(content="finished"),
        ]
        agent_session = _make_multi_event_session(events)

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=agent_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                agent_logger=mock_logger,
            )
            run = await manager.spawn(user_id=1, task="log all task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # SubagentStarted + all events + SubagentStopped
        expected_calls = len(events) + 2
        assert mock_logger.record_event.call_count == expected_calls, (
            f"Expected {expected_calls} record_event calls "
            f"(SubagentStarted + {len(events)} events + SubagentStopped), "
            f"got {mock_logger.record_event.call_count}"
        )

    async def test_agent_logger_receives_subagent_started_first(self) -> None:
        """SubagentStarted is the first call to record_event — opens the log file."""
        from archon.ai.event_mapper import Response, SubagentStarted, ThinkingResult

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = MagicMock(side_effect=lambda ev: received.append(ev))

        events = [ThinkingResult(content="thought"), Response(content="done")]
        agent_session = _make_multi_event_session(events)

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=agent_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(user_id=1, task="order test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert isinstance(received[0], SubagentStarted), (
            f"First record_event call must be SubagentStarted, got {type(received[0])}"
        )
        assert received[0].agent_id == run.run_id
        assert received[0].agent_name == run.name

    async def test_agent_logger_receives_subagent_stopped_last(self) -> None:
        """SubagentStopped is the last call to record_event — finalizes the log file."""
        from archon.ai.event_mapper import Response, SubagentStopped, ThinkingResult

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = MagicMock(side_effect=lambda ev: received.append(ev))

        events = [ThinkingResult(content="thought"), Response(content="done")]
        agent_session = _make_multi_event_session(events)

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=agent_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(user_id=1, task="order test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert isinstance(received[-1], SubagentStopped), (
            f"Last record_event call must be SubagentStopped, got {type(received[-1])}"
        )
        assert received[-1].agent_id == run.run_id
        assert received[-1].agent_name == run.name

    async def test_agent_logger_subagent_stopped_emitted_on_failure(self) -> None:
        """SubagentStopped is emitted even when the agent session raises an exception."""
        from archon.ai.event_mapper import SubagentStarted, SubagentStopped

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = MagicMock(side_effect=lambda ev: received.append(ev))

        failing_session = _make_failing_claude_session(error="crash")

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(user_id=1, task="failing task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed"
        types = [type(e) for e in received]
        assert SubagentStarted in types, "SubagentStarted must be emitted even on failure"
        assert SubagentStopped in types, "SubagentStopped must be emitted even on failure"
        assert types.index(SubagentStarted) < types.index(SubagentStopped), (
            "SubagentStarted must come before SubagentStopped"
        )

    async def test_agent_logger_subagent_stopped_emitted_on_cancel(self) -> None:
        """SubagentStopped is emitted even when the agent is cancelled mid-run."""
        from archon.ai.event_mapper import SubagentStarted, SubagentStopped

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = MagicMock(side_effect=lambda ev: received.append(ev))

        slow_session = _make_slow_claude_session(delay=30.0)

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(user_id=1, task="slow task")
            await asyncio.sleep(0.05)  # let the task enter the event loop
            await manager.cancel(run.run_id)
            await asyncio.sleep(0.05)  # let cancellation propagate

        assert run.status == "cancelled"
        types = [type(e) for e in received]
        assert SubagentStarted in types, "SubagentStarted must be emitted before cancel"
        assert SubagentStopped in types, "SubagentStopped must be emitted on cancellation"

    async def test_background_agent_no_agent_logger_does_not_crash(self) -> None:
        """When agent_logger=None, the agent still runs without crashing."""
        from archon.ai.event_mapper import Response

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())
        fast_session = _make_mock_claude_session(result="ok")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)  # no agent_logger
            run = await manager.spawn(user_id=1, task="no logger task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "completed"


# ──────────────────────────────────────────────────────────────────
# FR.15 — Per-agent working beacon
# ──────────────────────────────────────────────────────────────────


def _make_beacon_bot(message_id: int = 12345) -> MagicMock:
    """Bot mock with both send_message and edit_message_text as AsyncMocks.

    The send_message return value has a real integer .message_id so the
    BackgroundAgentManager can capture it and start the beacon task.
    """
    sent = MagicMock()
    sent.message_id = message_id

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=sent)
    bot.edit_message_text = AsyncMock()
    return bot


def _make_multi_event_session_with_tools(
    tool_count: int = 3, thinking_count: int = 2
) -> MagicMock:
    """Mock ClaudeSession that yields ToolStarted + ThinkingStarted events then a Response."""
    from archon.ai.event_mapper import Response, ThinkingStarted, ToolStarted

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    events = (
        [ToolStarted(name=f"Tool{i}") for i in range(tool_count)]
        + [ThinkingStarted() for _ in range(thinking_count)]
        + [Response(content="done")]
    )

    async def _send(prompt: str):  # type: ignore[return]
        for ev in events:
            yield ev

    session.send = _send
    return session


def _make_pausing_session(pause_secs: float = 0.15) -> MagicMock:
    """Mock session that pauses long enough for the beacon to fire, then completes."""
    from archon.ai.event_mapper import Response, ToolStarted, ThinkingStarted

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        yield ToolStarted(name="Read")
        yield ToolStarted(name="Bash")
        yield ThinkingStarted()
        await asyncio.sleep(pause_secs)  # beacon fires during this pause
        yield Response(content="paused result")

    session.send = _send
    return session


# ── _agent_status_text helper ─────────────────────────────────────


class TestAgentStatusText:
    """Unit tests for the _agent_status_text module-level helper."""

    def test_no_counts_shows_verb_only(self) -> None:
        text = _agent_status_text("Atlas", 0, 0, "working")
        assert text == "🤖 Agent <b>Atlas</b> is working..."

    def test_with_tools_only(self) -> None:
        text = _agent_status_text("Nova", 5, 0, "pondering")
        assert "5 tools" in text
        assert "thinking" not in text
        assert "🤖 Agent <b>Nova</b>" in text

    def test_with_thinking_only(self) -> None:
        text = _agent_status_text("Sage", 0, 2, "working")
        assert "2 thinking" in text
        assert "tools" not in text

    def test_with_both_counts(self) -> None:
        text = _agent_status_text("Orion", 7, 3, "brewing")
        assert "7 tools" in text
        assert "3 thinking" in text
        assert "🤖 Agent <b>Orion</b> is brewing..." in text

    def test_tool_singular(self) -> None:
        text = _agent_status_text("Echo", 1, 0, "working")
        assert "1 tool" in text
        assert "1 tools" not in text

    def test_tool_plural(self) -> None:
        text = _agent_status_text("Echo", 2, 0, "working")
        assert "2 tools" in text

    def test_counts_in_parentheses(self) -> None:
        text = _agent_status_text("Flux", 4, 1, "working")
        assert "(" in text
        assert ")" in text

    def test_no_counts_no_parentheses(self) -> None:
        text = _agent_status_text("Gale", 0, 0, "deliberating")
        assert "(" not in text
        assert ")" not in text

    def test_default_word_is_working(self) -> None:
        text = _agent_status_text("Jade", 0, 0)
        assert "working" in text


# ── _AGENT_BEACON_WORDS constant ──────────────────────────────────


class TestAgentBeaconWords:
    def test_beacon_words_is_non_empty_tuple(self) -> None:
        assert isinstance(_AGENT_BEACON_WORDS, tuple)
        assert len(_AGENT_BEACON_WORDS) > 0

    def test_beacon_words_are_strings(self) -> None:
        assert all(isinstance(w, str) for w in _AGENT_BEACON_WORDS)

    def test_working_not_in_beacon_words(self) -> None:
        """'working' is the initial word; it must not appear in the rotation pool."""
        assert "working" not in _AGENT_BEACON_WORDS


# ── AgentRun with beacon fields ───────────────────────────────────


class TestAgentRunBeaconFields:
    def test_beacon_message_id_defaults_to_none(self) -> None:
        run = AgentRun(
            run_id="abc",
            name="Atlas",
            task="t",
            context="",
            user_id=1,
            started_at=0.0,
        )
        assert run.beacon_message_id is None

    def test_beacon_ready_is_asyncio_event(self) -> None:
        run = AgentRun(
            run_id="abc",
            name="Atlas",
            task="t",
            context="",
            user_id=1,
            started_at=0.0,
        )
        assert isinstance(run._beacon_ready, asyncio.Event)

    def test_beacon_ready_starts_not_set(self) -> None:
        run = AgentRun(
            run_id="abc",
            name="Atlas",
            task="t",
            context="",
            user_id=1,
            started_at=0.0,
        )
        assert not run._beacon_ready.is_set()


# ── BackgroundAgentManager constructor — beacon_interval_minutes ──


class TestBeaconConstructor:
    def test_beacon_interval_defaults_to_two(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        assert manager._beacon_interval_minutes == 2

    def test_beacon_interval_configurable(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm, beacon_interval_minutes=5)
        assert manager._beacon_interval_minutes == 5

    def test_beacon_interval_zero_disables_beacon(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm, beacon_interval_minutes=0)
        assert manager._beacon_interval_minutes == 0


# ── spawn() stores beacon_message_id and sets _beacon_ready ───────


class TestSpawnBeaconSetup:
    async def test_spawn_sets_beacon_message_id_from_send_message_return(self) -> None:
        """spawn() calls _notify_spawn which captures the message_id from bot.send_message."""
        bot = _make_beacon_bot(message_id=9999)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=_make_slow_claude_session(),
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="beacon setup test")

        assert run.beacon_message_id == 9999
        await manager.stop_all()

    async def test_spawn_sets_beacon_ready_event(self) -> None:
        """_beacon_ready is set after spawn notification completes."""
        bot = _make_beacon_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=_make_slow_claude_session(),
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="beacon ready test")

        assert run._beacon_ready.is_set()
        await manager.stop_all()

    async def test_spawn_sets_beacon_ready_even_when_send_message_fails(self) -> None:
        """_beacon_ready is set even if the Telegram send_message call fails."""
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("Telegram flap"))
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=_make_slow_claude_session(),
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="failed spawn notification")

        # _beacon_ready must be set so _run_agent doesn't hang
        assert run._beacon_ready.is_set()
        # beacon_message_id is None because notification failed
        assert run.beacon_message_id is None
        await manager.stop_all()

    async def test_beacon_message_id_none_when_send_message_has_no_message_id_attr(
        self,
    ) -> None:
        """If bot.send_message returns an object without message_id, beacon is not started."""
        returned = MagicMock(spec=[])  # no attributes at all
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=returned)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=_make_slow_claude_session(),
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="no message_id test")

        assert run.beacon_message_id is None
        assert run._beacon_ready.is_set()
        await manager.stop_all()


# ── Beacon fires and edits spawn message ──────────────────────────


class TestBeaconFires:
    async def test_beacon_disabled_when_interval_zero(self) -> None:
        """beacon_interval_minutes=0 → edit_message_text is never called."""
        bot = _make_beacon_bot(message_id=111)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Use a session that pauses briefly; if beacon were enabled, it would fire.
        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0
            )
            run = await manager.spawn(user_id=1, task="no beacon test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        bot.edit_message_text.assert_not_called()

    async def test_beacon_not_started_when_message_id_none(self) -> None:
        """When send_message fails (no message_id), the beacon task is never started."""
        bot = MagicMock()
        bot.send_message = AsyncMock(side_effect=Exception("Telegram flap"))
        bot.edit_message_text = AsyncMock()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Short interval so any accidental beacon would fire quickly.
        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="no msg_id beacon test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        bot.edit_message_text.assert_not_called()

    async def test_beacon_fires_and_calls_edit_message_text(self) -> None:
        """When beacon_interval_minutes is short, edit_message_text is called at least once."""
        # Interval: 0.001 min = ~60ms.  Session pauses 0.15s → beacon fires ~2x.
        bot = _make_beacon_bot(message_id=4242)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="beacon fires test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert bot.edit_message_text.await_count >= 1

    async def test_beacon_edits_correct_message_id(self) -> None:
        """edit_message_text is called with the message_id from the spawn notification."""
        bot = _make_beacon_bot(message_id=7777)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="beacon message_id test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert bot.edit_message_text.await_count >= 1
        call_kwargs = bot.edit_message_text.call_args_list[0][1]
        assert call_kwargs.get("message_id") == 7777

    async def test_beacon_edits_correct_chat_id(self) -> None:
        """edit_message_text is called with chat_id == user_id (Telegram private chats)."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=55, task="chat_id test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        call_kwargs = bot.edit_message_text.call_args_list[0][1]
        assert call_kwargs.get("chat_id") == 55

    async def test_beacon_message_contains_agent_name(self) -> None:
        """The beacon text always contains the agent name."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="name in beacon test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert bot.edit_message_text.await_count >= 1
        beacon_text: str = bot.edit_message_text.call_args_list[0][1].get("text", "")
        assert run.name in beacon_text

    async def test_beacon_first_edit_uses_working_verb(self) -> None:
        """First beacon edit uses the word 'working', not a random verb."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="first verb test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        first_text: str = bot.edit_message_text.call_args_list[0][1].get("text", "")
        assert "working" in first_text

    async def test_beacon_subsequent_edits_use_beacon_words(self) -> None:
        """After the first edit, subsequent edits use words from _AGENT_BEACON_WORDS."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Use a long pause (0.3s) with interval 0.001 min (~60ms) → at least 4 fires
        session = _make_pausing_session(pause_secs=0.35)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="beacon words test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        calls = bot.edit_message_text.call_args_list
        assert len(calls) >= 2, "Need at least 2 beacon fires to test word rotation"
        second_text: str = calls[1][1].get("text", "")
        # Second edit must use one of the beacon words (not "working")
        assert any(word in second_text for word in _AGENT_BEACON_WORDS)

    async def test_beacon_includes_tool_counts_in_text(self) -> None:
        """Beacon text reflects cumulative ToolStarted event counts."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Session yields 2 ToolStarted events then pauses so beacon fires
        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="tool count test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # The pausing session yields 2 ToolStarted + 1 ThinkingStarted before pause
        last_text: str = bot.edit_message_text.call_args_list[-1][1].get("text", "")
        assert "tool" in last_text  # "2 tools" or "1 tool"

    async def test_beacon_includes_thinking_counts_in_text(self) -> None:
        """Beacon text reflects cumulative ThinkingStarted event counts."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="thinking count test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # Pausing session yields 1 ThinkingStarted before pause
        last_text: str = bot.edit_message_text.call_args_list[-1][1].get("text", "")
        assert "thinking" in last_text

    async def test_beacon_uses_html_parse_mode(self) -> None:
        """edit_message_text is always called with parse_mode='HTML'."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="parse mode test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        for c in bot.edit_message_text.call_args_list:
            assert c[1].get("parse_mode") == "HTML"


# ── Beacon lifecycle — cancelled on completion/failure/cancel ─────


class TestBeaconLifecycle:
    async def test_beacon_cancelled_on_agent_completion(self) -> None:
        """No edit_message_text calls happen after agent completes.

        The agent completes almost instantly; if the beacon were not cancelled, it
        might fire after completion (wrong). We verify the count is consistent.
        """
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        fast_session = _make_mock_claude_session(result="done instantly")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=2
            )
            run = await manager.spawn(user_id=1, task="fast task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # Agent completes in <1ms; beacon interval is 2min → beacon never fires
        assert bot.edit_message_text.await_count == 0

    async def test_beacon_cancelled_on_agent_failure(self) -> None:
        """Beacon task is cancelled when the agent raises an exception."""
        bot = _make_beacon_bot(message_id=2)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        failing_session = _make_failing_claude_session(error="boom")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=2
            )
            run = await manager.spawn(user_id=1, task="failing task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed"
        assert bot.edit_message_text.await_count == 0

    async def test_beacon_cancelled_on_agent_cancellation(self) -> None:
        """Beacon task is cancelled when the agent is cancelled."""
        bot = _make_beacon_bot(message_id=3)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=_make_slow_claude_session(delay=30.0),
        ):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=2
            )
            run = await manager.spawn(user_id=1, task="cancelled task")
            await asyncio.sleep(0.05)
            await manager.cancel(run.run_id)
            await asyncio.sleep(0.05)

        assert run.status == "cancelled"
        # Beacon was sleeping for 2 min so it never fired
        assert bot.edit_message_text.await_count == 0

    async def test_beacon_survives_edit_api_error(self) -> None:
        """If edit_message_text raises an exception, beacon keeps running silently."""
        bot = _make_beacon_bot(message_id=1)
        bot.edit_message_text = AsyncMock(side_effect=Exception("Telegram edit error"))
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Long enough pause to let the beacon fire 2+ times
        session = _make_pausing_session(pause_secs=0.25)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            # Must not raise
            run = await manager.spawn(user_id=1, task="edit error test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # edit_message_text was called (and failed silently) — agent still completed
        assert run.status == "completed"
        assert bot.edit_message_text.await_count >= 1

    async def test_beacon_does_not_fire_after_stop_all(self) -> None:
        """stop_all() cancels both the agent task and its beacon."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=_make_slow_claude_session(delay=30.0),
        ):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=2
            )
            run = await manager.spawn(user_id=1, task="stop_all beacon test")
            await asyncio.sleep(0.05)
            await manager.stop_all()
            await asyncio.sleep(0.05)

        assert run.status == "cancelled"
        assert bot.edit_message_text.await_count == 0


# ── Existing tests must still pass with beacon enabled (regression) ─


class TestBeaconRegressionExistingBehavior:
    """Ensure existing send_message call counts are not affected by the beacon."""

    async def test_send_message_count_unaffected_by_beacon_completion(self) -> None:
        """Completion: exactly 2 send_message calls (spawn + result), zero from beacon."""
        bot = _make_beacon_bot(message_id=5)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        fast_session = _make_mock_claude_session(result="fast")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=2
            )
            run = await manager.spawn(user_id=1, task="regression test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert bot.send_message.await_count == 2  # spawn + completion

    async def test_send_message_count_unaffected_by_beacon_failure(self) -> None:
        """Failure: exactly 2 send_message calls (spawn + error), zero from beacon."""
        bot = _make_beacon_bot(message_id=5)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        failing_session = _make_failing_claude_session(error="crash")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=2
            )
            run = await manager.spawn(user_id=1, task="failure regression")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert bot.send_message.await_count == 2  # spawn + failure notification

    async def test_orchestrator_beacon_not_affected(self) -> None:
        """handler.py _partial_update_task is unrelated to the agent beacon — both
        can coexist since they use separate bot calls on separate chat IDs."""
        # This test verifies that BackgroundAgentManager has NO interaction with
        # the orchestrator-level _partial_update_task (from handler.py).
        # Simply confirm agent beacon uses edit_message_text, NOT send_message.
        bot = _make_beacon_bot(message_id=100)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="orch beacon test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # Beacon uses edit_message_text (edits spawn msg), not send_message
        assert bot.edit_message_text.await_count >= 1
        # send_message used only for spawn + completion (2 calls)
        assert bot.send_message.await_count == 2
