"""Tests for scripts/update_models.py — run via subprocess with fake file paths."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "update_models.py"

CONSTANTS_TEMPLATE = """\
AVAILABLE_MODELS: dict[str, int] = {
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-4-6": 200_000,
}
DEFAULT_MODEL = "claude-sonnet-4-6"
"""

CONSTANTS_OLD_LIST_TEMPLATE = """\
AVAILABLE_MODELS = ["claude-opus-4-6", "claude-sonnet-4-6"]
DEFAULT_MODEL = "claude-sonnet-4-6"
"""

TOML_TEMPLATE = """\
[models]
default = "claude-sonnet-4-6"

[models.available]
# Each entry enables a model and declares its context window size (tokens).
# update_models.py keeps this list current on every release.
# Add custom or proxy models here with their actual context window.
"claude-opus-4-6" = 1_000_000
"claude-sonnet-4-6" = 200_000

[other]
key = "value"
"""

TOML_OLD_LIST_TEMPLATE = """\
[models]
default = "claude-sonnet-4-6"
available = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]

[other]
key = "value"
"""


def _run(
    tmp_path: Path,
    api_data: dict,
    constants_text: str = CONSTANTS_TEMPLATE,
    toml_text: str = TOML_TEMPLATE,
) -> subprocess.CompletedProcess:
    """Run the script with api_data piped to stdin, using temp file paths."""
    constants_file = tmp_path / "constants.py"
    constants_file.write_text(constants_text)

    example_file = tmp_path / "config.toml.example"
    example_file.write_text(toml_text)

    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(api_data),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "UPDATE_MODELS_CONSTANTS_PATH": str(constants_file),
            "UPDATE_MODELS_EXAMPLE_PATH": str(example_file),
        },
    )


def test_update_models_writes_dict_format(tmp_path: Path) -> None:
    """Model with context_window → dict entry written to constants.py."""
    api_data = {"data": [{"id": "claude-foo", "context_window": 300_000}]}
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "constants.py").read_text()
    assert '"claude-foo": 300_000' in text
    assert "AVAILABLE_MODELS: dict[str, int] = {" in text


def test_update_models_missing_context_window_defaults_200k(tmp_path: Path) -> None:
    """Model without context_window key → written with 200_000 fallback."""
    api_data = {"data": [{"id": "claude-no-window"}]}
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "constants.py").read_text()
    assert '"claude-no-window": 200_000' in text


def test_update_models_writes_toml_table(tmp_path: Path) -> None:
    """config.toml.example gets updated [models.available] table."""
    api_data = {"data": [{"id": "claude-foo", "context_window": 300_000}]}
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    toml_text = (tmp_path / "config.toml.example").read_text()
    assert "[models.available]" in toml_text
    assert '"claude-foo" = 300_000' in toml_text


def test_update_models_idempotent(tmp_path: Path) -> None:
    """Running update_models.py twice on already-updated files produces no diff."""
    api_data = {
        "data": [
            {"id": "claude-opus-4-6", "context_window": 1_000_000},
            {"id": "claude-sonnet-4-6", "context_window": 200_000},
        ]
    }
    # First run
    result1 = _run(tmp_path, api_data)
    assert result1.returncode == 0, result1.stderr
    constants_after_first = (tmp_path / "constants.py").read_text()
    toml_after_first = (tmp_path / "config.toml.example").read_text()

    # Second run — re-use the files written by the first run
    result2 = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(api_data),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "UPDATE_MODELS_CONSTANTS_PATH": str(tmp_path / "constants.py"),
            "UPDATE_MODELS_EXAMPLE_PATH": str(tmp_path / "config.toml.example"),
        },
    )
    assert result2.returncode == 0, result2.stderr
    assert (tmp_path / "constants.py").read_text() == constants_after_first
    assert (tmp_path / "config.toml.example").read_text() == toml_after_first
    assert "already up to date" in result2.stdout


def test_update_models_excludes_latest_aliases(tmp_path: Path) -> None:
    """-latest models are excluded from the output."""
    api_data = {
        "data": [
            {"id": "claude-sonnet-latest", "context_window": 200_000},
            {"id": "claude-sonnet-4-6", "context_window": 200_000},
        ]
    }
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "constants.py").read_text()
    assert "claude-sonnet-latest" not in text
    assert "claude-sonnet-4-6" in text


def test_update_models_ignores_non_claude(tmp_path: Path) -> None:
    """Non-claude-* models are excluded."""
    api_data = {
        "data": [
            {"id": "claude-sonnet-4-6", "context_window": 200_000},
            {"id": "gpt-4o", "context_window": 128_000},
            {"id": "gemini-pro", "context_window": 32_000},
        ]
    }
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "constants.py").read_text()
    assert "gpt-4o" not in text
    assert "gemini-pro" not in text
    assert "claude-sonnet-4-6" in text


def test_update_models_no_change_returns_unchanged(tmp_path: Path) -> None:
    """When content is already up-to-date, files are not rewritten."""
    # Build exactly what the script would produce
    api_data = {
        "data": [
            {"id": "claude-opus-4-6", "context_window": 1_000_000},
            {"id": "claude-sonnet-4-6", "context_window": 200_000},
        ]
    }
    # First run to get the canonical output
    result = _run(tmp_path, api_data)
    assert result.returncode == 0, result.stderr

    # Record mtimes after first run
    constants_mtime = (tmp_path / "constants.py").stat().st_mtime
    toml_mtime = (tmp_path / "config.toml.example").stat().st_mtime

    # Second run
    result2 = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(api_data),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "UPDATE_MODELS_CONSTANTS_PATH": str(tmp_path / "constants.py"),
            "UPDATE_MODELS_EXAMPLE_PATH": str(tmp_path / "config.toml.example"),
        },
    )
    assert result2.returncode == 0, result2.stderr
    assert "already up to date" in result2.stdout
    # Files must not have been rewritten
    assert (tmp_path / "constants.py").stat().st_mtime == constants_mtime
    assert (tmp_path / "config.toml.example").stat().st_mtime == toml_mtime


def test_update_models_empty_data_array(tmp_path: Path) -> None:
    """Empty data array → empty dict written, warning printed, exit 0."""
    result = _run(tmp_path, {"data": []})

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "constants.py").read_text()
    assert "AVAILABLE_MODELS: dict[str, int] = {}" in text
    assert "Warning: no models found" in result.stdout


def test_update_models_no_models_after_filtering(tmp_path: Path) -> None:
    """All models excluded by filter → empty dict, warning, exit 0."""
    api_data = {
        "data": [
            {"id": "gpt-4o", "context_window": 128_000},
            {"id": "claude-sonnet-latest", "context_window": 200_000},
        ]
    }
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "constants.py").read_text()
    assert "AVAILABLE_MODELS: dict[str, int] = {}" in text
    assert "Warning: no models found" in result.stdout


def test_update_models_converts_old_list_format_in_example(tmp_path: Path) -> None:
    """Old `available = [...]` format in config.toml.example is converted to [models.available] table."""
    api_data = {
        "data": [
            {"id": "claude-foo", "context_window": 300_000},
        ]
    }
    result = _run(
        tmp_path,
        api_data,
        constants_text=CONSTANTS_OLD_LIST_TEMPLATE,
        toml_text=TOML_OLD_LIST_TEMPLATE,
    )

    assert result.returncode == 0, result.stderr
    toml_text = (tmp_path / "config.toml.example").read_text()
    assert "[models.available]" in toml_text
    assert '"claude-foo" = 300_000' in toml_text
    # Old inline list format should be gone
    assert "available = [" not in toml_text


def test_update_models_idempotent_real_example(tmp_path: Path) -> None:
    """Running update_models.py twice on a copy of the real config.toml.example produces no diff."""
    real_example = Path(__file__).parents[2] / "examples" / "config.toml.example"
    toml_copy = tmp_path / "config.toml.example"
    toml_copy.write_text(real_example.read_text())

    # Use the model list that matches the current real file content
    api_data = {
        "data": [
            {"id": "claude-3-haiku-20240307", "context_window": 200_000},
            {"id": "claude-haiku-4-5-20251001", "context_window": 200_000},
            {"id": "claude-opus-4-1-20250805", "context_window": 200_000},
            {"id": "claude-opus-4-20250514", "context_window": 200_000},
            {"id": "claude-opus-4-5-20251101", "context_window": 200_000},
            {"id": "claude-opus-4-6", "context_window": 1_000_000},
            {"id": "claude-sonnet-4-20250514", "context_window": 200_000},
            {"id": "claude-sonnet-4-5-20250929", "context_window": 200_000},
            {"id": "claude-sonnet-4-6", "context_window": 200_000},
        ]
    }

    constants_file = tmp_path / "constants.py"
    constants_file.write_text(CONSTANTS_TEMPLATE)

    def _run_with_copy() -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT)],
            input=json.dumps(api_data),
            capture_output=True,
            text=True,
            env={
                **__import__("os").environ,
                "UPDATE_MODELS_CONSTANTS_PATH": str(constants_file),
                "UPDATE_MODELS_EXAMPLE_PATH": str(toml_copy),
            },
        )

    result1 = _run_with_copy()
    assert result1.returncode == 0, result1.stderr
    after_first = toml_copy.read_text()

    result2 = _run_with_copy()
    assert result2.returncode == 0, result2.stderr
    after_second = toml_copy.read_text()

    assert after_first == after_second, (
        "config.toml.example is not idempotent — second run produced a diff.\n"
        "First run output:\n" + after_first
    )

    # Content preservation: sections outside [models.available] must survive
    for section in ("[models.available]", "[plugins]", "[search]", "[schedule]",
                    "[background_agents]", "[voice]", "[reminder]", "[access]",
                    "[session]", "[output]", "[logging]", "[notifications]"):
        assert section in after_first, (
            f"Section {section!r} missing from output — regex ate content beyond [models.available]"
        )


def test_update_models_missing_example_file(tmp_path: Path) -> None:
    """Script succeeds even when config.toml.example does not exist."""
    api_data = {"data": [{"id": "claude-sonnet-4-6", "context_window": 200_000}]}
    constants_file = tmp_path / "constants.py"
    constants_file.write_text(CONSTANTS_TEMPLATE)
    missing_toml = tmp_path / "nonexistent.toml.example"

    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(api_data),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "UPDATE_MODELS_CONSTANTS_PATH": str(constants_file),
            "UPDATE_MODELS_EXAMPLE_PATH": str(missing_toml),
        },
    )

    assert result.returncode == 0, result.stderr
    # constants.py should be updated
    assert "claude-sonnet-4-6" in constants_file.read_text()
    # missing TOML file must NOT be created
    assert not missing_toml.exists()


def test_update_models_missing_pattern_exits_1(tmp_path: Path) -> None:
    """Constants file with no AVAILABLE_MODELS → exit 1 + warning to stderr."""
    no_pattern_constants = 'DEFAULT_MODEL = "claude-sonnet-4-6"\n'
    api_data = {"data": [{"id": "claude-sonnet-4-6", "context_window": 200_000}]}
    result = _run(tmp_path, api_data, constants_text=no_pattern_constants)

    assert result.returncode == 1
    assert "Warning: AVAILABLE_MODELS pattern not found" in result.stderr


def test_update_models_old_list_and_new_table_both_present(tmp_path: Path) -> None:
    """TOML with both available=[...] and [models.available] — script must not crash."""
    mixed_toml = """\
[models]
default = "claude-sonnet-4-6"
available = [
    "claude-opus-4-6",
]

[models.available]
# Each entry enables a model and declares its context window size (tokens).
# update_models.py keeps this list current on every release.
# Add custom or proxy models here with their actual context window.
"claude-opus-4-6" = 1_000_000

[other]
key = "value"
"""
    api_data = {"data": [{"id": "claude-opus-4-6", "context_window": 1_000_000}]}
    result = _run(tmp_path, api_data, toml_text=mixed_toml)

    assert result.returncode == 0, result.stderr
    toml_out = (tmp_path / "config.toml.example").read_text()
    # [models.available] table must be present
    assert "[models.available]" in toml_out
    assert '"claude-opus-4-6" = 1_000_000' in toml_out
    # old inline list must be removed
    assert "available = [" not in toml_out
    # no duplicate section headers
    assert toml_out.count("[models.available]") == 1


def test_release_sh_stages_config_example(tmp_path: Path) -> None:
    """release.sh contains staging logic for both constants.py and config.toml.example."""
    release_sh = Path(__file__).parents[2] / "release.sh"
    content = release_sh.read_text()

    assert "git add examples/config.toml.example" in content
    assert "git diff --quiet examples/config.toml.example" in content
    assert "git add archon/ai/constants.py" in content
    assert "git diff --quiet archon/ai/constants.py" in content
