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
    sm.track_context = MagicMock()
    sm.inject_agent_context = MagicMock()
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
    async def test_spawn_create_task_raises_does_not_leave_phantom_run_global_patch(self) -> None:
        """If create_task() raises (global patch), _runs must not contain a phantom entry."""
        bot = _make_bot()
        sm = _make_session_manager()

        with patch("archon.ai.background_agent_manager.ClaudeSession",
                   return_value=_make_slow_claude_session()):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)

        with patch("asyncio.create_task", side_effect=RuntimeError("loop closing")):
            with pytest.raises(RuntimeError, match="loop closing"):
                await manager.spawn(user_id=1, task="will fail")

        assert len(manager._runs) == 0, "phantom run must not remain in _runs"
        assert len(manager.list_running(user_id=1)) == 0

    async def test_spawn_create_task_raises_does_not_leave_phantom_run(self) -> None:
        """If create_task() raises, _runs must not contain the phantom entry,
        and _active_names must also be empty (F4)."""
        bot = _make_bot()
        sm = _make_session_manager()

        with patch(
            "archon.ai.background_agent_manager.asyncio.create_task",
            side_effect=RuntimeError("event loop is closed"),
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            with pytest.raises(RuntimeError, match="event loop is closed"):
                await manager.spawn(user_id=1, task="phantom task")

        assert len(manager._runs) == 0
        assert len(manager._active_names) == 0, (
            "_active_names must be empty after create_task failure"
        )


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

    async def test_successful_run_result_markdown_converted_to_html(self) -> None:
        """Agent result Markdown is converted to Telegram HTML before sending."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        md_result = "## Summary\n\n**Key finding**: `config.toml` was updated."
        fast_session = _make_mock_claude_session(result=md_result)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="md task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        all_text = " ".join(c[0][1] for c in bot.send_message.call_args_list)
        # Markdown must be rendered as HTML, not sent as raw asterisks/hashes
        assert "<b>Summary</b>" in all_text          # ## heading → <b>
        assert "<b>Key finding</b>" in all_text      # **bold** → <b>
        assert "<code>config.toml</code>" in all_text  # `code` → <code>
        assert "**" not in all_text                  # no raw markdown left
        assert "##" not in all_text

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

    async def test_notify_success_chunks_stay_within_limit_and_keep_html_balanced(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = AgentRun(
            run_id="r1",
            name="Atlas",
            task="task",
            context="",
            user_id=1,
            started_at=1.0,
            result="**w** " * 2000,
        )

        await manager._notify_success(run)

        messages = [call[0][1] for call in bot.send_message.call_args_list]
        assert len(messages) > 2
        for message in messages:
            assert len(message) <= 4000
        chunk_msgs = [m for m in messages if m.startswith("[")]
        assert chunk_msgs
        for message in chunk_msgs:
            assert message.count("<b>") == message.count("</b>")

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

    async def test_notify_spawn_escapes_agent_name(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = AgentRun(
            run_id="r2",
            name='Atlas <& "Ops">',
            task="task",
            context="",
            user_id=1,
            started_at=1.0,
        )

        await manager._notify_spawn(run)

        msg = bot.send_message.call_args[0][1]
        assert "Atlas &lt;&amp; &quot;Ops&quot;&gt;" in msg

    async def test_notify_failure_escapes_agent_name_and_error(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = AgentRun(
            run_id="r3",
            name="Atlas <Ops>",
            task="task",
            context="",
            user_id=1,
            started_at=1.0,
            error='boom <bad> & "worse"',
        )

        await manager._notify_failure(run)

        msg = bot.send_message.call_args[0][1]
        assert "Atlas &lt;Ops&gt;" in msg
        assert "boom &lt;bad&gt; &amp; &quot;worse&quot;" in msg


def test_agent_status_text_escapes_agent_name() -> None:
    text = _agent_status_text('Atlas <& "Ops">', tool_count=1, thinking_count=1)
    assert "Atlas &lt;&amp; &quot;Ops&quot;&gt;" in text


# ──────────────────────────────────────────────────────────────────
# Completion signaling (Phase 2 Task #4)
# ──────────────────────────────────────────────────────────────────


class TestCompletionSignaling:
    async def test_done_event_set_after_agent_completes(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        fast_session = _make_mock_claude_session(result="done")
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)
        assert run.done.is_set()

    async def test_done_event_set_after_agent_fails(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        failing_session = _make_failing_claude_session(error="boom")
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="bad task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)
        assert run.done.is_set()

    async def test_done_event_set_after_agent_is_cancelled(self) -> None:
        bot = _make_bot()
        sm = _make_session_manager()
        slow_session = _make_slow_claude_session(delay=10.0)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="slow task")
            await asyncio.sleep(0.05)
            await manager.cancel(run.run_id)
            # Allow cancellation to propagate
            await asyncio.sleep(0.1)
        assert run.done.is_set()

    async def test_done_event_not_set_initially(self) -> None:
        run = AgentRun(
            run_id="test",
            name="Test",
            task="t",
            context="",
            user_id=1,
            started_at=1.0,
        )
        assert not run.done.is_set()

    async def test_log_path_populated_after_agent_starts(self) -> None:
        from archon.ai.agent_logger import AgentLogger
        import tempfile
        bot = _make_bot()
        sm = _make_session_manager()
        fast_session = _make_mock_claude_session(result="done")
        with tempfile.TemporaryDirectory() as tmpdir:
            agent_logger = AgentLogger(tmpdir)
            with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
                manager = BackgroundAgentManager(
                    bot=bot, session_manager=sm, agent_logger=agent_logger,
                )
                run = await manager.spawn(user_id=1, task="task")
                if run._task_ref:
                    await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)
            assert run.log_path is not None
            assert run.log_path.exists()


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
        mock_logger.record_event = AsyncMock(
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
        mock_logger.record_event = AsyncMock()

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
        assert mock_logger.record_event.await_count == expected_calls, (
            f"Expected {expected_calls} record_event calls "
            f"(SubagentStarted + {len(events)} events + SubagentStopped), "
            f"got {mock_logger.record_event.await_count}"
        )

    async def test_agent_logger_receives_subagent_started_first(self) -> None:
        """SubagentStarted is the first call to record_event — opens the log file."""
        from archon.ai.event_mapper import Response, SubagentStarted, ThinkingResult

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

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
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

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
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

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
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

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
# HistoryManager integration
# ──────────────────────────────────────────────────────────────────


def _make_history_manager() -> MagicMock:
    hm = MagicMock()
    hm.record_event = AsyncMock()
    return hm


class TestHistoryManagerIntegration:
    async def test_successful_completion_calls_history_manager(self) -> None:
        """On success, history_manager.record_event is called with a Response event."""
        from archon.ai.event_mapper import Response as ResponseEvent

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        fast_session = _make_mock_claude_session(result="agent finished")
        hm = _make_history_manager()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, history_manager=hm)
            run = await manager.spawn(user_id=42, task="do work")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        hm.record_event.assert_awaited_once()
        event = hm.record_event.call_args[0][1]
        assert isinstance(event, ResponseEvent)
        assert run.name in event.content
        assert "agent finished" in event.content
        # first positional arg is user_id
        assert hm.record_event.call_args[0][0] == 42

    async def test_none_history_manager_does_not_crash_on_success(self) -> None:
        """When history_manager is None, successful completion still works."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        fast_session = _make_mock_claude_session(result="ok")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)  # no history_manager
            run = await manager.spawn(user_id=1, task="task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "completed"

    async def test_failure_calls_history_manager_with_error_event(self) -> None:
        """On failure, history_manager.record_event is called with an ErrorEvent."""
        from archon.ai.event_mapper import ErrorEvent

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        failing_session = _make_failing_claude_session(error="disk full")
        hm = _make_history_manager()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, history_manager=hm)
            run = await manager.spawn(user_id=7, task="doomed")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed"
        hm.record_event.assert_awaited_once()
        event = hm.record_event.call_args[0][1]
        assert isinstance(event, ErrorEvent)
        assert run.name in event.message
        assert "disk full" in event.message
        assert hm.record_event.call_args[0][0] == 7

    async def test_empty_result_does_not_call_history_manager(self) -> None:
        """When the agent produces an empty result, history_manager is NOT called."""
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))
        empty_session = _make_mock_claude_session(result="")
        hm = _make_history_manager()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=empty_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, history_manager=hm)
            run = await manager.spawn(user_id=1, task="quiet task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "completed"
        hm.record_event.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────
# BAM-2 — history_manager.record_event() in failure path must be guarded
# ──────────────────────────────────────────────────────────────────


class TestHistoryManagerFailureGuard:
    async def test_history_manager_record_event_raises_in_failure_path_does_not_propagate(
        self,
    ) -> None:
        """If history_manager.record_event() raises in the failure handler, the
        exception must NOT propagate out of _run_agent(). The run must still end
        with status='failed' and run.done must be set.

        BAM-2: the failure path was missing a try/except around record_event(),
        so a disk-full or I/O error would replace the original exception and
        leave the agent in an inconsistent state.
        """
        bot = _make_bot()
        sm = _make_session_manager()
        failing_session = _make_failing_claude_session(error="agent crash")

        hm = MagicMock()
        hm.record_event = AsyncMock(side_effect=OSError("disk full"))

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, history_manager=hm)
            run = await manager.spawn(user_id=1, task="failing task")
            if run._task_ref:
                # _run_agent catches exceptions internally — task must complete cleanly
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed", f"Expected 'failed', got {run.status!r}"
        assert run.done.is_set(), "run.done must be set even when record_event raises"
        # history_manager.record_event must still have been called (the call just raised)
        hm.record_event.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────
# FR.15 — Per-agent working beacon
# ──────────────────────────────────────────────────────────────────


def _make_beacon_bot(message_id: int = 12345) -> MagicMock:
    """Bot mock with send_message and edit_message_text as AsyncMocks.

    edit_message_text is included so tests that assert it is NOT called still work.
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
    """Mock ClaudeSession that yields ToolStarted + ThinkingResult events then a Response."""
    from archon.ai.event_mapper import Response, ThinkingResult, ToolStarted

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    events = (
        [ToolStarted(name=f"Tool{i}") for i in range(tool_count)]
        + [ThinkingResult(content=f"thought{i}") for i in range(thinking_count)]
        + [Response(content="done")]
    )

    async def _send(prompt: str):  # type: ignore[return]
        for ev in events:
            yield ev

    session.send = _send
    return session


def _make_pausing_session(pause_secs: float = 0.15) -> MagicMock:
    """Mock session that pauses long enough for the beacon to fire, then completes."""
    from archon.ai.event_mapper import Response, ToolStarted, ThinkingResult

    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    async def _send(prompt: str):  # type: ignore[return]
        yield ToolStarted(name="Read")
        yield ToolStarted(name="Bash")
        yield ThinkingResult(content="pondering")
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


# ── Beacon fires: new message first, edits subsequent ─────────────


class TestBeaconFires:
    async def test_beacon_disabled_when_interval_zero(self) -> None:
        """beacon_interval_minutes=0 → no beacon messages sent or edited."""
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

        # Beacon disabled: only spawn + completion use send_message, no edits ever.
        bot.edit_message_text.assert_not_called()
        assert bot.send_message.await_count == 2  # spawn + completion

    async def test_beacon_no_fire_when_agent_finishes_before_interval(self) -> None:
        """Sleep-first design: if agent finishes before interval elapses, no beacon fires.

        beacon_interval = 0.5 s, agent pause = 0.30 s → beacon sleeps 0.5 s,
        agent done at 0.30 s, beacon cancelled mid-sleep → 0 beacon messages.
        This is intentional: short agents don't need status notifications.
        """
        bot = _make_beacon_bot(message_id=55)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.30)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                beacon_interval_minutes=0.5 / 60.0,  # 0.5-second interval
            )
            run = await manager.spawn(user_id=1, task="short run beacon test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # Beacon didn't fire (agent finished before interval elapsed).
        bot.edit_message_text.assert_not_called()
        assert bot.send_message.await_count == 2  # spawn + completion only

    async def test_beacon_first_fire_sends_new_message(self) -> None:
        """First beacon fire sends a NEW message (push notification), not an edit.

        Interval: 0.001 min ≈ 60 ms.  Session pauses 0.15 s → beacon fires
        after 60 ms sleep → first fire uses send_message (not edit_message_text).
        """
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

        # spawn + at least 1 beacon send + completion ≥ 3
        assert bot.send_message.await_count >= 3, (
            "Expected at least 3 send_message calls (spawn + beacon + completion)"
        )

    async def test_beacon_subsequent_fires_send_new_messages(self) -> None:
        """Every beacon fire sends a new message — no edits ever.

        Interval: 0.001 min ≈ 60 ms.  Session pauses 0.35 s → at least 4 fires,
        all as new send_message calls.  edit_message_text must never be called.
        """
        bot = _make_beacon_bot(message_id=7777)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.35)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="beacon all-send test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # All beacon fires use send_message — never edit_message_text.
        bot.edit_message_text.assert_not_called()
        # spawn + at least 3 beacon fires + completion ≥ 5
        assert bot.send_message.await_count >= 5, (
            f"Expected ≥5 send_message calls (spawn + beacons + completion), "
            f"got {bot.send_message.await_count}"
        )

    async def test_beacon_uses_correct_chat_id(self) -> None:
        """All beacon send_message calls use chat_id == user_id."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        session = _make_pausing_session(pause_secs=0.35)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=55, task="chat_id test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # All send_message calls use user_id=55
        for c in bot.send_message.call_args_list:
            assert c[0][0] == 55 or c[1].get("chat_id") == 55

        # No edits ever
        bot.edit_message_text.assert_not_called()

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

        # Find the first beacon send_message call (index 1: spawn is index 0)
        calls = bot.send_message.call_args_list
        assert len(calls) >= 2, "Expected at least spawn + 1 beacon send_message"
        beacon_text: str = calls[1][0][1]  # second send_message is first beacon
        assert run.name in beacon_text

    async def test_beacon_first_fire_uses_working_verb(self) -> None:
        """First beacon send uses the word 'working', not a random verb."""
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

        # Index 1 = first beacon send_message (index 0 is spawn notification)
        calls = bot.send_message.call_args_list
        assert len(calls) >= 2
        first_beacon_text: str = calls[1][0][1]
        assert "working" in first_beacon_text

    async def test_beacon_subsequent_fires_use_beacon_words(self) -> None:
        """After the first fire, subsequent new messages use words from _AGENT_BEACON_WORDS."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Long pause (0.35 s) with 60 ms interval → at least 4 fires total
        # send_message index: 0=spawn, 1=first beacon ("working"), 2+=subsequent beacons
        session = _make_pausing_session(pause_secs=0.35)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="beacon words test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # No edits ever; must have spawn + first beacon + at least one more beacon
        bot.edit_message_text.assert_not_called()
        calls = bot.send_message.call_args_list
        assert len(calls) >= 3, (
            "Need ≥3 send_message calls (spawn + first beacon + second beacon) "
            "to test word rotation"
        )
        # Index 2 = second beacon fire → must use one of the beacon words (not "working")
        second_beacon_text: str = calls[2][0][1]
        assert any(word in second_beacon_text for word in _AGENT_BEACON_WORDS)

    async def test_beacon_includes_tool_counts_in_text(self) -> None:
        """Beacon text reflects cumulative ToolStarted event counts."""
        bot = _make_beacon_bot(message_id=1)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Session yields 2 ToolStarted + 1 ThinkingResult then pauses so beacon fires
        session = _make_pausing_session(pause_secs=0.15)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="tool count test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # First beacon is the second send_message call
        calls = bot.send_message.call_args_list
        assert len(calls) >= 2
        first_beacon_text: str = calls[1][0][1]
        assert "tool" in first_beacon_text  # "2 tools" or "1 tool"

    async def test_beacon_includes_thinking_counts_in_text(self) -> None:
        """Beacon text reflects cumulative ThinkingResult event counts."""
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

        # Pausing session yields 1 ThinkingResult before pause
        calls = bot.send_message.call_args_list
        assert len(calls) >= 2
        first_beacon_text: str = calls[1][0][1]
        assert "thinking" in first_beacon_text

    async def test_beacon_uses_html_parse_mode(self) -> None:
        """All beacon send_message calls use parse_mode='HTML'."""
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

        # No edits ever
        bot.edit_message_text.assert_not_called()
        # All beacon send_message calls use parse_mode='HTML' (keyword arg)
        for c in bot.send_message.call_args_list:
            assert c[1].get("parse_mode") == "HTML"


# ── Beacon lifecycle — cancelled on completion/failure/cancel ─────


class TestBeaconLifecycle:
    async def test_beacon_cancelled_on_agent_completion(self) -> None:
        """No beacon messages sent after agent completes (sleep-first, 2-min interval).

        Agent completes in <1 ms; beacon interval is 2 min → the beacon's first
        sleep outlasts the agent → cancelled before first fire.
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

        # Beacon never fired: only spawn + completion send_message calls.
        bot.edit_message_text.assert_not_called()
        assert bot.send_message.await_count == 2

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
        """Beacon task is cancelled when the agent is cancelled.

        Agent cancelled at 0.05 s; beacon interval is 2 min → beacon's sleep is
        still running at cancellation time → no beacon fires at all.
        """
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
        # Sleep-first: beacon was in its 2-min sleep when cancelled → 0 fires.
        bot.edit_message_text.assert_not_called()

    async def test_beacon_survives_api_error(self) -> None:
        """If send_message or edit_message_text raises, beacon keeps running silently."""
        bot = _make_beacon_bot(message_id=1)
        bot.send_message = AsyncMock(side_effect=Exception("Telegram error"))
        bot.edit_message_text = AsyncMock(side_effect=Exception("Telegram edit error"))
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Long enough pause to let the beacon fire 2+ times
        session = _make_pausing_session(pause_secs=0.25)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            # Must not raise even though every Telegram call fails
            run = await manager.spawn(user_id=1, task="api error test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # Agent completed despite all Telegram errors (errors swallowed silently)
        assert run.status == "completed"
        # send_message was called (spawn + beacon attempts); all failed but loop continued
        assert bot.send_message.await_count >= 2

    async def test_beacon_does_not_fire_after_stop_all(self) -> None:
        """stop_all() cancels both the agent task and its beacon.

        Stopped at 0.05 s; beacon interval is 2 min → beacon's sleep is
        still running at stop_all time → no beacon fires at all.
        """
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
        # Sleep-first: beacon was in its 2-min sleep when stop_all cancelled it.
        bot.edit_message_text.assert_not_called()


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
        can coexist since they use separate bot calls on separate chat IDs.

        Every beacon fire sends a new message; edit_message_text is never called.
        """
        bot = _make_beacon_bot(message_id=100)
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock(is_alive=True))

        # Long pause to trigger multiple beacon fires
        session = _make_pausing_session(pause_secs=0.35)
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, beacon_interval_minutes=0.001
            )
            run = await manager.spawn(user_id=1, task="orch beacon test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        # spawn + multiple beacon sends + completion ≥ 3
        assert bot.send_message.await_count >= 3
        # No edits ever — clean message history
        bot.edit_message_text.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# Log bookending — user_request / agent_task / final_result
# ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestLogBookending:
    """SubagentStarted carries user_request + agent_task; run.result captures Response content."""

    async def test_subagent_started_carries_user_request(self) -> None:
        """SubagentStarted.user_request equals the user_request passed to spawn()."""
        from archon.ai.event_mapper import SubagentStarted

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

        session = _make_mock_claude_session(result="done")
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(
                user_id=1,
                task="do the audit",
                user_request="Please audit my docs.",
            )
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        started = next(e for e in received if isinstance(e, SubagentStarted))
        assert started.user_request == "Please audit my docs.", (
            f"Expected user_request='Please audit my docs.', got {started.user_request!r}"
        )

    async def test_subagent_started_carries_agent_task_without_context(self) -> None:
        """SubagentStarted.agent_task equals the bare task when no context is given."""
        from archon.ai.event_mapper import SubagentStarted

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

        session = _make_mock_claude_session(result="done")
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(user_id=1, task="read the config")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        started = next(e for e in received if isinstance(e, SubagentStarted))
        assert started.agent_task == "read the config", (
            f"Expected agent_task='read the config', got {started.agent_task!r}"
        )

    async def test_subagent_started_carries_agent_task_with_context(self) -> None:
        """SubagentStarted.agent_task is the full 'Context:\\n...\\n\\nTask:\\n...' prompt."""
        from archon.ai.event_mapper import SubagentStarted

        received: list = []
        mock_logger = MagicMock()
        mock_logger.record_event = AsyncMock(side_effect=lambda ev: received.append(ev))

        session = _make_mock_claude_session(result="done")
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm, agent_logger=mock_logger)
            run = await manager.spawn(
                user_id=1,
                task="read the config",
                context="The project uses TOML",
            )
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        started = next(e for e in received if isinstance(e, SubagentStarted))
        assert "Context:" in started.agent_task
        assert "The project uses TOML" in started.agent_task
        assert "Task:" in started.agent_task
        assert "read the config" in started.agent_task

    async def test_run_result_set_from_response_event(self) -> None:
        """run.result equals the Response content from the agent."""
        session = _make_mock_claude_session(result="Config audit complete.")
        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="audit task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.result == "Config audit complete.", (
            f"Expected run.result='Config audit complete.', got {run.result!r}"
        )

    async def test_run_result_empty_on_no_response(self) -> None:
        """run.result is None when the agent produces no Response event."""
        from archon.ai.event_mapper import ToolStarted, ToolResult
        session = MagicMock()
        session.start = AsyncMock()
        session.stop = AsyncMock()
        session.is_alive = True

        async def _send(prompt: str):  # type: ignore[return]
            yield ToolStarted(name="Bash", input="ls")
            yield ToolResult(content="file.txt")

        session.send = _send

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="silent task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert not run.result, f"Expected empty run.result when no Response was yielded, got {run.result!r}"

    async def test_run_result_holds_last_response_when_multiple_emitted(self) -> None:
        """When multiple Response events are yielded, run.result holds the last one."""
        from archon.ai.event_mapper import Response
        session = MagicMock()
        session.start = AsyncMock()
        session.stop = AsyncMock()
        session.is_alive = True

        async def _send(prompt: str):  # type: ignore[return]
            yield Response(content="intermediate summary")
            yield Response(content="final answer")

        session.send = _send

        bot = _make_bot()
        sm = _make_session_manager()
        sm.get_or_create = AsyncMock(return_value=MagicMock())

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="multi-response task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.result == "final answer", (
            f"Expected run.result='final answer' (last response), got {run.result!r}"
        )


# ──────────────────────────────────────────────────────────────────
# Agent completion context tracking
# ──────────────────────────────────────────────────────────────────


async def test_agent_completion_tracks_context() -> None:
    """After agent completes successfully, session_manager.track_context is called."""
    session = _make_mock_claude_session(result="Agent finished the refactoring")
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(
            user_id=42, task="refactor auth", user_request="please refactor auth"
        )
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "completed"
    # track_context is called twice: once at spawn (started), once at completion
    assert sm.track_context.call_count == 2
    completion_call = sm.track_context.call_args_list[1][0]
    assert completion_call[0] == 42  # user_id
    assert "please refactor auth" in completion_call[1]  # prompt = user_request
    assert "completed" in completion_call[2].lower()  # summary contains "completed"


async def test_agent_failure_does_not_track_context() -> None:
    """Failed/cancelled agents don't record context."""
    session = _make_failing_claude_session(error="boom")
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(user_id=42, task="fail task")
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "failed"
    # spawn always calls track_context once (started); completion is not reached on failure
    assert sm.track_context.call_count == 1
    assert "started" in sm.track_context.call_args[0][2].lower()


# ──────────────────────────────────────────────────────────────────
# Bug fixes — F3, F4, F5, F6
# ──────────────────────────────────────────────────────────────────


class TestBugF3SessionStopOnCancel:
    """F3: session.stop() must be called even when CancelledError fires through
    the agent_logger.record_event await in the inner finally block."""

    async def test_session_stop_called_when_logger_raises_cancelled_error_on_cancel(
        self,
    ) -> None:
        """When the agent is cancelled and record_event raises CancelledError,
        session.stop() must still be called (not skipped)."""
        slow_session = _make_slow_claude_session(delay=30.0)

        # Mock logger whose record_event raises CancelledError on the second call
        # (the SubagentStopped call in the inner finally) to simulate the bug.
        call_count = 0

        async def _record_event_raises_on_second_call(ev: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError("simulated cancellation in logger")

        mock_logger = MagicMock()
        mock_logger.record_event = AsyncMock(
            side_effect=_record_event_raises_on_second_call
        )

        bot = _make_bot()
        sm = _make_session_manager()

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=slow_session,
        ):
            manager = BackgroundAgentManager(
                bot=bot, session_manager=sm, agent_logger=mock_logger
            )
            run = await manager.spawn(user_id=1, task="cancel logger test")
            await asyncio.sleep(0.05)  # let the task enter _run_agent
            await manager.cancel(run.run_id)
            await asyncio.sleep(0.1)  # let cancellation propagate

        # session.stop() must have been called regardless of the logger failure
        slow_session.stop.assert_awaited()


class TestBugF4NamePoolLeakOnCreateTaskFailure:
    """F4: _active_names must be released when asyncio.create_task() raises in spawn()."""

    async def test_active_names_empty_after_create_task_raises(self) -> None:
        """If asyncio.create_task() raises, the assigned name must be released."""
        bot = _make_bot()
        sm = _make_session_manager()

        with patch(
            "archon.ai.background_agent_manager.asyncio.create_task",
            side_effect=RuntimeError("event loop is closed"),
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            with pytest.raises(RuntimeError, match="event loop is closed"):
                await manager.spawn(user_id=1, task="phantom task")

        assert len(manager._active_names) == 0, (
            f"_active_names must be empty after create_task failure, "
            f"got {manager._active_names}"
        )
        assert len(manager._runs) == 0


class TestBugF5NotifySuccessExceptionKeepsStatusCompleted:
    """F5: run.status must remain 'completed' even if _notify_success raises."""

    async def test_status_remains_completed_when_notify_success_raises(self) -> None:
        """If _notify_success() raises, the outer except must NOT reset status to 'failed'."""
        fast_session = _make_mock_claude_session(result="good result")
        bot = _make_bot()
        sm = _make_session_manager()

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=fast_session,
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            # Patch _notify_success to raise after status is set to completed
            manager._notify_success = AsyncMock(
                side_effect=RuntimeError("md_to_html blew up")
            )
            run = await manager.spawn(user_id=1, task="notify-fail task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "completed", (
            f"run.status must remain 'completed' even when _notify_success raises, "
            f"got {run.status!r}"
        )


class TestBugF6BaseExceptionReleasesName:
    """F6: _release_name must be called in finally so BaseException subclasses
    (other than CancelledError) don't leak the name permanently."""

    async def test_name_released_after_base_exception(self) -> None:
        """When _run_agent exits via a BaseException, the name must be released."""
        bot = _make_bot()
        sm = _make_session_manager()

        # A session whose send() raises a BaseException (not CancelledError / Exception)
        session = MagicMock()
        session.start = AsyncMock()
        session.stop = AsyncMock()
        session.is_alive = True

        async def _send_raises_base(prompt: str):  # type: ignore[return]
            raise BaseException("synthetic base exception")
            yield  # make it an async generator

        session.send = _send_raises_base

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            return_value=session,
        ):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="base exception task")
            try:
                if run._task_ref:
                    await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)
            except BaseException:
                pass
            # Allow any propagation to settle
            await asyncio.sleep(0.1)

        # The name must have been released back to the pool
        assert run.name not in manager._active_names, (
            f"Name {run.name!r} leaked into _active_names after BaseException"
        )


async def test_agent_cancellation_does_not_track_context() -> None:
    """Cancelled agents don't record context."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    async def _slow_send(prompt: str):
        await asyncio.sleep(60)
        yield  # never reached

    session.send = _slow_send
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(user_id=42, task="cancel me")

        # Let the agent start, then cancel it
        await asyncio.sleep(0.05)
        await manager.cancel(run.run_id)

        if run._task_ref:
            with pytest.raises(asyncio.CancelledError):
                await run._task_ref

    assert run.status == "cancelled"
    # spawn always calls track_context once (started); completion is not reached on cancellation
    assert sm.track_context.call_count == 1
    assert "started" in sm.track_context.call_args[0][2].lower()


# ──────────────────────────────────────────────────────────────────
# inject_agent_context — completion injects background context; spawn does not
# ──────────────────────────────────────────────────────────────────


async def test_spawn_does_not_inject_context_to_session() -> None:
    """spawn() does NOT call inject_agent_context — context goes to track_context only."""
    session = _make_slow_claude_session(delay=10.0)
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        await manager.spawn(user_id=42, task="build the feature")

        sm.inject_agent_context.assert_not_called()

        await manager.stop_all()


async def test_completion_injects_background_context_to_session() -> None:
    """On completion, inject_agent_context is called with a status-only note.
    The result must NOT appear in the injected context (Bug 20 fix — it was already delivered via Telegram).
    """
    result_text = "done quickly"
    session = _make_mock_claude_session(result=result_text)
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(user_id=42, task="fast task")
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "completed"
    # completion injects context; spawn does not
    sm.inject_agent_context.assert_called_once()
    call_args = sm.inject_agent_context.call_args[0]
    assert call_args[0] == 42  # user_id
    injected = call_args[1]
    assert run.name in injected
    # result must NOT be in injected context (Bug 20 fix)
    assert result_text not in injected
    # task content must NOT leak into injected context either
    assert run.task not in injected, (
        f"Bug 20: run.task '{run.task}' leaked into injected context:\n{injected!r}"
    )
    # must include status-only framing with do-not-echo instruction
    assert "already delivered" in injected or "do not" in injected.lower()


async def test_spawn_also_tracks_context_for_orch_session() -> None:
    """spawn() calls track_context at spawn time (before agent completes)."""
    session = _make_slow_claude_session(delay=10.0)
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(
            user_id=42, task="analyse logs", user_request="please analyse logs"
        )

        # Agent is still running — track_context must already have been called at spawn
        sm.track_context.assert_called_once()
        call_args = sm.track_context.call_args[0]
        assert call_args[0] == 42  # user_id
        assert "please analyse logs" in call_args[1]  # user_request used as prompt
        assert run.name in call_args[2]  # agent name in summary
        assert "started" in call_args[2].lower()

        await manager.stop_all()


async def test_failure_does_not_inject_context() -> None:
    """Failed agents do not call inject_agent_context (neither at spawn nor completion)."""
    session = _make_failing_claude_session(error="something went wrong")
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(user_id=42, task="fail task")
        if run._task_ref:
            await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

    assert run.status == "failed"
    sm.inject_agent_context.assert_not_called()


async def test_cancellation_does_not_inject_context() -> None:
    """Cancelled agents do not call inject_agent_context."""
    session = MagicMock()
    session.start = AsyncMock()
    session.stop = AsyncMock()
    session.is_alive = True

    async def _slow_send(prompt: str):
        await asyncio.sleep(60)
        yield  # never reached

    session.send = _slow_send
    bot = _make_bot()
    sm = _make_session_manager()

    with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=session):
        manager = BackgroundAgentManager(bot=bot, session_manager=sm)
        run = await manager.spawn(user_id=42, task="cancel me too")

        await asyncio.sleep(0.05)
        await manager.cancel(run.run_id)

        if run._task_ref:
            with pytest.raises(asyncio.CancelledError):
                await run._task_ref

    assert run.status == "cancelled"
    sm.inject_agent_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# BAM does NOT inject CLAUDE.md — context flows via spawn() context param
# ──────────────────────────────────────────────────────────────────


class TestBackgroundAgentNoClaudeMdInjection:
    async def test_inject_context_not_called_even_when_claude_md_exists(self, tmp_path) -> None:
        """BAM does NOT inject CLAUDE.md — context comes from spawn() context param instead."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Architecture\nThis is project context.")

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=str(tmp_path),
            )
            run = await manager.spawn(user_id=1, task="Do something")
            await run.done.wait()

        mock_session.inject_context.assert_not_called()

    async def test_context_param_flows_to_prompt(self, tmp_path) -> None:
        """Context passed to spawn() is included in the prompt sent to the session."""
        from archon.ai.event_mapper import Response

        bot = _make_bot()
        sm = _make_session_manager()
        sent_prompts: list[str] = []

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()
        mock_session.inject_context = MagicMock()

        async def _send(prompt: str):
            sent_prompts.append(prompt)
            yield Response(content="done")

        mock_session.send = _send

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=str(tmp_path),
            )
            run = await manager.spawn(
                user_id=1,
                task="Do the work",
                context="Conversation summary: user wants to update config module.",
            )
            await run.done.wait()

        assert len(sent_prompts) == 1
        assert "Conversation summary" in sent_prompts[0]
        assert "Do the work" in sent_prompts[0]


# ──────────────────────────────────────────────────────────────────
# agents.md injection at spawn time
# ──────────────────────────────────────────────────────────────────


class TestBackgroundAgentAgentsMdInjection:
    async def test_background_agent_injects_agents_md_when_present(self, tmp_path) -> None:
        """_run_agent() injects agents.md content into the session after start()."""
        agents_md = tmp_path / "agents.md"
        agents_md.write_text("## Harbor\nSpecialist for data pipelines.")

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=str(tmp_path),
            )
            run = await manager.spawn(user_id=1, task="Do something")
            await run.done.wait()

        mock_session.inject_context.assert_called_once()
        call_args = mock_session.inject_context.call_args[0][0]
        assert "# Workspace Agents" in call_args
        assert "Harbor" in call_args
        assert "Specialist for data pipelines." in call_args

    async def test_background_agent_skips_agents_md_when_absent(self, tmp_path) -> None:
        """_run_agent() does not call inject_context when agents.md is missing."""
        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=str(tmp_path),
            )
            run = await manager.spawn(user_id=1, task="Do something")
            await run.done.wait()

        mock_session.inject_context.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# REMINDER.md injection at spawn time
# ──────────────────────────────────────────────────────────────────


class TestBackgroundAgentReminderMdInjection:
    async def test_run_agent_injects_reminder_when_file_exists(self, tmp_path) -> None:
        """REMINDER.md in cwd → inject_context called with XML-wrapped content (agents.md absent → only reminder call)."""
        reminder_md = tmp_path / "REMINDER.md"
        reminder_md.write_text("Always follow project constraints.")

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        # Mock load_workspace_agents to return None so this test is independent
        # of whether agents.md is present — it verifies the reminder path only.
        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            with patch(
                "archon.ai.background_agent_manager.load_workspace_agents",
                return_value=None,
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=str(tmp_path),
                )
                run = await manager.spawn(user_id=1, task="Do something")
                await run.done.wait()

        mock_session.inject_context.assert_called_once()
        injected = mock_session.inject_context.call_args[0][0]
        assert "Always follow project constraints." in injected
        assert "system_reminder" in injected

    async def test_run_agent_no_reminder_injection_when_cwd_is_none(self) -> None:
        """cwd=None → inject_context not called at all.

        cwd=None prevents both agents.md and REMINDER.md injection:
        load_workspace_agents(None) returns None, and the REMINDER.md block
        is guarded by `if self._cwd is not None`. So assert_not_called() is
        the correct assertion here — there is nothing to inject.
        """
        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=None,
            )
            run = await manager.spawn(user_id=1, task="Do something")
            await run.done.wait()

        mock_session.inject_context.assert_not_called()

    async def test_run_agent_no_reminder_injection_when_file_absent(self, tmp_path) -> None:
        """cwd set but no REMINDER.md → reminder inject_context call skipped.

        agents.md is present so inject_context is called exactly once (for agents.md).
        If the reminder code accidentally injected when no REMINDER.md exists,
        call_count would become 2 and the test would catch it.
        """
        # agents.md present → inject_context called once (agents.md only)
        (tmp_path / "agents.md").write_text("## Atlas\nSpecialist agent.")
        # No REMINDER.md → reminder injection must be skipped

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=str(tmp_path),
            )
            run = await manager.spawn(user_id=1, task="Do something")
            await run.done.wait()

        # agents.md injected (call 1); no REMINDER.md → no second call
        assert mock_session.inject_context.call_count == 1
        injected = mock_session.inject_context.call_args[0][0]
        assert "Atlas" in injected  # confirm it was agents.md, not an accidental reminder

    async def test_run_agent_reminder_error_does_not_kill_agent(self, tmp_path) -> None:
        """build_reminder_injection raising RuntimeError → agent still completes successfully."""
        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            with patch(
                "archon.ai.background_agent_manager.build_reminder_injection",
                side_effect=RuntimeError("unexpected error"),
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=str(tmp_path),
                )
                run = await manager.spawn(user_id=1, task="Do something")
                await run.done.wait()

        assert run.status == "completed"

    async def test_run_agent_reminder_inject_context_error_is_logged_as_warning(
        self, tmp_path, caplog
    ) -> None:
        """inject_context raising RuntimeError → agent still completes AND a WARNING is logged (Fix 3)."""
        import logging

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock(side_effect=RuntimeError("broken"))

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            with patch(
                "archon.ai.background_agent_manager.build_reminder_injection",
                return_value="<system_reminder>content</system_reminder>",
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=str(tmp_path),
                )
                with caplog.at_level(logging.WARNING, logger="archon"):
                    run = await manager.spawn(user_id=1, task="Do something")
                    await run.done.wait()

        assert run.status == "completed"
        warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "Failed to inject REMINDER.md" in m
            for m in warning_messages
        ), f"Expected a WARNING about failed injection, got: {warning_messages}"

    async def test_run_agent_injection_order_agents_then_reminder_then_task(
        self, tmp_path
    ) -> None:
        """With both agents.md and REMINDER.md present: agents.md injected first, then REMINDER.md, then task prompt."""
        from archon.ai.event_mapper import Response

        agents_md = tmp_path / "agents.md"
        agents_md.write_text("## Atlas\nSpecialist agent.")
        reminder_md = tmp_path / "REMINDER.md"
        reminder_md.write_text("Always apply constraints.")

        bot = _make_bot()
        sm = _make_session_manager()

        inject_calls: list[str] = []
        sent_prompts: list[str] = []

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()
        mock_session.is_alive = True
        mock_session.inject_context = MagicMock(side_effect=lambda ctx: inject_calls.append(ctx))

        async def _send(prompt: str):
            sent_prompts.append(prompt)
            yield Response(content="done")

        mock_session.send = _send

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            manager = BackgroundAgentManager(
                bot=bot,
                session_manager=sm,
                cwd=str(tmp_path),
            )
            run = await manager.spawn(user_id=1, task="Run the plan")
            await run.done.wait()

        assert len(inject_calls) == 2
        assert "Atlas" in inject_calls[0]        # agents.md first
        assert "Always apply constraints." in inject_calls[1]  # REMINDER.md second
        assert "Run the plan" in sent_prompts[0]  # task last

    async def test_run_agent_reminder_injection_logged_to_history_manager(
        self, tmp_path
    ) -> None:
        """REMINDER.md injected → record_archon_message called with agent name."""
        reminder_md = tmp_path / "REMINDER.md"
        reminder_md.write_text("Always follow project constraints.")

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        hm = _make_history_manager()
        hm.record_archon_message = AsyncMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            with patch(
                "archon.ai.background_agent_manager.load_workspace_agents",
                return_value=None,
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=str(tmp_path),
                    history_manager=hm,
                )
                run = await manager.spawn(user_id=1, task="Do something")
                await run.done.wait()

        hm.record_archon_message.assert_called_once()
        msg = hm.record_archon_message.call_args[0][0]
        assert "REMINDER.md" in msg
        assert run.name in msg

    async def test_run_agent_reminder_no_history_log_when_history_manager_is_none(
        self, tmp_path
    ) -> None:
        """REMINDER.md injected with no history_manager → no crash."""
        reminder_md = tmp_path / "REMINDER.md"
        reminder_md.write_text("Always follow project constraints.")

        bot = _make_bot()
        sm = _make_session_manager()
        mock_session = _make_mock_claude_session("result")
        mock_session.inject_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            with patch(
                "archon.ai.background_agent_manager.load_workspace_agents",
                return_value=None,
            ):
                manager = BackgroundAgentManager(
                    bot=bot,
                    session_manager=sm,
                    cwd=str(tmp_path),
                )
                run = await manager.spawn(user_id=1, task="Do something")
                await run.done.wait()

        # inject_context still called (REMINDER injected), no exception raised
        mock_session.inject_context.assert_called_once()


# ──────────────────────────────────────────────────────────────────
# Fix A: session.stop() called in finally even when run raises
# Fix B: session.stop() errors during cancellation are logged
# ──────────────────────────────────────────────────────────────────


class TestSessionStopReliability:
    async def test_session_stop_called_when_run_succeeds(self) -> None:
        """session.stop() is called unconditionally on the success path."""
        bot = _make_bot()
        sm = _make_session_manager()
        fast_session = _make_mock_claude_session(result="done")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        fast_session.stop.assert_awaited_once()

    async def test_session_stop_called_when_run_fails(self) -> None:
        """session.stop() is called unconditionally even when the agent raises."""
        bot = _make_bot()
        sm = _make_session_manager()
        failing_session = _make_failing_claude_session(error="boom")

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="failing task")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed"
        failing_session.stop.assert_awaited_once()

    async def test_session_stop_called_when_cancelled(self) -> None:
        """session.stop() is called unconditionally when the agent is cancelled."""
        bot = _make_bot()
        sm = _make_session_manager()
        slow_session = _make_slow_claude_session(delay=30.0)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="slow task")
            await asyncio.sleep(0.05)
            await manager.cancel(run.run_id)
            await asyncio.sleep(0.1)

        assert run.status == "cancelled"
        slow_session.stop.assert_awaited_once()

    async def test_session_stop_error_during_failure_is_logged(self, caplog) -> None:
        """If session.stop() raises on the failure path, the error is logged as a warning."""
        import logging

        bot = _make_bot()
        sm = _make_session_manager()
        failing_session = _make_failing_claude_session(error="send boom")
        failing_session.stop = AsyncMock(side_effect=RuntimeError("stop also failed"))

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=failing_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            with caplog.at_level(logging.WARNING, logger="archon"):
                run = await manager.spawn(user_id=1, task="stop-error task")
                if run._task_ref:
                    await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.status == "failed"
        assert any("session.stop() failed" in r.message for r in caplog.records)

    async def test_session_stop_error_during_cancellation_is_logged(self, caplog) -> None:
        """If session.stop() raises during cancellation, the error is logged (not silenced)."""
        import logging

        bot = _make_bot()
        sm = _make_session_manager()
        slow_session = _make_slow_claude_session(delay=30.0)
        slow_session.stop = AsyncMock(side_effect=RuntimeError("stop failed on cancel"))

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            with caplog.at_level(logging.WARNING, logger="archon"):
                run = await manager.spawn(user_id=1, task="cancel stop-error task")
                await asyncio.sleep(0.05)
                await manager.cancel(run.run_id)
                await asyncio.sleep(0.1)

        assert run.status == "cancelled"
        assert any("session.stop() failed" in r.message for r in caplog.records)

    async def test_session_stop_error_does_not_prevent_done_event(self) -> None:
        """Even if session.stop() raises, run.done is still set so waiters are unblocked."""
        bot = _make_bot()
        sm = _make_session_manager()
        fast_session = _make_mock_claude_session(result="ok")
        fast_session.stop = AsyncMock(side_effect=RuntimeError("stop boom"))

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=fast_session):
            manager = BackgroundAgentManager(bot=bot, session_manager=sm)
            run = await manager.spawn(user_id=1, task="done-event test")
            if run._task_ref:
                await asyncio.wait_for(asyncio.shield(run._task_ref), timeout=5.0)

        assert run.done.is_set()


# ──────────────────────────────────────────────────────────────────
# BUG FIX: spawn() must not shadow 'task' parameter with asyncio.Task
# ──────────────────────────────────────────────────────────────────


class TestSpawnTaskRefNotShadowed:
    @pytest.mark.asyncio
    async def test_task_ref_is_asyncio_task_not_string(self) -> None:
        """AgentRun._task_ref must be an asyncio.Task, not the task string parameter."""
        bot = _make_bot()
        sm = _make_session_manager()
        slow_session = _make_slow_claude_session(delay=10.0)

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=slow_session):
            with patch("archon.ai.background_agent_manager.load_workspace_agents", return_value=None):
                manager = BackgroundAgentManager(bot=bot, session_manager=sm)
                run = await manager.spawn(user_id=1, task="my task string")

        # _task_ref must be an asyncio.Task, not the string "my task string"
        assert isinstance(run._task_ref, asyncio.Task), (
            f"_task_ref should be asyncio.Task but got {type(run._task_ref)}"
        )
        assert run.task == "my task string", "task string field must be preserved"

        # Cleanup
        if run._task_ref and not run._task_ref.done():
            run._task_ref.cancel()
            import contextlib
            with contextlib.suppress(asyncio.CancelledError):
                await run._task_ref


# ──────────────────────────────────────────────────────────────────
# Bug 21 — current-baseline characterization test
# ──────────────────────────────────────────────────────────────────


class TestBug21TwoAgentsTwoBeacons:
    """This test documents the CURRENT (unfixed) behavior of Bug 21.
    When Bug 21 is fixed, this test should be updated to expect batched beacons.
    """

    @pytest.mark.asyncio
    async def test_two_parallel_agents_produce_two_beacons_per_interval(self) -> None:
        """Two parallel agents each fire their own beacon per interval → 2 beacon messages."""
        bot = _make_beacon_bot(message_id=9999)
        sm = _make_session_manager()

        session1 = _make_pausing_session(pause_secs=0.25)
        session2 = _make_pausing_session(pause_secs=0.25)
        sessions = iter([session1, session2])

        with patch(
            "archon.ai.background_agent_manager.ClaudeSession",
            side_effect=lambda **kw: next(sessions),
        ):
            with patch("archon.ai.background_agent_manager.load_workspace_agents", return_value=None):
                manager = BackgroundAgentManager(
                    bot=bot, session_manager=sm, beacon_interval_minutes=0.001,
                )
                run1 = await manager.spawn(user_id=1, task="agent-1 task")
                run2 = await manager.spawn(user_id=1, task="agent-2 task")

                await asyncio.wait_for(run1.done.wait(), timeout=5.0)
                await asyncio.wait_for(run2.done.wait(), timeout=5.0)

        # NOTE: This test only checks beacon *count* (>= 2), not that beacons
        # come from BOTH agents.  Verifying per-agent attribution would add
        # complexity without strengthening the characterisation signal.

        # Collect beacon messages: any send_message text containing "working"
        # or any _AGENT_BEACON_WORDS word (beacon fires use these).
        beacon_words = {"working"} | set(_AGENT_BEACON_WORDS)
        all_calls = bot.send_message.call_args_list
        beacon_msgs = [
            c for c in all_calls
            if any(word in str(c) for word in beacon_words)
        ]

        # Two agents = two separate beacons (not batched).  Each agent fires
        # at least one beacon during its 0.25 s pause with a ~60 ms interval.
        assert len(beacon_msgs) >= 2, (
            f"Expected at least 2 beacon messages (one per agent), got {len(beacon_msgs)}"
        )
