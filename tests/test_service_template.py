"""Tests for systemd service template hardening (T44)."""
from __future__ import annotations

import configparser
from pathlib import Path

_SERVICE_FILE = Path(__file__).resolve().parents[1] / "scripts" / "archon.service"


def _parse_service() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(_SERVICE_FILE)
    return cp


def test_restart_sec():
    cp = _parse_service()
    assert cp.get("Service", "RestartSec") == "5"


def test_timeout_stop_sec():
    cp = _parse_service()
    assert cp.get("Service", "TimeoutStopSec") == "10"


def test_after_network_online():
    cp = _parse_service()
    assert "network-online.target" in cp.get("Unit", "After")


def test_wants_network_online():
    cp = _parse_service()
    assert "network-online.target" in cp.get("Unit", "Wants")


def test_restart_on_failure():
    cp = _parse_service()
    assert cp.get("Service", "Restart") == "on-failure"


def test_placeholders_present():
    content = _SERVICE_FILE.read_text()
    assert "__ARCHON_DIR__" in content
    assert "__UV_PATH__" in content
    assert "__LOG_FILE__" in content
