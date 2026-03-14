"""Windows service stub — not yet implemented."""
from __future__ import annotations

from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

_NOT_SUPPORTED = "Windows service management not yet supported — run Archon manually with: uv run python main.py"


class WindowsService(PlatformService):
    """Stub for Windows — all lifecycle methods raise NotImplementedError."""

    @property
    def service_name(self) -> str:
        return "windows"

    def register(self, dry_run: bool = False) -> int:
        raise NotImplementedError(_NOT_SUPPORTED)

    def unregister(self, dry_run: bool = False) -> int:
        raise NotImplementedError(_NOT_SUPPORTED)

    def is_installed(self) -> bool:
        return False

    def start(self, dry_run: bool = False) -> int:
        raise NotImplementedError(_NOT_SUPPORTED)

    def stop(self, dry_run: bool = False) -> int:
        raise NotImplementedError(_NOT_SUPPORTED)

    def restart(self, dry_run: bool = False) -> int:
        raise NotImplementedError(_NOT_SUPPORTED)

    def status(self) -> ServiceInfo:
        return ServiceInfo(running=False, label="archon")

    def remediation_hint(self) -> str:
        return _NOT_SUPPORTED

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        return 0
