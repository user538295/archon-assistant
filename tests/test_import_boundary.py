"""Import boundary lint: Archon side (FEAT-038 Task 9.2).

Rules enforced:
- No file under archon/ or tests/ may import from `archon.search.*` (old deleted package).
- No file under archon/ may import from `archon_search.*`, except archon/ai/search_client.py.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
ARCHON_PKG = ROOT / "archon"
TESTS_PKG = ROOT / "tests"

EXEMPT = {"archon/ai/search_client.py"}


def _imports_in_file(path: Path) -> list[tuple[str, int]]:
    """Return (module_string, lineno) for every import in the file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imports.append((module, node.lineno))
    return imports


def _collect_violations(path: Path, bad_prefix: str) -> list[str]:
    rel = path.relative_to(ROOT)
    violations = []
    for module, lineno in _imports_in_file(path):
        if module == bad_prefix.rstrip(".") or module.startswith(bad_prefix):
            violations.append(f"{rel}:{lineno}: imports {module!r}")
    return violations


def test_no_archon_search_imports_in_archon_or_tests() -> None:
    """Neither archon/ nor tests/ may import from `archon.search.*` (old deleted package)."""
    all_violations: list[str] = []
    for directory in (ARCHON_PKG, TESTS_PKG):
        for py_file in sorted(directory.rglob("*.py")):
            all_violations.extend(_collect_violations(py_file, "archon.search."))

    assert not all_violations, (
        "Found imports from `archon.search.*` (deleted package) — "
        f"{len(all_violations)} violation(s):\n" + "\n".join(all_violations)
    )


def test_no_archon_search_imports_outside_search_client() -> None:
    """archon/ must not import `archon_search.*` except in search_client.py."""
    all_violations: list[str] = []
    for py_file in sorted(ARCHON_PKG.rglob("*.py")):
        rel = str(py_file.relative_to(ROOT))
        if rel in EXEMPT:
            continue
        all_violations.extend(_collect_violations(py_file, "archon_search."))

    assert not all_violations, (
        "Found imports from `archon_search.*` outside search_client.py — "
        f"{len(all_violations)} violation(s):\n" + "\n".join(all_violations)
    )


def test_search_client_is_exempt() -> None:
    """search_client.py is in EXEMPT and actually imports archon_search.* (exemption is load-bearing)."""
    search_client = ARCHON_PKG / "ai" / "search_client.py"
    assert search_client.exists(), f"search_client.py not found at {search_client}"

    rel = str(search_client.relative_to(ROOT))
    assert rel in EXEMPT, f"{rel!r} must be listed in EXEMPT"

    # Confirm the exemption is load-bearing: the file must import archon_search.*
    violations = _collect_violations(search_client, "archon_search.")
    assert violations, (
        f"{rel} does not import 'archon_search.*' — the EXEMPT entry is dead config. "
        "Either remove it from EXEMPT or restore the import."
    )
