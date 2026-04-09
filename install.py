# /// script
# requires-python = ">=3.12"
# dependencies = ["tomlkit>=0.12"]
# ///
"""Archon Assistant — Python installer (PEP 723 inline-script).

Usage (one command, no pre-clone needed):
    uv run https://raw.githubusercontent.com/user538295/archon-assistant/v<TAG>/install.py

Security note: always pin the URL to a release tag or commit SHA, never main.
"""

from __future__ import annotations

import argparse
import getpass
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import tomlkit
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


__version__ = "26.4.926"

REPO_URL = "https://github.com/user538295/archon-assistant.git"

# Files and directories to include in the sparse checkout.
# Everything else (Documentation/, tests/, .claude/, contributing.md) is excluded.
_SPARSE_PATHS = [
    "archon",
    "scripts",
    "schedules",
    "skills",
    "examples",
    "workspace",
    "main.py",
    "pyproject.toml",
    "uv.lock",
    "README.md",
]

_PLIST_NAME = "com.archon.assistant.plist"
_SERVICE_LABEL = "com.archon.assistant"
_DEFAULT_HEALTH_PORT = 18182
_MIN_UV_VERSION = (0, 4)
_MIN_PYTHON = (3, 12)

# ── ANSI colours (stdlib only) ─────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_RED = "\033[0;31m"
_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_CYAN = "\033[0;36m"


class Console:
    """Thin output wrapper; set quiet=True to suppress all non-error output."""

    def __init__(self, quiet: bool = False) -> None:
        self._quiet = quiet

    def info(self, msg: str) -> None:
        if not self._quiet:
            print(f"  {_CYAN}▸{_RESET} {msg}")

    def success(self, msg: str) -> None:
        if not self._quiet:
            print(f"  {_GREEN}✔{_RESET} {msg}")

    def warn(self, msg: str) -> None:
        if not self._quiet:
            print(f"  {_YELLOW}⚠{_RESET}  {msg}")

    def error(self, msg: str) -> None:
        print(f"\n  {_RED}✖ Error:{_RESET} {msg}\n", file=sys.stderr)

    def ask(self, prompt: str) -> str:
        if self._quiet:
            return ""
        return input(f"  {_BOLD}?{_RESET}  {prompt} ")


@dataclass(frozen=True)
class InstallerPaths:
    app: Path
    candidate: Path
    previous: Path


def _paths(archon_home: Path) -> InstallerPaths:
    app = archon_home / "app"
    return InstallerPaths(
        app=app,
        candidate=archon_home / "app.candidate",
        previous=archon_home / "app.previous",
    )


# ── Version parsing ────────────────────────────────────────────────


def _parse_version(text: str) -> tuple[int, ...]:
    """Extract the first version tuple from a string, e.g. 'uv 0.5.0' → (0, 5, 0)."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not m:
        return (0,)
    return tuple(int(x) for x in m.groups() if x is not None)


def _app_version(app_dir: Path) -> str:
    """Return the Archon version string from a git repo directory.

    Tries an exact tag match first (tagged releases); falls back to
    YY.M.<commit-count> (local/dev builds).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(app_dir), "describe", "--tags", "--exact-match", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip().lstrip("v")
    except FileNotFoundError:
        pass
    try:
        result = subprocess.run(
            ["git", "-C", str(app_dir), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        from datetime import datetime

        now = datetime.now()
        return f"{now.year % 100}.{now.month}.{result.stdout.strip()}"
    except Exception:
        return "unknown"


# ── Pure functions ─────────────────────────────────────────────────


def check_prerequisites(console: Console | None = None) -> None:
    """Verify git, uv (≥0.4), Python 3.12+, and claude CLI are installed.

    Raises FileNotFoundError if git or claude is not on PATH.
    Raises SystemExit(1) if uv is missing or any version requirement is not met.
    """
    con = console or Console()
    con.info("Checking prerequisites...")

    # git — propagates FileNotFoundError if missing
    result = subprocess.run(["git", "--version"], capture_output=True, text=True)
    con.success(f"git: {result.stdout.strip()}")

    # uv — caught here to provide a clear error message
    try:
        result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
    except FileNotFoundError:
        con.error("uv not found. Install from https://docs.astral.sh/uv/ and retry.")
        sys.exit(1)
    uv_ver = _parse_version(result.stdout)
    if uv_ver < _MIN_UV_VERSION:
        con.error(f"uv ≥ 0.4 required (found {result.stdout.strip()})")
        sys.exit(1)
    con.success(f"uv: {result.stdout.strip()}")

    # Python via uv
    py = subprocess.run(
        ["uv", "run", "python", "--version"], capture_output=True, text=True
    )
    py_text = py.stdout or py.stderr
    py_ver = _parse_version(py_text)
    if py_ver < _MIN_PYTHON:
        con.error(
            f"Python 3.12+ required (found {py_text.strip()}). Run: uv python install 3.12"
        )
        sys.exit(1)
    con.success(f"Python: {py_text.strip()}")

    # claude CLI — propagates FileNotFoundError if missing
    claude = subprocess.run(["claude", "--version"], capture_output=True, text=True)
    con.success(f"claude: {claude.stdout.strip()}")


def fetch_or_update_app(
    tag: str,
    app_dir: Path,
    repo_url: str = REPO_URL,
    dry_run: bool = False,
    console: Console | None = None,
) -> None:
    """Clone (fresh install) or fetch+checkout (update) the Archon app.

    Fresh install clones to app.partial first; renames to app_dir on success.
    On failure, app.partial is left intact for debugging and app_dir is absent.
    """
    con = console or Console()
    partial = app_dir.parent / (app_dir.name + ".partial")

    if (app_dir / ".git").exists():
        # Update path: fetch tags then checkout the pinned tag
        con.info(f"Updating app to v{tag}...")
        if dry_run:
            con.info(f"[dry-run] Would git fetch --tags in {app_dir}")
            con.info(f"[dry-run] Would git checkout v{tag}")
            return
        subprocess.run(
            ["git", "-C", str(app_dir), "fetch", "--tags", "--quiet"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(app_dir), "checkout", f"v{tag}", "--quiet"],
            check=True,
        )
        con.success(f"App updated to v{tag}")
    else:
        # Fresh install: clone to partial, rename on success
        con.info(f"Cloning app v{tag} to {app_dir}...")
        if dry_run:
            con.info(
                f"[dry-run] Would sparse-clone --depth 1 --branch v{tag} {repo_url} {partial}"
            )
            return
        app_dir.parent.mkdir(parents=True, exist_ok=True)
        if partial.exists():
            shutil.rmtree(partial)
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--no-checkout",
                "--branch",
                f"v{tag}",
                repo_url,
                str(partial),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(partial), "sparse-checkout", "set"] + _SPARSE_PATHS,
            check=True,
        )
        subprocess.run(["git", "-C", str(partial), "checkout"], check=True)
        partial.rename(app_dir)
        con.success("App cloned")


def _run_with_retry(
    fn: Callable[[], None],
    stage_name: str,
    console: Console,
    attempts: int = 3,
    initial_delay: float = 1.0,
    multiplier: float = 2.0,
    max_delay: float = 8.0,
) -> None:
    delay = initial_delay
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            fn()
            return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= attempts:
                break
            console.warn(
                f"{stage_name} failed (attempt {attempt}/{attempts}): {exc}. "
                f"Retrying in {delay:.0f}s..."
            )
            time.sleep(delay)
            delay = min(delay * multiplier, max_delay)
    if last_error is None:
        raise RuntimeError(f"{stage_name} failed without error details")
    raise last_error


def _prepare_candidate(
    paths: InstallerPaths,
    dry_run: bool,
    console: Console,
    *,
    tag: str | None = None,
    local_src: Path | None = None,
) -> None:
    if tag is None and local_src is None:
        raise ValueError("Either tag or local_src must be provided")
    if dry_run:
        console.info(f"[dry-run] Would prepare candidate in {paths.candidate}")
        return
    if paths.candidate.exists():
        shutil.rmtree(paths.candidate, ignore_errors=True)
    paths.candidate.parent.mkdir(parents=True, exist_ok=True)

    def _clone() -> None:
        if local_src is not None:
            subprocess.run(
                ["git", "clone", "--local", str(local_src), str(paths.candidate)],
                check=True,
            )
        else:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--no-checkout",
                    "--branch",
                    f"v{tag}",
                    REPO_URL,
                    str(paths.candidate),
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(paths.candidate), "sparse-checkout", "set"]
                + _SPARSE_PATHS,
                check=True,
            )
            subprocess.run(["git", "-C", str(paths.candidate), "checkout"], check=True)

    ref_label = f"local ({local_src})" if local_src is not None else f"v{tag}"
    console.info(f"Preparing candidate {ref_label}...")
    _run_with_retry(_clone, "Candidate clone", console)
    if not paths.candidate.exists():
        if paths.app.exists():
            shutil.copytree(paths.app, paths.candidate)
        else:
            raise FileNotFoundError(
                f"Candidate directory was not created by clone: {paths.candidate}"
            )
    console.success("Candidate prepared")


def _activate_candidate(paths: InstallerPaths, console: Console, dry_run: bool) -> None:
    if dry_run:
        console.info(f"[dry-run] Would activate {paths.candidate} -> {paths.app}")
        return
    if not paths.candidate.exists():
        raise FileNotFoundError(f"Candidate directory missing: {paths.candidate}")
    if paths.previous.exists():
        shutil.rmtree(paths.previous, ignore_errors=True)
    if paths.app.exists():
        paths.app.rename(paths.previous)
    paths.candidate.rename(paths.app)


def _cleanup_post_success(
    paths: InstallerPaths, console: Console, dry_run: bool
) -> None:
    if dry_run:
        return
    if paths.previous.exists():
        shutil.rmtree(paths.previous, ignore_errors=True)
    if paths.candidate.exists():
        shutil.rmtree(paths.candidate, ignore_errors=True)
    console.success("Transaction finalized")


def _rollback_activation(
    paths: InstallerPaths, console: Console, dry_run: bool
) -> bool:
    if dry_run:
        console.info("[dry-run] Would rollback candidate activation")
        return True
    try:
        if paths.app.exists():
            shutil.rmtree(paths.app, ignore_errors=True)
        if not paths.previous.exists():
            if platform.system() == "Linux":
                hint = "Restore manually from backup and reload systemd."
            elif platform.system() == "Darwin":
                hint = "Restore manually from backup and reload launchd."
            else:
                hint = "Restore manually from backup."
            console.error(f"Rollback failed: previous app version is missing. {hint}")
            return False
        paths.previous.rename(paths.app)
        if paths.candidate.exists():
            shutil.rmtree(paths.candidate, ignore_errors=True)
        console.warn("Rollback completed; previous version restored.")
        return True
    except Exception as exc:  # noqa: BLE001
        console.error(
            "Rollback failed. Manual recovery required.\n"
            f"Filesystem state error: {exc}\n"
            f"Inspect logs: tail -f {paths.app.parent / 'logs' / 'archon.log'}"
        )
        return False


def _remediation_message(archon_home: Path) -> str:
    """Return a platform-appropriate manual remediation message."""
    if platform.system() == "Linux":
        return (
            "Manual remediation: restore ~/.archon/app from ~/.archon/app.previous "
            "and run: systemctl --user restart archon"
        )
    if platform.system() == "Darwin":
        return (
            "Manual remediation: restore ~/.archon/app from ~/.archon/app.previous "
            "and run: launchctl unload/load ~/Library/LaunchAgents/com.archon.assistant.plist"
        )
    return "Manual remediation: restore ~/.archon/app from ~/.archon/app.previous and restart the service."


def _install_cli_symlink(archon_home: Path, dry_run: bool, console: Console) -> None:
    """Symlink ~/.local/bin/archon -> ~/.archon/app/.venv/bin/archon for PATH access."""
    src = archon_home / "app" / ".venv" / "bin" / "archon"
    dest_dir = Path.home() / ".local" / "bin"
    dest = dest_dir / "archon"
    if dry_run:
        console.info(f"[dry-run] Would symlink {dest} -> {src}")
        return
    if not src.exists():
        console.warn(f"CLI entry point not found: {src} (skipping symlink)")
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    dest.symlink_to(src)
    console.success(f"CLI installed: {dest}")


def _get_archon_bin(paths: InstallerPaths) -> Path:
    """Return the platform-appropriate archon CLI entry point path."""
    if sys.platform == "win32":
        return paths.app / ".venv" / "Scripts" / "archon.exe"
    return paths.app / ".venv" / "bin" / "archon"


def _search_already_enabled(archon_home: Path) -> bool:
    """Return True if search.enabled is set to true in the existing config."""
    config_path = archon_home / "config.toml"
    if not config_path.exists():
        return False
    try:
        cfg = tomllib.loads(config_path.read_text())
        return bool(cfg.get("search", {}).get("enabled", False))
    except (tomllib.TOMLDecodeError, OSError, ValueError):
        return False


def _voice_already_enabled(archon_home: Path) -> bool:
    """Return True if voice.enabled is set to true in the existing config."""
    config_path = archon_home / "config.toml"
    if not config_path.exists():
        return False
    try:
        cfg = tomllib.loads(config_path.read_text())
        return bool(cfg.get("voice", {}).get("enabled", False))
    except (tomllib.TOMLDecodeError, OSError, ValueError):
        return False


def _offer_search_setup(
    paths: InstallerPaths,
    console: Console,
    non_interactive: bool,
    reinstall: bool = False,
    dry_run: bool = False,
) -> None:
    """Interactively offer to set up RAG after a successful install.

    When reinstall=True (update with search already enabled), skips the prompt but
    still reinstalls search dependencies into the new venv.
    When non_interactive=True and reinstall=False (fresh non-interactive install), skips entirely.
    When dry_run=True, prints what would be done without executing.
    """
    if non_interactive and not reinstall:
        return

    if not reinstall:
        if dry_run:
            console.info("[dry-run] Would offer to enable semantic search (RAG)")
            return
        try:
            answer = console.ask("Enable semantic search (RAG)? [y/N]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if answer != "y":
            return

    if dry_run:
        console.info("[dry-run] Would run: archon search install --non-interactive")
        return

    archon_bin = _get_archon_bin(paths)
    if not archon_bin.exists():
        console.warn("archon binary not found — run 'archon search install' manually.")
        return

    try:
        result = subprocess.run([str(archon_bin), "search", "install", "--non-interactive"], check=False)
        if result.returncode != 0:
            console.warn("Search installation failed. Run 'archon search install' to retry.")
            return

        if not reinstall:
            rc_cfg = subprocess.run([str(archon_bin), "config", "set", "search.enabled", "true"], check=False).returncode
            if rc_cfg != 0:
                console.warn("Failed to enable search in config. Run: archon config set search.enabled true")
                return

            rc_restart = subprocess.run([str(archon_bin), "restart"], check=False).returncode
            if rc_restart != 0:
                console.warn("Failed to restart Archon. Run: archon restart")
                return
    except OSError as exc:
        console.warn(f"Search setup failed: {exc}. Run 'archon search install' to retry.")
        return

    if reinstall:
        console.success("Search service reinstalled.")
    else:
        console.success("Search enabled. Indexing in background — run 'archon search status' to track progress.")


def _offer_voice_setup(
    paths: InstallerPaths,
    console: Console,
    non_interactive: bool,
    reinstall: bool = False,
    dry_run: bool = False,
) -> None:
    """Interactively offer to set up voice features after a successful install.

    When reinstall=True (update with voice already enabled), skips the prompt but
    still reinstalls voice dependencies into the new venv.
    When non_interactive=True and reinstall=False (fresh non-interactive install), skips entirely.
    When dry_run=True, prints what would be done without executing.
    """
    if non_interactive and not reinstall:
        return

    if not reinstall:
        if dry_run:
            console.info("[dry-run] Would offer to enable voice features (STT/TTS)")
            return
        try:
            answer = console.ask("Enable voice features (STT/TTS)? [y/N]").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if answer != "y":
            return

    if dry_run:
        console.info("[dry-run] Would run: archon voice install --non-interactive")
        return

    archon_bin = _get_archon_bin(paths)
    if not archon_bin.exists():
        console.warn("archon binary not found — run 'archon voice install' manually.")
        return

    try:
        result = subprocess.run([str(archon_bin), "voice", "install", "--non-interactive"], check=False)
        if result.returncode != 0:
            console.warn("Voice installation failed. Run 'archon voice install' to retry.")
            return
        if not reinstall:
            rc_cfg = subprocess.run([str(archon_bin), "config", "set", "voice.enabled", "true"], check=False).returncode
            if rc_cfg != 0:
                console.warn("Failed to enable voice. Run: archon config set voice.enabled true")
                return
        console.success("Voice configured. Start or restart Archon: archon restart")
    except OSError as exc:
        console.warn(f"Voice setup failed: {exc}. Run 'archon voice install' to retry.")


def _verify_service_health(console: Console, dry_run: bool) -> bool:
    if dry_run:
        return True
    return verify_running(retries=10, delay=2.0, console=console)


def write_config(
    archon_home: Path,
    bot_token: str,
    user_ids: list[int],
    dry_run: bool = False,
    console: Console | None = None,
) -> None:
    """Write ~/.archon/.env and ~/.archon/config.toml.

    On fresh install: renders config.toml from examples/config.toml.example template.
    On update (config.toml exists): merges only allowed_user_ids and working_directory;
    all other user-set keys are preserved via tomlkit (comments included).
    The bot token is shell-quoted (shlex.quote) to handle special characters.

    Raises FileNotFoundError if the config.toml.example template is missing
    (checked unconditionally, even in dry-run mode).
    """
    con = console or Console()
    env_file = archon_home / ".env"
    config_file = archon_home / "config.toml"
    workspace_dir = archon_home / "workspace"

    # Validate template existence unconditionally (both dry-run and live)
    template_path = archon_home / "app.candidate" / "examples" / "config.toml.example"
    if not template_path.exists():
        raise FileNotFoundError(
            f"config.toml.example not found at {template_path} — cannot generate config"
        )

    # Write .env with shell-quoted token
    safe_token = shlex.quote(bot_token.strip())
    env_content = f"TELEGRAM_BOT_TOKEN={safe_token}\n"
    if not dry_run:
        env_file.write_text(env_content)
        env_file.chmod(0o600)
        con.success("~/.archon/.env written")
    else:
        con.info(f"[dry-run] Would write {env_file}")

    # Write or update config.toml
    if config_file.exists() and not dry_run:
        doc = tomlkit.parse(config_file.read_text(encoding="utf-8"))
        doc.setdefault("access", tomlkit.table())["allowed_user_ids"] = user_ids
        doc.setdefault("session", tomlkit.table())["working_directory"] = str(workspace_dir)
        if "models" not in doc:
            con.info(
                "Note: your config has no [models] section. Add one to enable the "
                "/models keyboard — see examples/config.toml.example for the format."
            )
        config_file.write_text(tomlkit.dumps(doc), encoding="utf-8")
        con.success("~/.archon/config.toml updated")
    elif not dry_run:
        template_text = template_path.read_text()
        config_file.write_text(_render_config_template(template_text, user_ids, workspace_dir))
        con.success("~/.archon/config.toml written")
    else:
        con.info(f"[dry-run] Would write {config_file}")


_SYSTEMD_SERVICE_NAME = "archon.service"


def register_service(
    app_dir: Path,
    archon_home: Path,
    dry_run: bool = False,
    console: Console | None = None,
) -> None:
    """Install and start the system service (macOS launchd or Linux systemd).

    Reads the service template from app_dir/scripts/, substitutes placeholders,
    writes to the OS-appropriate location, and enables/starts the service.
    """
    con = console or Console()
    logs_dir = archon_home / "logs"
    log_file = str(logs_dir / "archon.log")
    if not dry_run:
        logs_dir.mkdir(parents=True, exist_ok=True)

    if platform.system() == "Linux":
        service_dest = (
            Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_SERVICE_NAME
        )

        if dry_run:
            con.info(f"[dry-run] Would write {service_dest}")
            con.info("[dry-run] Would run systemctl --user daemon-reload")
            con.info("[dry-run] Would run systemctl enable --user archon")
            con.info("[dry-run] Would run systemctl start --user archon")
            return

        uv_path = shutil.which("uv") or "uv"
        template = (app_dir / "scripts" / _SYSTEMD_SERVICE_NAME).read_text()
        service_content = (
            template.replace("__ARCHON_DIR__", str(app_dir))
            .replace("__UV_PATH__", uv_path)
            .replace("__LOG_FILE__", log_file)
        )

        # Inject current PATH so the service has access to uv, node, etc.
        current_path = os.environ.get("PATH", "")
        if current_path:
            service_content = service_content.replace(
                "[Service]\n",
                f"[Service]\nEnvironment=PATH={current_path}\n",
            )

        service_dest.parent.mkdir(parents=True, exist_ok=True)
        service_dest.write_text(service_content)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--user", "archon"], check=True)
        subprocess.run(["systemctl", "start", "--user", "archon"], check=True)
        # Enable lingering so the user service survives logout
        try:
            user = os.environ.get("USER") or getpass.getuser()
            subprocess.run(["loginctl", "enable-linger", user], check=True)
        except (subprocess.CalledProcessError, OSError):
            con.warn("loginctl enable-linger failed — the service may stop on logout")
        con.success("systemd user service enabled and started")
    elif platform.system() == "Darwin":
        launch_agents = Path.home() / "Library" / "LaunchAgents"
        plist_dest = launch_agents / _PLIST_NAME

        if dry_run:
            con.info(f"[dry-run] Would write {plist_dest}")
            con.info(f"[dry-run] Would launchctl load {plist_dest}")
            return

        uv_path = shutil.which("uv") or "uv"
        template = (app_dir / "scripts" / _PLIST_NAME).read_text()
        plist_content = (
            template.replace("__ARCHON_DIR__", str(app_dir))
            .replace("__UV_PATH__", uv_path)
            .replace("__LOG_FILE__", log_file)
        )

        launch_agents.mkdir(parents=True, exist_ok=True)
        plist_dest.write_text(plist_content)
        # Unload if already loaded (idempotent: check=False)
        subprocess.run(
            ["launchctl", "unload", str(plist_dest)], check=False, capture_output=True
        )
        subprocess.run(["launchctl", "load", str(plist_dest)], check=True)
        con.success("launchd service loaded — auto-starts on login")
    else:
        raise RuntimeError(f"Unsupported platform: {platform.system()}")


def verify_running(
    host: str = "localhost",
    port: int = _DEFAULT_HEALTH_PORT,
    retries: int = 5,
    delay: float = 2.0,
    console: Console | None = None,
) -> bool:
    """Poll the Archon health endpoint; return True when the service responds."""
    con = console or Console()
    url = f"http://{host}:{port}/health"
    con.info("Waiting for Archon to start...")
    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(url, timeout=3)
            if resp.status == 200:
                return True
        except Exception:  # noqa: BLE001
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return False


# ── Config helpers ─────────────────────────────────────────────────


def _render_config_template(template: str, user_ids: list[int], workspace_dir: Path) -> str:
    """Substitute install-time values in the config.toml.example template."""
    if not user_ids:
        raise ValueError("user_ids must not be empty")
    ids_toml = ", ".join(str(uid) for uid in user_ids)
    # Escape backslashes for TOML string value (Windows paths).
    # Use lambda replacement to prevent re.sub from interpreting backslashes.
    safe_dir = str(workspace_dir).replace("\\", "\\\\")
    template = re.sub(
        r"^allowed_user_ids\s*=.*$",
        lambda _: f"allowed_user_ids = [{ids_toml}]",
        template,
        flags=re.MULTILINE,
    )
    template = re.sub(
        r"^working_directory\s*=.*$",
        lambda _: f'working_directory = "{safe_dir}"',
        template,
        flags=re.MULTILINE,
    )
    return template



def _collect_config_noninteractive(console: Console) -> tuple[str, list[int]]:
    """Read ARCHON_BOT_TOKEN and ARCHON_USER_IDS from environment.

    Returns (raw_token, user_ids). Exits non-zero on missing or malformed values.
    """
    token = os.environ.get("ARCHON_BOT_TOKEN", "").strip()
    if not token:
        console.error("ARCHON_BOT_TOKEN must be set for --non-interactive mode")
        sys.exit(1)

    raw_ids = os.environ.get("ARCHON_USER_IDS", "").strip()
    if not raw_ids:
        console.error("ARCHON_USER_IDS must be set for --non-interactive mode")
        sys.exit(1)

    try:
        user_ids = [int(uid.strip()) for uid in raw_ids.split(",") if uid.strip()]
    except ValueError:
        console.error(
            "ARCHON_USER_IDS must be comma-separated integers, e.g. '12345,67890'"
        )
        sys.exit(1)

    if not user_ids:
        console.error("ARCHON_USER_IDS must contain at least one user ID")
        sys.exit(1)

    return token, user_ids


def _collect_config_interactive(
    console: Console,
    archon_home: Path,
) -> tuple[str, list[int]]:
    """Prompt the user for bot token and Telegram user IDs."""
    # Bot token
    existing_token = ""
    env_file = archon_home / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                raw = line.split("=", 1)[1].strip()
                existing_token = shlex.split(raw)[0] if raw else ""
                break

    if existing_token and existing_token != "your_bot_token_here":
        token = console.ask(
            f"Telegram bot token [{existing_token[:8]}…] (Enter to keep):"
        ).strip()
        if not token:
            token = existing_token
    else:
        token = console.ask("Telegram bot token (from @BotFather):").strip()
        if not token:
            console.error("Bot token is required.")
            sys.exit(1)

    # User IDs
    _PLACEHOLDER_USER_ID = 123456789  # sentinel in config.toml.example
    existing_ids: list[int] = []
    config_file = archon_home / "config.toml"
    if config_file.exists():
        try:
            with open(config_file, "rb") as f:
                _doc = tomllib.load(f)
            raw_existing = _doc.get("access", {}).get("allowed_user_ids", [])
            # Only accept a list of actual integers, excluding the template placeholder.
            if isinstance(raw_existing, list) and all(isinstance(i, int) for i in raw_existing):
                existing_ids = [i for i in raw_existing if i != _PLACEHOLDER_USER_ID]
        except tomllib.TOMLDecodeError:
            console.error("~/.archon/config.toml is corrupted. Please enter your user IDs manually.")

    if existing_ids:
        ids_display = ", ".join(str(i) for i in existing_ids)
        raw_ids = console.ask(
            f"Your Telegram user ID(s) [{ids_display}] (Enter to keep):"
        ).strip()
        if not raw_ids:
            return token, existing_ids
    else:
        raw_ids = console.ask("Your Telegram user ID(s), comma-separated:").strip()
        if not raw_ids:
            console.error("User ID is required.")
            sys.exit(1)

    try:
        user_ids = [int(uid.strip()) for uid in raw_ids.split(",") if uid.strip()]
    except ValueError:
        console.error("User IDs must be integers, e.g. '12345,67890'")
        sys.exit(1)

    if not user_ids:
        console.error("At least one user ID is required.")
        sys.exit(1)

    return token, user_ids


def _read_existing_config(archon_home: Path, console: Console) -> tuple[str, list[int]]:
    """Read token and user IDs from existing ~/.archon config files."""
    token = ""
    env_file = archon_home / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                raw = line.split("=", 1)[1].strip()
                token = shlex.split(raw)[0] if raw else ""
                break

    user_ids: list[int] = []
    config_file = archon_home / "config.toml"
    if config_file.exists():
        with open(config_file, "rb") as f:
            doc = tomllib.load(f)
        user_ids = doc.get("access", {}).get("allowed_user_ids", [])

    if not token or not user_ids:
        console.error(
            "Existing config not found. Run without --update to perform a fresh install."
        )
        sys.exit(1)

    return token, user_ids


try:
    from archon.platform import get_search_service  # noqa: PLC0415
    from archon.search.install import SearchInstaller  # noqa: PLC0415
except Exception:
    get_search_service = None  # type: ignore[assignment]
    SearchInstaller = None  # type: ignore[assignment]


def _uninstall_search_service(dry_run: bool, console: Console) -> None:
    """Stop and unregister the search service if installed. Never raises."""
    try:
        if get_search_service is None or SearchInstaller is None:
            return
        svc = get_search_service()
        if svc.is_installed():
            SearchInstaller(dry_run=dry_run, console=console).run_uninstall()
    except Exception:
        pass  # search was never installed or unavailable — skip silently


def _do_uninstall(
    archon_home: Path,
    dry_run: bool,
    console: Console,
) -> None:
    """Stop and remove the system service and ~/.archon/app."""
    _uninstall_search_service(dry_run=dry_run, console=console)

    if platform.system() == "Linux":
        unit_file = Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_SERVICE_NAME
        if not unit_file.exists():
            console.warn("No systemd unit file found")
        elif dry_run:
            console.info("[dry-run] Would run systemctl stop --user archon")
            console.info("[dry-run] Would run systemctl disable --user archon")
            console.info(f"[dry-run] Would remove {unit_file}")
            console.info("[dry-run] Would run systemctl --user daemon-reload")
        else:
            subprocess.run(["systemctl", "stop", "--user", "archon"], check=False)
            subprocess.run(["systemctl", "disable", "--user", "archon"], check=False)
            unit_file.unlink(missing_ok=True)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
            console.success("Service stopped and removed")
    elif platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / _PLIST_NAME
        if plist.exists():
            if dry_run:
                console.info(f"[dry-run] Would launchctl unload {plist}")
                console.info(f"[dry-run] Would remove {plist}")
            else:
                subprocess.run(["launchctl", "unload", str(plist)], check=False)
                plist.unlink(missing_ok=True)
                console.success("Service stopped and removed")
        else:
            console.warn("No service plist found")
    else:
        raise RuntimeError(f"Unsupported platform: {platform.system()}")

    app_dir = archon_home / "app"
    if dry_run:
        console.info(f"[dry-run] Would remove {app_dir}")
    elif app_dir.exists():
        shutil.rmtree(app_dir)
        console.success(f"Removed {app_dir}")
    else:
        console.warn(f"Nothing to remove: {app_dir} does not exist")


def _request_documents_permission(app_dir: Path, console: Console, dry_run: bool) -> None:
    """Trigger the macOS Documents folder permission dialog during install.

    macOS TCC (Transparency, Consent, and Control) ties folder-access grants to
    the specific binary that first requests them.  On every install or update,
    ``uv sync --upgrade`` may download a new Python interpreter, changing its
    path inside uv's managed-python cache.  TCC sees a different binary and
    revokes the previously-granted permission.

    By listing ~/Documents here — using the same ``uv run python`` invocation
    that the daemon uses — we force macOS to show the consent dialog while the
    user is still at the terminal, rather than having the daemon fail silently
    in the background after the install completes.
    """
    if platform.system() != "Darwin" or dry_run:
        return
    console.info(
        "Requesting macOS Documents folder access…  "
        "If a permission dialog appears, click 'Allow'."
    )
    try:
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-c",
                "import os; os.listdir(os.path.expanduser('~/Documents'))",
            ],
            cwd=str(app_dir),
            check=False,
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            console.success("Documents folder access confirmed")
        else:
            console.warn(
                "Could not trigger Documents permission check. "
                "If Archon cannot access ~/Documents, grant access manually:\n"
                "  System Settings → Privacy & Security → Files and Folders"
            )
    except (OSError, subprocess.TimeoutExpired):
        console.warn(
            "Could not trigger Documents permission check. "
            "If Archon cannot access ~/Documents, grant access manually:\n"
            "  System Settings → Privacy & Security → Files and Folders"
        )


def _run_uv_sync(app_dir: Path, dry_run: bool, console: Console, extras: list[str] | None = None) -> None:
    if dry_run:
        console.info(f"[dry-run] Would uv sync in {app_dir}")
        return
    console.info("Installing Python dependencies...")
    cmd = ["uv", "sync", "--upgrade", "--quiet"]
    for extra in (extras or []):
        cmd.extend(["--extra", extra])
    _run_with_retry(
        lambda: subprocess.run(cmd, cwd=str(app_dir), check=True),
        "Dependency installation",
        console,
    )
    console.success("Dependencies installed")




def _install_workspace_templates(
    app_dir: Path, archon_home: Path, dry_run: bool, console: Console
) -> None:
    """Copy workspace template files from app/workspace/ to ~/.archon/workspace/.

    Only copies files that do not already exist, to preserve user customisations.
    """
    src_dir = app_dir / "workspace"
    dst_dir = archon_home / "workspace"
    if not src_dir.exists():
        console.warn(f"workspace template directory not found: {src_dir} (skipping)")
        return
    for src in src_dir.iterdir():
        if not src.is_file():
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue  # preserve user customisation
        if dry_run:
            console.info(f"[dry-run] Would copy {src.name} → {dst}")
        else:
            shutil.copy2(src, dst)
            console.success(f"{src.name} installed to ~/.archon/workspace/")


def _install_schedules(
    app_dir: Path, archon_home: Path, dry_run: bool, console: Console
) -> None:
    """Copy scheduled job templates from app/schedules/ to ~/.archon/schedules/.

    Supports both bundle directories (name/job.toml) and flat .toml files.
    Only copies entries that do not already exist, to preserve user customisations.
    Jobs are installed with enabled = true so they are active out of the box.
    """
    src_dir = app_dir / "schedules"
    dst_dir = archon_home / "schedules"
    if not src_dir.exists():
        console.warn(f"schedules directory not found: {src_dir} (skipping)")
        return
    dst_dir.mkdir(parents=True, exist_ok=True)

    # Migration: remove stale scripts from ~/.archon/scripts/ (moved into bundles)
    _STALE_SCRIPTS = ("health_check.sh", "qmd_checker.sh")
    stale_scripts_dir = archon_home / "scripts"
    for name in _STALE_SCRIPTS:
        stale = stale_scripts_dir / name
        if stale.exists():
            if dry_run:
                console.info(f"[dry-run] Would remove stale script {stale}")
            else:
                stale.unlink()

    # Phase 1 — bundle directories
    for entry in sorted(src_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / "job.toml").exists():
            continue
        dst = dst_dir / entry.name
        if dst.exists():
            # Bundle already installed — only refresh scripts/ subdirectory
            src_scripts = entry / "scripts"
            dst_scripts = dst / "scripts"
            if src_scripts.is_dir():
                if dry_run:
                    console.info(f"[dry-run] Would refresh scripts in {entry.name}/scripts/")
                else:
                    if dst_scripts.exists():
                        shutil.rmtree(dst_scripts)
                    shutil.copytree(src_scripts, dst_scripts, symlinks=True)
                    for script in dst_scripts.iterdir():
                        if script.is_file():
                            script.chmod(0o755)
            continue  # preserve job.toml and other user customisations
        if dry_run:
            console.info(f"[dry-run] Would install job bundle {entry.name}/ → {dst}")
        else:
            shutil.copytree(entry, dst, symlinks=True)
            # Rewrite enabled=false → true in job.toml
            job_toml = dst / "job.toml"
            content = job_toml.read_text()
            content = re.sub(
                r"^enabled\s*=\s*false", "enabled = true", content, flags=re.MULTILINE
            )
            job_toml.write_text(content)
            # Ensure executable bits on scripts/ contents
            scripts = dst / "scripts"
            if scripts.is_dir():
                for script in scripts.iterdir():
                    if script.is_file():
                        script.chmod(0o755)
            console.success(f"{entry.name}/ bundle installed to ~/.archon/schedules/")

    # Phase 2 — flat .toml files (backward compat)
    for src in sorted(src_dir.iterdir()):
        if not src.is_file() or src.suffix != ".toml":
            continue
        dst = dst_dir / src.name
        if dst.exists():
            continue  # preserve user customisation
        content = src.read_text()
        content = re.sub(
            r"^enabled\s*=\s*false", "enabled = true", content, flags=re.MULTILINE
        )
        if dry_run:
            console.info(
                f"[dry-run] Would install scheduled job {src.name} (enabled) → {dst}"
            )
        else:
            dst.write_text(content)
            console.success(f"{src.name} installed to ~/.archon/schedules/")


def _install_skills(
    app_dir: Path, workspace_dir: Path, dry_run: bool, console: Console
) -> None:
    """Copy skill directories from app/skills/ to the workspace's .claude/skills/.

    Skills are installed as project-scoped (inside the workspace's .claude/
    directory) so they are only available to Archon sessions, not globally.
    Only copies skill directories that contain a SKILL.md file and do not
    already exist in the destination, to preserve user customisations.
    """
    src_dir = app_dir / "skills"
    if not src_dir.exists():
        return
    dst_dir = workspace_dir / ".claude" / "skills"

    for entry in sorted(src_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not (entry / "SKILL.md").exists():
            continue
        dst = dst_dir / entry.name
        if dst.exists():
            continue  # preserve user customisation
        if dry_run:
            console.info(f"[dry-run] Would install skill {entry.name}/ → {dst}")
        else:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(entry, dst, symlinks=True)
            console.success(
                f"{entry.name}/ skill installed to {dst_dir.relative_to(workspace_dir.parent)}/"
            )


# ── CLI ────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install or update Archon Assistant.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print every action without executing it; exit 0",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Stop the service and remove ~/.archon/app",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Skip config prompts; only pull latest code + restart service",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read ARCHON_BOT_TOKEN and ARCHON_USER_IDS from environment",
    )
    parser.add_argument(
        "--tag",
        default=None,
        metavar="TAG",
        help="GitHub release tag to install (e.g. 26.3.198); omit to install from current directory",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Install from the current directory (default when --tag is omitted)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    console = Console()
    archon_home = Path.home() / ".archon"
    paths = _paths(archon_home)
    app_dir = paths.app
    tag = args.tag
    local_src = Path.cwd() if args.local else None
    if tag is not None and not re.fullmatch(
        r"[0-9]+\.[0-9]+\.[0-9]+[a-zA-Z0-9.\-]*", tag
    ):
        console.error(f"Invalid tag format: {tag!r}. Expected semver like '26.3.198'.")
        sys.exit(1)

    if args.uninstall:
        _do_uninstall(archon_home, args.dry_run, console)
        return

    try:
        check_prerequisites(console)
    except FileNotFoundError as exc:
        console.error(f"Missing prerequisite: {exc}")
        sys.exit(1)

    # Resolve the source: --local non-git dir or no source at all → use embedded tag.
    if local_src is not None and not (local_src / ".git").exists():
        local_src = None
    if tag is None and local_src is None:
        tag = __version__

    new_ver = tag if tag else _app_version(local_src)  # type: ignore[arg-type]

    # Collect config
    if args.update:
        bot_token, user_ids = _read_existing_config(archon_home, console)
        old_ver = _app_version(paths.app) if paths.app.exists() else "unknown"
        console.info(f"Updating Archon v{old_ver} → v{new_ver}")
    elif args.non_interactive:
        console.info(f"Installing Archon v{new_ver}")
        bot_token, user_ids = _collect_config_noninteractive(console)
    else:
        # Check for existing install and prompt for reinstall confirmation
        plist = Path.home() / "Library" / "LaunchAgents" / _PLIST_NAME
        linux_unit = (
            Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_SERVICE_NAME
        )
        already_installed = plist.exists() or linux_unit.exists()
        if already_installed:
            answer = console.ask(
                "Archon is already installed. Reinstall? [y/N]"
            ).strip()
            if answer.lower() != "y":
                console.info("Nothing changed. Exiting.")
                return
            # Unload before reinstalling
            if not args.dry_run:
                if platform.system() == "Linux":
                    subprocess.run(
                        ["systemctl", "stop", "--user", "archon"], check=False
                    )
                    subprocess.run(
                        ["systemctl", "disable", "--user", "archon"], check=False
                    )
                elif platform.system() == "Darwin":
                    subprocess.run(["launchctl", "unload", str(plist)], check=False)

        console.info(f"Installing Archon v{new_ver}")
        bot_token, user_ids = _collect_config_interactive(console, archon_home)

    # Create directories
    for subdir in ("workspace", "schedules", "scripts"):
        d = archon_home / subdir
        if not args.dry_run:
            d.mkdir(parents=True, exist_ok=True)
        else:
            console.info(f"[dry-run] Would create {d}")

    retry_flag = f"--tag {tag}" if tag else "--local"
    try:
        _prepare_candidate(paths, args.dry_run, console, tag=tag, local_src=local_src)
    except subprocess.CalledProcessError as exc:
        console.error(
            "Failed to prepare candidate app. Existing Archon version remains active.\n"
            f"Details: {exc}\n"
            f"Inspect logs: tail -f {archon_home / 'logs' / 'archon.log'}\n"
            f"Retry update: uv run install.py --update {retry_flag}"
        )
        sys.exit(1)
    write_config(
        archon_home, bot_token, user_ids, dry_run=args.dry_run, console=console
    )
    _sync_extras: list[str] = []
    if _search_already_enabled(archon_home):
        _sync_extras.append("search")
    if _voice_already_enabled(archon_home):
        _sync_extras.append("voice")
    try:
        _run_uv_sync(paths.candidate, dry_run=args.dry_run, console=console, extras=_sync_extras or None)
    except subprocess.CalledProcessError as exc:
        console.error(
            "Dependency installation failed in candidate. Existing Archon version remains active.\n"
            f"Details: {exc}\n"
            f"Inspect logs: tail -f {archon_home / 'logs' / 'archon.log'}\n"
            f"Retry update: uv run install.py --update {retry_flag}"
        )
        if paths.candidate.exists():
            shutil.rmtree(paths.candidate, ignore_errors=True)
        sys.exit(1)

    if platform.system() == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / _PLIST_NAME
        if not args.dry_run and plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], check=False)

    try:
        _activate_candidate(paths, console, args.dry_run)
        # Re-run uv sync in the final app directory so the generated entry-point
        # script (archon/cli/main.py) has the correct shebang pointing to
        # app/.venv/bin/python, not the now-deleted app.candidate/.venv/bin/python.
        _run_uv_sync(paths.app, dry_run=args.dry_run, console=console, extras=_sync_extras or None)
        _install_workspace_templates(paths.app, archon_home, args.dry_run, console)
        _install_schedules(paths.app, archon_home, args.dry_run, console)
        _install_skills(paths.app, archon_home / "workspace", args.dry_run, console)
        register_service(paths.app, archon_home, dry_run=args.dry_run, console=console)
    except Exception as exc:  # noqa: BLE001
        console.error(f"Activation failed: {exc}")
        if not _rollback_activation(paths, console, args.dry_run):
            sys.exit(1)
        register_service(paths.app, archon_home, dry_run=args.dry_run, console=console)
        if not _verify_service_health(console, args.dry_run):
            console.error(
                "Rollback service verification failed. "
                f"Manual recovery required. Log: {archon_home / 'logs' / 'archon.log'}"
            )
            sys.exit(1)
        console.warn("Update rolled back. Previous version is still running.")
        return

    running = _verify_service_health(console, args.dry_run)
    if running:
        _cleanup_post_success(paths, console, args.dry_run)
        _install_cli_symlink(paths.app.parent, args.dry_run, console)
        if not args.dry_run:
            console.success(f"Archon v{new_ver} is running!")
            _request_documents_permission(paths.app, console, args.dry_run)
        _offer_search_setup(
                paths,
                console,
                non_interactive=args.non_interactive,
                reinstall=args.update and _search_already_enabled(archon_home),
                dry_run=args.dry_run,
            )
        _offer_voice_setup(
                paths,
                console,
                non_interactive=args.non_interactive,
                reinstall=args.update and _voice_already_enabled(archon_home),
                dry_run=args.dry_run,
            )
        if args.dry_run:
            console.info("[dry-run] Complete — no changes were made.")
        return

    console.warn("Activation health check failed. Starting automatic rollback...")
    if not _rollback_activation(paths, console, args.dry_run):
        console.error(
            "Automatic rollback failed.\n"
            f"Inspect logs: tail -f {archon_home / 'logs' / 'archon.log'}\n"
            f"{_remediation_message(archon_home)}"
        )
        sys.exit(1)

    register_service(paths.app, archon_home, dry_run=args.dry_run, console=console)
    if _verify_service_health(console, args.dry_run):
        console.warn(
            "Update rolled back successfully. Previous version remains active."
        )
        return

    console.error(
        "Rollback completed but health check still failing.\n"
        f"Inspect logs: tail -f {archon_home / 'logs' / 'archon.log'}\n"
        f"{_remediation_message(archon_home)}"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
