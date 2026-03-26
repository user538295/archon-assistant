"""Platform type definitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GpuType = Literal["cuda", "apple_silicon", "none"]


@dataclass(frozen=True)
class ServiceInfo:
    """Status snapshot of the platform service."""

    running: bool
    service_name: str
    pid: int | None = None
    uptime: str | None = None
