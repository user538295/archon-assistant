"""Codebase invariant tests — structural checks that must hold across the whole repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent


def test_no_qmd_symbols_in_codebase() -> None:
    """No 'qmd' symbols must remain in .py, .toml, or .sh files (outside excluded dirs/lines).

    Excluded paths:
    - .git/
    - Documentation/Completed/   (historical docs by design)
    - Documentation/Backlog/     (feature spec references by design)
    - .claude/                   (worktree/memory metadata)

    Excluded lines:
    - Lines containing '_STALE_SCRIPTS' (intentional legacy migration list in install.py)
    """
    result = subprocess.run(
        [
            "grep",
            "-ri",
            "qmd",
            ".",
            "--include=*.py",
            "--include=*.toml",
            "--include=*.sh",
            "--exclude-dir=.git",
            "--exclude-dir=Documentation/Completed",
            "--exclude-dir=Documentation/Backlog",
            "--exclude-dir=.claude",
            "--exclude-dir=.venv",
        ],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
    )

    # Filter out:
    # - lines containing _STALE_SCRIPTS (intentional legacy migration list in install.py)
    # - lines from this invariant test file itself (contains the grep pattern as a string)
    _this_file = "tests/test_codebase_invariants.py"
    matches = [
        line
        for line in result.stdout.splitlines()
        if "_STALE_SCRIPTS" not in line and _this_file not in line
    ]

    assert matches == [], (
        f"Found {len(matches)} unexpected 'qmd' reference(s) in the codebase:\n"
        + "\n".join(matches)
    )
