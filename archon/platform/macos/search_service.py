"""macOS LaunchdSearchService — manages archon.search.server as a launchd daemon."""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from archon.platform.service import PlatformService
from archon.platform.types import ServiceInfo

log = logging.getLogger("archon")

_LABEL = "com.archon.search"
_LEGACY_LABEL = "com.archon.rag"
_LEGACY_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.archon.rag.plist"

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/sbin/taskpolicy</string>
        <string>-b</string>
        <string>{python}</string>
        <string>-m</string>
        <string>archon.search.server</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{cwd}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ARCHON_CONFIG</key>
        <string>{config_path}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
    <key>KeepAlive</key>
    <true/>
    <key>RunAtLoad</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>60</integer>
</dict>
</plist>
"""


class LaunchdSearchService(PlatformService):
    """Manages the search server as a macOS launchd user agent."""

    @property
    def service_name(self) -> str:
        return "launchd-search"

    @property
    def _plist_path(self) -> Path:
        return Path.home() / "Library" / "LaunchAgents" / "com.archon.search.plist"

    def is_installed(self) -> bool:
        return self._plist_path.exists()

    def register(self, dry_run: bool = False) -> int:
        cwd = str(Path(__file__).resolve().parents[3])
        config_path = str(Path.home() / ".archon" / "config.toml")
        log_path = str(Path.home() / ".archon" / "search" / "archon-search.log")

        content = _PLIST_TEMPLATE.format(
            label=_LABEL,
            python=sys.executable,
            cwd=cwd,
            config_path=config_path,
            log_path=log_path,
        )

        if not dry_run:
            try:
                self._plist_path.parent.mkdir(parents=True, exist_ok=True)
                Path(log_path).parent.mkdir(parents=True, exist_ok=True)
                self._plist_path.write_text(content)
            except PermissionError:
                log.error("Permission denied writing %s", self._plist_path)
                return 1
        return 0

    def _is_loaded(self) -> bool:
        try:
            result = self._run(["launchctl", "list", _LABEL])
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def unregister(self, dry_run: bool = False) -> int:
        if self._is_loaded():
            try:
                result = self._run(["launchctl", "unload", str(self._plist_path)], dry_run=dry_run)
                if not dry_run and result.returncode != 0:
                    log.warning("launchctl unload failed (rc=%d): %s", result.returncode, result.stderr)
                    return 1
            except FileNotFoundError:
                log.error("launchctl binary not found")
                return 1
        if not dry_run and self._plist_path.exists():
            self._plist_path.unlink()
        return 0

    def start(self, dry_run: bool = False) -> int:
        if dry_run:
            return 0
        if not self.is_installed():
            log.error("Plist not installed at %s", self._plist_path)
            return 1
        if self._is_loaded():
            return 0
        try:
            r1 = self._run(["launchctl", "load", str(self._plist_path)])
            if r1.returncode != 0:
                return 1
            r2 = self._run(["launchctl", "start", _LABEL])
            return 0 if r2.returncode == 0 else 1
        except FileNotFoundError:
            log.error("launchctl binary not found")
            return 1

    def stop(self, dry_run: bool = False) -> int:
        if dry_run:
            return 0
        if not self._is_loaded():
            return 0
        try:
            result = self._run(["launchctl", "unload", str(self._plist_path)])
            return 0 if result.returncode == 0 else 1
        except FileNotFoundError:
            log.error("launchctl binary not found")
            return 1

    def restart(self, dry_run: bool = False) -> int:
        rc_stop = self.stop(dry_run=dry_run)
        rc_start = self.start(dry_run=dry_run)
        return 0 if rc_stop == 0 and rc_start == 0 else 1

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

        return ServiceInfo(running=True, service_name=_LABEL, pid=pid)

    def remediation_hint(self) -> str:
        return "Run `archon search install` to register the search service"

    def pre_activate_cleanup(self, dry_run: bool = False) -> int:
        """Stop and remove the legacy com.archon.rag service to free the port."""
        if dry_run:
            return 0
        try:
            result = self._run(["launchctl", "list", _LEGACY_LABEL])
            if result.returncode != 0:
                return 0  # legacy service not loaded
            if _LEGACY_PLIST_PATH.exists():
                self._run(["launchctl", "unload", str(_LEGACY_PLIST_PATH)])
                _LEGACY_PLIST_PATH.unlink(missing_ok=True)
                log.info("Stopped and removed legacy %s service", _LEGACY_LABEL)
        except FileNotFoundError:
            return 0  # launchctl not found
        except Exception as exc:
            log.warning("pre_activate_cleanup: failed to remove legacy service: %s", exc)
        return 0
