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
        """Sparse clone is called with correct flags, followed by sparse-checkout set and checkout."""
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
        assert "--filter=blob:none" in clone_call, "blobless filter not set"
        assert "--no-checkout" in clone_call, "--no-checkout flag missing"
        assert any(str(tmp_path) in arg for arg in clone_call), "target path missing"

        sparse_calls = [c for c in calls if "sparse-checkout" in c]
        assert sparse_calls, "git sparse-checkout set was not called"
        sparse_call = sparse_calls[0]
        assert "set" in sparse_call, "sparse-checkout 'set' subcommand missing"
        for path in install._SPARSE_PATHS:
            assert path in sparse_call, f"sparse path '{path}' missing from sparse-checkout call"

        checkout_calls = [c for c in calls if c[-1:] == ["checkout"]]
        assert checkout_calls, "git checkout was not called after sparse-checkout"

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
        _setup_template(archon_home)

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
        _setup_template(archon_home)
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

    def test_update_path_preserves_existing_models(self, tmp_path: Path) -> None:
        """Update path must NOT replace a user-customized [models] section."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
            '[models]\navailable = ["custom-model"]\ndefault = "custom-model"\n'
        )

        install.write_config(archon_home, "token", [111], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["models"]["available"] == ["custom-model"]  # not replaced

    def test_update_path_no_model_injection_when_absent(self, tmp_path: Path) -> None:
        """Update path must NOT inject a [models] section when one is absent."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
        )

        install.write_config(archon_home, "token", [111], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert "models" not in doc, "[models] must not be injected on update"

    def test_update_path_warns_when_models_absent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Update path emits an info message when [models] is absent."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
        )
        con = install.Console(quiet=False)

        install.write_config(archon_home, "token", [111], console=con)

        out = capsys.readouterr().out
        assert "models" in out
        assert "config.toml.example" in out

    def test_token_with_special_chars_is_shell_quoted(self, tmp_path: Path) -> None:
        """Bot token containing $, !, @ is written safely to .env."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        token = "my$token!@#special"

        install.write_config(archon_home, token, [123], console=_quiet())

        env_content = (archon_home / ".env").read_text()
        quoted = shlex.quote(token.strip())
        assert f"TELEGRAM_BOT_TOKEN={quoted}" in env_content

    def test_dry_run_writes_no_files(self, tmp_path: Path) -> None:
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)

        install.write_config(archon_home, "token", [123], dry_run=True, console=_quiet())

        assert not (archon_home / ".env").exists()
        assert not (archon_home / "config.toml").exists()

    def test_write_config_update_preserves_comments(self, tmp_path: Path) -> None:
        """Update path preserves standalone section comments, standalone comment lines,
        and inline comments on non-patched keys. RED before Task 1.2 (tomli_w strips comments)."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        config_text = (
            "# Top-level section comment\n"
            "[access]\n"
            "allowed_user_ids = [111]\n"
            "\n"
            "# standalone comment line\n"
            "[notifications]\n"
            'mode = "quiet"  # notification mode\n'
        )
        (archon_home / "config.toml").write_text(config_text)

        install.write_config(archon_home, "token", [222], console=_quiet())

        result = (archon_home / "config.toml").read_text()
        assert "# Top-level section comment" in result, "standalone section comment lost"
        assert "# standalone comment line" in result, "standalone comment line lost"
        assert "# notification mode" in result, "inline comment on non-patched key lost"

    def test_write_config_update_preserves_user_keys(self, tmp_path: Path) -> None:
        """User-added keys outside [access]/[session] survive an update."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
            '[logging]\nlog_level = "DEBUG"\n'
        )

        install.write_config(archon_home, "token", [222], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["logging"]["log_level"] == "DEBUG"

    def test_write_config_update_patches_only_target_fields(self, tmp_path: Path) -> None:
        """Only allowed_user_ids and working_directory are modified; all other values unchanged."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\ninactivity_timeout_seconds = 900\n"
            "[output]\nmax_message_length = 8000\n"
            '[notifications]\nmode = "verbose"\n'
        )
        workspace_dir = str(archon_home / "workspace")

        install.write_config(archon_home, "token", [999], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["access"]["allowed_user_ids"] == [999]
        assert doc["session"]["working_directory"] == workspace_dir
        assert doc["session"]["inactivity_timeout_seconds"] == 900
        assert doc["output"]["max_message_length"] == 8000
        assert doc["notifications"]["mode"] == "verbose"

    def test_write_config_update_valid_toml_after_patch(self, tmp_path: Path) -> None:
        """Output file after update must be valid TOML."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
        )

        install.write_config(archon_home, "token", [222], console=_quiet())

        result = (archon_home / "config.toml").read_text()
        doc = tomllib.loads(result)  # must not raise
        assert doc["access"]["allowed_user_ids"] == [222]

    def test_write_config_update_windows_style_path_roundtrip(self, tmp_path: Path) -> None:
        """tomlkit correctly escapes backslashes in working_directory (Windows path round-trip)."""
        import tomlkit as _tomlkit

        doc = _tomlkit.parse(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
        )
        windows_path = "C:\\Users\\archon\\workspace"
        doc["session"]["working_directory"] = windows_path
        output = _tomlkit.dumps(doc)
        parsed = tomllib.loads(output)
        assert parsed["session"]["working_directory"] == windows_path

    def test_write_config_update_missing_access_section(self, tmp_path: Path) -> None:
        """When [access] section is absent, write_config creates it with correct allowed_user_ids."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[session]\nworking_directory = '/old'\n"
        )

        install.write_config(archon_home, "token", [444], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["access"]["allowed_user_ids"] == [444]

    def test_write_config_update_missing_session_section(self, tmp_path: Path) -> None:
        """When [session] section is absent, write_config creates it with correct working_directory."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
        )
        workspace_dir = str(archon_home / "workspace")

        install.write_config(archon_home, "token", [111], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["session"]["working_directory"] == workspace_dir

    def test_write_config_update_inline_comments_on_patched_keys(self, tmp_path: Path) -> None:
        """tomlkit preserves inline comments even on patched keys after update."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]  # whitelist\n"
            "[session]\nworking_directory = '/old'\n"
        )

        install.write_config(archon_home, "token", [222], console=_quiet())

        result = (archon_home / "config.toml").read_text()
        # tomlkit preserves inline comments on patched keys (unlike tomli_w)
        assert "# whitelist" in result
        doc = tomllib.loads(result)
        assert doc["access"]["allowed_user_ids"] == [222], "patched value should be updated"

    def test_write_config_update_models_warning(self, tmp_path: Path) -> None:
        """con.info() is called with a message about [models] when section is absent."""
        from unittest.mock import MagicMock

        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home)
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [111]\n"
            "[session]\nworking_directory = '/old'\n"
        )
        con = MagicMock(spec=install.Console)

        install.write_config(archon_home, "token", [111], console=con)

        calls = [str(c) for c in con.info.call_args_list]
        assert any("models" in c for c in calls), "Expected con.info() call mentioning 'models'"


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
# Interactive config collection — "Enter to keep" behaviour
# ══════════════════════════════════════════════════════════════════════════════


class TestCollectConfigInteractive:
    """Tests for _collect_config_interactive — existing-value "Enter to keep" paths."""

    def _make_home(self, tmp_path: Path, token: str, user_ids: list[int]) -> Path:
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text(f"TELEGRAM_BOT_TOKEN='{token}'\n")
        (archon_home / "config.toml").write_text(
            f"[access]\nallowed_user_ids = [{', '.join(str(i) for i in user_ids)}]\n"
        )
        return archon_home

    def test_enter_keeps_existing_user_ids(self, tmp_path: Path) -> None:
        """Pressing Enter for user IDs retains the existing IDs from config.toml."""
        archon_home = self._make_home(tmp_path, "existing_token", [12345, 67890])
        # quiet console: ask() always returns "" — simulates pressing Enter
        _, user_ids = install._collect_config_interactive(_quiet(), archon_home)
        assert user_ids == [12345, 67890]

    def test_enter_keeps_existing_token(self, tmp_path: Path) -> None:
        """Pressing Enter for token retains the existing token."""
        archon_home = self._make_home(tmp_path, "existing_token_abc", [11111])
        # quiet console: ask() always returns "" — simulates pressing Enter
        token, _ = install._collect_config_interactive(_quiet(), archon_home)
        assert token == "existing_token_abc"

    def test_new_user_ids_override_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Entering new user IDs replaces the existing ones."""
        archon_home = self._make_home(tmp_path, "existing_token", [11111])
        # Use real console so ask() calls input(); monkeypatch returns values per call
        responses = iter(["", "22222, 33333"])  # Enter (keep token), new IDs
        monkeypatch.setattr("builtins.input", lambda _: next(responses))
        _, user_ids = install._collect_config_interactive(install.Console(), archon_home)
        assert user_ids == [22222, 33333]

    def test_no_existing_user_ids_requires_input(self, tmp_path: Path) -> None:
        """When no existing user IDs and Enter is pressed, exits with error (code 1)."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        # Supply a token so we reach the user ID prompt; no config.toml → no existing IDs
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='tok'\n")
        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_interactive(_quiet(), archon_home)
        assert exc_info.value.code == 1

    def test_placeholder_user_id_not_kept(self, tmp_path: Path) -> None:
        """Template placeholder ID (123456789) is not offered as an existing value."""
        archon_home = self._make_home(tmp_path, "existing_token", [123456789])
        # quiet console: ask() always returns "" for all prompts
        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_interactive(_quiet(), archon_home)
        # Placeholder filtered out → falls through to fresh-input path → empty input → exit
        assert exc_info.value.code == 1

    def test_corrupted_config_toml_falls_through_to_input(self, tmp_path: Path) -> None:
        """A corrupted config.toml does not crash — falls through to require fresh input."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='tok'\n")
        (archon_home / "config.toml").write_text("this is not valid toml ][[\n")
        # quiet console returns "" → no existing IDs recovered → exits asking for fresh input
        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_interactive(_quiet(), archon_home)
        assert exc_info.value.code == 1

    def test_non_integer_user_ids_in_config_ignored(self, tmp_path: Path) -> None:
        """Non-integer values in allowed_user_ids are ignored; falls through to fresh input."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='tok'\n")
        # TOML with string values in the array — invalid for Archon
        (archon_home / "config.toml").write_text(
            '[access]\nallowed_user_ids = ["abc", "def"]\n'
        )
        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_interactive(_quiet(), archon_home)
        assert exc_info.value.code == 1

    def test_empty_user_ids_list_in_config_requires_input(self, tmp_path: Path) -> None:
        """allowed_user_ids = [] in config is not offered as "keep" — requires fresh input."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='tok'\n")
        (archon_home / "config.toml").write_text("[access]\nallowed_user_ids = []\n")
        with pytest.raises(SystemExit) as exc_info:
            install._collect_config_interactive(_quiet(), archon_home)
        assert exc_info.value.code == 1


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
            "[search]\nenabled = true\n"
        )
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        (app_dir / "scripts").mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (app_dir / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
        # examples must be in app_dir so copytree propagates it to app.candidate
        (app_dir / "examples").mkdir()
        (app_dir / "examples" / "config.toml.example").write_text(_MINIMAL_TEMPLATE)
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.input") as mock_input, \
             patch("install._offer_search_setup") as mock_offer, \
             patch("install._offer_voice_setup"), \
             patch("install.verify_running", return_value=True):
            install.main(["--update"])

        mock_input.assert_not_called()
        # _offer_search_setup is called but with non_interactive=True (skips prompt internally)
        mock_offer.assert_called_once()
        assert mock_offer.call_args.kwargs["non_interactive"] is True

    def test_update_without_rag_offers_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--update offers RAG setup when RAG is not yet enabled."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [99999]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
            "[search]\nenabled = false\n"
        )
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        (app_dir / "scripts").mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (app_dir / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
        (app_dir / "examples").mkdir()
        (app_dir / "examples" / "config.toml.example").write_text(_MINIMAL_TEMPLATE)
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install._offer_search_setup") as mock_offer, \
             patch("install._offer_voice_setup"), \
             patch("install.verify_running", return_value=True):
            install.main(["--update"])

        mock_offer.assert_called_once()
        assert mock_offer.call_args.kwargs["non_interactive"] is False

    def test_update_without_rag_section_offers_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--update offers RAG when config has no [rag] section (pre-RAG install)."""
        monkeypatch.setenv("HOME", str(tmp_path))

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
        (app_dir / "examples").mkdir()
        (app_dir / "examples" / "config.toml.example").write_text(_MINIMAL_TEMPLATE)
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install._offer_search_setup") as mock_offer, \
             patch("install._offer_voice_setup"), \
             patch("install.verify_running", return_value=True):
            install.main(["--update"])

        mock_offer.assert_called_once()

    def test_non_interactive_update_skips_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--non-interactive --update does not offer RAG even if RAG is not enabled."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        (archon_home / ".env").write_text("TELEGRAM_BOT_TOKEN='existing_token'\n")
        (archon_home / "config.toml").write_text(
            "[access]\nallowed_user_ids = [99999]\n"
            "[session]\nworking_directory = '/tmp/w'\n"
            "[search]\nenabled = false\n"
        )
        app_dir = archon_home / "app"
        app_dir.mkdir()
        (app_dir / ".git").mkdir()
        (app_dir / "scripts").mkdir()
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME
        (app_dir / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
        (app_dir / "examples").mkdir()
        (app_dir / "examples" / "config.toml.example").write_text(_MINIMAL_TEMPLATE)
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install._offer_search_setup") as mock_offer, \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--update"])

        # _offer_search_setup is called with non_interactive=True — it will skip the prompt
        mock_offer.assert_called_once()
        assert mock_offer.call_args.kwargs["non_interactive"] is True


# ══════════════════════════════════════════════════════════════════════════════
# TestSearchAlreadyEnabled
# ══════════════════════════════════════════════════════════════════════════════


class TestSearchAlreadyEnabled:
    """Unit tests for _search_already_enabled helper."""

    def test_no_config_file_returns_false(self, tmp_path: Path) -> None:
        """Returns False when config.toml does not exist."""
        assert install._search_already_enabled(tmp_path) is False

    def test_rag_enabled_true_returns_true(self, tmp_path: Path) -> None:
        """Returns True when search.enabled = true in config."""
        (tmp_path / "config.toml").write_text("[search]\nenabled = true\n")
        assert install._search_already_enabled(tmp_path) is True

    def test_rag_enabled_false_returns_false(self, tmp_path: Path) -> None:
        """Returns False when search.enabled = false in config."""
        (tmp_path / "config.toml").write_text("[search]\nenabled = false\n")
        assert install._search_already_enabled(tmp_path) is False

    def test_no_rag_section_returns_false(self, tmp_path: Path) -> None:
        """Returns False when config has no [rag] section."""
        (tmp_path / "config.toml").write_text("[access]\nallowed_user_ids = [123]\n")
        assert install._search_already_enabled(tmp_path) is False

    def test_corrupt_toml_returns_false(self, tmp_path: Path) -> None:
        """Returns False when config.toml is not valid TOML."""
        (tmp_path / "config.toml").write_text("this is not : valid = [[toml\n")
        assert install._search_already_enabled(tmp_path) is False


class TestVoiceAlreadyEnabled:
    """Unit tests for _voice_already_enabled helper."""

    def test_voice_already_enabled_true(self, tmp_path: Path) -> None:
        """Returns True when voice.enabled = true in config."""
        (tmp_path / "config.toml").write_text("[voice]\nenabled = true\n")
        assert install._voice_already_enabled(tmp_path) is True

    def test_voice_already_enabled_false(self, tmp_path: Path) -> None:
        """Returns False when config has no [voice] section."""
        (tmp_path / "config.toml").write_text("[access]\nallowed_user_ids = [123]\n")
        assert install._voice_already_enabled(tmp_path) is False

    def test_voice_already_enabled_missing_file(self, tmp_path: Path) -> None:
        """Returns False when config.toml does not exist."""
        assert install._voice_already_enabled(tmp_path) is False

    def test_voice_already_enabled_malformed_toml(self, tmp_path: Path) -> None:
        """Returns False when config.toml is not valid TOML — no exception raised."""
        (tmp_path / "config.toml").write_text("this is not : valid = [[toml\n")
        assert install._voice_already_enabled(tmp_path) is False


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
            install._do_uninstall(archon_home, dry_run=False, console=_quiet())

        assert not plist.exists()
        calls_flat = [c.args[0] for c in mock_run.call_args_list]
        assert any("unload" in cmd for cmd in calls_flat)

    def test_uninstall_removes_app_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uninstall removes app_dir (~/.archon/app) but preserves config."""
        monkeypatch.setenv("HOME", str(tmp_path))

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / _PLIST_NAME
        plist.write_text("<plist/>")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)
        (app_dir / "somefile.txt").write_text("data")
        config = archon_home / "config.toml"
        config.write_text("[session]")

        with patch("install.subprocess.run", side_effect=_make_fake_run()):
            install._do_uninstall(archon_home, dry_run=False, console=_quiet())

        assert not app_dir.exists()
        assert config.exists(), "config.toml must be preserved — uninstall only removes app_dir"

    def test_uninstall_warns_when_app_dir_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Uninstall does not raise when app_dir does not exist."""
        monkeypatch.setenv("HOME", str(tmp_path))

        archon_home = tmp_path / ".archon"
        # app_dir intentionally not created

        # Should not raise
        install._do_uninstall(archon_home, dry_run=False, console=_quiet())

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
            install._do_uninstall(archon_home, dry_run=True, console=_quiet())

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
        install._do_uninstall(archon_home, dry_run=False, console=_quiet())


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
                _add_example_to_candidate(candidate)
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
        # examples must be in app_dir so copytree propagates it to app.candidate
        (app_dir / "examples").mkdir()
        (app_dir / "examples" / "config.toml.example").write_text(_MINIMAL_TEMPLATE)

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
        _setup_template(archon_home)
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
                _add_example_to_candidate(candidate)
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
        _setup_template(archon_home)
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
                _add_example_to_candidate(candidate)
                return _subprocess_ok()
            return base_fake_run(cmd, **kw)

        with patch("install.subprocess.run", side_effect=fake_run_with_candidate), \
             patch("install.verify_running", side_effect=[False, True]):
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
        _setup_template(archon_home)
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
                _add_example_to_candidate(candidate)
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
                _add_example_to_candidate(candidate)
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

        # installer runs uv sync twice: once in app.candidate, once in app after activation
        assert sync_calls == 4


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

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.write_config"):
            # --dry-run avoids filesystem side effects; no SystemExit expected
            install.main(["--non-interactive", "--dry-run", "--tag", "1.0.0"])

    def test_tag_with_prerelease_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pre-release semver tag (e.g. 1.0.0-rc.1) is accepted."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.write_config"):
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

    def test_no_tag_skips_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Omitting --tag skips tag validation entirely (local install path)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "test_token")
        monkeypatch.setenv("ARCHON_USER_IDS", "12345")
        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.write_config"):
            install.main(["--non-interactive", "--dry-run"])  # no --tag, must not raise


class TestLocalInstall:
    """--local flag and default-local behaviour (no --tag given)."""

    def _fake_run_capturing(
        self, archon_home: Path, plist_src: Path
    ):  # type: ignore[return]
        """Return a fake subprocess.run that captures clone calls and creates candidate."""
        clone_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> object:
            if cmd[0] == "git" and "clone" in cmd:
                clone_cmds.append(cmd)
                candidate = archon_home / "app.candidate"
                candidate.mkdir(parents=True, exist_ok=True)
                (candidate / ".git").mkdir()
                scripts = candidate / "scripts"
                scripts.mkdir()
                (scripts / _PLIST_NAME).write_text(plist_src.read_text())
                _add_example_to_candidate(candidate)
                return _subprocess_ok()
            return _make_fake_run()(cmd, **kw)

        return fake_run, clone_cmds

    def test_no_tag_uses_local_clone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --tag, installer does git clone --local from cwd."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "1")
        archon_home = tmp_path / ".archon"
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        fake_run, clone_cmds = self._fake_run_capturing(archon_home, plist_src)
        with patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive"])

        assert clone_cmds, "git clone was not called"
        clone_flat = " ".join(clone_cmds[0])
        assert "--local" in clone_flat, "expected --local flag for local install"
        assert install.REPO_URL not in clone_flat, "should not use remote URL for local install"

    def test_local_flag_uses_local_clone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--local flag also triggers git clone --local from cwd."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "1")
        archon_home = tmp_path / ".archon"
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        fake_run, clone_cmds = self._fake_run_capturing(archon_home, plist_src)
        with patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--local"])

        assert clone_cmds, "git clone was not called"
        assert "--local" in " ".join(clone_cmds[0])

    def test_local_non_git_directory_falls_back_to_embedded_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running from a non-git directory without --tag falls back to __version__ as the tag."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "1")
        monkeypatch.chdir(tmp_path)  # tmp_path has no .git

        clone_cmds: list[list[str]] = []
        archon_home = tmp_path / ".archon"
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        fake_run, clone_cmds = self._fake_run_capturing(archon_home, plist_src)
        with patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive"])

        # Should have cloned from GitHub using the embedded __version__ tag
        clone_urls = [" ".join(c) for c in clone_cmds if "clone" in c]
        assert any(install.__version__ in url for url in clone_urls), (
            f"Expected clone with tag {install.__version__!r}. Got: {clone_urls}"
        )

    def test_tag_overrides_local_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Providing --tag uses GitHub clone, not local clone."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "1")
        archon_home = tmp_path / ".archon"
        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        fake_run, clone_cmds = self._fake_run_capturing(archon_home, plist_src)
        with patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--tag", "26.3.198"])

        assert clone_cmds, "git clone was not called"
        clone_flat = " ".join(clone_cmds[0])
        assert "--local" not in clone_flat, "should not use --local for tag install"
        assert "v26.3.198" in clone_flat, "pinned tag should appear in clone command"


# ══════════════════════════════════════════════════════════════════════════════
# Non-interactive skips RAG prompt
# ══════════════════════════════════════════════════════════════════════════════


class TestNonInteractiveSkipsRagPrompt:
    def test_non_interactive_does_not_prompt_for_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-interactive installs complete without prompting for RAG setup.

        This test verifies that main() runs cleanly in --non-interactive mode
        with no interactive RAG prompts.
        """
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
        # examples must be in app_dir so copytree propagates it to app.candidate
        (app_dir / "examples").mkdir()
        (app_dir / "examples" / "config.toml.example").write_text(_MINIMAL_TEMPLATE)

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive"])

        # If we reach here without AttributeError, no interactive RAG prompt was called.


# ══════════════════════════════════════════════════════════════════════════════
# Bundle scripts installation (via _install_schedules)
# ══════════════════════════════════════════════════════════════════════════════


class TestBundleScriptsInstallation:
    """health_check.sh is bundled inside schedules/health-summary/scripts/
    and is installed by _install_schedules, not by a separate helper-scripts step."""

    def test_bundle_scripts_installed_and_executable(self, tmp_path: Path) -> None:
        """Bundle scripts are copied to ~/.archon/schedules/<bundle>/scripts/ with +x."""
        app_dir = tmp_path / "app"
        bundle_scripts = app_dir / "schedules" / "health-summary" / "scripts"
        bundle_scripts.mkdir(parents=True)
        (app_dir / "schedules" / "health-summary" / "job.toml").write_text(
            "cron = '0 6 * * *'\nenabled = false\n"
            "[pipeline]\nhealth_check_tool = 'scripts/health_check.sh'\n"
        )
        (bundle_scripts / "health_check.sh").write_text("#!/bin/bash\necho health_check.sh")

        archon_home = tmp_path / ".archon"
        archon_home.mkdir()

        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())

        dst = archon_home / "schedules" / "health-summary" / "scripts" / "health_check.sh"
        assert dst.exists(), "health_check.sh not installed"
        assert dst.stat().st_mode & 0o111, "health_check.sh not executable"

    def test_bundle_scripts_not_in_archon_scripts_dir(self, tmp_path: Path) -> None:
        """Scripts are no longer copied to ~/.archon/scripts/ — they live in the bundle."""
        app_dir = tmp_path / "app"
        bundle_scripts = app_dir / "schedules" / "health-summary" / "scripts"
        bundle_scripts.mkdir(parents=True)
        (app_dir / "schedules" / "health-summary" / "job.toml").write_text(
            "cron = '0 6 * * *'\nenabled = false\n"
            "[pipeline]\nhealth_check_tool = 'scripts/health_check.sh'\n"
        )
        (bundle_scripts / "health_check.sh").write_text("#!/bin/bash")

        archon_home = tmp_path / ".archon"
        (archon_home / "scripts").mkdir(parents=True)

        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())

        assert not (archon_home / "scripts" / "health_check.sh").exists()

    def test_update_refreshes_bundle_scripts(self, tmp_path: Path) -> None:
        """On update, scripts/ inside an existing bundle are overwritten with new content."""
        app_dir = tmp_path / "app"
        bundle_scripts_src = app_dir / "schedules" / "health-summary" / "scripts"
        bundle_scripts_src.mkdir(parents=True)
        (app_dir / "schedules" / "health-summary" / "job.toml").write_text(
            "cron = '0 6 * * *'\nenabled = false\n"
        )
        (bundle_scripts_src / "health_check.sh").write_text("#!/bin/bash\n# NEW health_check.sh")

        archon_home = tmp_path / ".archon"
        # Simulate existing installation: bundle dir with old scripts and custom job.toml
        dst_bundle = archon_home / "schedules" / "health-summary"
        dst_scripts = dst_bundle / "scripts"
        dst_scripts.mkdir(parents=True)
        (dst_scripts / "health_check.sh").write_text("#!/bin/bash\n# OLD health_check.sh")
        (dst_bundle / "job.toml").write_text("cron = '0 8 * * *'\nenabled = true\n# custom")

        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())

        # Script must be refreshed with new content
        dst = dst_scripts / "health_check.sh"
        assert "# NEW health_check.sh" in dst.read_text(), "health_check.sh not refreshed"
        assert dst.stat().st_mode & 0o111, "health_check.sh not executable after update"

        # job.toml must NOT be overwritten (user customisation preserved)
        job_toml = (dst_bundle / "job.toml").read_text()
        assert "0 8 * * *" in job_toml, "job.toml cron schedule was overwritten"
        assert "# custom" in job_toml, "job.toml user comment was overwritten"

    def test_cleanup_stale_archon_scripts(self, tmp_path: Path) -> None:
        """Stale ~/.archon/scripts/ files listed in _STALE_SCRIPTS are removed on install."""
        app_dir = tmp_path / "app"
        (app_dir / "schedules").mkdir(parents=True)  # empty schedules dir

        archon_home = tmp_path / ".archon"
        stale_scripts_dir = archon_home / "scripts"
        stale_scripts_dir.mkdir(parents=True)
        stale_health = stale_scripts_dir / "health_check.sh"
        stale_health.write_text("#!/bin/bash\n# stale")
        # An unrelated file should survive
        other = stale_scripts_dir / "other_script.sh"
        other.write_text("#!/bin/bash\n# keep me")

        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())

        assert not stale_health.exists(), "stale health_check.sh was not removed"
        assert other.exists(), "unrelated script was incorrectly removed"


# ══════════════════════════════════════════════════════════════════════════════
# Linux systemd support
# ══════════════════════════════════════════════════════════════════════════════

_SERVICE_TEMPLATE = """\
[Unit]
Description=Archon Assistant
After=network.target

[Service]
Type=simple
WorkingDirectory=__ARCHON_DIR__
ExecStart=__ARCHON_DIR__/.venv/bin/python main.py
StandardOutput=append:__LOG_FILE__
StandardError=append:__LOG_FILE__
Restart=on-failure

[Install]
WantedBy=default.target
"""


def _setup_linux_app_dir(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal app dir with the systemd service template."""
    app_dir = tmp_path / ".archon" / "app"
    (app_dir / "scripts").mkdir(parents=True)
    (app_dir / "scripts" / "archon.service").write_text(_SERVICE_TEMPLATE)
    archon_home = tmp_path / ".archon"
    return app_dir, archon_home


class TestLinuxSupport:
    def test_linux_service_registered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Linux, service file is written and systemctl enable/start are called."""
        monkeypatch.setenv("HOME", str(tmp_path))

        app_dir, archon_home = _setup_linux_app_dir(tmp_path)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install.register_service(app_dir, archon_home, console=_quiet())

        # Service file written to correct location
        service_dest = tmp_path / ".config" / "systemd" / "user" / "archon.service"
        assert service_dest.exists(), "archon.service not written"
        content = service_dest.read_text()
        assert "__ARCHON_DIR__" not in content
        assert "__LOG_FILE__" not in content
        assert ".venv/bin/python" in content

        # systemctl commands called
        flat_cmds = [" ".join(c) for c in calls]
        assert any("daemon-reload" in c for c in flat_cmds), "daemon-reload not called"
        assert any("enable" in c and "archon" in c for c in flat_cmds), "enable not called"
        assert any("start" in c and "archon" in c for c in flat_cmds), "start not called"

    def test_linux_service_dry_run_no_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dry_run=True writes nothing and calls no subprocess on Linux."""
        monkeypatch.setenv("HOME", str(tmp_path))

        app_dir, archon_home = _setup_linux_app_dir(tmp_path)

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run") as mock_run:
            install.register_service(app_dir, archon_home, dry_run=True, console=_quiet())

        service_dest = tmp_path / ".config" / "systemd" / "user" / "archon.service"
        assert not service_dest.exists(), "No file should be written in dry-run"
        mock_run.assert_not_called()

    def test_linux_uninstall_stops_and_removes_service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_do_uninstall on Linux calls systemctl stop/disable and removes the unit file."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Create the service unit file
        unit_dir = tmp_path / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        unit_file = unit_dir / "archon.service"
        unit_file.write_text("[Unit]\nDescription=test\n")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._do_uninstall(archon_home, dry_run=False, console=_quiet())

        flat_cmds = [" ".join(c) for c in calls]
        assert any("stop" in c and "archon" in c for c in flat_cmds), "systemctl stop not called"
        assert any("disable" in c and "archon" in c for c in flat_cmds), "systemctl disable not called"
        assert any("daemon-reload" in c for c in flat_cmds), "daemon-reload not called after removal"
        assert not unit_file.exists(), "unit file not removed"

    def test_linux_uninstall_dry_run_no_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_do_uninstall dry_run=True makes no changes on Linux."""
        monkeypatch.setenv("HOME", str(tmp_path))

        unit_dir = tmp_path / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        unit_file = unit_dir / "archon.service"
        unit_file.write_text("[Unit]\nDescription=test\n")

        archon_home = tmp_path / ".archon"
        app_dir = archon_home / "app"
        app_dir.mkdir(parents=True)

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run") as mock_run:
            install._do_uninstall(archon_home, dry_run=True, console=_quiet())

        assert unit_file.exists(), "unit file must not be removed in dry-run"
        mock_run.assert_not_called()

    def test_main_detects_existing_linux_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Linux, main() detects an existing install via the systemd unit file."""
        monkeypatch.setenv("HOME", str(tmp_path))

        # Create existing service unit file
        unit_dir = tmp_path / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "archon.service").write_text("[Unit]\nDescription=test\n")

        mock_input = MagicMock(return_value="n")

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.input", mock_input):
            install.main([])

        # input() must have been called — the "already installed" prompt was shown
        mock_input.assert_called()


# ══════════════════════════════════════════════════════════════════════════════
# _install_workspace_templates
# ══════════════════════════════════════════════════════════════════════════════


class TestInstallWorkspaceTemplates:
    def _make_app(self, tmp_path: Path, files: dict[str, str]) -> Path:
        """Create app/workspace/ with the given filename→content mapping."""
        app_dir = tmp_path / "app"
        ws = app_dir / "workspace"
        ws.mkdir(parents=True)
        for name, content in files.items():
            (ws / name).write_text(content)
        return app_dir

    def test_copies_all_templates_when_dst_absent(self, tmp_path: Path) -> None:
        """All workspace template files are copied when none exist in the destination."""
        app_dir = self._make_app(tmp_path, {"REMINDER.md": "# Reminder", "AGENTS.md": "# Agents"})

        archon_home = tmp_path / ".archon"
        (archon_home / "workspace").mkdir(parents=True)

        install._install_workspace_templates(app_dir, archon_home, dry_run=False, console=_quiet())

        assert (archon_home / "workspace" / "REMINDER.md").read_text() == "# Reminder"
        assert (archon_home / "workspace" / "AGENTS.md").read_text() == "# Agents"

    def test_skips_existing_files(self, tmp_path: Path) -> None:
        """Files already present in the destination are never overwritten."""
        app_dir = self._make_app(tmp_path, {"REMINDER.md": "# New", "AGENTS.md": "# New"})

        archon_home = tmp_path / ".archon"
        ws_dst = archon_home / "workspace"
        ws_dst.mkdir(parents=True)
        (ws_dst / "REMINDER.md").write_text("# User reminder")
        (ws_dst / "AGENTS.md").write_text("# User agents")

        install._install_workspace_templates(app_dir, archon_home, dry_run=False, console=_quiet())

        assert (ws_dst / "REMINDER.md").read_text() == "# User reminder"
        assert (ws_dst / "AGENTS.md").read_text() == "# User agents"

    def test_copies_only_missing_files(self, tmp_path: Path) -> None:
        """Only files absent from the destination are copied; existing ones are preserved."""
        app_dir = self._make_app(tmp_path, {"REMINDER.md": "# Template", "AGENTS.md": "# Template"})

        archon_home = tmp_path / ".archon"
        ws_dst = archon_home / "workspace"
        ws_dst.mkdir(parents=True)
        (ws_dst / "REMINDER.md").write_text("# User reminder")  # exists — keep
        # AGENTS.md absent — should be copied

        install._install_workspace_templates(app_dir, archon_home, dry_run=False, console=_quiet())

        assert (ws_dst / "REMINDER.md").read_text() == "# User reminder"
        assert (ws_dst / "AGENTS.md").read_text() == "# Template"

    def test_skips_when_src_dir_absent(self, tmp_path: Path) -> None:
        """Missing app/workspace/ directory does not raise; emits a warning instead."""
        app_dir = tmp_path / "app"
        app_dir.mkdir(parents=True)  # no workspace/ inside

        archon_home = tmp_path / ".archon"
        (archon_home / "workspace").mkdir(parents=True)

        install._install_workspace_templates(app_dir, archon_home, dry_run=False, console=_quiet())

        assert list((archon_home / "workspace").iterdir()) == []

    def test_dry_run_copies_nothing(self, tmp_path: Path) -> None:
        """dry_run=True logs intent but writes no files."""
        app_dir = self._make_app(tmp_path, {"REMINDER.md": "# Template", "AGENTS.md": "# Template"})

        archon_home = tmp_path / ".archon"
        (archon_home / "workspace").mkdir(parents=True)

        install._install_workspace_templates(app_dir, archon_home, dry_run=True, console=_quiet())

        assert list((archon_home / "workspace").iterdir()) == []


# ══════════════════════════════════════════════════════════════════════════════
# _default_config — [models] section
# ══════════════════════════════════════════════════════════════════════════════


class TestDefaultConfigModels:
    def test_default_config_contains_models_section(self, tmp_path: Path) -> None:
        """Fresh install from real example must include a [models] section matching constants.py."""
        from archon.ai.constants import AVAILABLE_MODELS, DEFAULT_MODEL

        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home, _REAL_EXAMPLE.read_text())

        install.write_config(archon_home, "token", [123], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert "models" in doc, "[models] section missing from fresh install config"
        assert doc["models"]["available"] == AVAILABLE_MODELS
        assert doc["models"]["default"] == DEFAULT_MODEL


# ══════════════════════════════════════════════════════════════════════════════
# write_config — template-based fresh install (Task 1.2)
# ══════════════════════════════════════════════════════════════════════════════

_REAL_EXAMPLE = Path(__file__).parent.parent / "examples" / "config.toml.example"

_MINIMAL_TEMPLATE = """\
[access]
allowed_user_ids = [123456789]

[session]
working_directory = "~/.archon/workspace"
inactivity_timeout_seconds = 1800

[output]
max_message_length = 4000
truncation_strategy = "split"

[notifications]
mode = "normal"

[history]
enabled = true

[logging]
log_file = "~/.archon/logs/archon.log"
log_level = "INFO"

[models]
available = ["claude-sonnet-4-6", "claude-haiku-4-5"]
default = "claude-sonnet-4-6"

[search]
enabled = false

[schedule]
enabled = true

[background_agents]
spawn_rule = "auto"

[voice]
enabled = false

[reminder]
enabled = true
"""


def _setup_template(archon_home: Path, template_text: str = _MINIMAL_TEMPLATE) -> Path:
    """Create the template at the expected path under app.candidate."""
    template_path = archon_home / "app.candidate" / "examples"
    template_path.mkdir(parents=True, exist_ok=True)
    example = template_path / "config.toml.example"
    example.write_text(template_text)
    return example


def _add_example_to_candidate(candidate: Path) -> None:
    """Create examples/config.toml.example inside a (fake) cloned candidate dir."""
    examples = candidate / "examples"
    examples.mkdir(parents=True, exist_ok=True)
    (examples / "config.toml.example").write_text(_MINIMAL_TEMPLATE)


class TestWriteConfigFreshInstallTemplate:
    def test_write_config_fresh_install_uses_template(self, tmp_path: Path) -> None:
        """Fresh install reads the example template and substitutes user_ids + workspace_dir."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home, _MINIMAL_TEMPLATE)

        install.write_config(archon_home, "token", [111], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert doc["access"]["allowed_user_ids"] == [111]
        expected_workspace = str(archon_home / "workspace")
        assert doc["session"]["working_directory"] == expected_workspace

    def test_write_config_fresh_install_contains_all_sections(self, tmp_path: Path) -> None:
        """Fresh install from real example contains all expected section headers."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home, _REAL_EXAMPLE.read_text())

        install.write_config(archon_home, "token", [42], console=_quiet())

        written = (archon_home / "config.toml").read_text()
        expected_sections = [
            "[access]",
            "[session]",
            "[output]",
            "[logging]",
            "[notifications]",
            "[history]",
            "[models]",
            "[plugins]",
            "[search]",
            "[schedule]",
            "[background_agents]",
            "[voice]",
            "[reminder]",
        ]
        for section in expected_sections:
            assert section in written, f"Section {section!r} missing from written config"

    def test_write_config_fresh_install_no_sentinel_remaining(self, tmp_path: Path) -> None:
        """Fresh install must not contain the template placeholder values."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home, _REAL_EXAMPLE.read_text())

        install.write_config(archon_home, "token", [999], console=_quiet())

        written = (archon_home / "config.toml").read_text()
        assert "123456789" not in written, "Placeholder user_id still in written config"
        # working_directory placeholder must be substituted; other occurrences (e.g. RAG
        # collections) are legitimate config values and may remain.
        assert 'working_directory = "~/.archon/workspace"' not in written, (
            "Placeholder working_directory still in written config"
        )

    def test_write_config_fresh_install_produces_valid_toml(self, tmp_path: Path) -> None:
        """Fresh install output must parse as valid TOML without errors."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home, _REAL_EXAMPLE.read_text())

        install.write_config(archon_home, "token", [777], console=_quiet())

        written = (archon_home / "config.toml").read_text()
        doc = tomllib.loads(written)  # must not raise
        assert "access" in doc

    def test_write_config_fresh_install_models_list(self, tmp_path: Path) -> None:
        """Fresh install from real example has correct models.available and models.default."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        _setup_template(archon_home, _REAL_EXAMPLE.read_text())

        install.write_config(archon_home, "token", [42], console=_quiet())

        doc = tomllib.loads((archon_home / "config.toml").read_text())
        assert "models" in doc
        assert doc["models"]["available"] == ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5"]
        assert doc["models"]["default"] == "claude-sonnet-4-6"

    def test_write_config_fresh_install_missing_example_raises(self, tmp_path: Path) -> None:
        """write_config raises FileNotFoundError when config.toml.example is missing."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        # Do NOT create the template

        with pytest.raises(FileNotFoundError, match="config.toml.example not found"):
            install.write_config(archon_home, "token", [123], console=_quiet())

    def test_write_config_dry_run_missing_example_raises(self, tmp_path: Path) -> None:
        """dry_run also validates template existence and raises FileNotFoundError."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        # Do NOT create the template

        with pytest.raises(FileNotFoundError, match="config.toml.example not found"):
            install.write_config(archon_home, "token", [123], dry_run=True, console=_quiet())

    def test_write_config_update_path_raises_when_template_missing(
        self, tmp_path: Path
    ) -> None:
        """write_config raises FileNotFoundError on update path when template is missing."""
        archon_home = tmp_path / ".archon"
        archon_home.mkdir()
        # Create existing config.toml — this makes it an update, not a fresh install
        (archon_home / "config.toml").write_text("[access]\nallowed_user_ids = [999]\n")
        # Do NOT create the template at app.candidate/examples/config.toml.example

        with pytest.raises(FileNotFoundError):
            install.write_config(archon_home, "token", [123], dry_run=False, console=_quiet())

    def test_sparse_paths_includes_examples(self) -> None:
        """_SPARSE_PATHS must contain 'examples' (not the flat 'config.toml.example')."""
        assert "examples" in install._SPARSE_PATHS
        assert "config.toml.example" not in install._SPARSE_PATHS


# ══════════════════════════════════════════════════════════════════════════════
# T39 — Guard plist unload on Linux
# ══════════════════════════════════════════════════════════════════════════════


class TestT39PlistUnloadGuard:
    """The pre-activation plist unload in main() must NOT run on Linux."""

    def test_linux_no_launchctl_before_activation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On mocked Linux, launchctl is never called during the pre-activation unload."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "123")
        monkeypatch.setenv("USER", "testuser")

        # Create a plist file — on Linux this must be ignored
        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / _PLIST_NAME
        plist.write_text("<plist/>")

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            if cmd[0] == "git" and "clone" in cmd:
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir(exist_ok=True)
                (target / "scripts").mkdir(parents=True, exist_ok=True)
                (target / "scripts" / "archon.service").write_text(_SERVICE_TEMPLATE)
                _add_example_to_candidate(target)
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.check_prerequisites"), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--tag", "1.0.0"])

        # Verify no launchctl commands were issued
        assert not any(cmd[0] == "launchctl" for cmd in calls), \
            "launchctl must not be called on Linux"

    def test_macos_launchctl_called_normally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On macOS, launchctl unload is called when the plist exists before activation."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "123")

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        plist = launch_agents / _PLIST_NAME
        plist.write_text("<plist/>")

        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            if cmd[0] == "git" and "clone" in cmd:
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir(exist_ok=True)
                (target / "scripts").mkdir(parents=True, exist_ok=True)
                (target / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
                _add_example_to_candidate(target)
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.check_prerequisites"), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--tag", "1.0.0"])

        assert any(cmd[0] == "launchctl" and "unload" in cmd for cmd in calls), \
            "launchctl unload must be called on macOS when plist exists"


# ══════════════════════════════════════════════════════════════════════════════
# T40 — Platform-conditional error messages
# ══════════════════════════════════════════════════════════════════════════════


class TestT40ErrorMessages:
    """Error/remediation messages must be platform-appropriate."""

    def test_rollback_failure_message_macos(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On macOS, rollback failure message mentions launchd, not systemctl."""
        monkeypatch.setenv("HOME", str(tmp_path))
        archon_home = tmp_path / ".archon"
        paths = install._paths(archon_home)
        # No previous version — rollback fails
        paths.app.mkdir(parents=True)

        with patch("install.platform.system", return_value="Darwin"):
            result = install._rollback_activation(paths, install.Console(), dry_run=False)

        assert result is False
        captured = capsys.readouterr()
        assert "launchd" in captured.err or "launchctl" in captured.err

    def test_rollback_failure_message_linux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On Linux, rollback failure message mentions systemctl, not launchctl."""
        monkeypatch.setenv("HOME", str(tmp_path))
        archon_home = tmp_path / ".archon"
        paths = install._paths(archon_home)
        paths.app.mkdir(parents=True)

        with patch("install.platform.system", return_value="Linux"):
            result = install._rollback_activation(paths, install.Console(), dry_run=False)

        assert result is False
        captured = capsys.readouterr()
        assert "systemd" in captured.err or "systemctl" in captured.err

    def test_health_check_rollback_message_macos(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The remediation message at line ~1048 must mention launchctl on macOS."""
        con = install.Console()
        with patch("install.platform.system", return_value="Darwin"):
            msg = install._remediation_message(Path.home() / ".archon")
        assert "launchctl" in msg or "launchd" in msg

    def test_health_check_rollback_message_linux(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The remediation message at line ~1048 must mention systemctl on Linux."""
        con = install.Console()
        with patch("install.platform.system", return_value="Linux"):
            msg = install._remediation_message(Path.home() / ".archon")
        assert "systemctl" in msg


# ══════════════════════════════════════════════════════════════════════════════
# T40a — _do_uninstall() platform bugs
# ══════════════════════════════════════════════════════════════════════════════


class TestT40aDoUninstallPlatform:
    """_do_uninstall() must use correct commands per platform."""

    def test_linux_uninstall_no_launchctl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On Linux, _do_uninstall never calls launchctl."""
        monkeypatch.setenv("HOME", str(tmp_path))

        unit_dir = tmp_path / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "archon.service").write_text("[Unit]\n")

        archon_home = tmp_path / ".archon"
        (archon_home / "app").mkdir(parents=True)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._do_uninstall(archon_home, dry_run=False, console=_quiet())

        assert not any(cmd[0] == "launchctl" for cmd in calls), \
            "launchctl must not be called on Linux"
        assert any(cmd[0] == "systemctl" for cmd in calls), \
            "systemctl commands must be used on Linux"

    def test_macos_uninstall_no_systemctl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On macOS, _do_uninstall never calls systemctl."""
        monkeypatch.setenv("HOME", str(tmp_path))

        launch_agents = tmp_path / "Library" / "LaunchAgents"
        launch_agents.mkdir(parents=True)
        (launch_agents / _PLIST_NAME).write_text("<plist/>")

        archon_home = tmp_path / ".archon"
        (archon_home / "app").mkdir(parents=True)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._do_uninstall(archon_home, dry_run=False, console=_quiet())

        assert not any(cmd[0] == "systemctl" for cmd in calls), \
            "systemctl must not be called on macOS"
        assert any(cmd[0] == "launchctl" for cmd in calls), \
            "launchctl must be used on macOS"


# ══════════════════════════════════════════════════════════════════════════════
# T41 — loginctl enable-linger on Linux install
# ══════════════════════════════════════════════════════════════════════════════


class TestT41LoginctlEnableLinger:
    """register_service() on Linux must call loginctl enable-linger."""

    def test_linger_called_on_linux(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """loginctl enable-linger is called after systemctl enable on Linux."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USER", "testuser")

        app_dir, archon_home = _setup_linux_app_dir(tmp_path)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install.register_service(app_dir, archon_home, console=_quiet())

        assert any(cmd[0] == "loginctl" and "enable-linger" in cmd for cmd in calls), \
            "loginctl enable-linger must be called on Linux"

    def test_linger_failure_is_warning_not_abort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """If loginctl enable-linger fails, install continues with a warning."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USER", "testuser")

        app_dir, archon_home = _setup_linux_app_dir(tmp_path)

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            if cmd[0] == "loginctl":
                raise subprocess.CalledProcessError(1, cmd)
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run):
            # Must NOT raise
            install.register_service(app_dir, archon_home, console=install.Console())

        captured = capsys.readouterr()
        assert "linger" in captured.out.lower() or "linger" in captured.err.lower(), \
            "Warning about linger failure must be shown"


# ══════════════════════════════════════════════════════════════════════════════
# T42 — Integration tests
# ══════════════════════════════════════════════════════════════════════════════


class TestT42Integration:
    """Full install/uninstall flow integration tests with mocked subprocess."""

    def test_full_linux_install_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full Linux install: systemd commands in correct order + linger."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "123")
        monkeypatch.setenv("USER", "testuser")

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            if cmd[0] == "git" and "clone" in cmd:
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir(exist_ok=True)
                (target / "scripts").mkdir(parents=True, exist_ok=True)
                (target / "scripts" / "archon.service").write_text(_SERVICE_TEMPLATE)
                _add_example_to_candidate(target)
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.check_prerequisites"), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--tag", "1.0.0"])

        # Verify systemd commands in order
        svc_cmds = [cmd for cmd in calls if cmd[0] in ("systemctl", "loginctl")]
        assert len(svc_cmds) >= 4, f"Expected >=4 systemd/loginctl commands, got: {svc_cmds}"

        # daemon-reload before enable before start
        reload_idx = next(i for i, cmd in enumerate(calls) if "daemon-reload" in cmd)
        enable_idx = next(i for i, cmd in enumerate(calls) if "enable" in cmd and "archon" in cmd)
        start_idx = next(i for i, cmd in enumerate(calls) if "start" in cmd and "archon" in cmd)
        assert reload_idx < enable_idx < start_idx, \
            "Commands must be: daemon-reload -> enable -> start"

        # loginctl enable-linger called after systemctl start
        linger_idx = next(i for i, cmd in enumerate(calls) if cmd[0] == "loginctl" and "enable-linger" in cmd)
        assert linger_idx > start_idx, \
            "loginctl enable-linger must be called after systemctl start"

        # No launchctl at all
        assert not any(cmd[0] == "launchctl" for cmd in calls), \
            "launchctl must not appear in Linux install flow"

    def test_full_linux_uninstall_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full Linux uninstall: correct systemctl stop/disable/remove, no launchctl."""
        monkeypatch.setenv("HOME", str(tmp_path))

        unit_dir = tmp_path / ".config" / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "archon.service").write_text("[Unit]\n")

        archon_home = tmp_path / ".archon"
        (archon_home / "app").mkdir(parents=True)

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install.main(["--uninstall"])

        assert any(cmd[0] == "systemctl" and "stop" in cmd for cmd in calls)
        assert any(cmd[0] == "systemctl" and "disable" in cmd for cmd in calls)
        assert any(cmd[0] == "systemctl" and "daemon-reload" in cmd for cmd in calls)
        assert not any(cmd[0] == "launchctl" for cmd in calls)
        assert not (unit_dir / "archon.service").exists()

    def test_macos_install_regression(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """macOS install still uses launchctl and no systemctl."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "123")

        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            calls.append(list(cmd))
            if cmd[0] == "git" and "clone" in cmd:
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir(exist_ok=True)
                (target / "scripts").mkdir(parents=True, exist_ok=True)
                (target / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
                _add_example_to_candidate(target)
            return _subprocess_ok()

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.check_prerequisites"), \
             patch("install.verify_running", return_value=True):
            install.main(["--non-interactive", "--tag", "1.0.0"])

        assert any(cmd[0] == "launchctl" and "load" in cmd for cmd in calls), \
            "launchctl load must be called on macOS"
        assert not any(cmd[0] == "systemctl" for cmd in calls), \
            "systemctl must not appear in macOS install flow"


# ── _install_schedules with bundle support ────────────────────────


class TestInstallSchedulesBundles:
    """Tests for _install_schedules() with directory-based bundles."""

    def test_copies_bundle_directories(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        bundle = app_dir / "schedules" / "myjob"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\nenabled = false\n\n[pipeline]\necho_tool = "echo x"\n')
        archon_home = tmp_path / "archon"
        archon_home.mkdir()
        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())
        dst = archon_home / "schedules" / "myjob" / "job.toml"
        assert dst.exists()

    def test_flat_files_backward_compat(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        sched = app_dir / "schedules"
        sched.mkdir(parents=True)
        (sched / "flat.toml").write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo x"\n')
        archon_home = tmp_path / "archon"
        archon_home.mkdir()
        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())
        assert (archon_home / "schedules" / "flat.toml").exists()

    def test_preserves_executable_bits(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        bundle = app_dir / "schedules" / "myjob"
        scripts = bundle / "scripts"
        scripts.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\nenabled = false\n\n[pipeline]\nrun_tool = "scripts/go.sh"\n')
        script = scripts / "go.sh"
        script.write_text("#!/bin/bash\necho hi")
        script.chmod(0o644)  # source NOT executable
        archon_home = tmp_path / "archon"
        archon_home.mkdir()
        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())
        installed = archon_home / "schedules" / "myjob" / "scripts" / "go.sh"
        assert installed.stat().st_mode & 0o755 == 0o755

    def test_rewrites_enabled_in_bundle(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        bundle = app_dir / "schedules" / "myjob"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\nenabled = false\n\n[pipeline]\necho_tool = "echo x"\n')
        archon_home = tmp_path / "archon"
        archon_home.mkdir()
        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())
        content = (archon_home / "schedules" / "myjob" / "job.toml").read_text()
        assert "enabled = true" in content

    def test_skips_existing_bundle(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        bundle = app_dir / "schedules" / "existing"
        bundle.mkdir(parents=True)
        (bundle / "job.toml").write_text('cron = "* * * * *"\n\n[pipeline]\necho_tool = "echo new"\n')
        archon_home = tmp_path / "archon"
        dst = archon_home / "schedules" / "existing"
        dst.mkdir(parents=True)
        (dst / "job.toml").write_text('cron = "0 6 * * *"\n\n[pipeline]\necho_tool = "echo old"\n')
        install._install_schedules(app_dir, archon_home, dry_run=False, console=_quiet())
        content = (archon_home / "schedules" / "existing" / "job.toml").read_text()
        assert "echo old" in content  # not overwritten


# ── _install_skills ───────────────────────────────────────────────


class TestInstallSkills:
    """Tests for _install_skills() — copies skill dirs to workspace/.claude/skills/."""

    def test_copies_skill_directory(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        skill = app_dir / "skills" / "archon-schedule"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: archon-schedule\n---\n# Skill")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        install._install_skills(app_dir, workspace, dry_run=False, console=_quiet())
        dst = workspace / ".claude" / "skills" / "archon-schedule" / "SKILL.md"
        assert dst.exists()
        assert "archon-schedule" in dst.read_text()

    def test_skips_existing_skill(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        skill = app_dir / "skills" / "archon-schedule"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# New version")
        workspace = tmp_path / "workspace"
        dst = workspace / ".claude" / "skills" / "archon-schedule"
        dst.mkdir(parents=True)
        (dst / "SKILL.md").write_text("# User version")
        install._install_skills(app_dir, workspace, dry_run=False, console=_quiet())
        assert (dst / "SKILL.md").read_text() == "# User version"

    def test_skips_dirs_without_skill_md(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        bad = app_dir / "skills" / "not-a-skill"
        bad.mkdir(parents=True)
        (bad / "random.txt").write_text("hello")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        install._install_skills(app_dir, workspace, dry_run=False, console=_quiet())
        assert not (workspace / ".claude" / "skills" / "not-a-skill").exists()

    def test_skips_when_src_dir_absent(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        install._install_skills(app_dir, workspace, dry_run=False, console=_quiet())
        assert not (workspace / ".claude").exists()

    def test_dry_run_copies_nothing(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        skill = app_dir / "skills" / "archon-schedule"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# Skill")
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        install._install_skills(app_dir, workspace, dry_run=True, console=_quiet())
        assert not (workspace / ".claude").exists()


# ══════════════════════════════════════════════════════════════════════════════
# _render_config_template
# ══════════════════════════════════════════════════════════════════════════════


class TestRenderConfigTemplate:
    _TEMPLATE = """\
[access]
allowed_user_ids = [999]

[session]
working_directory = "/old/path"
inactivity_timeout_seconds = 1800

[output]
max_message_length = 4000
"""

    def test_render_config_template_replaces_user_ids_line(self) -> None:
        result = install._render_config_template(self._TEMPLATE, [123456], Path("/home/user/work"))
        assert "allowed_user_ids = [123456]" in result
        # C1-T-09: old placeholder value must be gone
        assert "allowed_user_ids = [999]" not in result
        # C1-T-02: output must be valid TOML
        tomllib.loads(result)

    def test_render_config_template_replaces_working_directory_line(self) -> None:
        result = install._render_config_template(self._TEMPLATE, [1], Path("/home/user/work"))
        assert 'working_directory = "/home/user/work"' in result
        # C1-T-09: old placeholder value must be gone
        assert 'working_directory = "/old/path"' not in result

    def test_render_config_template_preserves_other_lines(self) -> None:
        result = install._render_config_template(self._TEMPLATE, [1], Path("/tmp/w"))
        assert "inactivity_timeout_seconds = 1800" in result
        assert "max_message_length = 4000" in result

    def test_render_config_template_multiple_user_ids(self) -> None:
        result = install._render_config_template(self._TEMPLATE, [1, 2], Path("/tmp/w"))
        assert "allowed_user_ids = [1, 2]" in result

    def test_render_config_template_preserves_comments(self) -> None:
        template = "# top comment\n" + self._TEMPLATE
        result = install._render_config_template(template, [1], Path("/tmp/w"))
        assert "# top comment" in result

    def test_render_config_template_passthrough_when_user_ids_line_absent(self) -> None:
        # C1-T-01: template without allowed_user_ids line — re.sub silently no-ops for that key, no crash.
        # Task 1.2 guarantees valid templates before calling this function, so pass-through is acceptable.
        template_no_ids = "[session]\nworking_directory = \"/old/path\"\n"
        result = install._render_config_template(template_no_ids, [42], Path("/tmp/w"))
        # The working_directory substitution still runs; allowed_user_ids silently no-ops.
        assert 'working_directory = "/tmp/w"' in result
        assert "allowed_user_ids" not in result

    def test_render_config_template_passthrough_when_working_directory_line_absent(self) -> None:
        # C1-T-01: template without working_directory line — re.sub silently no-ops for that key, no crash.
        template_no_wd = "[access]\nallowed_user_ids = [999]\n"
        result = install._render_config_template(template_no_wd, [42], Path("/tmp/w"))
        # The allowed_user_ids substitution still runs; working_directory silently no-ops.
        assert "allowed_user_ids = [42]" in result
        assert "working_directory" not in result

    def test_render_config_template_raises_on_empty_user_ids(self) -> None:
        with pytest.raises(ValueError, match="user_ids must not be empty"):
            install._render_config_template(self._TEMPLATE, [], Path("/tmp/w"))

    def test_render_config_template_escapes_windows_backslashes(self) -> None:
        result = install._render_config_template(
            self._TEMPLATE, [1], Path("C:\\Users\\test\\work")
        )
        # The result must be valid TOML
        parsed = tomllib.loads(result)
        assert parsed["session"]["working_directory"] == "C:\\Users\\test\\work"


# ══════════════════════════════════════════════════════════════════════════════
# Post-install RAG guidance (Task A.1)
# ══════════════════════════════════════════════════════════════════════════════


class TestPostInstallRagGuidance:
    """RAG setup is offered interactively after a successful install."""

    def _make_paths(self, tmp_path: Path) -> install.InstallerPaths:
        archon_home = tmp_path / ".archon"
        paths = install._paths(archon_home)
        archon_bin = paths.app / ".venv" / "bin" / "archon"
        archon_bin.parent.mkdir(parents=True, exist_ok=True)
        archon_bin.write_text("#!/bin/sh\necho archon")
        archon_bin.chmod(0o755)
        return paths

    def test_interactive_install_prompts_for_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_offer_search_setup calls input() with a RAG-related prompt."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        with patch("install.input", return_value="n") as mock_input, \
             patch("install.subprocess.run"):
            install._offer_search_setup(paths, console, non_interactive=False)

        mock_input.assert_called_once()
        prompt_text = mock_input.call_args[0][0]
        assert "RAG" in prompt_text or "semantic" in prompt_text.lower(), (
            f"Unexpected prompt: {prompt_text!r}"
        )

    def test_user_confirms_rag_runs_archon_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Confirming RAG runs rag install, config set, and restart."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            subprocess_calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_search_setup(paths, console, non_interactive=False)

        cmd_strings = [" ".join(c) for c in subprocess_calls]
        assert any("search" in s and "install" in s for s in cmd_strings), (
            f"archon search install not called. calls={cmd_strings}"
        )
        assert any("config" in s and "search.enabled" in s for s in cmd_strings), (
            f"archon config set search.enabled not called. calls={cmd_strings}"
        )
        assert any("restart" in s for s in cmd_strings), (
            f"archon restart not called. calls={cmd_strings}"
        )
        # Verify ordering: rag install → config set → restart
        rag_idx = next(i for i, s in enumerate(cmd_strings) if "search" in s and "install" in s)
        cfg_idx = next(i for i, s in enumerate(cmd_strings) if "config" in s and "search.enabled" in s)
        restart_idx = next(i for i, s in enumerate(cmd_strings) if "restart" in s)
        assert rag_idx < cfg_idx < restart_idx, (
            f"Command ordering violated: search={rag_idx}, config={cfg_idx}, restart={restart_idx}"
        )

    def test_user_declines_rag_skips_commands(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Declining RAG skips the rag install, config set, and restart commands."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        with patch("install.input", return_value="n"), \
             patch("install.subprocess.run") as mock_run:
            install._offer_search_setup(paths, console, non_interactive=False)

        mock_run.assert_not_called()

    def test_offer_search_setup_prints_status_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Success path prints the status hint message."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", return_value=_subprocess_ok()):
            install._offer_search_setup(paths, console, non_interactive=False)

        captured = capsys.readouterr().out
        assert "archon search status" in captured
        assert "Indexing in background" in captured

    def test_non_interactive_skips_rag_prompt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """non_interactive=True skips the RAG prompt entirely."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        with patch("install.input") as mock_input, \
             patch("install.subprocess.run") as mock_run:
            install._offer_search_setup(paths, console, non_interactive=True)

        mock_input.assert_not_called()
        mock_run.assert_not_called()

    def test_dry_run_skips_rag_setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--dry-run does not invoke _offer_search_setup (service never actually started)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("ARCHON_BOT_TOKEN", "tok")
        monkeypatch.setenv("ARCHON_USER_IDS", "123")

        with patch("install.subprocess.run", side_effect=_make_fake_run()), \
             patch("install.write_config"), \
             patch("install._offer_search_setup") as mock_offer:
            install.main(["--non-interactive", "--dry-run", "--tag", "1.0.0"])

        mock_offer.assert_not_called()

    def test_main_calls_offer_search_setup_on_fresh_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """main() calls _offer_search_setup after a successful interactive install."""
        monkeypatch.setenv("HOME", str(tmp_path))

        plist_src = _REPO_ROOT / "scripts" / _PLIST_NAME

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            if cmd[0] == "git" and "clone" in cmd:
                target = Path(cmd[-1])
                target.mkdir(parents=True, exist_ok=True)
                (target / ".git").mkdir(exist_ok=True)
                (target / "scripts").mkdir(parents=True, exist_ok=True)
                (target / "scripts" / _PLIST_NAME).write_text(plist_src.read_text())
                _add_example_to_candidate(target)
            return _subprocess_ok()

        # Provide interactive input values: bot token, user IDs
        input_values = iter(["tok", "123"])
        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", side_effect=fake_run), \
             patch("install.check_prerequisites"), \
             patch("install.verify_running", return_value=True), \
             patch("install.input", side_effect=lambda _: next(input_values)), \
             patch("install._offer_search_setup") as mock_offer, \
             patch("install._offer_voice_setup"):
            install.main(["--tag", "1.0.0"])

        mock_offer.assert_called_once()
        assert mock_offer.call_args.kwargs["non_interactive"] is False

    def test_rag_install_failure_skips_config_and_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If rag install fails, config set and restart are not called."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            subprocess_calls.append(list(cmd))
            m = MagicMock()
            # First call (rag install) fails
            m.returncode = 1 if ("search" in cmd and "install" in cmd) else 0
            return m

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_search_setup(paths, console, non_interactive=False)

        cmd_strings = [" ".join(c) for c in subprocess_calls]
        assert not any("config" in s for s in cmd_strings), (
            f"config set should not be called after search install failure. calls={cmd_strings}"
        )
        assert not any("restart" in s for s in cmd_strings), (
            f"restart should not be called after search install failure. calls={cmd_strings}"
        )

    def test_config_set_failure_skips_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If config set fails, restart is not called."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            subprocess_calls.append(list(cmd))
            m = MagicMock()
            # config set fails, everything else succeeds
            m.returncode = 1 if "config" in cmd else 0
            return m

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_search_setup(paths, console, non_interactive=False)

        cmd_strings = [" ".join(c) for c in subprocess_calls]
        assert any("search" in s and "install" in s for s in cmd_strings), (
            f"search install should have been called. calls={cmd_strings}"
        )
        assert not any("restart" in s for s in cmd_strings), (
            f"restart should not be called after config set failure. calls={cmd_strings}"
        )

    def test_eof_during_prompt_skips_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EOFError from input() (piped stdin / CI) is handled gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        with patch("install.input", side_effect=EOFError), \
             patch("install.subprocess.run") as mock_run:
            install._offer_search_setup(paths, console, non_interactive=False)

        mock_run.assert_not_called()

    def test_keyboard_interrupt_during_prompt_skips_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KeyboardInterrupt from input() is handled gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        with patch("install.input", side_effect=KeyboardInterrupt), \
             patch("install.subprocess.run") as mock_run:
            install._offer_search_setup(paths, console, non_interactive=False)

        mock_run.assert_not_called()

    def test_search_install_invoked_with_non_interactive_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_offer_search_setup passes --non-interactive to 'archon search install'.

        This is the mechanism that makes the install path non-blocking:
        SearchInstaller.run(non_interactive=True) returns as soon as the service is
        ready — it does NOT call _bootstrap_collections or block on indexing.
        A missing --non-interactive flag would prompt for input and stall the installer.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        captured_cmds: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            captured_cmds.append(list(cmd))
            return _subprocess_ok()

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_search_setup(paths, console, non_interactive=False)

        # Find the 'archon search install' subprocess call
        search_install_cmds = [c for c in captured_cmds if "search" in c and "install" in c]
        assert search_install_cmds, f"'archon search install' was not called. calls={captured_cmds}"

        search_install_cmd = search_install_cmds[0]
        assert "--non-interactive" in search_install_cmd, (
            f"'archon search install' must include --non-interactive to avoid blocking on indexing. "
            f"cmd={search_install_cmd}"
        )

    def test_offer_search_setup_success_message_signals_background_indexing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """On success, _offer_search_setup prints a message mentioning background indexing,
        and makes exactly 3 subprocess calls (search install, config set, restart) — no extra
        blocking sync step injected between service start and function return.
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            subprocess_calls.append(list(cmd))
            return _subprocess_ok()

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_search_setup(paths, console, non_interactive=False)

        captured = capsys.readouterr().out

        # Success message must mention background indexing — not "done" or "complete"
        assert "background" in captured.lower(), (
            f"Success message must indicate background (non-blocking) indexing. output={captured!r}"
        )

        # Exactly 3 subprocess calls: search install, config set, restart — no extra blocking sync step
        assert len(subprocess_calls) == 3, (
            f"Expected exactly 3 subprocess calls (search install → config set → restart), "
            f"got {len(subprocess_calls)}: {subprocess_calls}"
        )

    def test_oserror_during_rag_install_skips_remaining_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError from subprocess.run (e.g., permission denied) is handled gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)
        console = install.Console()

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            subprocess_calls.append(list(cmd))
            raise OSError("Permission denied")

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_search_setup(paths, console, non_interactive=False)

        # Only the first call should have been attempted before OSError was raised
        assert len(subprocess_calls) == 1

    def test_missing_archon_binary_skips_rag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If archon binary not found, RAG setup is skipped gracefully."""
        monkeypatch.setenv("HOME", str(tmp_path))
        archon_home = tmp_path / ".archon"
        paths = install._paths(archon_home)
        # Intentionally do NOT create the archon binary
        console = install.Console()

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run") as mock_run:
            install._offer_search_setup(paths, console, non_interactive=False)

        mock_run.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# _offer_voice_setup
# ══════════════════════════════════════════════════════════════════════════════


class TestOfferVoiceSetup:
    """_offer_voice_setup offers voice install after a successful main() run."""

    def _make_paths(self, tmp_path: Path) -> install.InstallerPaths:
        archon_home = tmp_path / ".archon"
        paths = install._paths(archon_home)
        archon_bin = paths.app / ".venv" / "bin" / "archon"
        archon_bin.parent.mkdir(parents=True, exist_ok=True)
        archon_bin.write_text("#!/bin/sh\necho archon")
        archon_bin.chmod(0o755)
        return paths

    def test_offer_voice_setup_non_interactive_skips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """non_interactive=True -> console.ask is NOT called."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        with patch("install.input") as mock_input, \
             patch("install.subprocess.run") as mock_run:
            install._offer_voice_setup(paths, install.Console(), non_interactive=True)

        mock_input.assert_not_called()
        mock_run.assert_not_called()

    def test_offer_voice_setup_user_declines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Answering 'n' -> zero subprocess.run calls."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        with patch("install.input", return_value="n"), \
             patch("install.subprocess.run") as mock_run:
            install._offer_voice_setup(paths, install.Console(), non_interactive=False)

        mock_run.assert_not_called()

    def test_offer_voice_setup_happy_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All subprocesses rc=0 -> console.success called with message containing 'restart'."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        success_messages: list[str] = []
        console = install.Console()
        console.success = lambda msg: success_messages.append(msg)  # type: ignore[method-assign]

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", return_value=_subprocess_ok()):
            install._offer_voice_setup(paths, console, non_interactive=False)

        assert success_messages, "console.success should have been called"
        assert any("restart" in m.lower() for m in success_messages), (
            f"Expected 'restart' in success message, got: {success_messages}"
        )

    def test_offer_voice_setup_install_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Voice install rc=1 -> console.warn called, config set NOT called."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        warn_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]

        subprocess_calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            subprocess_calls.append(list(cmd))
            m = MagicMock()
            m.returncode = 1
            return m

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_voice_setup(paths, console, non_interactive=False)

        assert warn_messages, "console.warn should have been called"
        cmd_strings = [" ".join(c) for c in subprocess_calls]
        assert not any("config" in s for s in cmd_strings), (
            f"config set should not be called after voice install failure. calls={cmd_strings}"
        )

    def test_offer_voice_setup_config_set_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Install ok, config set rc=1 -> console.warn, success NOT called."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        warn_messages: list[str] = []
        success_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]
        console.success = lambda msg: success_messages.append(msg)  # type: ignore[method-assign]

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            m = MagicMock()
            m.returncode = 1 if "config" in cmd else 0
            return m

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_voice_setup(paths, console, non_interactive=False)

        assert warn_messages, "console.warn should have been called"
        assert not success_messages, "console.success should NOT have been called"

    def test_offer_voice_setup_eoferror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """EOFError from console.ask -> returns silently, no subprocess."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        with patch("install.input", side_effect=EOFError), \
             patch("install.subprocess.run") as mock_run:
            install._offer_voice_setup(paths, install.Console(), non_interactive=False)

        mock_run.assert_not_called()

    def test_offer_voice_setup_keyboardinterrupt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """KeyboardInterrupt from console.ask -> returns silently, no subprocess."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        with patch("install.input", side_effect=KeyboardInterrupt), \
             patch("install.subprocess.run") as mock_run:
            install._offer_voice_setup(paths, install.Console(), non_interactive=False)

        mock_run.assert_not_called()

    def test_offer_voice_setup_archon_bin_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """archon_bin.exists()=False -> console.warn called, no subprocess."""
        monkeypatch.setenv("HOME", str(tmp_path))
        archon_home = tmp_path / ".archon"
        paths = install._paths(archon_home)
        # Intentionally do NOT create the archon binary

        warn_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run") as mock_run:
            install._offer_voice_setup(paths, console, non_interactive=False)

        assert warn_messages, "console.warn should have been called"
        mock_run.assert_not_called()

    def test_offer_voice_setup_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """subprocess raises OSError -> console.warn called, no re-raise."""
        monkeypatch.setenv("HOME", str(tmp_path))
        paths = self._make_paths(tmp_path)

        warn_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]

        def fake_run(cmd: list[str], **kw: object) -> MagicMock:
            raise OSError("Permission denied")

        with patch("install.input", return_value="y"), \
             patch("install.subprocess.run", side_effect=fake_run):
            install._offer_voice_setup(paths, console, non_interactive=False)

        assert warn_messages, "console.warn should have been called"


class TestRequestDocumentsPermission:
    """_request_documents_permission triggers the TCC dialog on macOS."""

    def test_skipped_on_dry_run(self, tmp_path: Path) -> None:
        """dry_run=True must skip subprocess entirely."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        console = install.Console()

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run") as mock_run:
            install._request_documents_permission(app_dir, console, dry_run=True)

        mock_run.assert_not_called()

    def test_skipped_on_non_darwin(self, tmp_path: Path) -> None:
        """Non-macOS platforms must skip subprocess entirely."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        console = install.Console()

        with patch("install.platform.system", return_value="Linux"), \
             patch("install.subprocess.run") as mock_run:
            install._request_documents_permission(app_dir, console, dry_run=False)

        mock_run.assert_not_called()

    def test_runs_uv_python_on_darwin(self, tmp_path: Path) -> None:
        """On macOS with dry_run=False, uv run python is invoked in app_dir; success logged on returncode 0."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        success_messages: list[str] = []
        console = install.Console()
        console.success = lambda msg: success_messages.append(msg)  # type: ignore[method-assign]

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", return_value=mock_result) as mock_run:
            install._request_documents_permission(app_dir, console, dry_run=False)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "uv"
        assert cmd[1] == "run"
        assert cmd[2] == "python"
        assert cmd[3] == "-c"
        assert "~/Documents" in cmd[4]
        assert mock_run.call_args[1]["cwd"] == str(app_dir)
        assert success_messages, "console.success should be called when returncode == 0"

    def test_nonzero_returncode_emits_warn_not_success(self, tmp_path: Path) -> None:
        """Non-zero returncode (e.g. permission denied) must emit warn, not success."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        warn_messages: list[str] = []
        success_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]
        console.success = lambda msg: success_messages.append(msg)  # type: ignore[method-assign]

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", return_value=mock_result):
            install._request_documents_permission(app_dir, console, dry_run=False)

        assert warn_messages, "console.warn should be called when returncode != 0"
        assert not success_messages, "console.success must NOT be called when returncode != 0"

    def test_oserror_emits_warn_not_raises(self, tmp_path: Path) -> None:
        """OSError from subprocess.run must emit a warning, not propagate."""
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        warn_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", side_effect=OSError("not found")):
            install._request_documents_permission(app_dir, console, dry_run=False)

        assert warn_messages, "console.warn should have been called on OSError"

    def test_timeout_expired_emits_warn_not_raises(self, tmp_path: Path) -> None:
        """subprocess.TimeoutExpired must emit a warning, not propagate."""
        import subprocess as _subprocess
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        warn_messages: list[str] = []
        console = install.Console()
        console.warn = lambda msg: warn_messages.append(msg)  # type: ignore[method-assign]

        with patch("install.platform.system", return_value="Darwin"), \
             patch("install.subprocess.run", side_effect=_subprocess.TimeoutExpired(cmd="uv", timeout=15)):
            install._request_documents_permission(app_dir, console, dry_run=False)

        assert warn_messages, "console.warn should have been called on TimeoutExpired"
