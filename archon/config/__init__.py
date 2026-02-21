"""Config package — exposes typed config singleton and loader utilities."""
from archon.config.loader import Config, ConfigError, load_config

_config: Config | None = None

# Type annotation only (no value) — __getattr__ handles runtime access (PEP 562).
config: Config


def __getattr__(name: str) -> object:
    global _config
    if name == "config":
        if _config is None:
            _config = load_config()
        return _config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Config", "ConfigError", "load_config", "config"]
