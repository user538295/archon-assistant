"""Service lifecycle management: start, stop, restart."""
from __future__ import annotations

from typing import Callable

from archon.platform import get_service


def _run_action(action: str) -> int:
    """Execute a service lifecycle *action* with error handling.

    *action* is one of ``"start"``, ``"stop"``, ``"restart"``.
    Returns 0 on success, 1 on failure.
    """
    try:
        service = get_service()
    except Exception as exc:
        print(f"Error: {exc}")
        return 1

    method: Callable[[], int] = getattr(service, action)
    try:
        rc = method()
    except Exception as exc:
        print(f"Error: {exc}")
        if hasattr(service, "remediation_hint"):
            print(service.remediation_hint())
        return 1

    past = {"start": "started", "stop": "stopped", "restart": "restarted"}[action]
    if rc == 0:
        print(f"Archon {past}")
    else:
        print(f"Failed to {action} Archon")
    return rc


def run_start() -> int:
    """Start the Archon service. Returns 0 on success, 1 on failure."""
    return _run_action("start")


def run_stop() -> int:
    """Stop the Archon service. Returns 0 on success, 1 on failure."""
    return _run_action("stop")


def run_restart() -> int:
    """Restart the Archon service. Returns 0 on success, 1 on failure."""
    return _run_action("restart")
