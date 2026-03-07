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
# Integration: CLAUDE.md injected exactly once per background agent
# ──────────────────────────────────────────────────────────────────


class TestClaudeMdInjectedExactlyOnce:
    """Verify that CLAUDE.md content reaches background agents exactly once.

    The risk: PlanExecutor previously passed CLAUDE.md as context="" to
    spawn(), AND BackgroundAgentManager._run_agent() also called
    session.inject_context() — causing double injection.

    After the fix: PlanExecutor passes context="" always; BAM injects once.
    """

    async def test_plan_spawn_gets_claude_md_exactly_once(self, tmp_path) -> None:
        """CLAUDE.md content appears exactly once in agent session interactions."""
        from archon.ai.background_agent_manager import BackgroundAgentManager
        from archon.ai.plan_executor import PlanExecutor
        from archon.ai.agent_plan import AgentPlan, AgentTask
        from archon.ai.event_mapper import Response

        claude_md = tmp_path / "CLAUDE.md"
        sentinel = "UNIQUE_SENTINEL_VALUE_XYZ"
        claude_md.write_text(f"# Project\n{sentinel}")

        # Track all inject_context calls and prompts sent
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
            executor = PlanExecutor(bam=bam, bot=bot, user_id=1, cwd=str(tmp_path))

            plan = AgentPlan(
                scope="large",
                summary="Test",
                agents=[AgentTask(id="a1", task="Do work")],
            )
            await executor.execute(plan)

        # Wait for agent to complete
        runs = list(bam._runs.values())
        assert len(runs) == 1
        await runs[0].done.wait()

        # sentinel must appear exactly once across all inject_context calls
        inject_count = sum(sentinel in text for text in injected_texts)
        assert inject_count == 1, (
            f"Expected CLAUDE.md content injected exactly once, got {inject_count}. "
            f"inject_context called {len(injected_texts)} times."
        )

        # sentinel must NOT appear in the task prompt itself (PlanExecutor passes context="")
        prompt_count = sum(sentinel in p for p in sent_prompts)
        assert prompt_count == 0, (
            f"CLAUDE.md content leaked into the task prompt — double injection detected. "
            f"Prompt count: {prompt_count}"
        )
