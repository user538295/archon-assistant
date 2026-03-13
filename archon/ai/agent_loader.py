"""Agent loader — reads all Claude Code agents from ~/.claude/agents/*.md.

Agents whose ``name`` field ends with ``-archon`` are considered *Archon agents*
(``is_archon=True``) and are included in every Claude session.  Other agents
(TUI-only agents such as ``devils-advocate``) are loaded and surfaced via
``/agents`` for information, but are **not** passed to the Claude SDK.

Load order returned by :meth:`AgentLoader.load_all`:
  1. Archon agents, sorted alphabetically by filename.
  2. Non-archon agents, sorted alphabetically by filename.

Default directory: ``~/.claude/agents/``
"""
import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from archon.ai.skill_loader import _FRONTMATTER_RE, _parse_frontmatter

logger = logging.getLogger("archon")


async def load_workspace_agents(cwd: str | None) -> str | None:
    """Read agents.md from the workspace directory and return formatted context.

    Returns a ``# Workspace Agents\\n\\n<content>`` string ready for injection,
    or ``None`` when there is nothing to inject (no cwd, file absent, or empty).
    """
    if not cwd:
        return None
    agents_path = Path(cwd) / "agents.md"
    try:
        content = (
            await asyncio.to_thread(agents_path.read_text, encoding="utf-8")
        ).strip()
    except FileNotFoundError:
        logger.debug("agents.md not found in workspace: %s", agents_path)
        return None
    except OSError as exc:
        logger.warning("Could not read agents.md: %s", exc)
        return None
    if not content:
        return None
    logger.info("Injecting agents.md into session (%d chars): %s", len(content), agents_path)
    return f"# Workspace Agents\n\n{content}"


_ARCHON_SUFFIX = "-archon"


def _strip_quotes(value: str) -> str:
    """Strip a single pair of surrounding double-quotes from a YAML string value.

    ``"A quoted description"`` → ``A quoted description``.
    Strings without surrounding quotes are returned unchanged.
    """
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    return value


@dataclass
class Agent:
    """A single Claude Code agent loaded from a ``.md`` file in the agents directory."""

    name: str
    description: str
    prompt: str       # agent file body with frontmatter stripped
    model: str | None = None
    tools: list[str] = field(default_factory=list)

    @property
    def is_archon(self) -> bool:
        """True when this agent is designed for the Archon environment.

        An agent is considered an Archon agent when its ``name`` ends with
        the ``-archon`` suffix (e.g. ``researcher-archon``).  Non-archon
        agents (e.g. ``devils-advocate``) are TUI-only and are *not* passed
        to the Claude SDK.
        """
        return self.name.endswith(_ARCHON_SUFFIX)


class AgentLoader:
    """Load and cache all Claude Code agents from an agents directory.

    All ``.md`` files in *agents_dir* are loaded regardless of their name.
    Results are split into two groups and returned in this order:

    1. **Archon agents** (``agent.is_archon is True``) — sorted alphabetically.
    2. **Non-archon agents** — sorted alphabetically.

    Default directory: ``~/.claude/agents/``
    """

    def __init__(self, agents_dir: Path = Path("~/.claude/agents")) -> None:
        self._agents_dir = Path(agents_dir).expanduser()
        self._cache: list[Agent] | None = None

    def load_all(self) -> list[Agent]:
        """Return all valid agents, loaded from disk on the first call then cached.

        Archon agents appear before non-archon agents; each group is sorted
        alphabetically by filename.
        """
        if self._cache is not None:
            return self._cache

        archon_agents: list[Agent] = []
        other_agents: list[Agent] = []

        if not self._agents_dir.exists():
            logger.warning("Agents directory not found: %s", self._agents_dir)
            self._cache = archon_agents + other_agents
            return self._cache

        for entry in sorted(self._agents_dir.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix != ".md":
                continue
            agent = self._load_agent(entry)
            if agent is None:
                continue
            if agent.is_archon:
                archon_agents.append(agent)
            else:
                other_agents.append(agent)

        self._cache = archon_agents + other_agents
        return self._cache

    def get(self, name: str) -> "Agent | None":
        """Return the agent with the given name, or None if not found."""
        for agent in self.load_all():
            if agent.name == name:
                return agent
        return None

    def _load_agent(self, path: Path) -> "Agent | None":
        """Parse a single ``.md`` agent file; return None and log a warning on any error."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read agent file %s: %s", path.name, exc)
            return None

        fm = _parse_frontmatter(text)
        if fm is None:
            logger.warning(
                "Agent %s skipped: missing or malformed YAML frontmatter", path.name
            )
            return None

        name = _strip_quotes(fm.get("name", "").strip())
        description = _strip_quotes(fm.get("description", "").strip())

        if not name:
            logger.warning(
                "Agent %s skipped: 'name' field missing in frontmatter", path.name
            )
            return None
        if not description:
            logger.warning(
                "Agent %s skipped: 'description' field missing in frontmatter", path.name
            )
            return None

        model_raw = _strip_quotes(fm.get("model", "").strip())
        model: str | None = model_raw if model_raw else None

        tools_raw = fm.get("tools", "").strip()
        tools: list[str] = (
            [t.strip() for t in tools_raw.split(",") if t.strip()]
            if tools_raw
            else []
        )

        # Detect multiline YAML list-style tools: (e.g. "tools:\n  - Read\n  - Write")
        # The single-line key-value parser silently drops these; warn the user.
        if not tools and "tools:" in text:
            logger.warning(
                "Agent %s: 'tools' field detected but parsed as empty — "
                "multiline YAML list format (tools:\\n  - Item) is not supported; "
                "use comma-separated format instead: tools: Read, Write",
                path.name,
            )

        # Strip frontmatter to get the agent's prompt body
        prompt = _FRONTMATTER_RE.sub("", text).strip()

        return Agent(
            name=name,
            description=description,
            prompt=prompt,
            model=model,
            tools=tools,
        )
