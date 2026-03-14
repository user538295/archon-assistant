"""macOS LaunchdService implementation."""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from archon.platform import get_runtime
from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

log = logging.getLogger("archon")

_LABEL = "com.archon.assistant"
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


class LaunchdService(PlatformService):
    """Manages Archon as a macOS launchd service."""

    _PLIST_PATH: Path = Path.home() / "Library" / "LaunchAgents" / "com.archon.assistant.plist"
    _TEMPLATE_PATH: Path = _SCRIPTS_DIR / "com.archon.assistant.plist"

    @property
    def service_name(self) -> str:
        return "launchd"

    # ── T8 ────────────────────────────────────────────────────────────

    def is_installed(self) -> bool:
        return self._PLIST_PATH.exists()

    # ── T9 ────────────────────────────────────────────────────────────

    def _is_loaded(self) -> bool:
        try:
            result = self._run(["launchctl", "list", _LABEL])
            return result.returncode == 0
        except FileNotFoundError:
            return False

    # ── T10 ───────────────────────────────────────────────────────────

    def start(self, dry_run: bool = False) -> int:
        if not self.is_installed():
            log.error("Plist not installed at %s", self._PLIST_PATH)
            return 1
        if self._is_loaded():
            return 0
        try:
            result = self._run(
                ["launchctl", "load", str(self._PLIST_PATH)], dry_run=dry_run
            )
            return 0 if result.returncode == 0 else 1
        except FileNotFoundError:
            log.error("launchctl binary not found")
            return 1

    # ── T11 ───────────────────────────────────────────────────────────

    def stop(self, dry_run: bool = False) -> int:
        if not self._is_loaded():
            return 0
        try:
            if self._PLIST_PATH.exists():
                result = self._run(
                    ["launchctl", "unload", str(self._PLIST_PATH)], dry_run=dry_run
                )
            else:
                result = self._run(
                    ["launchctl", "bootout", f"gui/{_label_uid()}", _LABEL],
                    dry_run=dry_run,
                )
            return 0 if result.returncode == 0 else 1
        except FileNotFoundError:
            log.error("launchctl binary not found")
            return 1

    # ── T12 ───────────────────────────────────────────────────────────

    def status(self) -> ServiceInfo:
        try:
            result = self._run(["launchctl", "list", _LABEL])
        except FileNotFoundError:
            return ServiceInfo(running=False, service_name=_LABEL)

        if result.returncode != 0:
            return ServiceInfo(running=False, service_name=_LABEL)

        pid_match = re.search(r'"PID"\s*=\s*(\d+)', result.stdout)
        if not pid_match:
            return ServiceInfo(running=False, service_name=_LABEL)

        try:
            pid = int(pid_match.group(1))
        except ValueError:
            return ServiceInfo(running=False, service_name=_LABEL)

        if pid == 0:
            return ServiceInfo(running=False, service_name=_LABEL, pid=0)

        try:
            uptime = get_runtime().process_uptime(pid)
        except Exception:
            uptime = None
        return ServiceInfo(running=True, service_name=_LABEL, pid=pid, uptime=uptime)

    # ── T13 ───────────────────────────────────────────────────────────

    def remediation_hint(self) -> str:
        return (
            f"Try: launchctl unload {self._PLIST_PATH} && "
            f"launchctl load {self._PLIST_PATH}"
        )

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        if self._is_loaded():
            try:
                result = self._run(
                    ["launchctl", "unload", str(self._PLIST_PATH)], dry_run=dry_run
                )
                if result.returncode != 0:
                    log.warning("launchctl unload failed (rc=%d): %s", result.returncode, result.stderr)
                    return 1
            except FileNotFoundError:
                log.error("launchctl binary not found")
                return 1
        return 0

    # ── T14 ───────────────────────────────────────────────────────────

    def restart(self, dry_run: bool = False) -> int:
        stop_rc = self.stop(dry_run=dry_run)
        start_rc = self.start(dry_run=dry_run)
        return 0 if stop_rc == 0 and start_rc == 0 else 1

    # ── T15 ───────────────────────────────────────────────────────────

    def register(self, dry_run: bool = False) -> int:
        try:
            template = self._TEMPLATE_PATH.read_text()
        except FileNotFoundError:
            log.error("Plist template not found at %s", self._TEMPLATE_PATH)
            return 1

        archon_dir = str(Path(__file__).resolve().parents[3])
        uv_path = shutil.which("uv") or "uv"
        log_file = str(Path.home() / ".archon" / "logs" / "archon.log")

        content = (
            template.replace("__ARCHON_DIR__", archon_dir)
            .replace("__UV_PATH__", uv_path)
            .replace("__LOG_FILE__", log_file)
        )

        if not dry_run:
            try:
                self._PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
                with open(self._PLIST_PATH, "w") as f:
                    f.write(content)
            except PermissionError:
                log.error("Permission denied writing %s", self._PLIST_PATH)
                return 1

        try:
            result = self._run(
                ["launchctl", "load", str(self._PLIST_PATH)], dry_run=dry_run
            )
        except FileNotFoundError:
            log.error("launchctl binary not found")
            if not dry_run and self._PLIST_PATH.exists():
                self._PLIST_PATH.unlink()
            return 1
        if result.returncode != 0:
            log.error("launchctl load failed: %s", result.stderr)
            if not dry_run and self._PLIST_PATH.exists():
                self._PLIST_PATH.unlink()
            return 1
        return 0

    def unregister(self, dry_run: bool = False) -> int:
        if self._is_loaded():
            try:
                result = self._run(
                    ["launchctl", "unload", str(self._PLIST_PATH)], dry_run=dry_run
                )
                if not dry_run and result.returncode != 0:
                    log.warning("launchctl unload failed (rc=%d): %s", result.returncode, result.stderr)
                    return 1
            except FileNotFoundError:
                log.error("launchctl binary not found")
                return 1

        if not dry_run and self._PLIST_PATH.exists():
            self._PLIST_PATH.unlink()
        return 0


def _label_uid() -> int:
    """Get current user's UID for bootout domain target."""
    import os
    return os.getuid()
