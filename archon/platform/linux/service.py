"""Linux SystemdService implementation."""
from __future__ import annotations

import getpass
import logging
import os
import re
import shutil
from pathlib import Path

from archon.platform import get_runtime
from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

log = logging.getLogger("archon")

_UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "archon.service"
_SERVICE_NAME = "archon"
_TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "scripts" / "archon.service"


def _read_template() -> str:
    return _TEMPLATE_PATH.read_text()


class SystemdService(PlatformService):
    """Manages Archon as a Linux systemd user service."""

    @property
    def service_name(self) -> str:
        return "systemd"

    # ── T20 ──

    def is_installed(self) -> bool:
        return _UNIT_PATH.exists()

    # ── T21 ──

    def start(self, dry_run: bool = False) -> int:
        try:
            result = self._run(
                ["systemctl", "--user", "start", _SERVICE_NAME], dry_run=dry_run
            )
            if result.returncode != 0:
                log.warning("systemctl start failed (rc=%d): %s", result.returncode, result.stderr)
                return 1
            return 0
        except Exception:
            log.exception("Failed to start systemd service")
            return 1

    # ── T22 ──

    def stop(self, dry_run: bool = False) -> int:
        try:
            result = self._run(
                ["systemctl", "--user", "stop", _SERVICE_NAME], dry_run=dry_run
            )
            if result.returncode != 0:
                log.warning("systemctl stop failed (rc=%d): %s", result.returncode, result.stderr)
                return 1
            return 0
        except Exception:
            log.exception("Failed to stop systemd service")
            return 1

    # ── T23 ──

    def status(self) -> ServiceInfo:
        try:
            is_active_result = self._run(
                ["systemctl", "--user", "is-active", _SERVICE_NAME]
            )
            running = is_active_result.stdout.strip() == "active"

            pid_result = self._run(
                ["systemctl", "--user", "show", _SERVICE_NAME, "--property=MainPID"]
            )
            pid = self._parse_pid(pid_result.stdout)

            uptime: str | None = None
            if pid is not None:
                uptime = get_runtime().process_uptime(pid)

            return ServiceInfo(running=running, service_name=_SERVICE_NAME, pid=pid, uptime=uptime)
        except Exception:
            log.exception("Failed to query systemd service status")
            return ServiceInfo(running=False, service_name=_SERVICE_NAME)

    @staticmethod
    def _parse_pid(stdout: str) -> int | None:
        match = re.search(r"MainPID=(\d+)", stdout)
        if not match:
            return None
        pid = int(match.group(1))
        return pid if pid != 0 else None

    # ── T24 ──

    def remediation_hint(self) -> str:
        return (
            "Try: systemctl --user status archon\n"
            "Logs: journalctl --user -u archon -f"
        )

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        self.stop(dry_run=dry_run)  # ignore errors
        return 0

    # ── T25 ──

    def restart(self, dry_run: bool = False) -> int:
        try:
            result = self._run(
                ["systemctl", "--user", "restart", _SERVICE_NAME], dry_run=dry_run
            )
            if result.returncode != 0:
                log.warning("systemctl restart failed (rc=%d): %s", result.returncode, result.stderr)
                return 1
            return 0
        except Exception:
            log.exception("Failed to restart systemd service")
            return 1

    # ── T26 ──

    def register(self, dry_run: bool = False) -> int:
        try:
            template = _read_template()
            uv_path = shutil.which("uv") or "uv"
            archon_dir = str(Path(__file__).resolve().parents[3])
            log_file = str(
                Path.home() / ".archon" / "logs" / "archon.log"
            )
            content = (
                template.replace("__ARCHON_DIR__", archon_dir)
                .replace("__UV_PATH__", uv_path)
                .replace("__LOG_FILE__", log_file)
            )

            env_path = os.environ.get("PATH", "/usr/bin:/bin")
            env_line = f'Environment="PATH={env_path}"'
            content = content.replace("[Service]\n", f"[Service]\n{env_line}\n", 1)

            if not dry_run:
                _UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
                _UNIT_PATH.write_text(content)

            # daemon-reload
            result = self._run(
                ["systemctl", "--user", "daemon-reload"], dry_run=dry_run
            )
            if not dry_run and result.returncode != 0:
                return 1

            # enable
            result = self._run(
                ["systemctl", "--user", "enable", _SERVICE_NAME], dry_run=dry_run
            )
            if not dry_run and result.returncode != 0:
                log.warning("systemctl enable failed (rc=%d): %s", result.returncode, result.stderr)
                self._cleanup_unit_file()
                self._run(["systemctl", "--user", "daemon-reload"], dry_run=dry_run)
                return 1

            # start
            result = self._run(
                ["systemctl", "--user", "start", _SERVICE_NAME], dry_run=dry_run
            )
            if not dry_run and result.returncode != 0:
                log.warning(
                    "systemctl start failed (rc=%d): %s — service is enabled and will start on next boot",
                    result.returncode,
                    result.stderr,
                )

            # enable-linger
            user = os.environ.get("USER") or getpass.getuser()
            self._run(
                ["loginctl", "enable-linger", user], dry_run=dry_run
            )

            return 0
        except Exception:
            log.exception("Failed to register systemd service")
            return 1

    def unregister(self, dry_run: bool = False) -> int:
        try:
            self._run(
                ["systemctl", "--user", "stop", _SERVICE_NAME], dry_run=dry_run
            )
            self._run(
                ["systemctl", "--user", "disable", _SERVICE_NAME], dry_run=dry_run
            )
            if not dry_run:
                self._cleanup_unit_file()
            self._run(
                ["systemctl", "--user", "daemon-reload"], dry_run=dry_run
            )
            return 0
        except Exception:
            log.exception("Failed to unregister systemd service")
            return 1

    @staticmethod
    def _cleanup_unit_file() -> None:
        try:
            _UNIT_PATH.unlink(missing_ok=True)
        except OSError:
            pass
