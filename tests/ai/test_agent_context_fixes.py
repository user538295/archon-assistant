"""Tests for agent spawning context gap fixes."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

PROMPTS_DIR = Path(__file__).parent.parent.parent / "archon" / "ai" / "prompts"


class TestRouteTaskPromptFilePathInstructions:
    def test_prompt_instructs_to_include_absolute_file_paths(self):
        content = (PROMPTS_DIR / "route_task.md").read_text()
        # Must explicitly tell LLM to include absolute file paths
        assert "absolute" in content.lower() or "file path" in content.lower()

    def test_prompt_tasks_must_be_self_contained_with_paths(self):
        content = (PROMPTS_DIR / "route_task.md").read_text()
        # The self-contained requirement must now include file paths
        lower = content.lower()
        assert "path" in lower

    def test_prompt_mentions_workspace_context(self):
        content = (PROMPTS_DIR / "route_task.md").read_text()
        lower = content.lower()
        assert "workspace" in lower or "directory" in lower or "working" in lower


# ──────────────────────────────────────────────────────────────────
# Integration: CLAUDE.md never reaches agents; Haiku summary does
# ──────────────────────────────────────────────────────────────────


class TestNoClaudeMdInAgents:
    """Verify CLAUDE.md does NOT reach background agents.

    Context now flows exclusively via the Haiku conversation summary,
    passed as context_summary to PlanExecutor and forwarded to spawn().
    No file dependency — works on any machine, any project.
    """

    async def test_claude_md_not_injected_into_plan_spawn(self, tmp_path) -> None:
        """CLAUDE.md content does NOT appear in agent session — no inject_context, not in prompt."""
        from archon.ai.background_agent_manager import BackgroundAgentManager
        from archon.ai.plan_executor import PlanExecutor
        from archon.ai.agent_plan import AgentPlan, AgentTask
        from archon.ai.event_mapper import Response

        claude_md = tmp_path / "CLAUDE.md"
        sentinel = "UNIQUE_SENTINEL_VALUE_XYZ"
        claude_md.write_text(f"# Project\n{sentinel}")

        injected_texts: list[str] = []
        sent_prompts: list[str] = []

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()
        mock_session.inject_context = MagicMock(side_effect=injected_texts.append)

        async def _send(prompt: str):
            sent_prompts.append(prompt)
            yield Response(content="done")

        mock_session.send = _send

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        sm.track_context = MagicMock()
        sm.inject_agent_context = MagicMock()

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            bam = BackgroundAgentManager(bot=bot, session_manager=sm, cwd=str(tmp_path))
            executor = PlanExecutor(
                bam=bam, bot=bot, user_id=1, cwd=str(tmp_path), context_summary=""
            )
            plan = AgentPlan(
                scope="large",
                summary="Test",
                agents=[AgentTask(id="a1", task="Do work")],
            )
            await executor.execute(plan)

        runs = list(bam._runs.values())
        assert len(runs) == 1
        await runs[0].done.wait()

        # CLAUDE.md sentinel must not appear anywhere
        assert all(sentinel not in t for t in injected_texts), "CLAUDE.md injected via inject_context"
        assert all(sentinel not in p for p in sent_prompts), "CLAUDE.md leaked into task prompt"

    async def test_haiku_summary_flows_to_agent_via_context_param(self, tmp_path) -> None:
        """Conversation summary (not CLAUDE.md) flows to agents as the context prompt prefix."""
        from archon.ai.background_agent_manager import BackgroundAgentManager
        from archon.ai.plan_executor import PlanExecutor
        from archon.ai.agent_plan import AgentPlan, AgentTask
        from archon.ai.event_mapper import Response

        sent_prompts: list[str] = []

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.stop = AsyncMock()
        mock_session.inject_context = MagicMock()

        async def _send(prompt: str):
            sent_prompts.append(prompt)
            yield Response(content="done")

        mock_session.send = _send

        bot = MagicMock()
        bot.send_message = AsyncMock()
        sm = MagicMock()
        sm.track_context = MagicMock()
        sm.inject_agent_context = MagicMock()

        summary = "User is refactoring /project/auth.py — the authentication module."

        with patch("archon.ai.background_agent_manager.ClaudeSession", return_value=mock_session):
            bam = BackgroundAgentManager(bot=bot, session_manager=sm, cwd=str(tmp_path))
            executor = PlanExecutor(
                bam=bam, bot=bot, user_id=1, cwd=str(tmp_path), context_summary=summary
            )
            plan = AgentPlan(
                scope="large",
                summary="Auth refactor",
                agents=[AgentTask(id="a1", task="Update auth module")],
            )
            await executor.execute(plan)

        runs = list(bam._runs.values())
        assert len(runs) == 1
        await runs[0].done.wait()

        assert len(sent_prompts) == 1
        assert "/project/auth.py" in sent_prompts[0]
        assert "Update auth module" in sent_prompts[0]
