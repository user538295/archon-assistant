"""PlatformService abstract base class."""
from __future__ import annotations

from abc import ABC, abstractmethod

from archon.platform._run_mixin import RunMixin
from archon.platform.types import ServiceInfo


class PlatformService(RunMixin, ABC):
    """ABC for platform-specific service lifecycle management.

    All mutating methods return int (0=success, 1=failure).
    They do NOT raise on operational failures.
    """

    def __init__(self) -> None:
        super().__init__()

    @property
    @abstractmethod
    def service_name(self) -> str:
        """Platform service manager name (e.g. 'launchd', 'systemd')."""

    @abstractmethod
    def register(self, dry_run: bool = False) -> int:
        """Install the service definition. Returns 0 on success."""

    @abstractmethod
    def unregister(self, dry_run: bool = False) -> int:
        """Remove the service definition. Returns 0 on success."""

    @abstractmethod
    def is_installed(self) -> bool:
        """Check if the service definition exists."""

    @abstractmethod
    def start(self, dry_run: bool = False) -> int:
        """Start the service. Returns 0 on success."""

    @abstractmethod
    def stop(self, dry_run: bool = False) -> int:
        """Stop the service. Returns 0 on success."""

    @abstractmethod
    def restart(self, dry_run: bool = False) -> int:
        """Restart the service. Returns 0 on success."""

    @abstractmethod
    def status(self) -> ServiceInfo:
        """Query current service status. Never raises."""

    @abstractmethod
    def remediation_hint(self) -> str:
        """Platform-specific troubleshooting text."""

    @abstractmethod
    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        """Idempotent cleanup before activation. Returns 0 on success."""
