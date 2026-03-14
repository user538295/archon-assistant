"""Tests for PlatformService ABC."""
import pytest

from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo


def test_cannot_instantiate_directly():
    with pytest.raises(TypeError):
        PlatformService()  # type: ignore[abstract]


class _IncompleteService(PlatformService):
    """Missing some abstract methods."""

    @property
    def service_name(self) -> str:
        return "test"


def test_incomplete_subclass_raises():
    with pytest.raises(TypeError):
        _IncompleteService()  # type: ignore[abstract]


class _CompleteService(PlatformService):
    """Minimal concrete implementation."""

    @property
    def service_name(self) -> str:
        return "test"

    def register(self, dry_run: bool = False) -> int:
        return 0

    def unregister(self, dry_run: bool = False) -> int:
        return 0

    def is_installed(self) -> bool:
        return True

    def start(self, dry_run: bool = False) -> int:
        return 0

    def stop(self, dry_run: bool = False) -> int:
        return 0

    def restart(self, dry_run: bool = False) -> int:
        return 0

    def status(self) -> ServiceInfo:
        return ServiceInfo(running=False, service_name="test")

    def remediation_hint(self) -> str:
        return "hint"

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        return 0


def test_complete_subclass_instantiates():
    svc = _CompleteService()
    assert svc.service_name == "test"


def test_inherited_run_mixin():
    svc = _CompleteService()
    result = svc._run(["echo"], dry_run=True)
    assert svc.command_log == [["echo"]]
    assert result.returncode == 0
