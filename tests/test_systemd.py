"""S4.3 — systemd unit file structural tests."""
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SERVICE = REPO_ROOT / "scripts" / "archon.service"


# ──────────────────────────────────────────────────────────────────
# Unit file
# ──────────────────────────────────────────────────────────────────


def test_service_file_exists() -> None:
    assert SERVICE.exists(), f"{SERVICE} not found"


def _service_text() -> str:
    return SERVICE.read_text()


def _has_section(section: str) -> bool:
    return f"[{section}]" in _service_text()


def test_service_has_unit_section() -> None:
    assert _has_section("Unit")


def test_service_has_service_section() -> None:
    assert _has_section("Service")


def test_service_has_install_section() -> None:
    assert _has_section("Install")


def test_service_has_restart_on_failure() -> None:
    assert "Restart=on-failure" in _service_text()


def test_service_has_archon_dir_placeholder() -> None:
    assert "__ARCHON_DIR__" in _service_text()


def test_service_has_uv_path_placeholder() -> None:
    assert "__UV_PATH__" in _service_text()


def test_service_has_log_file_placeholder() -> None:
    assert "__LOG_FILE__" in _service_text()


def test_service_has_wanted_by() -> None:
    assert "WantedBy=" in _service_text()
