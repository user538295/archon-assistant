"""Windows RAG service stub — manual run only."""
from __future__ import annotations

import logging

from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

log = logging.getLogger("archon")

_NOT_SUPPORTED = (
    "RAG service management not supported on Windows; "
    "run `python -m archon.rag.server` manually"
)


class WindowsRagService(PlatformService):
    """Stub — all lifecycle methods log a warning and return 1."""

    @property
    def service_name(self) -> str:
        return "windows-rag"

    def register(self, dry_run: bool = False) -> int:
        log.warning(_NOT_SUPPORTED)
        return 1

    def unregister(self, dry_run: bool = False) -> int:
        log.warning(_NOT_SUPPORTED)
        return 1

    def is_installed(self) -> bool:
        return False

    def start(self, dry_run: bool = False) -> int:
        log.warning(_NOT_SUPPORTED)
        return 1

    def stop(self, dry_run: bool = False) -> int:
        log.warning(_NOT_SUPPORTED)
        return 1

    def restart(self, dry_run: bool = False) -> int:
        log.warning(_NOT_SUPPORTED)
        return 1

    def status(self) -> ServiceInfo:
        return ServiceInfo(running=False, service_name="archon-rag")

    def remediation_hint(self) -> str:
        return _NOT_SUPPORTED

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        return 0
