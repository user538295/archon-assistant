"""Windows service stub — not yet implemented."""
from __future__ import annotations

import logging

from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

logger = logging.getLogger("archon")

_NOT_SUPPORTED = "Windows service management not yet supported — run Archon manually with: uv run python main.py"


class WindowsService(PlatformService):
    """Stub for Windows — all lifecycle methods return failure (1) with a warning."""

    @property
    def service_name(self) -> str:
        return "windows"

    def register(self, dry_run: bool = False) -> int:
        logger.warning(_NOT_SUPPORTED)
        return 1

    def unregister(self, dry_run: bool = False) -> int:
        logger.warning(_NOT_SUPPORTED)
        return 1

    def is_installed(self) -> bool:
        return False

    def start(self, dry_run: bool = False) -> int:
        logger.warning(_NOT_SUPPORTED)
        return 1

    def stop(self, dry_run: bool = False) -> int:
        logger.warning(_NOT_SUPPORTED)
        return 1

    def restart(self, dry_run: bool = False) -> int:
        logger.warning(_NOT_SUPPORTED)
        return 1

    def status(self) -> ServiceInfo:
        return ServiceInfo(running=False, service_name="archon")

    def remediation_hint(self) -> str:
        return _NOT_SUPPORTED

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        return 0
