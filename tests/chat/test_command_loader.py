"""Tests for CommandLoader — TDD first pass."""

import pytest
from pathlib import Path

from archon.chat.command_loader import CommandInfo, CommandLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_md(directory: Path, *names: str) -> None:
    """Create empty .md files in *directory* for each stem in *names*."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / f"{name}.md").write_text("")


# ---------------------------------------------------------------------------
# load_all tests
# ---------------------------------------------------------------------------


def test_load_all_global_only(tmp_path: Path) -> None:
    """Two .md files in global dir, no project dir → two global CommandInfo entries."""
    global_dir = tmp_path / "global"
    make_md(global_dir, "deploy", "status")

    loader = CommandLoader(global_dir=global_dir, project_dir=None)
    result = loader.load_all()

    assert len(result) == 2
    assert all(cmd.source == "global" for cmd in result)
    assert {cmd.name for cmd in result} == {"deploy", "status"}


def test_load_all_project_only(tmp_path: Path) -> None:
    """No global dir, project dir with two files → two project entries."""
    project_dir = tmp_path / "project"
    make_md(project_dir, "build", "test")

    loader = CommandLoader(global_dir=tmp_path / "no_global", project_dir=project_dir)
    result = loader.load_all()

    assert len(result) == 2
    assert all(cmd.source == "project" for cmd in result)
    assert {cmd.name for cmd in result} == {"build", "test"}


def test_load_all_both_dirs(tmp_path: Path) -> None:
    """Both dirs with distinct files → all entries, globals first."""
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    make_md(global_dir, "alpha", "beta")
    make_md(project_dir, "gamma", "delta")

    loader = CommandLoader(global_dir=global_dir, project_dir=project_dir)
    result = loader.load_all()

    assert len(result) == 4
    global_entries = [c for c in result if c.source == "global"]
    project_entries = [c for c in result if c.source == "project"]
    assert len(global_entries) == 2
    assert len(project_entries) == 2
    # globals appear before project entries
    last_global_idx = max(result.index(c) for c in global_entries)
    first_project_idx = min(result.index(c) for c in project_entries)
    assert last_global_idx < first_project_idx


def test_load_all_empty_dirs(tmp_path: Path) -> None:
    """Dirs exist but are empty → returns []."""
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    global_dir.mkdir()
    project_dir.mkdir()

    loader = CommandLoader(global_dir=global_dir, project_dir=project_dir)
    assert loader.load_all() == []


def test_load_all_missing_dirs(tmp_path: Path) -> None:
    """Paths do not exist → returns [], no exception."""
    loader = CommandLoader(
        global_dir=tmp_path / "nonexistent_global",
        project_dir=tmp_path / "nonexistent_project",
    )
    assert loader.load_all() == []


def test_load_all_ignores_non_md(tmp_path: Path) -> None:
    """Non-.md files (.txt, .yaml) are excluded."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "deploy.md").write_text("")
    (global_dir / "notes.txt").write_text("")
    (global_dir / "config.yaml").write_text("")

    loader = CommandLoader(global_dir=global_dir, project_dir=None)
    result = loader.load_all()

    assert len(result) == 1
    assert result[0].name == "deploy"


def test_load_all_sorted_within_source(tmp_path: Path) -> None:
    """Files in non-alpha order on disk → output sorted A-Z by name within each source."""
    global_dir = tmp_path / "global"
    make_md(global_dir, "zebra", "apple", "mango")

    loader = CommandLoader(global_dir=global_dir, project_dir=None)
    result = loader.load_all()

    names = [cmd.name for cmd in result]
    assert names == sorted(names)


def test_load_all_collision_project_wins(tmp_path: Path) -> None:
    """Same name in both dirs → only project entry in result, global suppressed."""
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    make_md(global_dir, "deploy", "status")
    make_md(project_dir, "deploy", "build")

    loader = CommandLoader(global_dir=global_dir, project_dir=project_dir)
    result = loader.load_all()

    names = [cmd.name for cmd in result]
    # "deploy" appears only once
    assert names.count("deploy") == 1
    # the single "deploy" entry is from project
    deploy_entry = next(c for c in result if c.name == "deploy")
    assert deploy_entry.source == "project"
    # total: status (global) + deploy (project) + build (project) = 3
    assert len(result) == 3


def test_load_all_ignores_invalid_names(tmp_path: Path) -> None:
    """Files with invalid stems (spaces, dots, slashes) are excluded from results."""
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    (global_dir / "valid-command.md").write_text("")
    (global_dir / "has space.md").write_text("")
    (global_dir / "has.dot.md").write_text("")

    loader = CommandLoader(global_dir=global_dir, project_dir=None)
    result = loader.load_all()

    assert len(result) == 1
    assert result[0].name == "valid-command"


# ---------------------------------------------------------------------------
# exists tests
# ---------------------------------------------------------------------------


def test_exists_global(tmp_path: Path) -> None:
    """<name>.md in global dir → True."""
    global_dir = tmp_path / "global"
    make_md(global_dir, "deploy")

    loader = CommandLoader(global_dir=global_dir, project_dir=None)
    assert loader.exists("deploy") is True


def test_exists_project(tmp_path: Path) -> None:
    """<name>.md in project dir → True."""
    project_dir = tmp_path / "project"
    make_md(project_dir, "build")

    loader = CommandLoader(global_dir=None, project_dir=project_dir)
    assert loader.exists("build") is True


def test_exists_collision(tmp_path: Path) -> None:
    """Name in both dirs → True."""
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    make_md(global_dir, "shared")
    make_md(project_dir, "shared")

    loader = CommandLoader(global_dir=global_dir, project_dir=project_dir)
    assert loader.exists("shared") is True


def test_exists_false(tmp_path: Path) -> None:
    """Name absent from both dirs → False."""
    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    make_md(global_dir, "deploy")
    make_md(project_dir, "build")

    loader = CommandLoader(global_dir=global_dir, project_dir=project_dir)
    assert loader.exists("nonexistent") is False


def test_exists_rejects_path_traversal(tmp_path: Path) -> None:
    """exists('../../etc/passwd') returns False without touching filesystem."""
    loader = CommandLoader(global_dir=tmp_path / "global", project_dir=None)
    assert loader.exists("../../etc/passwd") is False
