"""Import boundary lint: Archon side (FEAT-038 Task 9.2).

Rules enforced:
- No file under archon/ or tests/ may import from `archon.search.*` (old deleted package).
- No file under archon/ may import from `archon_search.*`, except archon/ai/search_client.py.
- No file under tests/ may import from `archon_search.*`, except explicitly exempted files
  that legitimately need archon_search types for fixture construction or boundary testing.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).parent.parent
ARCHON_PKG = ROOT / "archon"
TESTS_PKG = ROOT / "tests"

EXEMPT = {"archon/ai/search_client.py"}

# Tests that legitimately import archon_search types for fixture construction or boundary testing.
# These are at the HTTP boundary or need archon_search types for mock return values only.
_TESTS_ARCHON_SEARCH_EXEMPT = {
    "tests/ai/test_search_client.py",           # tests SearchClient itself, needs archon_search types
    "tests/ai/test_search_context_provider.py", # needs archon_search types for mock return values
    "tests/cli/test_doctor.py",                 # uses archon_search.progress types for IndexingState fixtures
    "tests/cli/test_search_cmd.py",             # uses archon_search types for CLI command fixtures
    "tests/integration/test_search_routing.py", # type-only imports (SearchResult, RouteResponse) for fixtures
    "tests/e2e/conftest.py",                    # ASGI transport fixtures — constructs real FastAPI app from archon_search
    "tests/e2e/test_search_client_e2e.py",      # Suite 1 e2e happy-path tests — uses IngestJob, JobStatus, path_to_collection_name
}


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


def test_no_archon_search_behavior_imports_in_tests() -> None:
    """tests/ must not import `archon_search.*` except in explicitly exempted test files.

    Exempted files are those that legitimately need archon_search types for fixture
    construction or that test the HTTP boundary (SearchClient) directly.
    """
    all_violations: list[str] = []
    for py_file in sorted(TESTS_PKG.rglob("*.py")):
        rel = str(py_file.relative_to(ROOT))
        if rel in _TESTS_ARCHON_SEARCH_EXEMPT:
            continue
        all_violations.extend(_collect_violations(py_file, "archon_search."))

    assert not all_violations, (
        "Found imports from `archon_search.*` in non-exempt test files — "
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


def test_tests_archon_search_exemptions_are_load_bearing() -> None:
    """Each entry in _TESTS_ARCHON_SEARCH_EXEMPT exists and imports archon_search.* (no stale exemptions)."""
    for rel_path in sorted(_TESTS_ARCHON_SEARCH_EXEMPT):
        path = ROOT / rel_path
        assert path.exists(), f"{rel_path!r} listed in _TESTS_ARCHON_SEARCH_EXEMPT but file not found"

        violations = _collect_violations(path, "archon_search.")
        assert violations, (
            f"{rel_path} does not import 'archon_search.*' — the _TESTS_ARCHON_SEARCH_EXEMPT entry is dead config. "
            "Either remove it from _TESTS_ARCHON_SEARCH_EXEMPT or restore the import."
        )
