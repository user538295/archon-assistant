# Plugin Support Implementation Plan

## 1. Overview

This plan adds Claude Code plugin support to Archon. When the Archon daemon starts, it reads the same plugin registry and enabled-state files that the Claude Code TUI uses (`~/.claude/plugins/installed_plugins.json` and `~/.claude/settings.json`), resolves which plugins are enabled, and passes their local paths to the Claude Agent SDK via `ClaudeAgentOptions.plugins`. The SDK then handles everything internally: MCP server startup, `CLAUDE.md` system prompt injection, and skill availability.

As a secondary concern, Archon's own skill registry (used by the `/skills` Telegram command) is extended to also enumerate skills bundled inside plugins, so users can see the full set of available skills without leaving Telegram.

The primary motivation is that `claude-mem` (and similar installed plugins) provide persistent cross-session memory via MCP. Without this change, Archon's Claude sessions have no access to those tools and the memory commands users expect simply do not work.

## 2. Architecture

Two-layer strategy:

### Layer 1 — SDK-native plugin loading (runtime capability)

`ClaudeAgentOptions` accepts a `plugins` list of `{"type": "local", "path": str}` dicts. Passing an installed plugin's cache directory here is sufficient for the SDK to:

- Start MCP servers defined in the plugin's `.mcp.json` (with `${CLAUDE_PLUGIN_ROOT}` resolved)
- Inject the plugin's `CLAUDE.md` into the system prompt
- Expose any tools/resources the MCP servers provide

This is the only thing needed for actual Claude capability extension.

### Layer 2 — Archon skill registry extension (UX / `/skills` command)

The existing `SkillLoader` reads `~/.claude/skills/*/SKILL.md`. Plugins bundle their own skills under `skills/*/SKILL.md` inside the plugin cache directory. A new `PluginLoader` class reads both the SDK plugin configs (Layer 1) and the plugin-bundled skills, merging them into the skill list the `/skills` command shows.

Dependency flow after the change:

```
Gateway._run()
  ├── SkillLoader()          — personal skills (~/.claude/skills/)
  ├── PluginLoader(cfg)      — enabled plugins
  │     ├── .get_sdk_configs()  → list[SdkPluginConfig]  → ClaudeSession → SDK
  │     └── .get_skills()       → list[Skill]            → SessionManager → ClaudeSession system prompt
  └── SessionManager(plugin_loader=plugin_loader, skill_loader=skill_loader)
        └── factory → ClaudeSession(plugins=[...], skills=[...])
```

## 3. New file: `archon/ai/plugin_loader.py`

### Responsibilities

- Read `~/.claude/plugins/installed_plugins.json` to get the install path of each plugin.
- Read `~/.claude/settings.json` to filter down to enabled plugins only.
- For each enabled plugin, load its manifest (`plugin.json`) and discover bundled skills.
- Expose `get_sdk_configs()` → list of dicts ready for `ClaudeAgentOptions.plugins`.
- Expose `get_skills()` → list of `Skill` objects (namespaced with `plugin-name:skill-name`).

### Dataclass: `PluginInfo`

```python
from dataclasses import dataclass, field

@dataclass
class PluginInfo:
    key: str            # e.g. "claude-mem@thedotmack"
    name: str           # e.g. "claude-mem"
    marketplace: str    # e.g. "thedotmack"
    version: str        # e.g. "10.3.1"
    install_path: str   # absolute path to plugin cache dir
    description: str = ""
    skills: list = field(default_factory=list)  # list[Skill]
```

### Class: `PluginLoader`

```python
import json
import logging
from pathlib import Path

from archon.ai.skill_loader import Skill

logger = logging.getLogger(__name__)

_DEFAULT_PLUGINS_DIR = Path.home() / ".claude" / "plugins"
_DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


class PluginLoader:
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
        """Load and cache all enabled plugins. Idempotent after first call."""
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

    def get_sdk_configs(self) -> list[dict]:
        """Return plugin configs ready for ClaudeAgentOptions.plugins."""
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
        """Read ~/.claude/settings.json and return the set of enabled plugin keys."""
        if not self._settings_path.exists():
            logger.debug("settings.json not found at %s", self._settings_path)
            return set()
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            enabled_plugins: dict = data.get("enabledPlugins", {})
            return {k for k, v in enabled_plugins.items() if v is True}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read settings.json: %s", exc)
            return set()

    def _read_installed_plugins(self) -> dict[str, str]:
        """Read installed_plugins.json → {key: installPath}."""
        registry = self._plugins_dir / "installed_plugins.json"
        if not registry.exists():
            logger.debug("installed_plugins.json not found at %s", registry)
            return {}
        try:
            data = json.loads(registry.read_text(encoding="utf-8"))
            # Format: list of {"key": "...", "installPath": "..."}
            #   OR:  dict of {"key": {"installPath": "..."}}
            # Handle both shapes defensively.
            result: dict[str, str] = {}
            if isinstance(data, list):
                for entry in data:
                    key = entry.get("key", "")
                    path = entry.get("installPath", "")
                    if key and path:
                        result[key] = path
            elif isinstance(data, dict):
                for key, val in data.items():
                    if isinstance(val, dict):
                        path = val.get("installPath", "")
                    else:
                        path = str(val)
                    if path:
                        result[key] = path
            return result
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read installed_plugins.json: %s", exc)
            return {}

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
        """Read skills/*/SKILL.md inside a plugin directory."""
        skills: list[Skill] = []
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            return skills

        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                content = skill_md.read_text(encoding="utf-8")
                # Namespace the skill name: "plugin-name:skill-dir-name"
                namespaced_name = f"{plugin_name}:{skill_dir.name}"
                skills.append(Skill(name=namespaced_name, content=content))
                logger.debug("Loaded plugin skill: %s", namespaced_name)
            except OSError as exc:
                logger.warning("Could not read skill %s: %s", skill_md, exc)

        return skills
```

## 4. Modified: `archon/ai/claude_session.py`

Add a `plugins` parameter to `__init__` and pass it through to `ClaudeAgentOptions`.

Current signature:
```python
def __init__(self, cwd: str | None = None, skills: list[Skill] | None = None, model: str | None = None)
```

New signature:
```python
def __init__(
    self,
    cwd: str | None = None,
    skills: list[Skill] | None = None,
    model: str | None = None,
    plugins: list[dict] | None = None,   # list[SdkPluginConfig]
)
```

Inside `start()`, where `ClaudeAgentOptions` is constructed, add `plugins=self._plugins or []`:

```python
options = ClaudeAgentOptions(
    permission_mode="bypassPermissions",
    cwd=self._cwd,
    system_prompt=self._build_system_prompt(),
    model=self._model,
    plugins=self._plugins or [],
)
```

Store in `__init__`:
```python
self._plugins: list[dict] | None = plugins
```

No other changes required in this file. The SDK handles the rest.

## 5. Modified: `archon/ai/session_manager.py`

Add a `plugin_loader` parameter. The factory lambda combines personal skills (from `skill_loader`) and plugin-bundled skills (from `plugin_loader`), and also passes SDK plugin configs to `ClaudeSession`.

New `__init__` signature:
```python
def __init__(
    self,
    timeout,
    cwd=None,
    session_factory=None,
    skill_loader=None,
    plugin_loader=None,       # NEW: PluginLoader | None
)
```

Updated factory construction (inside `__init__`, where the default factory lambda is built):
```python
if session_factory is None:
    def session_factory(c):
        # Collect skills from personal skill loader
        personal_skills = skill_loader.load_all() if skill_loader else []

        # Collect skills bundled inside plugins
        plugin_skills = plugin_loader.get_skills() if plugin_loader else []

        # Collect SDK plugin configs
        sdk_plugins = plugin_loader.get_sdk_configs() if plugin_loader else []

        return ClaudeSession(
            cwd=c,
            skills=personal_skills + plugin_skills,
            model=self._model,
            plugins=sdk_plugins,
        )
    self._session_factory = session_factory
else:
    self._session_factory = session_factory
```

Store the plugin_loader reference:
```python
self._plugin_loader = plugin_loader
```

No changes to session creation logic outside of the factory.

## 6. Modified: `archon/ai/__init__.py`

Add exports for the new types so callers can import from `archon.ai` consistently.

```python
from archon.ai.plugin_loader import PluginInfo, PluginLoader

__all__ = [
    ...,          # existing exports
    "PluginInfo",
    "PluginLoader",
]
```

## 7. Modified: `archon/gateway/gateway.py`

In `Gateway._run()`, instantiate `PluginLoader` using config values and pass it to `SessionManager`.

```python
from archon.ai.plugin_loader import PluginLoader

async def _run(self) -> None:
    ...
    skill_loader = SkillLoader()

    plugin_loader = PluginLoader(
        plugins_dir=self._config.plugins.plugins_dir or None,
        settings_path=self._config.plugins.settings_path or None,
    )

    session_manager = SessionManager(
        timeout=self._config.session.timeout,
        cwd=self._config.session.cwd,
        skill_loader=skill_loader,
        plugin_loader=plugin_loader,
    )
    ...
```

If `plugins.enabled` is `False` in config, pass `plugin_loader=None` instead so the feature can be disabled without code changes:

```python
plugin_loader = (
    PluginLoader(
        plugins_dir=self._config.plugins.plugins_dir or None,
        settings_path=self._config.plugins.settings_path or None,
    )
    if self._config.plugins.enabled
    else None
)
```

## 8. Modified: `archon/config/loader.py` and `config.toml`

### `loader.py` — add `PluginsConfig` dataclass

```python
@dataclass
class PluginsConfig:
    enabled: bool = True
    plugins_dir: str = ""       # empty = use default (~/.claude/plugins/)
    settings_path: str = ""     # empty = use default (~/.claude/settings.json)
```

Add it as a field on the top-level config dataclass:

```python
@dataclass
class Config:
    access: AccessConfig
    session: SessionConfig
    output: OutputConfig
    logging: LoggingConfig
    notifications: NotificationsConfig
    history: HistoryConfig
    models: ModelsConfig
    plugins: PluginsConfig = field(default_factory=PluginsConfig)  # NEW
```

Update the loader function to parse the `[plugins]` table:

```python
plugins_raw = data.get("plugins", {})
plugins = PluginsConfig(
    enabled=plugins_raw.get("enabled", True),
    plugins_dir=plugins_raw.get("plugins_dir", ""),
    settings_path=plugins_raw.get("settings_path", ""),
)
```

### `config.toml` — add `[plugins]` section

```toml
[plugins]
# Set to false to disable plugin loading entirely.
enabled = true

# Override the default plugin directory (~/.claude/plugins/).
# Leave empty to use the default.
plugins_dir = ""

# Override the path to ~/.claude/settings.json (for enabled plugin state).
# Leave empty to use the default.
settings_path = ""
```

## 9. Updated `commands.py` — `/skills` command

The `/skills` command currently calls `skill_loader.load_all()` to list skills. After this change, plugin-bundled skills appear in the system automatically because `SessionManager` already merges them. The `/skills` display should also show them.

The cleanest approach: give the command handler access to the `plugin_loader` reference (pass it alongside `skill_loader` when registering the router, the same way `skill_loader` is passed today).

In the handler:

```python
async def cmd_skills(message: Message, skill_loader: SkillLoader, plugin_loader: PluginLoader | None) -> None:
    personal = skill_loader.load_all()
    plugin_skills = plugin_loader.get_skills() if plugin_loader else []
    plugin_infos = plugin_loader.load_all() if plugin_loader else []

    lines: list[str] = []

    if personal:
        lines.append("Personal skills (~/.claude/skills/):")
        for s in personal:
            lines.append(f"  • {s.name}")
    else:
        lines.append("No personal skills found.")

    if plugin_infos:
        lines.append("")
        lines.append("Plugin skills:")
        for plugin in plugin_infos:
            if plugin.skills:
                lines.append(f"  [{plugin.key} v{plugin.version}]")
                for s in plugin.skills:
                    lines.append(f"    • {s.name}")

    if not personal and not plugin_skills:
        await message.answer("No skills available.")
        return

    await message.answer("\n".join(lines))
```

The `plugin_loader` is injected using aiogram's dependency injection the same way `skill_loader` already is — registered as middleware data or passed via router extras at setup time in `gateway.py`.

## 10. Testing Plan

Create `tests/ai/test_plugin_loader.py`.

### Test structure

Use `tmp_path` (pytest fixture) to create fake plugin directories.

```python
import json
from pathlib import Path
import pytest
from archon.ai.plugin_loader import PluginLoader, PluginInfo


def make_plugin_dir(tmp_path: Path, key: str, version: str = "1.0.0", skills: list[str] | None = None) -> Path:
    """Helper: create a minimal plugin cache directory."""
    name = key.split("@")[0]
    root = tmp_path / "plugins" / "cache" / key / version
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version, "description": "test"}),
        encoding="utf-8",
    )
    if skills:
        for skill_name in skills:
            skill_dir = root / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {skill_name}\nSkill content.", encoding="utf-8")
    return root


def write_registry(tmp_path: Path, entries: list[dict]) -> Path:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    registry = plugins_dir / "installed_plugins.json"
    registry.write_text(json.dumps(entries), encoding="utf-8")
    return plugins_dir


def write_settings(tmp_path: Path, enabled: dict) -> Path:
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": enabled}), encoding="utf-8")
    return settings
```

### Test cases

```python
def test_load_all_returns_enabled_plugins(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry(tmp_path, [{"key": "claude-mem@thedotmack", "installPath": str(root)}])
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].key == "claude-mem@thedotmack"
    assert plugins[0].version == "10.3.1"


def test_disabled_plugin_excluded(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry(tmp_path, [{"key": "claude-mem@thedotmack", "installPath": str(root)}])
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": False})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.load_all() == []


def test_get_sdk_configs_format(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry(tmp_path, [{"key": "claude-mem@thedotmack", "installPath": str(root)}])
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    configs = loader.get_sdk_configs()

    assert configs == [{"type": "local", "path": str(root)}]


def test_get_skills_namespaced(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1", skills=["mem-search", "mem-add"])
    plugins_dir = write_registry(tmp_path, [{"key": "claude-mem@thedotmack", "installPath": str(root)}])
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    skills = loader.get_skills()

    names = [s.name for s in skills]
    assert "claude-mem:mem-search" in names
    assert "claude-mem:mem-add" in names


def test_missing_registry_returns_empty(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    settings_path = write_settings(tmp_path, {})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    assert loader.load_all() == []


def test_missing_settings_treats_all_disabled(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry(tmp_path, [{"key": "claude-mem@thedotmack", "installPath": str(root)}])
    # Do NOT write settings.json

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(tmp_path / "nonexistent.json"))
    assert loader.load_all() == []


def test_plugin_without_skills(tmp_path):
    root = make_plugin_dir(tmp_path, "swift-lsp@claude-plugins-official", "1.0.0")
    plugins_dir = write_registry(tmp_path, [{"key": "swift-lsp@claude-plugins-official", "installPath": str(root)}])
    settings_path = write_settings(tmp_path, {"swift-lsp@claude-plugins-official": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    plugins = loader.load_all()

    assert len(plugins) == 1
    assert plugins[0].skills == []
    assert loader.get_skills() == []


def test_load_all_is_cached(tmp_path):
    root = make_plugin_dir(tmp_path, "claude-mem@thedotmack", "10.3.1")
    plugins_dir = write_registry(tmp_path, [{"key": "claude-mem@thedotmack", "installPath": str(root)}])
    settings_path = write_settings(tmp_path, {"claude-mem@thedotmack": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    first = loader.load_all()
    second = loader.load_all()
    assert first is second  # same list object, not reloaded


def test_invalid_install_path_skipped(tmp_path):
    plugins_dir = write_registry(tmp_path, [{"key": "bad-plugin@test", "installPath": "/nonexistent/path"}])
    settings_path = write_settings(tmp_path, {"bad-plugin@test": True})

    loader = PluginLoader(plugins_dir=str(plugins_dir), settings_path=str(settings_path))
    # Should not raise; bad plugin is skipped with a warning
    assert loader.load_all() == []
```

## 11. Implementation Order

Follow this order to keep the diff reviewable and each step individually testable:

1. **`archon/ai/plugin_loader.py`** — create the new file with `PluginInfo` and `PluginLoader`. No other code references it yet; can be unit-tested in isolation immediately.

2. **`tests/ai/test_plugin_loader.py`** — write and run the tests above. Confirm all pass before touching existing code.

3. **`archon/config/loader.py`** — add `PluginsConfig` dataclass and parse `[plugins]` table. Keep the field optional with a default so existing configs without `[plugins]` continue to work.

4. **`config.toml`** — append the `[plugins]` section with commented defaults.

5. **`archon/ai/claude_session.py`** — add `plugins` parameter to `__init__`, store as `self._plugins`, pass to `ClaudeAgentOptions` in `start()`. The parameter is optional and defaults to `None`, so no callers break.

6. **`archon/ai/session_manager.py`** — add `plugin_loader` parameter, update the default factory lambda to call `plugin_loader.get_sdk_configs()` and `plugin_loader.get_skills()`.

7. **`archon/ai/__init__.py`** — add `PluginLoader` and `PluginInfo` to exports.

8. **`archon/gateway/gateway.py`** — instantiate `PluginLoader` and pass it to `SessionManager`. Wire up the `enabled` config flag.

9. **`archon/chat/commands.py`** — update `/skills` handler to accept `plugin_loader` and display plugin-bundled skills. Update the registration call in `gateway.py` to inject `plugin_loader`.

10. **Manual smoke test** — restart the Archon daemon, send a message in Telegram that exercises `claude-mem` (e.g. `search my memory for X`). Confirm the MCP tool calls appear in the event stream.

## 12. Expected Behavior After Implementation

**On startup**, Archon reads:
- `~/.claude/plugins/installed_plugins.json` → finds `claude-mem@thedotmack` at its cache path and `swift-lsp@claude-plugins-official`
- `~/.claude/settings.json` → finds `claude-mem@thedotmack: true`, `swift-lsp@claude-plugins-official: true` (or whatever the user has set)

**Per session**, `ClaudeSession.start()` passes both enabled plugin paths to the SDK via `plugins=[{"type": "local", "path": "..."}, ...]`. The SDK starts the `mcp-search` MCP server (defined in `claude-mem`'s `.mcp.json`) and injects `claude-mem`'s `CLAUDE.md` into the system prompt.

**Effect on Claude**: The `mcp-search` tool becomes available in every Archon session, identical to how it works in the Claude Code TUI. Memory search and storage commands work transparently.

**`/skills` command**: Shows two sections — personal skills from `~/.claude/skills/` and plugin skills from enabled plugins, with namespaced names like `claude-mem:mem-search`.

**Disabling**: Setting `enabled = false` in `[plugins]` in `config.toml` skips plugin loading entirely, restoring the pre-change behavior without code modification.

**Plugins with no MCP/skills** (e.g. `swift-lsp` which only has a README): `PluginLoader` loads them, passes the install path to the SDK, but `get_skills()` returns nothing for them. The SDK determines whether there is anything useful in the directory.
