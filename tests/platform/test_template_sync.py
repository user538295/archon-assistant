"""T43 — Verify template placeholders are consistent across install.py and platform modules.

The same set of placeholders (__ARCHON_DIR__, __UV_PATH__, __LOG_FILE__) must be used
in install.py's register_service() and in both platform service register() methods.
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PLACEHOLDER_RE = re.compile(r"__[A-Z_]+__")


def _extract_placeholders(source: str) -> set[str]:
    """Return all __UPPER_CASE__ placeholders found in source text."""
    return set(_PLACEHOLDER_RE.findall(source))


def _template_placeholders() -> set[str]:
    """Read both template files and return the union of __UPPER__ placeholders."""
    plist = (_REPO_ROOT / "scripts" / "com.archon.assistant.plist").read_text()
    unit = (_REPO_ROOT / "scripts" / "archon.service").read_text()
    return _extract_placeholders(plist) | _extract_placeholders(unit)


class TestTemplatePlaceholderSync:
    """Placeholders in install.py must match those in platform service modules."""

    def test_install_py_placeholders(self) -> None:
        """install.py register_service uses exactly the expected placeholders."""
        src = (_REPO_ROOT / "install.py").read_text()
        # Extract the register_service function body
        start = src.index("def register_service(")
        # Find the next top-level def or end of file
        next_def = src.find("\ndef ", start + 1)
        body = src[start:next_def] if next_def != -1 else src[start:]

        placeholders = _extract_placeholders(body)
        expected = _template_placeholders()
        assert placeholders == expected, (
            f"install.py register_service placeholders mismatch: "
            f"got {placeholders}, expected {expected}"
        )

    def test_macos_service_placeholders(self) -> None:
        """LaunchdService.register() uses exactly the expected placeholders."""
        src = (_REPO_ROOT / "archon" / "platform" / "macos" / "service.py").read_text()
        start = src.index("def register(")
        next_def = src.find("\n    def ", start + 1)
        body = src[start:next_def] if next_def != -1 else src[start:]

        placeholders = _extract_placeholders(body)
        expected = _template_placeholders()
        assert placeholders == expected, (
            f"LaunchdService.register placeholders mismatch: "
            f"got {placeholders}, expected {expected}"
        )

    def test_linux_service_placeholders(self) -> None:
        """SystemdService.register() uses exactly the expected placeholders."""
        src = (_REPO_ROOT / "archon" / "platform" / "linux" / "service.py").read_text()
        start = src.index("def register(")
        next_def = src.find("\n    def ", start + 1)
        body = src[start:next_def] if next_def != -1 else src[start:]

        placeholders = _extract_placeholders(body)
        expected = _template_placeholders()
        assert placeholders == expected, (
            f"SystemdService.register placeholders mismatch: "
            f"got {placeholders}, expected {expected}"
        )

    def test_all_three_use_identical_placeholders(self) -> None:
        """All three register functions must use the exact same placeholder set."""
        install_src = (_REPO_ROOT / "install.py").read_text()
        start = install_src.index("def register_service(")
        next_def = install_src.find("\ndef ", start + 1)
        install_body = install_src[start:next_def] if next_def != -1 else install_src[start:]
        install_ph = _extract_placeholders(install_body)

        macos_src = (_REPO_ROOT / "archon" / "platform" / "macos" / "service.py").read_text()
        start = macos_src.index("def register(")
        next_def = macos_src.find("\n    def ", start + 1)
        macos_body = macos_src[start:next_def] if next_def != -1 else macos_src[start:]
        macos_ph = _extract_placeholders(macos_body)

        linux_src = (_REPO_ROOT / "archon" / "platform" / "linux" / "service.py").read_text()
        start = linux_src.index("def register(")
        next_def = linux_src.find("\n    def ", start + 1)
        linux_body = linux_src[start:next_def] if next_def != -1 else linux_src[start:]
        linux_ph = _extract_placeholders(linux_body)

        assert install_ph == macos_ph, (
            f"install.py vs macos mismatch: {install_ph} != {macos_ph}"
        )
        assert install_ph == linux_ph, (
            f"install.py vs linux mismatch: {install_ph} != {linux_ph}"
        )
