"""Platform abstraction layer for Archon.

Provides get_service() and get_runtime() lazy singletons with
override()/reset() for DI in tests.
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archon.platform.runtime import PlatformRuntime
    from archon.platform.service import PlatformService

_service: PlatformService | None = None
_runtime: PlatformRuntime | None = None

_UNSET = object()


def _detect() -> str:
    return sys.platform


def get_service() -> PlatformService:
    """Return the platform service singleton (lazy-initialized)."""
    global _service
    if _service is not None:
        return _service

    plat = _detect()
    if plat == "darwin":
        from archon.platform.macos.service import LaunchdService
        _service = LaunchdService()
    elif plat == "linux":
        from archon.platform.linux.service import SystemdService
        _service = SystemdService()
    elif plat == "win32":
        from archon.platform.windows.service import WindowsService
        _service = WindowsService()
    else:
        raise NotImplementedError(f"Unsupported platform: {plat}")
    return _service


def get_runtime() -> PlatformRuntime:
    """Return the platform runtime singleton (lazy-initialized)."""
    global _runtime
    if _runtime is not None:
        return _runtime

    plat = _detect()
    if plat == "darwin":
        from archon.platform.macos.runtime import MacRuntime
        _runtime = MacRuntime()
    elif plat == "linux":
        from archon.platform.linux.runtime import LinuxRuntime
        _runtime = LinuxRuntime()
    elif plat == "win32":
        from archon.platform.windows.runtime import WindowsRuntime
        _runtime = WindowsRuntime()
    else:
        raise NotImplementedError(f"Unsupported platform: {plat}")
    return _runtime


def override(
    service: PlatformService | None | object = _UNSET,
    runtime: PlatformRuntime | None | object = _UNSET,
) -> None:
    """Replace singletons for testing. Pass None to clear a singleton."""
    global _service, _runtime
    if service is not _UNSET:
        _service = service  # type: ignore[assignment]
    if runtime is not _UNSET:
        _runtime = runtime  # type: ignore[assignment]


def reset() -> None:
    """Clear singletons so next get_*() re-detects platform."""
    global _service, _runtime
    _service = None
    _runtime = None
