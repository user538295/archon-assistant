"""Tests for install.sh — one-click installer.

Section A: Static structural checks (no subprocess).
  Verify that install.sh encodes the option-1 contract:
  auto-clone to ~/.archon/app, curl|bash entry point, no BASH_SOURCE.

Section B: Dry-run behavioural checks (subprocess + stub commands).
  Run install.sh in an isolated temp HOME with all external binaries replaced
  by stubs.  Verifies file creation, git clone vs. git-fetch branch, and
  prerequisite-missing failure paths.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
INSTALLER = REPO_ROOT / "install.sh"


# ══════════════════════════════════════════════════════════════════════════════
# A.  Static structure tests — no processes spawned
# ══════════════════════════════════════════════════════════════════════════════

def _text() -> str:
    return INSTALLER.read_text()


def test_installer_exists() -> None:
    assert INSTALLER.exists(), "install.sh not found in repo root"


def test_installer_is_executable() -> None:
    assert INSTALLER.stat().st_mode & stat.S_IXUSR, "install.sh is not executable"


def test_installer_curl_pipe_bash_comment() -> None:
    """The canonical curl|bash usage must be documented at the top of the file."""
    text = _text()
    assert "curl -fsSL" in text
    assert "| bash" in text


def test_installer_no_bash_source() -> None:
    """Old in-place pattern (BASH_SOURCE) must be absent — app is now self-cloning."""
    assert "BASH_SOURCE" not in _text(), (
        "BASH_SOURCE found: installer still uses in-place execution pattern"
    )


def test_installer_fixed_app_dir_constant() -> None:
    """App must clone to a fixed, predictable location under $HOME."""
    assert 'ARCHON_APP_DIR="$HOME/.archon/app"' in _text()


def test_installer_archon_dir_derived_from_app_dir() -> None:
    """ARCHON_DIR (used by service templates) must come from the fixed clone dir."""
    assert 'ARCHON_DIR="$ARCHON_APP_DIR"' in _text()


def test_installer_has_repo_url_constant() -> None:
    text = _text()
    assert "REPO_URL=" in text
    assert "github.com" in text


def test_installer_has_repo_branch_constant() -> None:
    assert "REPO_BRANCH=" in _text()


def test_installer_fresh_clone_uses_repo_url() -> None:
    text = _text()
    assert "git clone" in text
    assert '"$REPO_URL"' in text


def test_installer_update_path_has_fetch_and_reset() -> None:
    text = _text()
    # Script uses `git -C <dir> fetch ...` — "git fetch" as a literal substring
    # won't appear; check for the actual fragments present.
    assert "fetch --quiet" in text
    assert "reset --hard" in text


def test_installer_checks_for_git_prerequisite() -> None:
    assert "command -v git" in _text()


def test_installer_checks_for_uv_prerequisite() -> None:
    assert "command -v uv" in _text()


def test_installer_checks_for_claude_prerequisite() -> None:
    assert "command -v claude" in _text()


def test_installer_writes_dot_env_file() -> None:
    text = _text()
    assert ".archon/.env" in text or "ARCHON_HOME/.env" in text


def test_installer_writes_config_toml() -> None:
    assert "config.toml" in _text()


def test_installer_service_uses_archon_dir_variable() -> None:
    """Service file sed substitution must reference $ARCHON_DIR (= ~/.archon/app)."""
    text = _text()
    assert "$ARCHON_DIR" in text           # runtime variable
    assert "__ARCHON_DIR__" in text        # placeholder being substituted


def test_installer_calls_uv_sync() -> None:
    assert "uv sync" in _text()


def test_installer_has_claude_mem_prompt() -> None:
    """Installer must offer a claude-mem plugin install option."""
    text = _text()
    assert "claude-mem" in text
    assert "claude plugin install claude-mem@thedotmack" in text
    assert "--scope project" in text
    assert "--scope user" in text


# ══════════════════════════════════════════════════════════════════════════════
# B.  Dry-run behavioural tests — subprocess with stub commands
# ══════════════════════════════════════════════════════════════════════════════

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_stub(path: Path, body: str) -> None:
    """Write an executable stub shell script to *path*."""
    path.write_text(f"#!/usr/bin/env bash\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ── fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def fake_env(tmp_path: Path):
    """
    Isolated environment for running install.sh without touching the real system.

    Provides:
      - Stub executables in tmp_path/bin: git, uv, claude, launchctl, systemctl
      - Isolated HOME at tmp_path/home
      - A stub invocation log at tmp_path/home/.stub.log
    """
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    bin_dir.mkdir()
    stub_log = home / ".stub.log"

    # ── git stub ──────────────────────────────────────────────────────────────
    # Records every call.  For `git clone`, creates the target directory and
    # copies the real repo's scripts/ so the installer can find service templates.
    _make_stub(bin_dir / "git", textwrap.dedent(f"""\
        echo "git $*" >> "{stub_log}"
        case "$1" in
            --version)
                echo "git version 2.43.0"
                ;;
            clone)
                # Always clone to $HOME/.archon/app (HOME is overridden per test)
                APP_DIR="$HOME/.archon/app"
                mkdir -p "$APP_DIR/.git"
                cp -r "{REPO_ROOT}/scripts" "$APP_DIR/scripts"
                ;;
            -C)
                # git -C <dir> <subcommand> [...]
                # $3 is the subcommand (fetch / reset)
                case "$3" in
                    fetch|reset) exit 0 ;;
                esac
                ;;
        esac
    """))

    # ── uv stub ───────────────────────────────────────────────────────────────
    _make_stub(bin_dir / "uv", textwrap.dedent(f"""\
        echo "uv $*" >> "{stub_log}"
        case "$1" in
            --version)
                echo "uv 0.4.0"
                ;;
            run)
                shift
                case "$1" in
                    python|python3)
                        shift
                        case "$1" in
                            --version) echo "Python 3.12.0" ;;
                            -c)        ;;   # no-op (QMD tomlkit path; QMD is skipped)
                        esac
                        ;;
                esac
                ;;
            sync)
                exit 0
                ;;
        esac
    """))

    # ── claude stub ───────────────────────────────────────────────────────────
    _make_stub(bin_dir / "claude", textwrap.dedent(f"""\
        echo "claude $*" >> "{stub_log}"
        echo "1.0.0"
    """))

    # ── launchctl stub (macOS) ────────────────────────────────────────────────
    _make_stub(bin_dir / "launchctl", textwrap.dedent(f"""\
        echo "launchctl $*" >> "{stub_log}"
        case "$1" in
            list)   echo "com.archon.assistant" ;;
            load|unload) exit 0 ;;
        esac
    """))

    # ── systemctl stub (Linux) ────────────────────────────────────────────────
    _make_stub(bin_dir / "systemctl", textwrap.dedent(f"""\
        echo "systemctl $*" >> "{stub_log}"
        exit 0
    """))

    yield {"home": home, "bin": bin_dir, "log": stub_log, "tmp": tmp_path}


# ── runner helper ─────────────────────────────────────────────────────────────

# Interactive stdin lines for a fresh install (no existing service)
_FRESH_STDIN = [
    "test_bot_token_abc123",   # Telegram bot token
    "987654321",               # Telegram user ID
    "n",                       # Install QMD? → no
    "1",                       # Install claude-mem? → skip
]


def _run(
    fake_env: dict,
    stdin_lines: list[str],
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(fake_env["home"])
    # Restrict PATH to stub dir + essential system dirs only.
    # This prevents user-installed tools (e.g. /opt/homebrew/bin/claude) from
    # being found when a stub is intentionally absent.
    env["PATH"] = str(fake_env["bin"]) + ":/usr/bin:/bin"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(INSTALLER)],
        input="\n".join(stdin_lines) + "\n",
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _log(fake_env: dict) -> str:
    """Return stub invocation log, or empty string if not yet written."""
    try:
        return fake_env["log"].read_text()
    except FileNotFoundError:
        return ""


# ── fresh-install tests ───────────────────────────────────────────────────────

@pytest.mark.skipif(
    os.uname().sysname != "Darwin",
    reason="launchd service path is macOS-specific",
)
def test_fresh_install_exits_zero(fake_env: dict) -> None:
    result = _run(fake_env, _FRESH_STDIN)
    assert result.returncode == 0, (
        f"installer exited {result.returncode}\n"
        f"--- STDOUT ---\n{result.stdout}\n"
        f"--- STDERR ---\n{result.stderr}"
    )


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_fresh_install_clones_repo(fake_env: dict) -> None:
    _run(fake_env, _FRESH_STDIN)
    assert "clone" in _log(fake_env), "git clone was not called during fresh install"


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_fresh_install_creates_dot_env(fake_env: dict) -> None:
    _run(fake_env, _FRESH_STDIN)
    env_file = fake_env["home"] / ".archon" / ".env"
    assert env_file.exists(), ".env not created"
    assert "test_bot_token_abc123" in env_file.read_text()


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_fresh_install_creates_config_toml(fake_env: dict) -> None:
    _run(fake_env, _FRESH_STDIN)
    config = fake_env["home"] / ".archon" / "config.toml"
    assert config.exists(), "config.toml not created"
    content = config.read_text()
    assert "987654321" in content, "user ID missing from config"
    assert "[access]" in content
    assert "allowed_user_ids" in content


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_fresh_install_creates_workspace_directory(fake_env: dict) -> None:
    _run(fake_env, _FRESH_STDIN)
    assert (fake_env["home"] / ".archon" / "workspace").is_dir()


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_fresh_install_service_file_placeholders_substituted(fake_env: dict) -> None:
    """Installed plist must have real paths, not __PLACEHOLDER__ tokens."""
    _run(fake_env, _FRESH_STDIN)
    plist = (
        fake_env["home"]
        / "Library"
        / "LaunchAgents"
        / "com.archon.assistant.plist"
    )
    assert plist.exists(), "plist not installed to LaunchAgents"
    content = plist.read_text()
    assert "__ARCHON_DIR__" not in content, "__ARCHON_DIR__ was not substituted"
    assert "__UV_PATH__" not in content, "__UV_PATH__ was not substituted"
    assert "__LOG_FILE__" not in content, "__LOG_FILE__ was not substituted"
    assert ".archon/app" in content, "plist does not reference ~/.archon/app"


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_fresh_install_calls_uv_sync(fake_env: dict) -> None:
    _run(fake_env, _FRESH_STDIN)
    assert "uv sync" in _log(fake_env), "uv sync was not called"


# ── update-install tests ──────────────────────────────────────────────────────

@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_update_install_uses_fetch_not_clone(fake_env: dict) -> None:
    """Re-running the installer on an existing install must update, not re-clone."""
    home = fake_env["home"]

    # Simulate existing installation: app dir + service plist already present
    app_dir = home / ".archon" / "app"
    (app_dir / ".git").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "scripts", app_dir / "scripts", dirs_exist_ok=True)
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.archon.assistant.plist").write_text("<plist/>")

    # Answer "y" to the "reinstall?" prompt, then normal inputs
    result = _run(fake_env, ["y"] + _FRESH_STDIN)
    assert result.returncode == 0, (
        f"update failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    log = _log(fake_env)
    assert "fetch" in log, "git fetch not called on update"
    assert "clone" not in log, "git clone was called on update (should not be)"


@pytest.mark.skipif(os.uname().sysname != "Darwin", reason="macOS only")
def test_update_install_preserves_existing_config(fake_env: dict) -> None:
    """Existing config.toml must be patched, not overwritten."""
    home = fake_env["home"]
    archon_home = home / ".archon"
    archon_home.mkdir(parents=True)

    # Pre-write a config with a custom value
    (archon_home / "config.toml").write_text(
        '[access]\nallowed_user_ids = [111]\n'
        '[session]\nworking_directory = "/old/path"\n'
        '[output]\nmax_message_length = 4000\n'
        'truncation_strategy = "split"\n'
        '[notifications]\nshow_thinking_result = true\n'
        'brief_tool_output = false\nconcise_mode = "off"\n'
        'concise_interval_minutes = 2\n'
        '[history]\nenabled = true\ndirectory = "~/.archon/history"\n'
        '[logging]\nlog_file = "~/.archon/archon.log"\nlog_level = "INFO"\n'
        '[qmd]\nenabled = false\nport = 8181\n'
        'history_collection = "archon-history"\n'
    )
    (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN=old_token\n")

    app_dir = home / ".archon" / "app"
    (app_dir / ".git").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "scripts", app_dir / "scripts", dirs_exist_ok=True)
    launch_agents = home / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)
    (launch_agents / "com.archon.assistant.plist").write_text("<plist/>")

    _run(fake_env, ["y"] + _FRESH_STDIN)

    content = (archon_home / "config.toml").read_text()
    # User ID should be updated to the new value
    assert "987654321" in content
    # Custom section markers still present (file not clobbered)
    assert "[access]" in content


# ── prerequisite-missing failure tests ───────────────────────────────────────

def test_missing_git_exits_nonzero(fake_env: dict) -> None:
    (fake_env["bin"] / "git").unlink()
    result = _run(fake_env, _FRESH_STDIN)
    assert result.returncode != 0


def test_missing_uv_exits_nonzero(fake_env: dict) -> None:
    (fake_env["bin"] / "uv").unlink()
    result = _run(fake_env, _FRESH_STDIN)
    assert result.returncode != 0


def test_missing_claude_exits_nonzero(fake_env: dict) -> None:
    (fake_env["bin"] / "claude").unlink()
    result = _run(fake_env, _FRESH_STDIN)
    assert result.returncode != 0


def test_empty_bot_token_exits_nonzero(fake_env: dict) -> None:
    """install.sh must die() when no bot token is provided."""
    result = _run(fake_env, ["", "987654321", "n"])
    assert result.returncode != 0
