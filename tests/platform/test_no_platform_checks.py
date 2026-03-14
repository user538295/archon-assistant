"""Guard test: no direct platform checks outside archon/platform/.

Walks all .py files under archon/ (excluding archon/platform/) and asserts
that none of them use raw platform-detection patterns like `platform.system`,
`sys.platform`, `os.name`, or `os.uname`.  All platform-dependent logic must
go through the archon.platform abstraction layer.
"""
from __future__ import annotations

import ast
from pathlib import Path

_ARCHON_ROOT = Path(__file__).resolve().parents[2] / "archon"
_EXCLUDED = _ARCHON_ROOT / "platform"

# Modules whose attributes we forbid and the forbidden attribute names.
_FORBIDDEN_ATTRS: dict[str, set[str]] = {
    "platform": {"system"},
    "sys": {"platform"},
    "os": {"name", "uname"},
}

# Direct "from X import Y" patterns that are forbidden.
_FORBIDDEN_FROM_IMPORTS: dict[str, set[str]] = {
    "platform": {"system"},
    "sys": {"platform"},
    "os": {"name", "uname"},
}


def _collect_aliases(tree: ast.Module) -> set[str]:
    """Walk Import/ImportFrom nodes and return local aliases for platform/sys/os."""
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FORBIDDEN_ATTRS:
                    aliases.add(alias.asname if alias.asname else alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_ATTRS:
                for alias in node.names:
                    # "from platform import system as plat_sys" — alias is plat_sys
                    # but we already catch that as a forbidden from-import below;
                    # here we care about "import platform as plat" style via ImportFrom
                    # with level=0 and wildcard-like usage.  Actually ImportFrom
                    # doesn't cover "import platform as plat" — that's ast.Import.
                    pass
    return aliases


def _resolve_module_name(name: str, aliases: dict[str, str]) -> str | None:
    """Map an alias back to its canonical module name, or return as-is if known."""
    if name in _FORBIDDEN_ATTRS:
        return name
    return aliases.get(name)


def _build_alias_map(tree: ast.Module) -> dict[str, str]:
    """Build alias→canonical_module map from Import nodes.

    Covers: `import platform as plat`, `import sys as _sys`, etc.
    """
    alias_map: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _FORBIDDEN_ATTRS:
                    local_name = alias.asname if alias.asname else alias.name
                    alias_map[local_name] = alias.name
    return alias_map


def _find_violations() -> list[str]:
    """Return a list of 'file:line description' strings for violations."""
    violations: list[str] = []

    for py_file in sorted(_ARCHON_ROOT.rglob("*.py")):
        # Skip the platform package itself
        try:
            py_file.relative_to(_EXCLUDED)
            continue
        except ValueError:
            pass  # not under archon/platform/ — check it

        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue

        alias_map = _build_alias_map(tree)

        for node in ast.walk(tree):
            # Attribute access: platform.system, sys.platform, os.name, os.uname
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                canonical = _resolve_module_name(node.value.id, alias_map)
                if canonical and node.attr in _FORBIDDEN_ATTRS.get(canonical, set()):
                    violations.append(
                        f"{py_file}:{node.lineno} {node.value.id}.{node.attr}"
                    )

            # Direct imports: from platform import system, from sys import platform,
            # from os import name, from os import uname
            if isinstance(node, ast.ImportFrom) and node.module in _FORBIDDEN_FROM_IMPORTS:
                forbidden = _FORBIDDEN_FROM_IMPORTS[node.module]
                for alias in node.names:
                    if alias.name in forbidden:
                        violations.append(
                            f"{py_file}:{node.lineno} from {node.module} import {alias.name}"
                        )

    return violations


def test_no_raw_platform_checks() -> None:
    """All platform detection must go through archon.platform, not raw stdlib."""
    violations = _find_violations()
    assert violations == [], (
        "Direct platform checks found outside archon/platform/:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
