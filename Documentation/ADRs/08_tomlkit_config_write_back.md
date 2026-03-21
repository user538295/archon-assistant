# ADR 08 — tomlkit for Config Write-back

**Purpose**: Architecture decision record for using tomlkit over stdlib tomllib for config persistence
**Audience**: Backend engineers
**Status**: Accepted
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

---

## Status

Accepted

## Date

2026-02-26

## Context

Archon's runtime settings (notification mode, interval, agent mode) can be changed via Telegram commands (e.g. `/notify`, `/quiet`, `/verbose`). These changes must persist across restarts in `config.toml`. The config file contains user-authored comments and hand-aligned values that must be preserved on every write.

Python 3.11+ ships with `tomllib` in the standard library for TOML **reading**, but `tomllib` is read-only by design. Writing TOML requires either:

1. Serialising from scratch (losing all comments and formatting)
2. Using a round-trip TOML library that preserves comments

Additionally, `config.toml` is a structured file with multiple sections; a write operation should only touch the specific keys being updated (`[notifications]`), leaving all other sections byte-identical to what the user wrote.

## Decision

Use **`tomlkit`** (a third-party TOML read/write library with comment preservation) for all write-back operations. Read operations at startup continue to use stdlib `tomllib` (faster, no extra dependency needed there).

The pattern in `archon/config/loader.py`:

```python
# Read at startup — fast, stdlib
import tomllib
with config_path.open("rb") as f:
    data = tomllib.load(f)

# Write at runtime — round-trip, preserves comments
import tomlkit
def save_notifications_config(notifications, config_file):
    with path.open("r", encoding="utf-8") as f:
        doc = tomlkit.load(f)
    doc["notifications"]["mode"] = notifications.mode
    doc["notifications"]["interval_minutes"] = notifications.interval_minutes
    atomic_write(path, tomlkit.dumps(doc))
```

Write-back is always performed via `atomic_write()` (write to `.toml.tmp`, then `os.replace()`), which is atomic on the same filesystem. A `.toml.bak` backup is created on every successful read so the system can self-heal from a corrupt `config.toml`.

## Consequences

### Positive

- User comments, blank lines, and formatting in `config.toml` survive runtime saves.
- Only the `[notifications]` section is modified; all other sections are byte-identical.
- The atomic write pattern prevents corruption if the process is killed mid-write.
- Automatic backup (`config.toml.bak`) enables self-healing on next startup.

### Negative

- `tomlkit` is an additional production dependency (not in stdlib).
- Two different libraries parse the same file format (`tomllib` at startup, `tomlkit` at runtime), which is conceptually odd.
- `tomlkit` is marginally slower than `tomllib` for reads (not a concern — writes happen infrequently).

## Alternatives Considered

### tomllib for reads + manual string manipulation for writes

Find the `[notifications]` section with regex and replace values. Fragile — breaks on valid TOML variations like inline tables or multi-line strings.

### tomlkit for everything (reads and writes)

Consistent but gives up the stdlib's validation speed for the read path. Since reads happen once at startup and writes happen rarely (user command), the asymmetry is acceptable.

### Store mutable runtime state separately (e.g. a `state.toml`)

Keep `config.toml` read-only and write runtime changes to a separate `~/.archon/state.toml`. Cleaner architecture but would break the user expectation that `config.toml` reflects the current config state.

### Use JSON or SQLite for runtime state

Both support read/write in stdlib. Rejected because the rest of the config is TOML and user-editable — introducing a second format for a small subset of keys would be confusing.

## Related Documents

- `archon/config/loader.py` — `save_notifications_config`, `atomic_write`
- [`Documentation/Architecture/130_data_architecture_and_persistence.md`](../Architecture/130_data_architecture_and_persistence.md) — atomic write pattern and backup mechanism
- [`Documentation/Architecture/500_development_workflows_and_conventions.md`](../Architecture/500_development_workflows_and_conventions.md) — dependency addition rationale
