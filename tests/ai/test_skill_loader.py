"""Tests for SkillLoader — S6.1."""
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from archon.ai.skill_loader import Skill, SkillLoader


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _write_skill(
    skills_dir: Path,
    name: str,
    description: str,
    content: str,
) -> Path:
    """Create a <skills_dir>/<name>/SKILL.md file and return its path."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{content}",
        encoding="utf-8",
    )
    return skill_file


def _write_raw(skills_dir: Path, name: str, raw_content: str) -> Path:
    """Write arbitrary raw content to <skills_dir>/<name>/SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(raw_content, encoding="utf-8")
    return skill_file


# ──────────────────────────────────────────────────────────────────
# load_all — happy path
# ──────────────────────────────────────────────────────────────────


def test_empty_skills_dir_returns_empty_list(tmp_path: Path) -> None:
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.load_all() == []


def test_load_all_returns_skill_objects(tmp_path: Path) -> None:
    _write_skill(tmp_path, "my-skill", "Does things", "# My Skill\nContent here.")
    loader = SkillLoader(skills_dir=tmp_path)
    skills = loader.load_all()
    assert len(skills) == 1
    assert isinstance(skills[0], Skill)


def test_load_all_parses_name(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha-skill", "Alpha desc", "body")
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.load_all()[0].name == "alpha-skill"


def test_load_all_parses_description(tmp_path: Path) -> None:
    _write_skill(tmp_path, "beta-skill", "Beta description text", "body")
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.load_all()[0].description == "Beta description text"


def test_load_all_content_excludes_frontmatter(tmp_path: Path) -> None:
    _write_skill(tmp_path, "gamma-skill", "Gamma desc", "# Body\nActual content.")
    loader = SkillLoader(skills_dir=tmp_path)
    content = loader.load_all()[0].content
    assert "name: gamma-skill" not in content
    assert "description:" not in content
    assert "---" not in content


def test_load_all_content_includes_body(tmp_path: Path) -> None:
    _write_skill(tmp_path, "delta-skill", "Delta desc", "# Instructions\nDo the thing.")
    loader = SkillLoader(skills_dir=tmp_path)
    content = loader.load_all()[0].content
    assert "# Instructions" in content
    assert "Do the thing." in content


def test_load_all_multiple_skills(tmp_path: Path) -> None:
    _write_skill(tmp_path, "skill-a", "A desc", "body A")
    _write_skill(tmp_path, "skill-b", "B desc", "body B")
    loader = SkillLoader(skills_dir=tmp_path)
    names = {s.name for s in loader.load_all()}
    assert names == {"skill-a", "skill-b"}


# ──────────────────────────────────────────────────────────────────
# load_all — error / edge cases
# ──────────────────────────────────────────────────────────────────


def test_load_all_nonexistent_dir_returns_empty_list(tmp_path: Path) -> None:
    loader = SkillLoader(skills_dir=tmp_path / "does-not-exist")
    assert loader.load_all() == []


def test_load_all_skips_dirs_without_skill_md(tmp_path: Path) -> None:
    (tmp_path / "empty-dir").mkdir()
    _write_skill(tmp_path, "real-skill", "Real desc", "real body")
    loader = SkillLoader(skills_dir=tmp_path)
    skills = loader.load_all()
    assert len(skills) == 1
    assert skills[0].name == "real-skill"


def test_load_all_malformed_frontmatter_skips_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(tmp_path, "bad-skill", "no frontmatter at all — just plain text")
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = SkillLoader(skills_dir=tmp_path)
        skills = loader.load_all()
    assert skills == []
    assert any("bad-skill" in r.message for r in caplog.records)


def test_load_all_missing_name_key_skips_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(
        tmp_path,
        "no-name-skill",
        "---\ndescription: Some description\n---\n\nbody",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = SkillLoader(skills_dir=tmp_path)
        skills = loader.load_all()
    assert skills == []


def test_load_all_missing_description_key_skips_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(
        tmp_path,
        "no-desc-skill",
        "---\nname: no-desc-skill\n---\n\nbody",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = SkillLoader(skills_dir=tmp_path)
        skills = loader.load_all()
    assert skills == []


def test_load_all_missing_closing_fence_skips_and_logs_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_raw(
        tmp_path,
        "unclosed-skill",
        "---\nname: unclosed-skill\ndescription: something\nbody without closing fence",
    )
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = SkillLoader(skills_dir=tmp_path)
        skills = loader.load_all()
    assert skills == []


def test_load_all_good_and_bad_skills_returns_only_good(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _write_skill(tmp_path, "good-skill", "Good desc", "good body")
    _write_raw(tmp_path, "bad-skill", "no frontmatter")
    with caplog.at_level(logging.WARNING, logger="archon"):
        loader = SkillLoader(skills_dir=tmp_path)
        skills = loader.load_all()
    assert len(skills) == 1
    assert skills[0].name == "good-skill"


# ──────────────────────────────────────────────────────────────────
# load_all — caching
# ──────────────────────────────────────────────────────────────────


def test_load_all_returns_same_list_on_second_call(tmp_path: Path) -> None:
    _write_skill(tmp_path, "cached-skill", "Cached desc", "body")
    loader = SkillLoader(skills_dir=tmp_path)
    first = loader.load_all()
    second = loader.load_all()
    assert first is second  # same object — cached


# ──────────────────────────────────────────────────────────────────
# get
# ──────────────────────────────────────────────────────────────────


def test_get_returns_skill_by_name(tmp_path: Path) -> None:
    _write_skill(tmp_path, "target-skill", "Target desc", "target body")
    _write_skill(tmp_path, "other-skill", "Other desc", "other body")
    loader = SkillLoader(skills_dir=tmp_path)
    skill = loader.get("target-skill")
    assert skill is not None
    assert skill.name == "target-skill"


def test_get_returns_none_for_unknown_name(tmp_path: Path) -> None:
    _write_skill(tmp_path, "existing-skill", "Existing desc", "body")
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.get("nonexistent-skill") is None


def test_get_returns_none_when_no_skills(tmp_path: Path) -> None:
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.get("anything") is None


# ──────────────────────────────────────────────────────────────────
# load_skill — public API
# ──────────────────────────────────────────────────────────────────


def test_load_skill_is_public(tmp_path: Path) -> None:
    """load_skill must be a public method (not prefixed with underscore)."""
    loader = SkillLoader(skills_dir=tmp_path)
    assert hasattr(loader, "load_skill")
    assert not hasattr(loader, "_load_skill")


def test_load_skill_returns_skill_for_valid_file(tmp_path: Path) -> None:
    path = _write_skill(tmp_path, "pub-skill", "Public API desc", "body text")
    loader = SkillLoader(skills_dir=tmp_path)
    skill = loader.load_skill(path)
    assert skill is not None
    assert skill.name == "pub-skill"
    assert skill.description == "Public API desc"
    assert "body text" in skill.content


def test_load_skill_returns_none_for_missing_frontmatter(tmp_path: Path) -> None:
    path = _write_raw(tmp_path, "bad", "no frontmatter here")
    loader = SkillLoader(skills_dir=tmp_path)
    assert loader.load_skill(path) is None


# ──────────────────────────────────────────────────────────────────
# Windows CRLF line endings
# ──────────────────────────────────────────────────────────────────


def test_crlf_frontmatter_parses_correctly(tmp_path: Path) -> None:
    """SKILL.md files with Windows CRLF line endings are parsed the same as LF."""
    crlf_content = "---\r\nname: crlf-skill\r\ndescription: CRLF desc\r\n---\r\n\r\nbody content"
    _write_raw(tmp_path, "crlf-skill", crlf_content)
    loader = SkillLoader(skills_dir=tmp_path)
    skills = loader.load_all()
    assert len(skills) == 1
    assert skills[0].name == "crlf-skill"
    assert skills[0].description == "CRLF desc"
    assert "body content" in skills[0].content


def test_crlf_frontmatter_body_excludes_frontmatter(tmp_path: Path) -> None:
    """Body content extracted from CRLF files must not contain frontmatter."""
    crlf_content = "---\r\nname: crlf2-skill\r\ndescription: desc\r\n---\r\n\r\n# Body\r\nActual content."
    _write_raw(tmp_path, "crlf2-skill", crlf_content)
    loader = SkillLoader(skills_dir=tmp_path)
    skills = loader.load_all()
    assert len(skills) == 1
    content = skills[0].content
    assert "---" not in content
    assert "name: crlf2-skill" not in content
    assert "# Body" in content
