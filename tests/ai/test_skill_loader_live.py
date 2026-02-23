"""S6.2 — Live skill loader test: real ~/.claude/skills/ directory, no mocks.

Skipped automatically if ~/.claude/skills/ does not exist or contains no skills.
Run with: uv run pytest -m live
"""
from pathlib import Path

import pytest

from archon.ai.skill_loader import SkillLoader

_SKILLS_DIR = Path("~/.claude/skills").expanduser()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _SKILLS_DIR.exists(),
        reason="~/.claude/skills/ directory not found",
    ),
]


def _require_skills(skills: list) -> None:
    """Skip test if no valid skills were loaded from the real directory."""
    if not skills:
        pytest.skip("No valid skills found in ~/.claude/skills/")


def test_live_load_all_returns_at_least_one_skill() -> None:
    """load_all() returns at least one Skill from the real skills directory."""
    loader = SkillLoader()
    skills = loader.load_all()
    _require_skills(skills)
    assert len(skills) >= 1


def test_live_skill_has_non_empty_name() -> None:
    """Every loaded Skill has a non-empty name field."""
    loader = SkillLoader()
    skills = loader.load_all()
    _require_skills(skills)
    for skill in skills:
        assert skill.name, f"Skill with empty name found: {skill}"


def test_live_skill_has_non_empty_description() -> None:
    """Every loaded Skill has a non-empty description field."""
    loader = SkillLoader()
    skills = loader.load_all()
    _require_skills(skills)
    for skill in skills:
        assert skill.description, f"Skill {skill.name!r} has empty description"


def test_live_skill_has_non_empty_content() -> None:
    """Every loaded Skill has a non-empty content field (frontmatter stripped)."""
    loader = SkillLoader()
    skills = loader.load_all()
    _require_skills(skills)
    for skill in skills:
        assert skill.content, f"Skill {skill.name!r} has empty content"


def test_live_get_returns_first_skill_by_name() -> None:
    """get(first_skill.name) returns the same Skill object as load_all()[0]."""
    loader = SkillLoader()
    skills = loader.load_all()
    _require_skills(skills)
    first = skills[0]
    found = loader.get(first.name)
    assert found is not None, f"get({first.name!r}) returned None"
    assert found.name == first.name
    assert found.description == first.description
    assert found.content == first.content


def test_live_get_returns_none_for_nonexistent_skill() -> None:
    """get('nonexistent-skill') returns None."""
    loader = SkillLoader()
    result = loader.get("nonexistent-skill-xyzzy-9999")
    assert result is None


def test_live_load_all_is_cached() -> None:
    """A second call to load_all() returns the identical list object (cached)."""
    loader = SkillLoader()
    first = loader.load_all()
    second = loader.load_all()
    assert first is second
