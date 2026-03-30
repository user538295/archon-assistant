"""Tests for scripts/update_models.py — run via subprocess with a fake constants.py."""
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "update_models.py"

CONSTANTS_TEMPLATE = """\
AVAILABLE_MODELS = ["claude-opus-4-6", "claude-sonnet-4-6"]
DEFAULT_MODEL = "claude-sonnet-4-6"
"""


def _run(tmp_path: Path, api_data: dict) -> subprocess.CompletedProcess:
    """Run the script with api_data piped to stdin, using a temp constants.py."""
    constants_file = tmp_path / "constants.py"
    constants_file.write_text(CONSTANTS_TEMPLATE)

    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(api_data),
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "UPDATE_MODELS_CONSTANTS_PATH": str(constants_file)},
    )


def test_update_models_replaces_available_models(tmp_path: Path) -> None:
    api_data = {"data": [{"id": "claude-foo-1"}, {"id": "claude-bar-2"}]}
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    constants_text = (tmp_path / "constants.py").read_text()
    assert 'AVAILABLE_MODELS = ["claude-bar-2", "claude-foo-1"]' in constants_text
    assert "Updated AVAILABLE_MODELS" in result.stdout


def test_update_models_excludes_latest_aliases(tmp_path: Path) -> None:
    api_data = {
        "data": [
            {"id": "claude-sonnet-latest"},
            {"id": "claude-sonnet-4-6"},
        ]
    }
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    constants_text = (tmp_path / "constants.py").read_text()
    assert "claude-sonnet-latest" not in constants_text
    assert "claude-sonnet-4-6" in constants_text


def test_update_models_no_change_returns_unchanged(tmp_path: Path) -> None:
    # Feed exactly what's already in the template (sorted order)
    api_data = {"data": [{"id": "claude-opus-4-6"}, {"id": "claude-sonnet-4-6"}]}
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    assert "already up to date" in result.stdout
    constants_text = (tmp_path / "constants.py").read_text()
    assert constants_text == CONSTANTS_TEMPLATE


def test_update_models_ignores_non_claude_models(tmp_path: Path) -> None:
    api_data = {
        "data": [
            {"id": "claude-sonnet-4-6"},
            {"id": "gpt-4o"},
            {"id": "gemini-pro"},
        ]
    }
    result = _run(tmp_path, api_data)

    assert result.returncode == 0, result.stderr
    constants_text = (tmp_path / "constants.py").read_text()
    assert "gpt-4o" not in constants_text
    assert "gemini-pro" not in constants_text
    assert "claude-sonnet-4-6" in constants_text
