"""Shared subprocess helper mixin for platform implementations."""
from __future__ import annotations

import subprocess


class RunMixin:
    """Mixin providing _run() with dry-run and command recording support."""

    def __init__(self) -> None:
        self.command_log: list[list[str]] = []

    def _run(
        self,
        cmd: list[str],
        dry_run: bool = False,
        stdout: str = "",
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess command, or record it if dry_run is True."""
        if dry_run:
            self.command_log.append(list(cmd))
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=stdout, stderr=""
            )
        return subprocess.run(cmd, capture_output=True, text=True)
