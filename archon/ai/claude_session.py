"""Claude session — wraps ClaudeSDKClient to provide typed event streaming."""
import logging
import os
from typing import TYPE_CHECKING, AsyncGenerator

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from archon.ai.event_mapper import Event, EventMapper

if TYPE_CHECKING:
    from archon.ai.skill_loader import Skill

logger = logging.getLogger("archon")


def _build_system_prompt(skills: "list[Skill]") -> str | None:
    """Build a compact skill registry string for the system prompt.

    Returns None when the skill list is empty so the option stays unset.
    """
    if not skills:
        return None
    lines = ["Available skills:"]
    for skill in skills:
        lines.append(f"- {skill.name}: {skill.description}")
    return "\n".join(lines)


class ClaudeSession:
    """Manages a single Claude conversation via the Claude Agent SDK."""

    def __init__(
        self,
        cwd: str | None = None,
        skills: "list[Skill] | None" = None,
        model: str | None = None,
    ) -> None:
        self._cwd = cwd
        self._model = model
        self._skills: list[Skill] = list(skills) if skills else []
        self._pending_skills: list[Skill] = []
        self._client: ClaudeSDKClient | None = None
        self._mapper = EventMapper()
        self._connected = False

    async def start(self) -> None:
        """Connect the SDK client and start the Claude process."""
        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=self._cwd,
            system_prompt=_build_system_prompt(self._skills),
            model=self._model,
        )
        self._client = ClaudeSDKClient(options=options)
        # Strip CLAUDECODE so the subprocess isn't rejected as a nested session
        claudecode = os.environ.pop("CLAUDECODE", None)
        try:
            await self._client.connect()
        finally:
            if claudecode is not None:
                os.environ["CLAUDECODE"] = claudecode
        self._connected = True
        logger.info("Claude session started (cwd=%s)", self._cwd)

    def activate_skill(self, skill: "Skill") -> None:
        """Queue a skill for one-shot injection into the next outgoing message."""
        self._pending_skills.append(skill)
        logger.info("Skill queued for next message: %s", skill.name)

    async def send(self, prompt: str) -> AsyncGenerator[Event, None]:
        """Send a prompt and yield typed archon events for the response.

        If skills are queued via activate_skill(), their full bodies are prepended
        as labelled context blocks before the user prompt (one-shot injection).
        The queue is cleared after the first send.
        """
        if self._client is None or not self._connected:
            raise RuntimeError("Session not started")

        if self._pending_skills:
            skill_blocks = "\n\n".join(
                f"[Skill: {s.name}]\n{s.content}\n[End Skill: {s.name}]"
                for s in self._pending_skills
            )
            full_prompt = f"{skill_blocks}\n\n{prompt}"
            self._pending_skills.clear()
        else:
            full_prompt = prompt

        await self._client.query(full_prompt)
        async for event in self._mapper.map_messages(self._client.receive_response()):
            yield event

    async def stop(self) -> None:
        """Disconnect the SDK client."""
        if self._client is not None and self._connected:
            try:
                await self._client.disconnect()
            except RuntimeError as exc:
                # anyio cancel scope can't be exited from a different task during shutdown
                logger.warning("Session disconnect skipped: %s", exc)
            finally:
                self._connected = False
            logger.info("Claude session stopped")

    @property
    def model(self) -> str | None:
        """The model override passed to this session, or None for SDK default."""
        return self._model

    @property
    def is_alive(self) -> bool:
        """True if the session is connected."""
        return self._connected
