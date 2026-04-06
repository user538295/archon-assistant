"""Linux SystemdSearchService — manages archon.search.server as a systemd user service."""
from __future__ import annotations

import getpass
import logging
import os
import re
import sys
from pathlib import Path

from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

log = logging.getLogger("archon")

_SERVICE_NAME = "archon-search"
_LEGACY_SERVICE_NAME = "archon-rag"
_LEGACY_UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "archon-rag.service"

_UNIT_TEMPLATE = """\
[Unit]
Description=Archon Search Server (archon-search)
After=network.target

[Service]
ExecStart={python} -m archon.search.server
WorkingDirectory={cwd}
Environment=ARCHON_CONFIG={config_path}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
"""


class SystemdSearchService(PlatformService):
    """Manages the search server as a Linux systemd user service."""

    @property
    def service_name(self) -> str:
        return "systemd-search"

    @property
    def _unit_path(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user" / "archon-search.service"

    def is_installed(self) -> bool:
        return self._unit_path.exists()

    def register(self, dry_run: bool = False) -> int:
        cwd = str(Path(__file__).resolve().parents[3])
        config_path = str(Path.home() / ".archon" / "config.toml")

        content = _UNIT_TEMPLATE.format(
            python=sys.executable,
            cwd=cwd,
            config_path=config_path,
        )

        if not dry_run:
            try:
                self._unit_path.parent.mkdir(parents=True, exist_ok=True)
                self._unit_path.write_text(content)
            except PermissionError:
                log.error("Permission denied writing %s", self._unit_path)
                return 1

        try:
            # daemon-reload so systemd sees the new unit
            result = self._run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
            if not dry_run and result.returncode != 0:
                return 1

            # enable so the service starts on boot
            result = self._run(
                ["systemctl", "--user", "enable", _SERVICE_NAME], dry_run=dry_run
            )
            if not dry_run and result.returncode != 0:
                log.warning("systemctl enable failed (rc=%d): %s", result.returncode, result.stderr)
                if self._unit_path.exists():
                    self._unit_path.unlink(missing_ok=True)
                self._run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
                return 1

            # enable-linger so the service survives session logout
            user = os.environ.get("USER") or getpass.getuser()
            self._run(["loginctl", "enable-linger", user], dry_run=dry_run)

        except Exception:
            log.exception("Failed to complete Search service registration")
            return 1

        return 0

    def unregister(self, dry_run: bool = False) -> int:
        try:
            self._run(["systemctl", "--user", "stop", _SERVICE_NAME], dry_run=dry_run)
            self._run(["systemctl", "--user", "disable", _SERVICE_NAME], dry_run=dry_run)
            if not dry_run and self._unit_path.exists():
                self._unit_path.unlink()
            self._run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
        except Exception:
            log.exception("Failed to unregister RAG systemd service")
            return 1
        return 0

    def start(self, dry_run: bool = False) -> int:
        try:
            result = self._run(
                ["systemctl", "--user", "start", _SERVICE_NAME], dry_run=dry_run
            )
            return 0 if result.returncode == 0 else 1
        except Exception:
            log.exception("Failed to start RAG systemd service")
            return 1

    def stop(self, dry_run: bool = False) -> int:
        try:
            result = self._run(
                ["systemctl", "--user", "stop", _SERVICE_NAME], dry_run=dry_run
            )
            return 0 if result.returncode == 0 else 1
        except Exception:
            log.exception("Failed to stop RAG systemd service")
            return 1

    def restart(self, dry_run: bool = False) -> int:
        try:
            result = self._run(
                ["systemctl", "--user", "restart", _SERVICE_NAME], dry_run=dry_run
            )
            return 0 if result.returncode == 0 else 1
        except Exception:
            log.exception("Failed to restart RAG systemd service")
            return 1

    def status(self) -> ServiceInfo:
        try:
            is_active = self._run(["systemctl", "--user", "is-active", _SERVICE_NAME])
            running = is_active.stdout.strip() == "active"

            pid_result = self._run(
                ["systemctl", "--user", "show", _SERVICE_NAME, "--property=MainPID"]
            )
            pid = self._parse_pid(pid_result.stdout)

            return ServiceInfo(running=running, service_name=_SERVICE_NAME, pid=pid)
        except Exception:
            log.exception("Failed to query RAG systemd service status")
            return ServiceInfo(running=False, service_name=_SERVICE_NAME)

    @staticmethod
    def _parse_pid(stdout: str) -> int | None:
        match = re.search(r"MainPID=(\d+)", stdout)
        if not match:
            return None
        pid = int(match.group(1))
        return pid if pid != 0 else None

    def remediation_hint(self) -> str:
        return "Run `archon search install` to register the search service"

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        """Stop and remove the legacy archon-rag service to free the port."""
        if dry_run:
            return 0
        try:
            result = self._run(["systemctl", "--user", "is-active", _LEGACY_SERVICE_NAME])
            if result.returncode != 0:
                return 0  # legacy service not active
            self._run(["systemctl", "--user", "stop", _LEGACY_SERVICE_NAME])
            self._run(["systemctl", "--user", "disable", _LEGACY_SERVICE_NAME])
            if _LEGACY_UNIT_PATH.exists():
                _LEGACY_UNIT_PATH.unlink(missing_ok=True)
            self._run(["systemctl", "--user", "daemon-reload"])
            log.info("Stopped and removed legacy %s service", _LEGACY_SERVICE_NAME)
        except Exception as exc:
            log.warning("pre_activate_cleanup: failed to remove legacy service: %s", exc)
        return 0
