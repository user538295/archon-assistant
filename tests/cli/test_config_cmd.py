from __future__ import annotations
import pytest
import tomlkit
from pathlib import Path
from unittest.mock import patch, MagicMock
import archon.cli.config_cmd as config_mod
from archon.cli.config_cmd import run_config, _coerce_value

SAMPLE_TOML = """\
[access]
allowed_user_ids = [123]

[notifications]
mode = "normal"
interval_minutes = 2

[session]
working_directory = "/tmp"
"""


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(SAMPLE_TOML)
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", cfg)
    return cfg


class Args:
    def __init__(self, config_command: str | None = None, key: str = "", value: str = "") -> None:
        self.config_command = config_command
        self.key = key
        self.value = value


def test_show_prints_content(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    result = run_config(Args("show"))
    assert result == 0
    out = capsys.readouterr().out
    assert "notifications" in out
    assert "mode" in out


def test_show_prints_path_header(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    run_config(Args("show"))
    out = capsys.readouterr().out
    assert str(config_file) in out


def test_show_missing_config_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "nonexistent.toml")
    result = run_config(Args("show"))
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_edit_opens_editor(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDITOR", "nano")
    with patch("archon.cli.config_cmd.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = run_config(Args("edit"))
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "nano"
    assert str(config_file) in cmd


def test_edit_uses_visual_when_no_editor(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setenv("VISUAL", "vim")
    with patch("archon.cli.config_cmd.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        run_config(Args("edit"))
    assert "vim" in mock_run.call_args[0][0]


def test_edit_falls_back_to_vi(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    with patch("archon.cli.config_cmd.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        run_config(Args("edit"))
    assert "vi" in mock_run.call_args[0][0]


def test_edit_missing_config_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "missing.toml")
    result = run_config(Args("edit"))
    assert result == 1


def test_get_existing_key(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    result = run_config(Args("get", key="notifications.mode"))
    assert result == 0
    assert "normal" in capsys.readouterr().out


def test_get_missing_key_returns_1(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    result = run_config(Args("get", key="nonexistent.deep.key"))
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_get_missing_config_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "missing.toml")
    result = run_config(Args("get", key="notifications.mode"))
    assert result == 1


def test_set_string_value(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    result = run_config(Args("set", key="notifications.mode", value="quiet"))
    assert result == 0
    parsed = tomlkit.parse(config_file.read_text())
    assert parsed["notifications"]["mode"] == "quiet"


def test_set_integer_value(config_file: Path) -> None:
    run_config(Args("set", key="notifications.interval_minutes", value="5"))
    parsed = tomlkit.parse(config_file.read_text())
    assert parsed["notifications"]["interval_minutes"] == 5
    assert isinstance(int(parsed["notifications"]["interval_minutes"]), int)


def test_set_bool_value(config_file: Path) -> None:
    # Add a bool field to test with
    config_file.write_text(SAMPLE_TOML + "\n[history]\nenabled = true\n")
    run_config(Args("set", key="history.enabled", value="false"))
    parsed = tomlkit.parse(config_file.read_text())
    assert parsed["history"]["enabled"] is False


def test_set_creates_new_section(config_file: Path) -> None:
    run_config(Args("set", key="voice.enabled", value="true"))
    parsed = tomlkit.parse(config_file.read_text())
    assert parsed["voice"]["enabled"] is True


def test_set_missing_config_returns_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setattr(config_mod, "_CONFIG_PATH", tmp_path / "missing.toml")
    result = run_config(Args("set", key="x.y", value="z"))
    assert result == 1


def test_run_config_defaults_to_show(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    result = run_config(Args(None))
    assert result == 0
    assert "notifications" in capsys.readouterr().out


def test_run_config_unknown_command_returns_1(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    result = run_config(Args("invalid"))
    assert result == 1


def test_coerce_value_int() -> None:
    result = _coerce_value("42")
    assert result == 42
    assert isinstance(result, int)


def test_coerce_value_bool_true() -> None:
    assert _coerce_value("true") is True
    assert _coerce_value("True") is True


def test_coerce_value_bool_false() -> None:
    assert _coerce_value("false") is False


def test_coerce_value_string() -> None:
    result = _coerce_value("normal")
    assert result == "normal"
    assert isinstance(result, str)


def test_set_preserves_other_keys(config_file: Path) -> None:
    run_config(Args("set", key="notifications.mode", value="quiet"))
    parsed = tomlkit.parse(config_file.read_text())
    assert 123 in parsed["access"]["allowed_user_ids"]
    assert parsed["session"]["working_directory"] == "/tmp"


def test_set_intermediate_key_not_table_returns_1(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    """Setting a.b.c when a.b is a scalar (not a table) must return 1, not crash."""
    # notifications.mode is a string; trying to set notifications.mode.x must fail gracefully
    result = run_config(Args("set", key="notifications.mode.invalid", value="v"))
    assert result == 1
    assert "Cannot set" in capsys.readouterr().out


def test_edit_editor_not_found_returns_1(config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    monkeypatch.setenv("EDITOR", "nonexistent_editor_xyz")
    with patch("archon.cli.config_cmd.subprocess.run", side_effect=FileNotFoundError):
        result = run_config(Args("edit"))
    assert result == 1
    assert "not found" in capsys.readouterr().out


def test_edit_editor_with_flags(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """EDITOR='code --wait' must split correctly so 'code' is the binary, not 'code --wait'."""
    monkeypatch.setenv("EDITOR", "code --wait")
    with patch("archon.cli.config_cmd.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        result = run_config(Args("edit"))
    assert result == 0
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "code"
    assert "--wait" in cmd
    assert str(config_file) in cmd


def test_edit_malformed_editor_returns_1(config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """EDITOR with unclosed quote raises ValueError from shlex.split — must return 1."""
    monkeypatch.setenv("EDITOR", "code '--wait")  # unclosed single quote
    result = run_config(Args("edit"))
    assert result == 1
    assert "Invalid EDITOR" in capsys.readouterr().out


def test_edit_malformed_visual_reports_visual(config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """When EDITOR is unset and VISUAL is malformed, error must say 'VISUAL' not 'EDITOR'."""
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setenv("VISUAL", "vim '--arg")  # unclosed single quote
    result = run_config(Args("edit"))
    assert result == 1
    out = capsys.readouterr().out
    assert "Invalid VISUAL" in out
    assert "Invalid EDITOR" not in out


def test_set_intermediate_key_is_string_not_table(config_file: Path, capsys: pytest.CaptureFixture) -> None:
    """Setting a key through a string value must fail: traversing into a string as a dict raises TypeError."""
    # notifications.mode = "normal"; traversing into "normal" (a string) as a dict must fail
    result = run_config(Args("set", key="notifications.mode.o.x", value="v"))
    assert result == 1
    assert "Cannot set" in capsys.readouterr().out


def test_set_uses_atomic_write(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_set must call atomic_write instead of write_text to avoid corrupt configs on crash."""
    calls: list[tuple] = []

    def fake_atomic_write(path: object, content: object) -> None:
        calls.append((path, content))
        # Actually write so the file is updated for verification
        config_file.write_text(str(content))  # type: ignore[arg-type]

    monkeypatch.setattr(config_mod, "atomic_write", fake_atomic_write)
    result = run_config(Args("set", key="notifications.mode", value="quiet"))
    assert result == 0
    assert len(calls) == 1, "atomic_write must be called exactly once per set"


# ──────────────────────────────────────────────────────────────────
# Issue #6 — _coerce_value supports arrays and floats
# ──────────────────────────────────────────────────────────────────


def test_coerce_value_json_array() -> None:
    result = _coerce_value("[1, 2, 3]")
    assert result == [1, 2, 3]
    assert isinstance(result, list)


def test_coerce_value_json_string_array() -> None:
    result = _coerce_value('["a", "b"]')
    assert result == ["a", "b"]
    assert isinstance(result, list)


def test_coerce_value_float() -> None:
    result = _coerce_value("3.14")
    assert result == 3.14
    assert isinstance(result, float)


def test_coerce_value_negative_float() -> None:
    result = _coerce_value("-1.5")
    assert result == -1.5
    assert isinstance(result, float)


def test_coerce_value_plain_string_not_json() -> None:
    """A plain string like 'hello' must not be parsed as JSON."""
    result = _coerce_value("hello")
    assert result == "hello"
    assert isinstance(result, str)


def test_coerce_value_json_object_stays_string() -> None:
    """JSON objects should not be coerced — only arrays are supported."""
    result = _coerce_value('{"a": 1}')
    assert isinstance(result, str)


def test_set_array_value(config_file: Path) -> None:
    """config set must write arrays correctly."""
    run_config(Args("set", key="access.allowed_user_ids", value="[111, 222]"))
    parsed = tomlkit.parse(config_file.read_text())
    assert parsed["access"]["allowed_user_ids"] == [111, 222]


# ──────────────────────────────────────────────────────────────────
# Issue #15 — config set warns on unknown top-level sections
# ──────────────────────────────────────────────────────────────────


def test_set_unknown_section_prints_warning(
    config_file: Path, capsys: pytest.CaptureFixture,
) -> None:
    """Setting a key in an unknown section prints a warning but still succeeds."""
    result = run_config(Args("set", key="foobar.key", value="val"))
    assert result == 0
    out = capsys.readouterr().out
    assert "Warning" in out
    parsed = tomlkit.parse(config_file.read_text())
    assert parsed["foobar"]["key"] == "val"


def test_set_known_section_no_warning(
    config_file: Path, capsys: pytest.CaptureFixture,
) -> None:
    """Setting a key in a known section must not print a warning."""
    result = run_config(Args("set", key="notifications.mode", value="quiet"))
    assert result == 0
    out = capsys.readouterr().out
    assert "Warning" not in out


# ──────────────────────────────────────────────────────────────────
# Issue #2 — _run_set must use file locking
# ──────────────────────────────────────────────────────────────────


def test_set_acquires_file_lock(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_set must acquire a file lock around read-modify-write."""
    lock_calls: list[str] = []

    original_file_lock = config_mod._file_lock
    original_file_unlock = config_mod._file_unlock

    def tracking_lock(f: object) -> None:
        lock_calls.append("lock")
        original_file_lock(f)

    def tracking_unlock(f: object) -> None:
        lock_calls.append("unlock")
        original_file_unlock(f)

    monkeypatch.setattr(config_mod, "_file_lock", tracking_lock)
    monkeypatch.setattr(config_mod, "_file_unlock", tracking_unlock)
    result = run_config(Args("set", key="notifications.mode", value="quiet"))
    assert result == 0
    assert "lock" in lock_calls, "file lock must be acquired"
    assert "unlock" in lock_calls, "file lock must be released"


def test_set_preserves_lock_file(config_file: Path) -> None:
    """Lock file must persist after _run_set — not unlinked."""
    run_config(Args("set", key="notifications.mode", value="quiet"))
    lock_file = config_file.with_suffix(".toml.lock")
    assert lock_file.exists()


# ──────────────────────────────────────────────────────────────────
# Issue #3 — _run_set round-trip validation
# ──────────────────────────────────────────────────────────────────


def test_set_restores_on_invalid_roundtrip(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    """If TOML round-trip validation fails, original content must be restored."""
    original_content = config_file.read_text()

    # Simulate tomlkit.dumps producing invalid TOML by patching tomllib.loads to fail
    import tomllib as _tomllib
    real_loads = _tomllib.loads

    def bad_loads(s: str) -> object:
        # Only fail on the round-trip check (after the write), not on initial parse
        if "quiet" in s:
            raise ValueError("simulated round-trip failure")
        return real_loads(s)

    monkeypatch.setattr("archon.cli.config_cmd.tomllib.loads", bad_loads)
    result = run_config(Args("set", key="notifications.mode", value="quiet"))
    assert result == 1
    out = capsys.readouterr().out
    assert "round-trip" in out.lower() or "validation" in out.lower() or "restore" in out.lower()
    # File must be restored to original content
    assert config_file.read_text() == original_content


# ──────────────────────────────────────────────────────────────────
# Issue #4 — _coerce_value rejects invalid arrays
# ──────────────────────────────────────────────────────────────────


def test_coerce_value_rejects_nested_arrays() -> None:
    """Arrays of arrays must be rejected (returned as string)."""
    result = _coerce_value("[[1, 2], [3, 4]]")
    assert isinstance(result, str)


def test_coerce_value_rejects_arrays_of_objects() -> None:
    """Arrays of objects must be rejected (returned as string)."""
    result = _coerce_value('[{"a": 1}]')
    assert isinstance(result, str)


def test_coerce_value_rejects_mixed_type_arrays() -> None:
    """Arrays with mixed types must be rejected (returned as string)."""
    result = _coerce_value('[1, "a"]')
    assert isinstance(result, str)


def test_coerce_value_accepts_homogeneous_int_array() -> None:
    result = _coerce_value("[1, 2, 3]")
    assert result == [1, 2, 3]


def test_coerce_value_accepts_homogeneous_string_array() -> None:
    result = _coerce_value('["a", "b", "c"]')
    assert result == ["a", "b", "c"]


def test_coerce_value_accepts_homogeneous_bool_array() -> None:
    result = _coerce_value("[true, false]")
    assert result == [True, False]


def test_coerce_value_accepts_homogeneous_float_array() -> None:
    result = _coerce_value("[1.1, 2.2]")
    assert result == [1.1, 2.2]


def test_coerce_value_accepts_empty_array() -> None:
    result = _coerce_value("[]")
    assert result == []
