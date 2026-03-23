# Bug 01 — /status command shows unexpected version string

Status: FIXED

## Description

The `/status` command returns:
> No active session | v0.1.0

The user considers the `| v0.1.0` part confusing and undesirable. The version string was not present before the redesign and its display format is unexpected.

## Context

From `archon/chat/commands.py`:
- Line 99: `f"✅ Session active | v{__version__}"`
- Line 117: `f"ℹ️ No active session | v{__version__}"`

The version comes from `archon/version.py` → `get_version()` which uses `importlib.metadata` or computes a YY.M.<git-count> string.

## Root cause

The version display was added at some point but the format/value is unexpected. The user's complaint: "What the hack is the '| v0.1.0' What is this trash?"

## Tasks

1. Investigate what `get_version()` returns and why it returns "0.1.0"
2. Investigate the UX of showing the version in `/status` - is it useful?
3. Decide: fix the version value, change the format, or remove the version from status
4. Write test(s) for the fix
5. Fix the bug

## AI Notes

### Fix applied 2026-03-11

**Root cause**: `importlib.metadata.version("archon")` resolves to "0.1.0" (the placeholder version in `pyproject.toml`), so `| v0.1.0` appeared in every `/status` response.

**Decision**: Remove the version string from `/status` entirely. The status output is about session state (active/inactive, cwd, uptime, message count) — version info is irrelevant there and unexpected by the user.

**Changes**:
- `archon/chat/commands.py`: Removed `from archon.version import __version__` import. Changed `"✅ Session active | v{__version__}"` → `"✅ Session active"` and `"ℹ️ No active session | v{__version__}"` → `"ℹ️ No active session"`.
- `tests/chat/test_commands.py`: Replaced `test_status_includes_version_when_active` and `test_status_includes_version_when_no_session` with inverted assertions (`assert __version__ not in text`) confirming version is absent.

All 176 tests pass.

## DA Review (2026-03-11)

**Verdict: Fix is correct. No issues found.**

### Evidence

1. **Version string removed from status output** -- Verified. `archon/chat/commands.py` line 98 now reads `"Session active"` (no version interpolation), and line 116 reads `"No active session"` (no version interpolation). Grep for `__version__` in `archon/chat/` returns zero matches. The import `from archon.version import __version__` is gone.

2. **No residual `__version__` usage in commands.py** -- Confirmed. The import was the only consumer in this file. `__version__` is still exported from `archon/__init__.py` and used in `archon/cli/update.py` and `archon/cli/status.py` (CLI commands, not the Telegram `/status` handler), which is correct -- those are separate entry points.

3. **Tests correctly verify absence** -- `test_status_does_not_include_version_when_active` and `test_status_does_not_include_version_when_no_session` both import `__version__` from `archon.version` and assert `__version__ not in text`. This is a sound approach: if someone re-introduces the version string, both tests will catch it.

4. **No other code path shows version in /status** -- The `status_command` function is the sole handler for `/status`. No other file in `archon/chat/` references `__version__`.

5. **Test count note** -- The doc claims "All 176 tests pass" which matches the 176 test functions in `tests/chat/test_commands.py` alone (likely scoped to that file). The full suite has ~1815 test functions. This is not an error -- just a scoped count.

### Minor observation (not a defect)

The two negative tests (`assert __version__ not in text`) are slightly fragile in theory: if `__version__` ever returned an empty string, the assertion would vacuously pass. In practice `archon/version.py` always returns a non-empty version string (either from `importlib.metadata` or the git-count fallback), so this is not a real risk.
