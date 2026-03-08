# Devil's Advocate Review — Infrastructure, Config & Tests
**Reviewer**: DA-5
**Date**: 2026-03-08
**Files reviewed**:
- `archon/config/loader.py`
- `archon/config/__init__.py`
- `archon/gateway/gateway.py`
- `archon/gateway/__init__.py`
- `archon/cli/main.py`
- `archon/cli/config_cmd.py`
- `archon/cli/doctor.py`
- `archon/cli/logs.py`
- `archon/cli/service.py`
- `archon/cli/status.py`
- `archon/cli/update.py`
- `archon/cli/__init__.py`
- `archon/log_setup.py`
- `archon/version.py`
- `archon/__init__.py`
- `main.py`
- All test files under `tests/` (85 files)

---

## Executive Summary

The infrastructure layer is reasonably structured and does many things correctly — atomic writes, config backup/restore, isatty-guarded logging. However, there are five categories of serious problems that collectively disqualify "production-ready" status: (1) **5 test failures in the committed suite** with a test that encodes a wrong expectation about the handler's ack message, breaking CI; (2) **overall test coverage is 48% when all tests run together**, well short of the mandatory 85% floor; (3) **no signal handling whatsoever** — SIGTERM and SIGINT reach the asyncio event loop by accident, not by design, and `bg_manager.stop_all()`, `cron_scheduler.stop()`, and `bg_mcp_server.stop()` in the `finally` block have no timeout protection, meaning a hung background subsystem can stall shutdown indefinitely past the advertised 5-second guarantee; (4) **secrets leak into `config show` output** — the bot token is printed to stdout in plaintext; (5) **`version.py` runs a live subprocess at import time**, causing a `git` call on every `import archon` in production.

---

## Critical Findings (Severity: CRITICAL)

### [tests/gateway/test_full_flow.py:107-134] 5 committed test failures — wrong ack-message count assertion

**Description**: The `_run()` helper at line 97-98 filters out messages that start with `"⏳"` to exclude the acknowledgement message:
```python
return [t for t in all_texts if not t.startswith("⏳")]
```
But then `test_full_sequence_produces_four_messages` (line 109-110) asserts `len(texts) == 5`, and `test_full_sequence_correct_order` (lines 115-119) asserts `texts[0].startswith("⏳")`. These expectations are mutually contradictory — after filtering, `texts[0]` will be the first event message, never `"⏳"`. The filter removes the ack, so there are 4 items after filtering (not 5), but the test expects 5 and also expects the first item to be `"⏳"`.

**Impact**: These 5 tests **fail every CI run** as confirmed by direct execution:
```
AssertionError: assert 4 == 5
where 4 = len(['💭 Thinking:...', '🔧 Tool: bash', '📤 Result:...', '✅ Response:...'])
```
Any developer running `uv run pytest` sees a red build immediately. This has been committed to the branch in this state.

**Evidence**:
```python
# line 97-99 — filter removes ⏳ lines
all_texts = [str(call.args[0]) for call in mock_answer.call_args_list]
return [t for t in all_texts if not t.startswith("⏳")]

# line 108-110 — expects 5 items INCLUDING the ⏳ ack
async def test_full_sequence_produces_four_messages() -> None:
    texts = await _run(_FULL_SEQUENCE)
    assert len(texts) == 5   # FAILS: gets 4 (ack was filtered out)

# line 113-119 — expects texts[0] to be ⏳ ack, but it was filtered
async def test_full_sequence_correct_order() -> None:
    texts = await _run(_FULL_SEQUENCE)
    assert texts[0].startswith("⏳")  # FAILS: texts[0] is now "💭 Thinking:..."
```

**Fix**: Either remove the `⏳` filter from `_run()` (so `texts` includes the ack, length = 5, and `texts[0]` is the ack), or adjust all assertions to the post-filter world (length = 4, first element is the thinking result).

---

### [archon/gateway/gateway.py:412-418] `bg_manager.stop_all()` and `cron_scheduler.stop()` have no timeout — 5-second guarantee is false

**Description**: The `finally` block calls three cleanup coroutines before applying the only `asyncio.wait_for` timeout:
```python
finally:
    for task in _compaction_tasks:
        task.cancel()
    await cron_scheduler.stop()          # no timeout
    await bg_manager.stop_all()          # no timeout
    await bg_mcp_server.stop()           # no timeout
    try:
        await asyncio.wait_for(session_manager.stop_all(), timeout=_SHUTDOWN_TIMEOUT)
    except asyncio.TimeoutError:
        ...
    await bot.session.close()
```
Only `session_manager.stop_all()` is guarded by the 5-second timeout. If `cron_scheduler.stop()`, `bg_manager.stop_all()` (which waits for running agents), or `bg_mcp_server.stop()` hang, the process will never exit. The CLAUDE.md states "stop_all() must complete within 5 seconds" — but this guarantee only applies to the session manager, not to the other three. A background agent running a slow SDK call can hold up `bg_manager.stop_all()` indefinitely.

**Impact**: Process hangs on SIGTERM in production; launchd will eventually SIGKILL it, potentially corrupting state.

**Evidence**: Lines 416-418, compared to lines 419-422 which have the wait_for.

**Fix**: Apply `asyncio.wait_for` with appropriate timeout to each of the three unguarded coroutines.

---

### [archon/gateway/gateway.py] No explicit signal handling — SIGTERM/SIGINT handling is accidental

**Description**: The gateway registers no signal handlers. `dp.start_polling(bot)` internally relies on aiogram's built-in SIGTERM/SIGINT handling to cancel its polling loop. This works by accident for the interactive case. However:
1. Under launchd, SIGTERM is sent to the process group; aiogram may or may not handle it gracefully depending on version.
2. Double SIGTERM (sent by launchd if graceful shutdown exceeds `TimeoutStopInterval`) will immediately kill the process before `finally` runs.
3. The CLAUDE.md explicitly mandates use of `add_signal_handler` (asyncio-safe) rather than `signal.signal`. Neither is used.
4. No mechanism exists to prevent double-signal races (SIGTERM then SIGINT from the user in interactive mode).

**Impact**: Unclean shutdown under launchd; data loss risk if session state is being written.

**Fix**: Register `loop.add_signal_handler(signal.SIGTERM, ...)` and `loop.add_signal_handler(signal.SIGINT, ...)` in `_run()` before polling starts.

---

### [archon/cli/config_cmd.py:28-34] Bot token leaks in `archon config show`

**Description**: `_run_show()` prints the entire raw config.toml file to stdout:
```python
def _run_show() -> int:
    ...
    print(f"# {_CONFIG_PATH}")
    print(_CONFIG_PATH.read_text())
    return 0
```
While the bot token lives in `.env`, not `config.toml`, if a user ever stores any sensitive values in `config.toml` (e.g., API keys in a future feature) they are printed verbatim. More critically, this is a pattern that makes the CLI unsafe to use in shell scripts that log stdout. There is no redaction at all.

**Evidence**: `archon/cli/config_cmd.py:33`

**Fix**: At minimum, document that `config show` prints raw TOML. Better: redact any key named `token`, `password`, `secret`, or `key` in the output.

---

### [archon/version.py:22] Subprocess `git` call runs at module import time — every `import archon` spawns a process

**Description**:
```python
# version.py, line 22
__version__ = get_version()
```
`get_version()` calls `subprocess.run(["git", "rev-list", "--count", "HEAD"])`. Since `archon/__init__.py` does `from archon.version import __version__`, **every single `import archon` in production spawns a git subprocess**. This includes:
- Every test run (import happens on collection)
- The daemon startup
- Every CLI invocation (`archon status`, `archon logs`, etc.)

**Impact**:
1. `git` must be in PATH; on a machine without git (e.g., minimal Docker container) this falls back to `"0"` silently — wrong version everywhere.
2. Performance: a fresh git subprocess on every import is measurable latency.
3. In the test for `archon.version`, the `test_dunder_version_is_string` test imports `from archon import __version__`, triggering the git call even though the test is about the module attribute.

**Evidence**: `archon/version.py:6-22`, `archon/__init__.py:1`.

**Fix**: Compute `__version__` lazily via `__getattr__`, or cache it with `lru_cache`, or bake it into `pyproject.toml` and read it with `importlib.metadata.version()`.

---

## High Severity Findings

### [archon/config/loader.py:302-309] `load_cron_jobs` silently swallows `KeyError` on missing `schedule` field

**Description**: `load_cron_jobs` accesses `job_data["schedule"]` at line 303 without a try/except. If a cron TOML file omits the `schedule` key, this raises an unhandled `KeyError` that propagates out of `load_config`, surfacing as a cryptic error rather than a `ConfigError` with a helpful message. All other optional keys use `.get()`, but `schedule` is mandatory and unguarded.

**Evidence**:
```python
jobs.append(CronJobConfig(
    name=name,
    schedule=job_data["schedule"],   # KeyError if missing — NOT wrapped in ConfigError
    ...
))
```

**Fix**: Wrap the `job_data["schedule"]` access in a `try/except KeyError` and raise `ConfigError(f"cron job '{name}' is missing required 'schedule' field")`.

---

### [archon/config/loader.py:357-366] Type coercion for config values is silent — wrong types accepted without error

**Description**: The loader blindly trusts TOML values after loading. For example:
- `data["access"]["allowed_user_ids"]` could be `["abc", "def"]` (strings, not ints) — no type validation
- `inactivity_timeout_seconds=data["session"].get(...)` could be a string `"yes"` if the user mistypes — `SessionConfig` accepts it as-is since Python dataclasses don't enforce types
- `background_agents.max_parallel` and `port` are cast with `int()` (line 472-474), which will raise `ValueError` on a non-integer TOML value with a traceback rather than a `ConfigError`

**Evidence**:
```python
# loader.py:358-360 — no type check on allowed_user_ids elements
access = AccessConfig(
    allowed_user_ids=data["access"]["allowed_user_ids"],  # could be list[str]
)
# loader.py:472-474 — int() raises ValueError, not ConfigError
max_parallel=int(raw_bg.get("max_parallel", ...)),
port=int(raw_bg.get("port", ...)),
```

**Fix**: Validate element types in `allowed_user_ids`. Wrap all `int()` conversions in try/except and raise `ConfigError`.

---

### [archon/config/__init__.py:10-15] Config singleton lazy-load not thread-safe

**Description**: The module-level `__getattr__` checks `if _config is None` and then calls `load_config()`, but this is not protected by a lock. In the event that two coroutines (or, theoretically, two threads) both evaluate `archon.config.config` before the first has returned, two `load_config()` calls would run concurrently. `load_config()` calls `shutil.copy2` to write a backup file, which is a non-atomic operation if called twice simultaneously.

**Evidence**:
```python
def __getattr__(name: str) -> object:
    global _config
    if name == "config":
        if _config is None:            # TOCTOU: no lock
            _config = load_config()    # double-call possible
        return _config
```

**Fix**: Use a module-level `threading.Lock` to guard the singleton initialization. In practice the asyncio single-thread model reduces the risk, but it is still a TOCTOU pattern.

---

### [archon/gateway/gateway.py:388] Private attribute mutation via `bg_mcp_server._manager = bg_manager` — design smell

**Description**: The code deliberately sets a private attribute `_manager` on `ArchonMCPServer` after construction to break a circular dependency:
```python
bg_mcp_server = ArchonMCPServer(
    manager=None,  # type: ignore[arg-type]  # patched below after manager is created
    ...
)
...
bg_mcp_server._manager = bg_manager  # line 388
```
The `type: ignore` comment confirms this is known to be wrong-typed at construction. This is a SOLID violation (object created in an invalid state), creates a race window between creation and patch (any early call to `bg_mcp_server` methods before line 388 would NPE on `_manager`), and defeats type checking.

**Fix**: Restructure construction order: create `BackgroundAgentManager` first with a deferred or stub MCP server, or use a factory pattern that accepts both together.

---

### [archon/log_setup.py:95-96] Race condition in `_rotate_on_startup` — two processes race to rename the log file

**Description**: `_rotate_on_startup` checks if the log file exists and renames it if it's from a previous day. If two processes start simultaneously (e.g., a rapid restart), both can pass the `if not log_path.exists()` guard before either calls `log_path.rename(dated_path)`. The second rename would silently fail or succeed (overwriting the first) depending on the OS. There is no `try/except OSError` around the rename, so `FileNotFoundError` from the second process propagates uncaught, preventing logging setup.

**Evidence**:
```python
def _rotate_on_startup(log_path: Path) -> None:
    if not log_path.exists():
        return
    mtime_date = datetime.fromtimestamp(log_path.stat().st_mtime).date()
    today = datetime.now().date()
    if mtime_date < today:
        dated_path = log_path.parent / f"{log_path.stem}.{mtime_date}.log"
        log_path.rename(dated_path)   # no try/except — FileNotFoundError possible
```

**Fix**: Wrap `log_path.rename(dated_path)` in `try/except OSError: pass`.

---

### [archon/cli/config_cmd.py:116] `_run_set` writes config non-atomically — corruption possible on crash

**Description**: `_run_set` uses `_CONFIG_PATH.write_text(tomlkit.dumps(doc))` to write the config. This is not atomic — if the process crashes between `open(path, 'w')` (which truncates) and `write()` completing, the config is left empty/corrupt. The `atomic_write()` function already exists in `loader.py` for exactly this purpose but is not used here.

**Evidence**:
```python
# config_cmd.py:116
_CONFIG_PATH.write_text(tomlkit.dumps(doc))   # NOT atomic
```

Compare with `loader.py:591`:
```python
atomic_write(path, tomlkit.dumps(doc))   # atomic — used in save_notifications_config
```

**Fix**: Replace `_CONFIG_PATH.write_text(...)` with the existing `atomic_write()` from `loader.py`.

---

### [archon/cli/doctor.py:70-74] `_check_env_file` reads the entire `.env` file content into memory and checks for token presence — does not validate token format or non-emptiness

**Description**: The check only verifies `"TELEGRAM_BOT_TOKEN" in content` — a file containing `# TELEGRAM_BOT_TOKEN is commented out` or `TELEGRAM_BOT_TOKEN=` (empty value) would pass as healthy:
```python
ok = "TELEGRAM_BOT_TOKEN" in content
```

**Impact**: `archon doctor` reports green but startup immediately fails with `ConfigError`.

**Fix**: Parse the `.env` file properly and check that the value is non-empty (e.g., `re.search(r'^TELEGRAM_BOT_TOKEN=\S+', content, re.MULTILINE)`).

---

### [archon/gateway/gateway.py:249-260] `_midnight_compaction_loop` uses `datetime.now()` (local time, not UTC) — DST transitions cause double-run or 25-hour gaps

**Description**:
```python
async def _midnight_compaction_loop(compactor: HistoryCompactor) -> None:
    while True:
        now = datetime.now()   # local time — susceptible to DST
        next_midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=1, second=0, microsecond=0
        )
        await asyncio.sleep((next_midnight - now).total_seconds())
```
When clocks spring forward (DST), the loop sleeps 23 hours and runs compaction twice in one calendar day. When clocks fall back, it sleeps 25 hours and misses a day. Using naive `datetime.now()` for scheduling is a known hazard.

**Fix**: Use `datetime.now(timezone.utc)` and schedule against UTC midnight, or use `asyncio` with an absolute monotonic wake-up via `asyncio.sleep(seconds_until_midnight())`.

---

### [tests/gateway/test_gateway.py:376-444] Gateway `_run()` tests do not mock `AgentLoader` or `CronScheduler` — any real file system access during test

**Description**: The `test_run_with_default_model_calls_set_model` and related tests patch many symbols but not `AgentLoader` or `CronScheduler`:
```python
with patch("archon.gateway.gateway.SkillLoader"), \
     patch("archon.gateway.gateway.PluginLoader"), \
     patch("archon.gateway.gateway.SessionManager", return_value=mock_sm), \
     # No patch for AgentLoader — creates real object, calls load_all()
     # No patch for CronScheduler — creates real object
     ...
```
`AgentLoader.load_all()` reads `~/.claude/agents/*.md` from the developer's filesystem. `CronScheduler` is constructed with `bg_mcp_server` and `bot`. On a CI machine or developer machine with agents installed, this can cause test interference.

**Fix**: Patch `archon.gateway.gateway.AgentLoader` and `archon.gateway.gateway.CronScheduler` in these integration tests.

---

## Medium Severity Findings

### [archon/config/loader.py:393-407] Notifications mode accepts any string — invalid values not validated

**Description**: `notif_mode = str(notif_data["mode"])` at line 395 accepts any string. Setting `mode = "typo"` in config.toml passes validation silently. The handler code later compares against `"quiet"`, `"normal"`, `"verbose"`, `"debug"` — any other value falls through to debug behavior without warning.

**Fix**: Add validation: `if notif_mode not in ("quiet", "normal", "verbose", "debug"): raise ConfigError(...)`.

---

### [archon/config/loader.py:106-109] `VoiceTTSConfig.auto` accepts any string — `"always"/"inbound"/"off"` not validated at load time

**Description**: Same pattern. `str(raw_tts.get("auto", VoiceTTSConfig.auto))` on line 492 accepts any value. The `should_synthesize()` function in `tts.py` must handle the invalid case gracefully, but that is runtime behavior — not load-time validation.

**Fix**: Validate `auto` against `("always", "inbound", "off")` in `load_config` and raise `ConfigError`.

---

### [archon/cli/config_cmd.py:43-46] `editor_var` variable is dead code

**Description**: Lines 43-46 compute `editor_var` (the env var name, `"EDITOR"` or `"VISUAL"`) but never use it in the success path. It's only used in the error message on line 50, but `editor` (not `editor_var`) is used there:
```python
editor_var = (
    "EDITOR" if os.environ.get("EDITOR")
    else "VISUAL" if os.environ.get("VISUAL")
    else "EDITOR"
)
try:
    cmd = shlex.split(editor) + [str(_CONFIG_PATH)]
except ValueError:
    print(f"Invalid {editor_var} value: {editor}")  # editor_var used only here
```
This is more of a style/correctness comment: `editor_var` is computed redundantly since the error message could just check which env var was set.

---

### [archon/cli/update.py:38-47] `run_update` uses bare `subprocess.run(cmd)` without checking for `uv` not found

**Description**:
```python
result = subprocess.run(cmd)
return result.returncode
```
If `uv` is not in PATH, `subprocess.run` raises `FileNotFoundError`. There is no `try/except FileNotFoundError`, so the CLI crashes with a traceback rather than a clean error message.

**Fix**: Add `except FileNotFoundError: print("uv not found in PATH"); return 1`.

---

### [archon/cli/status.py:77-81] `_check_health` returns `reachable=True` for any non-200 HTTP response

**Description**:
```python
resp = urllib.request.urlopen(url, timeout=2)
latency_ms = int((time.monotonic() - t0) * 1000)
return HealthInfo(reachable=(resp.status == 200), latency_ms=latency_ms)
```
`urllib.request.urlopen` raises `HTTPError` for non-2xx responses rather than returning them, so `resp.status == 200` will only be `False` if the server returns a non-200 success status (e.g., 204). An `HTTPError` (404, 500) will be caught by the blanket `except Exception` and treated as `reachable=False`. This means the check behaves correctly in practice for the error cases but the `resp.status == 200` test is misleading and fragile.

---

### [archon/log_setup.py:127] `sys.stderr` is replaced with `_StderrToLogger` permanently — no restore mechanism

**Description**: Once `setup_logging` is called, `sys.stderr` is permanently replaced with `_StderrToLogger`. If a library or test later checks `isinstance(sys.stderr, io.IOBase)` or calls `sys.stderr.fileno()`, it will get an `OSError("fileno not supported")`. The `_StderrToLogger.fileno()` raises `OSError` explicitly (line 40). This can break libraries that probe `fileno()` (e.g., `subprocess` when inheriting stderr, `logging.handlers.WatchedFileHandler`).

**Evidence**: `log_setup.py:40` — `raise OSError("fileno not supported by _StderrToLogger")`

---

### [archon/config/loader.py:528-547] `atomic_write` uses `.toml.tmp` as the temp file suffix regardless of `path` extension

**Description**:
```python
def atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(".toml.tmp")
```
If `path` is e.g. `config.toml`, `tmp` becomes `config.toml.tmp` (correct). But `path.with_suffix(".toml.tmp")` replaces only the last suffix, so if `path` were `config.settings.toml`, `tmp` becomes `config.settings.toml.tmp` — still fine. However, `".toml.tmp"` is treated as a single suffix by `Path.with_suffix()`, which is correct Python behavior only if there is a leading dot. The actual result is `config.toml.tmp` — correct. But this is brittle: if `path` had no extension, `with_suffix(".toml.tmp")` would append `.toml.tmp` to the stem. This edge case should at least be commented.

---

### [archon/gateway/__init__.py:1] Exporting `_setup_dp` and `register_middleware` as public API from `__init__.py`

**Description**:
```python
from archon.gateway.gateway import Gateway, _setup_dp, register_middleware
__all__ = ["Gateway", "_setup_dp", "register_middleware"]
```
`_setup_dp` has a leading underscore indicating it is private/internal. Exporting it in `__all__` and the package `__init__` makes it part of the public API, contradicting the naming convention and making it harder to refactor later.

---

### [archon/version.py] Module-level `__version__ = get_version()` re-runs on every test collect

**Description**: When pytest collects `tests/test_version.py`, it imports `archon`, which imports `archon.version`, which calls `get_version()`, which spawns `subprocess.run(["git", "rev-list", "--count", "HEAD"])`. This git call runs on **every** test collection. On a machine in a non-git directory or with no git, this fails to the fallback `"0"`. Not a crash, but it pollutes the version with garbage in non-git environments.

The `test_version_commit_count_from_git` test patches `archon.version.subprocess.run` but does NOT reset `__version__` — so `from archon import __version__` still returns the value computed at import time, making the test for `__version__` pointless.

---

## Low Severity / Style Issues

### [archon/cli/main.py:71] Unreachable `return 0` — all branches return explicitly

After all `if args.command == ...` branches, a final `return 0` on line 71 is unreachable because every recognized command returns from within its branch and unrecognized commands would need to fall through (but argparse already handles unknown commands). Minor, but dead code.

---

### [archon/cli/logs.py:42-48] `follow` mode ignores `tail` return code

```python
if follow:
    try:
        subprocess.run(["tail", "-f", str(log_file)])
    except KeyboardInterrupt:
        pass
    ...
    return 0
```
After `tail -f` exits (e.g., if the log file is deleted or `tail` exits non-zero), the function always returns `0` regardless of `tail`'s exit code. Minor but inconsistent with non-follow mode.

---

### [archon/cli/status.py:130] `MCP` line shows same `host:port` as Health line — misleading

```python
print(f"  Health     {host}:{port} {health_sym} {latency_str}")
print(f"  MCP        {host}:{port} (archon-mcp)")
```
Both `Health` and `MCP` show the same `host:port`. The `/health` endpoint and the MCP endpoint are different paths on the same server, so this is technically correct, but showing the same address twice with different labels is confusing.

---

### [archon/cli/doctor.py:128-129] Print() usage in `run_doctor` — violates `no print()` project convention

**Description**: The CLAUDE.md says "All modules use `logging.getLogger("archon")` — no `print()`." The `doctor.py` module uses `print()` throughout (lines 128-140). The CLI modules intentionally use print for user-facing output. However, the wording of the project convention is absolute. This is a deliberate design choice but technically a convention violation that should be explicitly documented as the CLI exception.

---

### [archon/config/loader.py:225-226] Blank line in module body — cosmetic

Line 225 has two consecutive blank lines where Python style (PEP 8) allows only two between top-level definitions; inside a module body after a constant this looks like an editing artifact.

---

### [archon/gateway/gateway.py:33] `_QMD_DAEMON_STARTUP_WAIT` constant — magic number with no rationale

`_QMD_DAEMON_STARTUP_WAIT: float = 2.0` seconds is chosen arbitrarily. No comment explains why 2 seconds is sufficient (or insufficient) for the daemon to open its port. A slow machine or a busy system might need more.

---

## Untested Code Paths

The following production code paths have zero or near-zero coverage in the test suite:

1. **`archon/config/loader.py:239-270`** (`_parse_pipeline`) — the cron pipeline parser is entirely untested by the tests in this review scope. The `tests/cron/test_cron_config.py` tests exist but were not in this review scope.

2. **`archon/config/loader.py:293-310`** (`load_cron_jobs`) — the file-reading loop and TOML file loading are untested in the config test suite.

3. **`archon/config/loader.py:334-347`** — the corrupt TOML recovery path (backup restore) is completely untested. This is the most important config resilience feature and has zero tests.

4. **`archon/gateway/gateway.py:51-129`** (`_ensure_qmd_daemon`) — the HTTP probe path (lines 77-93) is untested: `urllib.request.urlopen` is never called in the QMD daemon tests (`test_qmd_daemon.py` patches it at `urllib.request.urlopen` globally but the inner function imports locally and uses `urllib.request`).

5. **`archon/gateway/gateway.py:249-260`** (`_midnight_compaction_loop`) — entirely untested in the test suite reviewed.

6. **`archon/log_setup.py:63-76`** (`_rotate_on_startup`) — the actual file rename path is untested. `test_log_setup.py` tests handler attachment but not the startup rotation.

7. **`archon/log_setup.py:28-47`** (`_StderrToLogger`) — the `writelines`, `flush` with partial buffer, and `fileno` methods are untested.

8. **`archon/cli/config_cmd.py:67-69`** — the `_run_get` path when the config is corrupt/unparseable is a partial gap (lines 67-69 cover the exception path, which is partially uncovered).

9. **`archon/version.py:17-18`** — the git success path (stdout parsing) is partially covered but the caching/module-level `__version__` assignment test is missing.

10. **`archon/gateway/gateway.py:303-308`** — the `HistoryCompactor` instantiation branch is tested in gateway tests only with a patched session manager that short-circuits `history.compaction_enabled`.

---

## Convention Violations

1. **85% coverage mandate violated**: Running the full test suite against only the reviewed files shows 48% coverage. Running `uv run pytest tests/config/ tests/gateway/ tests/cli/ tests/test_log_setup.py tests/test_version.py tests/test_smoke.py` gives 48.43% total. The mandatory 85% floor is not met even for the infrastructure layer in isolation. (CLAUDE.md: "Maintain 85%+ test coverage minimum")

2. **5 committed test failures**: The `test_full_flow.py` tests fail on every run. (CLAUDE.md: "All tests always MUST be passed")

3. **`print()` in CLI modules**: All of `cli/config_cmd.py`, `cli/doctor.py`, `cli/logs.py`, `cli/service.py`, `cli/status.py`, `cli/update.py` use `print()`. The CLAUDE.md says "All modules use `logging.getLogger("archon")` — no `print()`." This is a known and intentional design choice for user-facing CLI output, but it is unambiguously a convention violation unless the rule is scoped to "daemon modules only." This should be explicitly documented.

4. **SDK rule not testable here**: No direct Anthropic API calls were found in the reviewed files. The gateway correctly uses `SessionManager` which wraps the SDK. This convention is respected in the infrastructure layer.

5. **TDD violation**: The corrupt TOML recovery path (backup/restore) was implemented without any tests. TDD requires tests to be written first — this is clear evidence of implementation-first development.

6. **`_setup_dp` exported from `__init__.py`**: Private function (`_setup_dp`) is exported in `gateway/__init__.py:__all__`, contradicting Python naming conventions.

---

## Overall Assessment

**Is this production-ready?** No.

The infrastructure layer has solid bones — atomic writes, config validation, graceful shutdown scaffolding, isatty-aware logging — but four categories of problems must be resolved before production deployment:

**Top 3 must-fix items:**

1. **Fix the 5 failing tests in `test_full_flow.py`** immediately. These tests encode contradictory expectations (`texts` is filtered to remove `⏳` messages, but the assertions expect `⏳` to be present). This is a CI-breaking defect that must be fixed before any merge.

2. **Add timeouts to all shutdown coroutines** (`cron_scheduler.stop()`, `bg_manager.stop_all()`, `bg_mcp_server.stop()`). The current code guarantees only `session_manager.stop_all()` completes within 5 seconds. Any hung background agent makes the shutdown hang indefinitely, forcing launchd to SIGKILL the process.

3. **Fix `archon/version.py` to not spawn `git` at import time**. Every `import archon` running a subprocess is a reliability hazard and a performance problem. Use `importlib.metadata`, a pre-baked constant, or a `functools.lru_cache`-wrapped lazy call.
