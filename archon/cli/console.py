"""Console output helper — thin wrapper around print/input with quiet mode support."""

from __future__ import annotations

import sys

_RESET = "\033[0m"
_BOLD = "\033[1m"
_RED = "\033[0;31m"
_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_CYAN = "\033[0;36m"


class Console:
    """Thin output wrapper; set quiet=True to suppress all non-error output."""

    def __init__(self, quiet: bool = False) -> None:
        self._quiet = quiet

    def info(self, msg: str) -> None:
        if not self._quiet:
            print(f"  {_CYAN}▸{_RESET} {msg}")

    def success(self, msg: str) -> None:
        if not self._quiet:
            print(f"  {_GREEN}✔{_RESET} {msg}")

    def warn(self, msg: str) -> None:
        if not self._quiet:
            print(f"  {_YELLOW}⚠{_RESET}  {msg}")

    def error(self, msg: str) -> None:
        print(f"\n  {_RED}✖ Error:{_RESET} {msg}\n", file=sys.stderr)

    def ask(self, prompt: str) -> str:
        if self._quiet:
            return ""
        return input(f"  {_BOLD}?{_RESET}  {prompt} ")
