"""Tests for install.py — Python installer (S16.1).

Unit tests mock subprocess.run and use tmp_path for filesystem isolation.
HOME is patched via monkeypatch.setenv so Path.home() returns tmp_path.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tomllib
import urllib.error
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ── Import install.py (repo root, not a package) ───────────────────
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import install  # noqa: E402  (must be after sys.path manipulation)

_PLIST_NAME = "com.archon.assistant.plist"


# ── Helpers ────────────────────────────────────────────────────────

def _quiet() -> install.Console:
    return install.Console(quiet=True)


def _subprocess_ok(stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.stdout = stdout
    m.stderr = ""
    m.returncode = 0
    return m


def _make_fake_run(**overrides: str) -> Callable[..., MagicMock]:
    """Return a side_effect for subprocess.run that answers version queries."""
    defaults = {
        "git": "git version 2.43.0\n",
        "uv --version": "uv 0.5.0\n",
        "uv run": "Python 3.12.0\n",
        "claude": "1.0.0\n",
    }
    defaults.update(overrides)

    def fake_run(cmd: list[str], **kw: object) -> MagicMock:
        if cmd[0] == "git":
            if len(cmd) >= 2 and cmd[1] == "--version":
                return _subprocess_ok(defaults["git"])
            return _subprocess_ok()
        if cmd[0] == "uv":
            if len(cmd) >= 2 and cmd[1] == "--version":
                return _subprocess_ok(defaults["uv --version"])
            if len(cmd) >= 2 and cmd[1] == "run":
                return _subprocess_ok(defaults["uv run"])
            return _subprocess_ok()
        if cmd[0] == "claude":
            return _subprocess_ok(defaults["claude"])
        if cmd[0] == "launchctl":
            return _subprocess_ok()
        return _subprocess_ok()

    return fake_run


# ══════════════════════════════════════════════════════════════════════════════
# check_prerequisites
# ══════════════════════════════════════════════════════════════════════════════


class TestCheckPrerequisites:
    def test_check_prerequisites_raises_on_missing_git(self) -> None:
        """FileNotFoundError propagates when git is not on PATH."""

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            if cmd[0] == "git":
                raise FileNotFoundError("git: No such file")
            return _subprocess_ok()

        with patch("install.subprocess.run", side_effect=fake_run):
            with pytest.raises(FileNotFoundError):
                install.check_prerequisites(_quiet())

    def test_missing_uv_exits_with_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Missing uv → clear error message and SystemExit with non-zero code."""

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            if cmd[0] == "git":
                return _subprocess_ok("git version 2.43.0\n")
            if cmd[0] == "uv":
                raise FileNotFoundError("uv: not found")
            return _subprocess_ok()

        with patch("install.subprocess.run", side_effect=fake_run):
            with pytest.raises(SystemExit) as exc_info:
                install.check_prerequisites(_quiet())
        assert exc_info.value.code != 0

    def test_prerequisites_pass_when_all_present(self) -> None:
        with patch("install.subprocess.run", side_effect=_make_fake_run()):
            install.check_prerequisites(_quiet())  # must not raise


# ══════════════════════════════════════════════════════════════════════════════
# fetch_or_update_app
# ══════════════════════════════════════════════════════════════════════════════


class TestFetchOrUpdateApp:
    def test_fresh_install_calls_git_clone(self, tmp_path: Path) -> None:
        """Clone is called with correct pinned tag URL and target."""
        app_dir = tmp_path / "app"
        calls: list[list[str]] = []

        partial = app_dir.parent / (app_dir.name + ".partial")

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            if "clone" in cmd:
                # Simulate git cloning into the partial dir (install.py renames it)
                partial.mkdir(parents=True, exist_ok=True)
                (partial / ".git").mkdir()
            return _subprocess_ok()

        with patch("install.subprocess.run", side_effect=fake_run):
            install.fetch_or_update_app("1.0.0", app_dir, console=_quiet())

        assert any("clone" in c for c in calls), "git clone was not called"
        clone_call = next(c for c in calls if "clone" in c)
        assert "v1.0.0" in clone_call, "pinned tag not passed to git clone"
        assert any(str(tmp_path) in arg for arg in clone_call), "target path missing"

    def test_update_install_calls_git_fetch_and_checkout(self, tmp_path: Path) -> None:
        """fetch+checkout called when .git exists; clone is NOT called."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.subprocess.run", side_effect=fake_run):
            install.fetch_or_update_app("1.0.0", app_dir, console=_quiet())

        flat = " ".join(arg for c in calls for arg in c)
        assert "fetch" in flat, "git fetch not called on update"
        assert "checkout" in flat, "git checkout not called on update"
        assert "clone" not in flat, "git clone should not be called on update"

    def test_partial_failure_leaves_partial_dir_not_app(self, tmp_path: Path) -> None:
        """Failed clone leaves app.partial; app dir must NOT be created."""
        app_dir = tmp_path / "app"
        partial = tmp_path / "app.partial"

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            if "clone" in cmd:
                # Simulate git creating the target dir before failing
                partial.mkdir(parents=True, exist_ok=True)
                raise subprocess.CalledProcessError(128, cmd)
            return _subprocess_ok()

        with patch("install.subprocess.run", side_effect=fake_run):
            with pytest.raises(subprocess.CalledProcessError):
                install.fetch_or_update_app("1.0.0", app_dir, console=_quiet())

        assert partial.exists(), "app.partial should remain after failed clone"
        assert not app_dir.exists(), "app dir must NOT exist after failed clone"

    def test_dry_run_does_not_call_git(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        with patch("install.subprocess.run") as mock_run:
            install.fetch_or_update_app("1.0.0", app_dir, dry_run=True, console=_quiet())
        mock_run.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# write_config
# ══════════════════════════════════════════════════════════════════════════════


class TestWriteConfig:
    def test_write_config_creates_expected_files(self, tmp_path: Path) -> None:
        """.env and config.toml are written with correct content."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()

        install.write_config(archon_home, "mytoken123", [987654321], console=_quiet())

        env_file = archon_home / ".env"
        config_file = archon_home / "config.toml"
        assert env_file.exists(), ".env not created"
        assert config_file.exists(), "config.toml not created"
        assert "mytoken123" in env_file.read_text()
        doc = tomllib.loads(config_file.read_text())
        assert 987654321 in doc["access"]["allowed_user_ids"]

    def test_update_preserves_user_config_keys(self, tmp_path: Path) -> None:
        """Keys other than allowed_user_ids and working_directory survive an update."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        # Pre-write config with custom values
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\ninactivity_timeout_seconds = 900\n"
            "[output]\nmax_message_length = 8000\n"
        )

        install.write_config(archon_home, "token", [222, 333], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["access"]["allowed_user_ids"] == [222, 333]  # updated
        assert doc["session"]["inactivity_timeout_seconds"] == 900  # preserved
        assert doc["output"]["max_message_length"] == 8000  # preserved

    def test_token_with_special_chars_is_shell_quoted(self, tmp_path: Path) -> None:
        """Bot token containing $, !, @ is written safely to .env."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        token = "my$token!@#special"

        install.write_config(archon_home, token, [123], console=_quiet())

        env_content = (archon_home / ".env").read_text()
        quoted = shlex.quote(token.strip())
        assert f"TELEGRAM_BOT_TOKEN={quoted}" in env_content

    def test_dry_run_writes_no_files(self, tmp_path: Path) -> None:
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()

        install.write_config(archon_home, "token", [123], dry_run=True, console=_quiet())

        assert not (archon_home / ".env").exists()
        assert not (archon_home / "config.toml").exists()


# ══════════════════════════════════════════════════════════════════════════════
# register_service
# ══════════════════════════════════════════════════════════════════════════════


class TestRegisterService:
    def test_service_placeholders_substituted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Installed plist has no __PLACEHOLDER__ tokens."""
        monkeypatch.setenv("HOME", str(tmp_path))

        app_dir = tmp_path / ".archon" / "app"
        (app_dir / "scripts").mkdir(parents=True)
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (app_dir / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())

        archon_home = tmp_path / ".archon"

        with patch("install.subprocess.run"):
            install.register_service(app_dir, archon_home, console=_quiet())

        plist_dest = tmp_path / "Library" / "LaunchAgents" / _PLIST_NAME
        assert plist_dest.exists(), "plist not installed to LaunchAgents"
        content = plist_dest.read_text()
        assert "__ARCHON_DIR__" not in content
        assert "__UV_PATH__" not in content
        assert "__LOG_FILE__" not in content

    def test_dry_run_makes_no_filesystem_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--dry-run produces no side effects."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token_abc")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        app_dir = tmp_path / ".archon" / "app"
        (app_dir / "scripts").mkdir(parents=True)
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (app_dir / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())

        archon_home = tmp_path / ".archon"
        archon_home.mkdir(exist_ok=True)

        with patch("install.subprocess.run"):
            install.register_service(app_dir, archon_home, dry_run=True, console=_quiet())

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        assert not launch_agents.exists() or not (launch_agents / _PLIST_NAME).exists()


# ══════════════════════════════════════════════════════════════════════════════
# Non-interactive mode
# ══════════════════════════════════════════════════════════════════════════════


class TestNonInteractive:
    def test_non_interactive_reads_env_vars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ARCHON_BOT_TOKEN + ARCHON_USER_IDS consumed correctly."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "env_token_123")
        monkeypatch.setenv("ARCHON_USER_IDS", "111,222,333")

        token, user_ids = install._collect_config_noninteractive(_quiet())

        assert "env_token_123" in token
        assert user_ids == [111, 222, 333]

    def test_non_interactive_exits_on_malformed_user_ids(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-integer value in ARCHON_USER_IDS exits non-zero."""
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token")
        monkeypatch.setenv("ARCHON_USER_IDS", "111,not-an-int,333")

        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_noninteractive(_quiet())
        assert exc_info.value.code != 0

    def test_non_interactive_exits_when_token_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ARCHON_BOT_TOKEN", raising=False)
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_noninteractive(_quiet())
        assert exc_info.value.code != 0


# ══════════════════════════════════════════════════════════════════════════════
# --update flag skips config prompts
# ══════════════════════════════════════════════════════════════════════════════


class TestUpdateFlag:
    def test_update_flag_skips_config_prompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--update does not call input()."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Set up existing installation
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [99999]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
        )
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        (app_dir / "scripts").mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (app_dir / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.input") as mock_input, \
             patch("install.verify_running", return_value=True):
            install.main(["--update"])

        mock_input.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# verify_running
# ══════════════════════════════════════════════════════════════════════════════


class TestVerifyRunning:
    def test_verify_running_returns_true_on_first_200(self) -> None:
        """Returns True immediately when urlopen returns status 200."""
        mock_response = MagicMock()
        mock_response.status = 200

        with patch("install.urllib.request.urlopen", return_value=mock_response):
            result = install.verify_running(retries=3, delay=0, console=_quiet())

        assert result is True

    def test_verify_running_returns_false_after_all_retries_exhausted(self) -> None:
        """Returns False when all retries raise URLError."""
        with patch(
            "install.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            result = install.verify_running(retries=3, delay=0, console=_quiet())

        assert result is False

    def test_verify_running_retries_on_failure_then_succeeds(self) -> None:
        """Returns True after two failures followed by a successful 200 response."""
        mock_200 = MagicMock()
        mock_200.status = 200

        with patch(
            "install.urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError("fail"),
                urllib.error.URLError("fail"),
                mock_200,
            ],
        ):
            result = install.verify_running(retries=3, delay=0, console=_quiet())

        assert result is True

    def test_verify_running_uses_correct_url(self) -> None:
        """Passes the correct health URL to urlopen."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_urlopen = MagicMock(return_value=mock_response)

        with patch("install.urllib.request.urlopen", mock_urlopen):
            install.verify_running(host="myhost", port=9999, retries=1, delay=0, console=_quiet())

        url_called = mock_urlopen.call_args[0][0]
        assert url_called == "http://myhost:9999/health"


# ══════════════════════════════════════════════════════════════════════════════
# _do_uninstall
# ══════════════════════════════════════════════════════════════════════════════


class TestDoUninstall:
    def test_uninstall_removes_plist_and_unloads_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """plist is deleted and launchctl unload is called when plist exists."""
        monkeypatch.setenv("HOME", str(tmp_path))

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / _PLIST_NAME
        plist.write_text("<plist/>")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)

        with patch("install.subprocess.run", side_effect=_make_fake_run()) as mock_run:
            install._do_uninstall(app_dir, purge=False, dry_run=False, console=_quiet())

        assert not plist.exists()
        calls_flat = [c.args[0] for c in mock_run.call_args_list]
        assert any("unload" in cmd for cmd in calls_flat)

    def test_uninstall_purge_removes_app_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With purge=True, app_dir is removed after uninstall."""
        monkeypatch.setenv("HOME", str(tmp_path))

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / _PLIST_NAME
        plist.write_text("<plist/>")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)
        (app_dir / "somefile.txt").write_text("data")

        with patch("install.subprocess.run", side_effect=_make_fake_run()):
            install._do_uninstall(app_dir, purge=True, dry_run=False, console=_quiet())

        assert not app_dir.exists()

    def test_uninstall_dry_run_makes_no_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dry_run=True leaves plist intact and does not call subprocess.run."""
        monkeypatch.setenv("HOME", str(tmp_path))

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / _PLIST_NAME
        plist.write_text("<plist/>")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)

        with patch("install.subprocess.run") as mock_run:
            install._do_uninstall(app_dir, purge=True, dry_run=True, console=_quiet())

        assert plist.exists()
        mock_run.assert_not_called()

    def test_uninstall_no_plist_warns_gracefully(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No exception raised when plist does not exist."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"

        # Should not raise
        install._do_uninstall(app_dir, purge=False, dry_run=False, console=_quiet())


# ══════════════════════════════════════════════════════════════════════════════
# Rollback on uv sync failure
# ══════════════════════════════════════════════════════════════════════════════


class TestRollbackOnUvSyncFailure:
    def test_fresh_install_rollback_removes_app_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """uv sync failure during fresh install removes app_dir (rollback)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        base_fake_run = _make_fake_run()

        def fake_run_with_sync_failure(cmd: list[str], **kw: object) -> object:
            if cmd[0] == "git" and "clone" in cmd:
                # Simulate git clone into transactional candidate dir
                candidate = archon_home / "app.candidate"
                candidate.mkdir(parents=True, exist_ok=True)
                (candidate / ".git").mkdir()
                scripts = candidate / "scripts"
                scripts.mkdir()
                (scripts / _PLIST_NAME).write_text(plist_src.read_text())
                return _subprocess_ok()
            if cmd[0] == "uv" and len(cmd) >= 2 and cmd[1] == "sync":
                raise subprocess.CalledProcessError(1, ["uv", "sync"])
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_sync_failure):
            with pytest.raises(SystemExit) as exc_info:
                install.main(["--non-interactive"])

        assert exc_info.value.code != 0
        assert not app_dir.exists(), "app_dir should be removed after fresh install rollback"

    def test_update_sync_failure_does_not_remove_app_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """uv sync failure during --update does NOT remove app_dir."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        archon_home.mkdir(parents=True)
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()

        scripts_dir = app_dir / "scripts"
        scripts_dir.mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (scripts_dir / _PLIST_NAME).write_text(plist_src.read_text())

        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [12345]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
        )

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        base_fake_run = _make_fake_run()

        def fake_run_with_sync_failure(cmd: list[str], **kw: object) -> object:
            if cmd[0] == "uv" and len(cmd) >= 2 and cmd[1] == "sync":
                raise subprocess.CalledProcessError(1, ["uv", "sync"])
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_sync_failure):
            with pytest.raises(SystemExit) as exc_info:
                install.main(["--update"])

        assert exc_info.value.code != 0
        assert app_dir.exists(), "app_dir must NOT be removed after failed --update"

    def test_update_sync_failure_keeps_existing_contents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Candidate sync failure must preserve existing active app content."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        archon_home.mkdir(parents=True)
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        (app_dir / "sentinel.txt").write_text("existing")

        scripts_dir = app_dir / "scripts"
        scripts_dir.mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (scripts_dir / _PLIST_NAME).write_text(plist_src.read_text())

        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [12345]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
        )

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        base_fake_run = _make_fake_run()

        def fake_run_with_sync_failure(cmd: list[str], **kw: object) -> object:
            if cmd[0] == "git" and "clone" in cmd:
                candidate = archon_home / "app.candidate"
                candidate.mkdir(parents=True, exist_ok=True)
                (candidate / ".git").mkdir(exist_ok=True)
                candidate_scripts = candidate / "scripts"
                candidate_scripts.mkdir(exist_ok=True)
                (candidate_scripts / _PLIST_NAME).write_text(plist_src.read_text())
                return _subprocess_ok()
            if cmd[0] == "uv" and len(cmd) >= 2 and cmd[1] == "sync":
                raise subprocess.CalledProcessError(1, ["uv", "sync"])
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_sync_failure):
            with pytest.raises(SystemExit) as exc_info:
                install.main(["--update"])

        assert exc_info.value.code != 0
        assert (app_dir / "sentinel.txt").read_text() == "existing"


class TestTransactionalActivation:
    def test_health_failure_rolls_back_to_previous(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failed post-activation health check restores previous active app."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        archon_home.mkdir(parents=True)
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        (app_dir / "version.txt").write_text("old")
        scripts_dir = app_dir / "scripts"
        scripts_dir.mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (scripts_dir / _PLIST_NAME).write_text(plist_src.read_text())

        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [12345]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
        )

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        base_fake_run = _make_fake_run()

        def fake_run_with_candidate(cmd: list[str], **kw: object) -> object:
            if cmd[0] == "git" and "clone" in cmd:
                candidate = archon_home / "app.candidate"
                candidate.mkdir(parents=True, exist_ok=True)
                (candidate / ".git").mkdir(exist_ok=True)
                (candidate / "version.txt").write_text("new")
                candidate_scripts = candidate / "scripts"
                candidate_scripts.mkdir(exist_ok=True)
                (candidate_scripts / _PLIST_NAME).write_text(plist_src.read_text())
                return _subprocess_ok()
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_candidate), \
             patch("install.verify_running", side_effect=[False, False, False, True]):
            install.main(["--update"])

        assert (archon_home / "app" / "version.txt").read_text() == "old"
        assert not (archon_home / "app.previous").exists()
        assert not (archon_home / "app.candidate").exists()

    def test_rollback_failure_exits_nonzero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If rollback cannot restore previous app, installer exits non-zero."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        archon_home.mkdir(parents=True)
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        scripts_dir = app_dir / "scripts"
        scripts_dir.mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (scripts_dir / _PLIST_NAME).write_text(plist_src.read_text())

        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [12345]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
        )

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        base_fake_run = _make_fake_run()

        def fake_run_with_candidate(cmd: list[str], **kw: object) -> object:
            if cmd[0] == "git" and "clone" in cmd:
                candidate = archon_home / "app.candidate"
                candidate.mkdir(parents=True, exist_ok=True)
                (candidate / ".git").mkdir(exist_ok=True)
                candidate_scripts = candidate / "scripts"
                candidate_scripts.mkdir(exist_ok=True)
                (candidate_scripts / _PLIST_NAME).write_text(plist_src.read_text())
                return _subprocess_ok()
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_candidate), \
             patch("install.verify_running", return_value=False), \
             patch("install._rollback_activation", return_value=False):
            with pytest.raises(SystemExit) as exc_info:
                install.main(["--update"])

        assert exc_info.value.code != 0

    def test_retries_uv_sync_on_transient_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """uv sync transient failures are retried and eventually succeed."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        archon_home = tmp_path / ".archon"
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        sync_calls = 0
        base_fake_run = _make_fake_run()

        def fake_run_with_transient_sync(cmd: list[str], **kw: object) -> object:
            nonlocal sync_calls
            if cmd[0] == "git" and "clone" in cmd:
                candidate = archon_home / "app.candidate"
                candidate.mkdir(parents=True, exist_ok=True)
                (candidate / ".git").mkdir(exist_ok=True)
                scripts = candidate / "scripts"
                scripts.mkdir(exist_ok=True)
                (scripts / _PLIST_NAME).write_text(plist_src.read_text())
                return _subprocess_ok()
            if cmd[0] == "uv" and len(cmd) >= 2 and cmd[1] == "sync":
                sync_calls += 1
                if sync_calls < 3:
                    raise subprocess.CalledProcessError(1, ["uv", "sync"])
                return _subprocess_ok()
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_transient_sync), \
             patch("install.verify_running", return_value=True), \
             patch("install.time.sleep"):
            install.main(["--non-interactive"])

        assert sync_calls == 3


# ══════════════════════════════════════════════════════════════════════════════
# --tag argument validation
# ══════════════════════════════════════════════════════════════════════════════


class TestTagValidation:
    def test_valid_tag_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid semver tag does not cause sys.exit from tag validation."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        with patch("install.subprocess.run", side_effect=_make_fake_run()):
            # --dry-run avoids filesystem side effects; no SystemExit expected
            install.main(["--non-interactive", "--dry-run", "--tag", "1.0.0"])

    def test_tag_with_prerelease_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-release semver tag (e.g. 1.0.0-rc.1) is accepted."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        with patch("install.subprocess.run", side_effect=_make_fake_run()):
            # Should not raise SystemExit
            install.main(["--non-interactive", "--dry-run", "--tag", "1.0.0-rc.1"])

    def test_invalid_tag_exits_nonzero(self) -> None:
        """Non-semver tag 'latest' causes SystemExit with non-zero code."""
        with pytest.raises(SystemExit) as exc_info:
            install.main(["--non-interactive", "--dry-run", "--tag", "latest"])
        assert exc_info.value.code != 0

    def test_tag_with_v_prefix_rejected(self) -> None:
        """Tag with 'v' prefix is rejected (installer adds the prefix itself)."""
        with pytest.raises(SystemExit) as exc_info:
            install.main(["--non-interactive", "--dry-run", "--tag", "v1.0.0"])
        assert exc_info.value.code != 0


# ══════════════════════════════════════════════════════════════════════════════
# Non-interactive skips QMD prompt
# ══════════════════════════════════════════════════════════════════════════════


class TestNonInteractiveSkipsQmd:
    def test_non_interactive_does_not_call_prompt_qmd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_prompt_qmd is never called when --non-interactive is passed."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)
        (app_dir / ".git").mkdir()

        scripts_dir = app_dir / "scripts"
        scripts_dir.mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (scripts_dir / _PLIST_NAME).write_text(plist_src.read_text())

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install._prompt_qmd") as mock_prompt_qmd, \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive"])

        mock_prompt_qmd.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# _copy_helper_scripts
# ══════════════════════════════════════════════════════════════════════════════


class TestCopyHelperScripts:
    def test_scripts_copied_and_made_executable(self, tmp_path: Path) -> None:
        """All helper scripts are copied to ~/.archon/scripts/ with +x permissions."""
        app_dir = tmp_path / "app"
        scripts_src = app_dir / "scripts"
        scripts_src.mkdir(parents=True)
        for name in install._HELPER_SCRIPTS:
            (scripts_src / name).write_text(f"#!/bin/bash\necho {name}")

        archon_home = tmp_path / ".archon"
        scripts_dst = archon_home / "scripts"
        scripts_dst.mkdir(parents=True)

        install._copy_helper_scripts(app_dir, archon_home, dry_run=False, console=_quiet())

        for name in install._HELPER_SCRIPTS:
            dst = scripts_dst / name
            assert dst.exists(), f"{name} not copied"
            assert dst.stat().st_mode & 0o111, f"{name} not executable"

    def test_missing_source_script_is_skipped(self, tmp_path: Path) -> None:
        """A missing source script emits a warning but does not raise."""
        app_dir = tmp_path / "app"
        (app_dir / "scripts").mkdir(parents=True)
        # Intentionally leave all scripts absent

        archon_home = tmp_path / ".archon"
        (archon_home / "scripts").mkdir(parents=True)

        # Should not raise
        install._copy_helper_scripts(app_dir, archon_home, dry_run=False, console=_quiet())

        for name in install._HELPER_SCRIPTS:
            assert not (archon_home / "scripts" / name).exists()

    def test_dry_run_copies_nothing(self, tmp_path: Path) -> None:
        """dry_run=True prints intent but writes no files."""
        app_dir = tmp_path / "app"
        scripts_src = app_dir / "scripts"
        scripts_src.mkdir(parents=True)
        for name in install._HELPER_SCRIPTS:
            (scripts_src / name).write_text("#!/bin/bash")

        archon_home = tmp_path / ".archon"
        (archon_home / "scripts").mkdir(parents=True)

        install._copy_helper_scripts(app_dir, archon_home, dry_run=True, console=_quiet())

        for name in install._HELPER_SCRIPTS:
            assert not (archon_home / "scripts" / name).exists()

    def test_existing_script_is_overwritten(self, tmp_path: Path) -> None:
        """Re-running copy overwrites stale scripts with the latest version."""
        app_dir = tmp_path / "app"
        scripts_src = app_dir / "scripts"
        scripts_src.mkdir(parents=True)

        archon_home = tmp_path / ".archon"
        scripts_dst = archon_home / "scripts"
        scripts_dst.mkdir(parents=True)

        name = install._HELPER_SCRIPTS[0]
        (scripts_src / name).write_text("#!/bin/bash\necho new")
        (scripts_dst / name).write_text("#!/bin/bash\necho old")

        install._copy_helper_scripts(app_dir, archon_home, dry_run=False, console=_quiet())

        assert (scripts_dst / name).read_text() == "#!/bin/bash\necho new"
