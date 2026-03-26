"""Tests that examples/config.toml.example stays in sync with Python dataclass defaults."""
from __future__ import annotations

import dataclasses
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from archon.config.loader import (
    BackgroundAgentsConfig,
    HistoryConfig,
    LoggingConfig,
    ModelsConfig,
    NotificationsAgentsConfig,
    NotificationsConfig,
    OutputConfig,
    PluginsConfig,
    RagConfig,
    ReminderConfig,
    ScheduleConfig,
    SessionConfig,
    VoiceConfig,
    VoiceSTTConfig,
    VoiceTTSConfig,
)

_EXAMPLE_PATH = Path(__file__).parent.parent.parent / "examples" / "config.toml.example"

# Fields intentionally excluded from the forward (example→Python) reverse check.
# Key: (dataclass_name, field_name), Value: reason
_SKIP_REVERSE: dict[tuple[str, str], str] = {
    ("NotificationsAgentsConfig", "mode"): "Inherit-from-parent default; commented out in example",
    ("SessionConfig", "attachments_dir"): "Optional path override; commented out in example",
    ("SessionConfig", "attachments_cleanup_hours"): "Optional; commented out in example",
    ("BackgroundAgentsConfig", "router_mcp_port"): "Optional port override; commented out in example",
    ("VoiceSTTConfig", "language"): "Optional BCP-47 hint; commented out in example",
}


def _load_example() -> dict[str, Any]:
    """Load config.toml.example with dummy values substituted for required fields."""
    text = _EXAMPLE_PATH.read_text()
    text = re.sub(r"^allowed_user_ids\s*=.*$", "allowed_user_ids = [99999]", text, flags=re.MULTILINE)
    text = re.sub(r"^working_directory\s*=.*$", 'working_directory = "/tmp"', text, flags=re.MULTILINE)
    return tomllib.loads(text)


def _check_section(
    parsed_section: dict[str, Any],
    dataclass_type: type,
    skip: list[str] | None = None,
) -> None:
    """Assert each uncommented key in the parsed section matches the dataclass default."""
    skip = skip or []
    fields_by_name = {f.name: f for f in dataclasses.fields(dataclass_type)}

    for key, value in parsed_section.items():
        if key in skip:
            continue
        if isinstance(value, dict):
            continue  # sub-section, handled separately

        assert key in fields_by_name, (
            f"Key {key!r} in example has no matching field in {dataclass_type.__name__}"
        )
        field = fields_by_name[key]

        if field.default is not dataclasses.MISSING:
            expected = field.default
        elif field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            expected = field.default_factory()  # type: ignore[misc]
        else:
            pytest.fail(f"Field {dataclass_type.__name__}.{key} has no default — cannot sync-check")

        assert value == expected, (
            f"{dataclass_type.__name__}.{key}: example has {value!r}, Python default is {expected!r}"
        )

    # Reverse check: every scalar-default field must be in the example OR in _SKIP_REVERSE
    for field in dataclasses.fields(dataclass_type):
        # Skip fields with no default at all (required fields)
        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:  # type: ignore[misc]
            continue
        # Skip factory fields that produce non-scalar values (lists, dicts, dataclasses)
        if field.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            factory_value = field.default_factory()  # type: ignore[misc]
            if not isinstance(factory_value, (str, int, float, bool)):
                continue
        skip_key = (dataclass_type.__name__, field.name)
        if skip_key in _SKIP_REVERSE:
            continue  # explicitly excluded
        if isinstance(parsed_section.get(field.name), dict):
            continue  # sub-section
        assert field.name in parsed_section, (
            f"{dataclass_type.__name__}.{field.name} has a default but is absent from example "
            f"and not in _SKIP_REVERSE. Add it to the example or to _SKIP_REVERSE with a reason."
        )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def parsed() -> dict[str, Any]:
    return _load_example()


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_example_session_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["session"], SessionConfig, skip=["working_directory"])


def test_example_output_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["output"], OutputConfig)


def test_example_logging_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["logging"], LoggingConfig)


def test_example_notifications_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["notifications"], NotificationsConfig, skip=["agents"])


def test_example_notifications_agents_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["notifications"]["agents"], NotificationsAgentsConfig)


def test_example_history_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["history"], HistoryConfig, skip=["suppressed_events"])


def test_example_plugins_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["plugins"], PluginsConfig)


def test_example_rag_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["rag"], RagConfig)


def test_example_schedule_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["schedule"], ScheduleConfig, skip=["jobs"])


def test_example_background_agents_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["background_agents"], BackgroundAgentsConfig)


def test_example_voice_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["voice"], VoiceConfig, skip=["stt", "tts"])
    _check_section(parsed["voice"]["stt"], VoiceSTTConfig)
    _check_section(parsed["voice"]["tts"], VoiceTTSConfig)


def test_example_reminder_defaults_match_python(parsed: dict[str, Any]) -> None:
    _check_section(parsed["reminder"], ReminderConfig)


def test_example_models_section_excluded_from_sync(parsed: dict[str, Any]) -> None:
    """The [models] section intentionally diverges: example lists real models, Python default is empty.

    This is by design — the example provides a useful starter list, but the Python
    dataclass default is an empty list to avoid hardcoding model names in code.
    """
    assert ModelsConfig().available == [], "Python default for models.available must remain empty"
    assert parsed["models"]["available"] != [], (
        "Example models.available must contain at least one model for a useful starter config"
    )


def test_all_python_defaults_have_example_entry(parsed: dict[str, Any]) -> None:
    """Explicit aggregate: all config dataclasses pass the reverse check.

    # NOTE: ModelsConfig is not included here intentionally.
    # The [models] section has an intentional divergence (example: curated list, Python: empty list).
    # See test_example_models_section_excluded_from_sync for the explicit documentation.
    """
    _check_section(parsed["session"], SessionConfig, skip=["working_directory"])
    _check_section(parsed["output"], OutputConfig)
    _check_section(parsed["logging"], LoggingConfig)
    _check_section(parsed["notifications"], NotificationsConfig, skip=["agents"])
    _check_section(parsed["notifications"]["agents"], NotificationsAgentsConfig)
    _check_section(parsed["history"], HistoryConfig, skip=["suppressed_events"])
    _check_section(parsed["plugins"], PluginsConfig)
    _check_section(parsed["rag"], RagConfig)
    _check_section(parsed["schedule"], ScheduleConfig, skip=["jobs"])
    _check_section(parsed["background_agents"], BackgroundAgentsConfig)
    _check_section(parsed["voice"], VoiceConfig, skip=["stt", "tts"])
    _check_section(parsed["voice"]["stt"], VoiceSTTConfig)
    _check_section(parsed["voice"]["tts"], VoiceTTSConfig)
    _check_section(parsed["reminder"], ReminderConfig)


def test_voice_tts_provider_default_is_edge() -> None:
    """VoiceTTSConfig.provider must default to 'edge' to match the example."""
    assert VoiceTTSConfig().provider == "edge"


def test_check_section_detects_value_mismatch() -> None:
    """_check_section must raise AssertionError when example value differs from Python default."""
    # inactivity_timeout_seconds default is 1800; inject a wrong value
    fake_section = {"inactivity_timeout_seconds": 9999}
    with pytest.raises(AssertionError, match="inactivity_timeout_seconds"):
        _check_section(fake_section, SessionConfig, skip=["working_directory"])
