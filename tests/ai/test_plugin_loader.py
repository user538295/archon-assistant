"""Tests for PluginLoader — reads Claude Code plugins from the standard registry."""
import json
from pathlib import Path

import pytest

from archon.ai.plugin_loader import PluginInfo, PluginLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_plugin_dir(
    tmp_path: Path,
    key: str,
    version: str = "1.0.0",
    skills: list[str] | None = None,
) -> Path:
    """Create a minimal plugin cache directory and return its path."""
    name = key.split("@")[0]
    root = tmp_path / "plugins" / "cache" / key / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version, "description": f"Test plugin {name}"}),
        encoding="utf-8",
    )
    if skills:
        for skill_name in skills:
            skill_dir = root / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            # Write a valid SKILL.md with frontmatter (required by SkillLoader._load_skill)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {skill_name}\ndescription: Description for {skill_name}\n---\n\n# {skill_name}\nSkill content.",
                encoding="utf-8",
            )
    return root


def write_registry_v2(tmp_path: Path, entries: dict[str, str]) -> Path:
    """Write a v2-format installed_plugins.json.

    *entries* maps plugin key → installPath.
    """
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    plugins_data: dict = {}
    for key, path in entries.items():
        plugins_data[key] = [{"installPath": path, "scope": "user", "version": "1.0.0"}]
    registry = plugins_dir / "installed_plugins.json"
    registry.write_text(
        json.dumps({"version": 2, "plugins": plugins_data}),
        encoding="utf-8",
    )
    return plugins_dir


def write_registry_legacy(tmp_path: Path, entries: list[dict]) -> Path:
    """Write a legacy list-format installed_plugins.json."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    registry = plugins_dir / "installed_plugins.json"
    registry.write_text(json.dumps(entries), encoding="utf-8")
    return plugins_dir


def write_settings(tmp_path: Path, enabled: dict[str, bool]) -> Path:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": enabled}), encoding="utf-8")
    return settings


# ---------------------------------------------------------------------------
# load_all()
# ---------------------------------------------------------------------------


def test_load_all_returns_enabled_plugins(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].key == "claude-mem@thedotmack"
    assert plugins[0].name == "claude-mem"
    assert plugins[0].marketplace == "thedotmack"
    assert plugins[0].version == "10.3.1"


def test_disabled_plugin_excluded(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": False})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.load_all() == []


def test_multiple_plugins_only_enabled_returned(tmp_path):
    root_a = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    root_b = make_plugin_dir(tmp_path, "swift-lsp@official", "1.0.0")
    plugins_dir = write_registry_v2(tmp_path, {
        "claude-mem@thedotmack": str(root_a),
        "swift-lsp@official": str(root_b),
    })
    settings_path = write_settings(tmp_path, {
        "claude-mem@thedotmack": True,
        "swift-lsp@official": False,
    })

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].key == "claude-mem@thedotmack"


def test_load_all_is_cached(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    first = loader.load_all()
    second = loader.load_all()
    assert first is second  # same list object — not reloaded


# ---------------------------------------------------------------------------
# get_sdk_configs()
# ---------------------------------------------------------------------------


def test_get_sdk_configs_format(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    configs = loader.get_sdk_configs()

    assert configs == [{"type": "local", "path": str(root)}]


def test_get_sdk_configs_empty_when_no_enabled_plugins(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    settings_path = write_settings(tmp_path, {})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.get_sdk_configs() == []


# ---------------------------------------------------------------------------
# get_skills()
# ---------------------------------------------------------------------------


def test_get_skills_namespaced(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1", skills=["mem-search", "mem-add"])
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    skills = loader.get_skills()

    names = [s.name for s in skills]
    assert "claude-mem:mem-search" in names
    assert "claude-mem:mem-add" in names


def test_get_skills_includes_description(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1", skills=["mem-search"])
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    skills = loader.get_skills()

    assert len(skills) == 1
    assert skills[0].description == "Description for mem-search"
    assert "Skill content." in skills[0].content


def test_plugin_without_skills(tmp_path):
    root = make_plugin_dir(tmp_path, "swift-lsp@official", "1.0.0")  # no skills
    plugins_dir = write_registry_v2(tmp_path, {"swift-lsp@official": str(root)})
    settings_path = write_settings(tmp_path, {"swift-lsp@official": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].skills == []
    assert loader.get_skills() == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_registry_returns_empty(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    settings_path = write_settings(tmp_path, {})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.load_all() == []


def test_missing_settings_treats_all_disabled(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    # No settings.json written

    loader = PluginLoader(
        plugins_dir=str(plugins_dir),
        settings_path=str(tmp_path / "nonexistent.json"),
    )
    assert loader.load_all() == []


def test_invalid_install_path_skipped(tmp_path):
    plugins_dir = write_registry_v2(tmp_path, {"bad-plugin@test": "/nonexistent/path"})
    settings_path = write_settings(tmp_path, {"bad-plugin@test": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    # Should not raise; bad plugin is logged as a warning and skipped
    assert loader.load_all() == []


def test_legacy_list_format(tmp_path):
    """installed_plugins.json written as a flat list still works."""
    root = make_plugin_dir(tmp_path, "some-plugin@vendor", "2.0.0")
    plugins_dir = write_registry_legacy(
        tmp_path,
        [{"key": "some-plugin@vendor", "installPath": str(root)}],
    )
    settings_path = write_settings(tmp_path, {"some-plugin@vendor": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].key == "some-plugin@vendor"


def test_plugin_key_without_at_sign(tmp_path):
    """Keys without '@' marketplace separator get 'unknown' as marketplace."""
    root = make_plugin_dir(tmp_path, "standalone", "1.0.0")
    # Write v2 registry manually because make_plugin_dir expects @ in key
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    registry = plugins_dir / "installed_plugins.json"
    registry.write_text(
        json.dumps({"version": 2, "plugins": {"standalone": [{"installPath": str(root)}]}}),
        encoding="utf-8",
    )
    settings_path = write_settings(tmp_path, {"standalone": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].marketplace == "unknown"


# ---------------------------------------------------------------------------
# Corrupt JSON edge cases — High + Medium gaps
# ---------------------------------------------------------------------------


def test_corrupt_json_registry_returns_empty(tmp_path):
    """Corrupt installed_plugins.json must log a warning and return no plugins."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    registry = plugins_dir / "installed_plugins.json"
    registry.write_text("{not valid json}", encoding="utf-8")
    settings_path = write_settings(tmp_path, {})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.load_all() == []


def test_corrupt_json_settings_treats_all_plugins_as_disabled(tmp_path):
    """Corrupt settings.json must log a warning and treat every plugin as disabled."""
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry_v2(tmp_path, {"claude-mem@thedotmack": str(root)})
    settings = tmp_path / "settings.json"
    settings.write_text("{not valid json}", encoding="utf-8")

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings))
    assert loader.load_all() == []


def test_corrupt_plugin_json_manifest_uses_unknown_version(tmp_path):
    """Corrupt plugin.json manifest must log a warning and fall back to version='unknown'."""
    key = "test-plugin@vendor"
    root = tmp_path / "plugins" / "cache" / key / "1.0.0"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{bad json}", encoding="utf-8")

    plugins_dir = write_registry_v2(tmp_path, {key: str(root)})
    settings_path = write_settings(tmp_path, {key: True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].version == "unknown"


def test_unrecognised_registry_format_returns_empty(tmp_path):
    """installed_plugins.json with an unexpected top-level type must log a warning
    and return no plugins."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    registry = plugins_dir / "installed_plugins.json"
    # A bare JSON string: neither a dict (with 'plugins') nor a list
    registry.write_text('"just a string"', encoding="utf-8")
    settings_path = write_settings(tmp_path, {})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.load_all() == []


def test_missing_plugin_json_manifest_uses_unknown_version(tmp_path):
    """A plugin whose .claude-plugin/plugin.json is absent gets version='unknown'."""
    key = "no-manifest@vendor"
    root = tmp_path / "plugins" / "cache" / key / "1.0.0"
    # Create the plugin dir WITHOUT a plugin.json manifest
    (root / ".claude-plugin").mkdir(parents=True)

    plugins_dir = write_registry_v2(tmp_path, {key: str(root)})
    settings_path = write_settings(tmp_path, {key: True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].version == "unknown"
