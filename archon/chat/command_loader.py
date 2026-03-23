"""CommandLoader — scans directories for Telegram slash-command .md files."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_VALID_NAME = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(frozen=True)
class CommandInfo:
    name: str
    source: Literal["global", "project"]


class CommandLoader:
    """Scans global and/or project command directories for *.md files.

    Does NOT cache — rescans filesystem on every call.
    """

    def __init__(
        self,
        global_dir: Path | None = None,
        project_dir: Path | None = None,
    ) -> None:
        if global_dir is None:
            global_dir = Path.home() / ".claude" / "commands"
        self._global_dir = global_dir
        self._project_dir = project_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> list[CommandInfo]:
        """Return all valid commands, globals first, each group sorted by name.

        Collision rule: if the same name appears in both directories, only the
        project entry is kept.
        """
        globals_ = self._scan(self._global_dir, "global")
        projects = self._scan(self._project_dir, "project")

        # Apply collision rule: project wins
        project_names = {cmd.name for cmd in projects}
        filtered_globals = [cmd for cmd in globals_ if cmd.name not in project_names]

        return filtered_globals + projects

    def exists(self, name: str) -> bool:
        """Return True if <name>.md exists in either directory.

        Returns False immediately if *name* fails ``^[a-zA-Z0-9_-]+$`` validation.
        """
        if not _VALID_NAME.match(name):
            return False

        for directory in (self._global_dir, self._project_dir):
            if directory is not None and (directory / f"{name}.md").is_file():
                return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan(
        self, directory: Path | None, source: Literal["global", "project"]
    ) -> list[CommandInfo]:
        """Scan *directory* for valid *.md files; returns sorted CommandInfo list."""
        if directory is None or not directory.is_dir():
            return []

        results: list[CommandInfo] = []
        for path in directory.glob("*.md"):
            stem = path.stem
            if _VALID_NAME.match(stem):
                results.append(CommandInfo(name=stem, source=source))

        return sorted(results, key=lambda c: c.name)
