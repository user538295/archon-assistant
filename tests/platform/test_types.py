"""Tests for ServiceInfo dataclass."""
from archon.platform.types import ServiceInfo


def test_construction_all_fields():
    info = ServiceInfo(running=True, pid=1234, label="com.archon.assistant", uptime="01:23:45")
    assert info.running is True
    assert info.pid == 1234
    assert info.label == "com.archon.assistant"
    assert info.uptime == "01:23:45"


def test_default_none_values():
    info = ServiceInfo(running=False, label="archon")
    assert info.pid is None
    assert info.uptime is None


def test_equality():
    a = ServiceInfo(running=True, pid=42, label="test", uptime="00:05:00")
    b = ServiceInfo(running=True, pid=42, label="test", uptime="00:05:00")
    assert a == b


def test_frozen():
    info = ServiceInfo(running=True, pid=1, label="test")
    try:
        info.running = False  # type: ignore[misc]
        raise AssertionError("Should have raised FrozenInstanceError")
    except AttributeError:
        pass
