"""Skill loader — reads Claude Code skills from ~/.claude/skills/*/SKILL.md."""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("archon")

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_KEY_VALUE_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.+)$")


@dataclass
class Skill:
    """A single Claude Code skill loaded from a SKILL.md file."""

    name: str
    description: str
    content: str  # SKILL.md body with frontmatter stripped


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    """Parse YAML frontmatter from *text*.

    Returns a dict of key→value pairs or None if frontmatter is absent or malformed.
    Only handles simple ``key: value`` pairs (no multiline, no nesting).
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return None
    fm_block = match.group(1)
    result: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = _KEY_VALUE_RE.match(line.strip())
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


class SkillLoader:
    """Load and cache Claude Code skills from a skills directory."""

    def __init__(self, skills_dir: Path = Path("~/.claude/skills")) -> None:
        self._skills_dir = Path(skills_dir).expanduser()
        self._cache: list[Skill] | None = None

    def load_all(self) -> list[Skill]:
        """Return all valid skills, loaded from disk on the first call then cached."""
        if self._cache is not None:
            return self._cache

        skills: list[Skill] = []

        if not self._skills_dir.exists():
            logger.warning("Skills directory not found: %s", self._skills_dir)
            self._cache = skills
            return self._cache

        for entry in sorted(self._skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.exists():
                continue
            skill = self._load_skill(skill_file)
            if skill is not None:
                skills.append(skill)

        self._cache = skills
        return self._cache

    def get(self, name: str) -> "Skill | None":
        """Return the skill with the given name, or None if not found."""
        for skill in self.load_all():
            if skill.name == name:
                return skill
        return None

    def _load_skill(self, path: Path) -> "Skill | None":
        """Parse a single SKILL.md file; return None and log a warning on any error."""
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not read skill file %s: %s", path, exc)
            return None

        fm = _parse_frontmatter(text)
        if fm is None:
            logger.warning(
                "Skill %s skipped: missing or malformed YAML frontmatter", path.parent.name
            )
            return None

        name = fm.get("name", "").strip()
        description = fm.get("description", "").strip()

        if not name:
            logger.warning("Skill %s skipped: 'name' field missing in frontmatter", path.parent.name)
            return None
        if not description:
            logger.warning(
                "Skill %s skipped: 'description' field missing in frontmatter", path.parent.name
            )
            return None

        # Strip frontmatter to get body content
        content = _FRONTMATTER_RE.sub("", text).strip()

        return Skill(name=name, description=description, content=content)
