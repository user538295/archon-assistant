"""Plugin loader — reads Claude Code plugins and exposes them to the SDK and skill registry."""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from archon.ai.skill_loader import Skill, SkillLoader

logger = logging.getLogger("archon")

_DEFAULT_PLUGINS_DIR = Path.home() / ".claude" / "plugins"
_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


@dataclass
class PluginInfo:
    """Metadata for a single installed and enabled plugin."""

    key: str            # e.g. "claude-mem@thedotmack"
    name: str           # e.g. "claude-mem"
    marketplace: str    # e.g. "thedotmack"
    version: str        # e.g. "10.3.1"
    install_path: str   # absolute path to the plugin cache directory
    description: str = ""
    skills: list["Skill"] = field(default_factory=list)


class PluginLoader:
    """Load enabled Claude Code plugins from the standard plugin registry.

    Reads ``~/.claude/plugins/installed_plugins.json`` (install paths) and
    ``~/.claude/settings.json`` (enabled state), then exposes:

    * ``get_sdk_configs()`` — dicts ready for ``ClaudeAgentOptions.plugins``
    * ``get_skills()``      — namespaced ``Skill`` objects from plugin bundles
    """

    def __init__(
        self,
        plugins_dir: str | None = None,
        settings_path: str | None = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir) if plugins_dir else _DEFAULT_PLUGINS_DIR
        self._settings_path = Path(settings_path) if settings_path else _DEFAULT_SETTINGS_PATH
        self._plugins: list[PluginInfo] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> list[PluginInfo]:
        """Load and cache all enabled plugins. Idempotent after the first call."""
        if self._plugins is not None:
            return self._plugins

        enabled_keys = self._read_enabled_keys()
        installed = self._read_installed_plugins()

        result: list[PluginInfo] = []
        for key, install_path in installed.items():
            if key not in enabled_keys:
                logger.debug("Plugin %s is installed but not enabled — skipping", key)
                continue
            info = self._load_plugin(key, install_path)
            if info is not None:
                result.append(info)

        logger.info("Loaded %d enabled plugin(s): %s", len(result), [p.key for p in result])
        self._plugins = result
        return self._plugins

    def get_sdk_configs(self) -> list[dict[str, str]]:
        """Return plugin configs ready for ``ClaudeAgentOptions.plugins``."""
        return [{"type": "local", "path": p.install_path} for p in self.load_all()]

    def get_skills(self) -> list[Skill]:
        """Return all skills bundled inside enabled plugins."""
        skills: list[Skill] = []
        for plugin in self.load_all():
            skills.extend(plugin.skills)
        return skills

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_enabled_keys(self) -> set[str]:
        """Read ``~/.claude/settings.json`` and return the set of enabled plugin keys."""
        if not self._settings_path.exists():
            logger.debug("settings.json not found at %s", self._settings_path)
            return set()
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            enabled_plugins: dict[str, object] = data.get("enabledPlugins", {})
            return {k for k, v in enabled_plugins.items() if v is True}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read settings.json: %s", exc)
            return set()

    def _read_installed_plugins(self) -> dict[str, str]:
        """Read ``installed_plugins.json`` and return ``{plugin_key: installPath}``.

        Actual format (Claude Code v2 registry)::

            {
              "version": 2,
              "plugins": {
                "claude-mem@thedotmack": [
                  {"installPath": "/path/to/cache/...", ...}
                ]
              }
            }

        The outer ``plugins`` dict maps each plugin key to a *list* of install
        objects; we always use the first element.
        """
        registry = self._plugins_dir / "installed_plugins.json"
        if not registry.exists():
            logger.debug("installed_plugins.json not found at %s", registry)
            return {}
        try:
            data = json.loads(registry.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read installed_plugins.json: %s", exc)
            return {}

        result: dict[str, str] = {}

        # v2 format: {"version": 2, "plugins": {"key": [{"installPath": "..."}]}}
        if isinstance(data, dict) and "plugins" in data:
            plugins_map = data["plugins"]
            if isinstance(plugins_map, dict):
                for key, installs in plugins_map.items():
                    if isinstance(installs, list) and installs:
                        path = installs[0].get("installPath", "")
                    elif isinstance(installs, dict):
                        path = installs.get("installPath", "")
                    else:
                        path = ""
                    if key and path:
                        result[key] = path
            return result

        # Legacy list format: [{"key": "...", "installPath": "..."}]
        if isinstance(data, list):
            for entry in data:
                key = entry.get("key", "")
                path = entry.get("installPath", "")
                if key and path:
                    result[key] = path
            return result

        logger.warning("installed_plugins.json has an unrecognised format; skipping")
        return result

    def _load_plugin(self, key: str, install_path: str) -> PluginInfo | None:
        """Load a single plugin from its install path."""
        root = Path(install_path)
        if not root.is_dir():
            logger.warning("Plugin %s install path does not exist: %s", key, install_path)
            return None

        # Parse key → name + marketplace
        if "@" in key:
            name, marketplace = key.rsplit("@", 1)
        else:
            name, marketplace = key, "unknown"

        # Read manifest
        manifest_path = root / ".claude-plugin" / "plugin.json"
        version = "unknown"
        description = ""
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                version = manifest.get("version", version)
                description = manifest.get("description", description)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read plugin.json for %s: %s", key, exc)

        # Discover bundled skills
        skills = self._load_plugin_skills(name, root)

        return PluginInfo(
            key=key,
            name=name,
            marketplace=marketplace,
            version=version,
            install_path=str(root),
            description=description,
            skills=skills,
        )

    def _load_plugin_skills(self, plugin_name: str, root: Path) -> list[Skill]:
        """Read ``skills/*/SKILL.md`` inside a plugin directory.

        Reuses ``SkillLoader._load_skill()`` to parse frontmatter (which
        extracts the ``name`` and ``description`` fields), then overrides
        the skill name with the namespaced form ``plugin-name:skill-dir-name``.
        """
        skills: list[Skill] = []
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            return skills

        # Temporary loader instance — used only for _load_skill() parsing
        _loader = SkillLoader(skills_dir)

        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            parsed = _loader._load_skill(skill_md)
            if parsed is None:
                continue
            namespaced_name = f"{plugin_name}:{skill_dir.name}"
            skills.append(
                Skill(
                    name=namespaced_name,
                    description=parsed.description,
                    content=parsed.content,
                )
            )
            logger.debug("Loaded plugin skill: %s", namespaced_name)

        return skills
