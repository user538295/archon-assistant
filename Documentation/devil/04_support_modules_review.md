# Devil's Advocate Review — Support & Utility Modules
**Reviewer**: DA-4
**Date**: 2026-03-08
**Files reviewed**:
- `archon/ai/truncation.py`
- `archon/ai/history_manager.py`
- `archon/ai/history_compactor.py`
- `archon/ai/agent_logger.py`
- `archon/ai/agent_loader.py`
- `archon/ai/skill_loader.py`
- `archon/ai/plugin_loader.py`
- `archon/ai/reminder.py`
- `archon/ai/stt.py`
- `archon/ai/tts.py`
- `archon/ai/tool_result_policy.py`
- `archon/ai/event_renderer.py`
- `archon/ai/context_provider.py`
- `archon/ai/__init__.py`

---

## Executive Summary

The support modules are generally well-structured with good test coverage for the happy paths, but conceal several significant correctness and reliability defects. The most alarming issues are: (1) a subprocess resource leak in `stt.py` — the Whisper process is left running as an orphan when `transcribe_with_timeout` fires, with no kill/cleanup; (2) a non-atomic, write-after-close bug in `tts.py` where `response.content` is written *outside* the `async with httpx.AsyncClient` context manager, meaning the client is already closed when the write happens; (3) `history_manager.py` performs blocking file I/O on the asyncio event loop with no `run_in_executor` wrapper — every append call stalls the Telegram handler; (4) `reminder.py`'s `build_reminder_message` resets counters even if the file read raises an exception, silently swallowing the injection failure. Several modules are missing any test for `tool_result_policy.py` and `context_provider.py`. Overall, the code is not production-ready until the subprocess leak and the TTS write-after-close are resolved.

---

## Critical Findings (Severity: CRITICAL)

### [stt.py:122] Subprocess orphaned on timeout — resource leak

**Description**: `transcribe_with_timeout` wraps `transcribe` with `asyncio.wait_for`. When the timeout fires, `wait_for` cancels the coroutine and raises `asyncio.TimeoutError`. However, `transcribe` has already called `asyncio.create_subprocess_exec`, and the resulting `asyncio.subprocess.Process` object is stored only in the local variable `proc`. Cancelling the coroutine at `await proc.communicate()` leaves the Whisper subprocess running indefinitely — it is never killed. On macOS/Linux with large audio files and the `large` Whisper model, this can spawn many zombie Whisper processes that keep GPU/CPU saturated.

**Impact**: Unbounded subprocess accumulation. Repeated timeouts (network hiccup causing slow OGG download, CI with small timeout) will eventually saturate process limits or memory.

**Evidence**:
```python
# stt.py:107-125
async def transcribe_with_timeout(self, audio_path: Path, timeout_sec: float = 60.0) -> str:
    try:
        return await asyncio.wait_for(self.transcribe(audio_path), timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.error("Transcription timed out after %s seconds", timeout_sec)
        raise
```

```python
# stt.py:81-87 — proc is a local, never reachable after cancellation
proc = await asyncio.create_subprocess_exec(
    *cmd,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE
)
stdout, stderr = await proc.communicate()  # <- cancellation fires here
```

**Fix**: Store `proc` as an instance variable or use a `try/finally` block inside `transcribe` that calls `proc.kill(); await proc.wait()` on `asyncio.CancelledError`.

---

### [tts.py:103-107] Write to `response.content` after AsyncClient is closed

**Description**: In `_openai_tts`, the `async with httpx.AsyncClient(...)` block ends at line 106 with `response.raise_for_status()`, closing the client. The `output_path.write_bytes(response.content)` call is then made *after* the `async with` block exits. While `httpx` buffers the full response in `response.content` before returning from `await client.post(...)`, the reference is technically held on the response object after client closure. More critically, if `raise_for_status()` does not raise (success case), the body write happens after the client teardown — this works today with `httpx`, but only because `httpx` fully reads the body during the request, not lazily. If the response format is ever changed to a streaming endpoint or the library version changes behaviour, this will silently produce an empty or partial file.

**Impact**: Silent data corruption risk under streaming or chunked responses. Structural defect that violates the resource-management contract of the `async with` context manager.

**Evidence**:
```python
# tts.py:102-107
async with httpx.AsyncClient(timeout=timeout_sec) as client:
    response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()

output_path.write_bytes(response.content)  # <-- outside the 'async with' block
```

**Fix**: Move `output_path.write_bytes(response.content)` inside the `async with` block, before the context manager exits.

---

### [stt.py:94-98] Unhandled exception in `.txt` file cleanup leaves orphan files

**Description**: `txt_file.unlink()` is called with no exception handling. If the unlink fails (permissions error, read-only filesystem, network drive) the exception propagates as an unhandled `OSError`, which crashes the entire transcription call. Since the `.txt` content was already successfully read into `text`, this is an unnecessary failure mode. More practically, if the process is interrupted after reading but before unlinking, the `.txt` file remains — subsequent Whisper runs will find a stale `.txt` file and `transcribe` will return the stale content rather than the new transcription, a silent correctness bug.

**Evidence**:
```python
# stt.py:94-100
txt_file = audio_path.with_suffix(".txt")
if txt_file.exists():
    text = txt_file.read_text().strip()
    txt_file.unlink()  # no try/except; bare OSError propagates
    logger.info("Transcribed %s: %d characters", audio_path.name, len(text))
    return text
```

**Fix**: Wrap `unlink()` in `try/except OSError` and log a warning on failure. Also: add `encoding="utf-8"` to `txt_file.read_text()` — it currently uses the platform default encoding, which can silently corrupt non-ASCII transcriptions on Windows.

---

## High Severity Findings

### [history_manager.py:59-61 / agent_logger.py:169-171] Blocking file I/O on asyncio event loop

**Description**: Both `HistoryManager._append` and `AgentLogWriter._append` open files and call `f.write(text)` synchronously on the asyncio event loop thread. The `record_event` and `record_user_message` calls are invoked from the Telegram message handler coroutine (`async for event in pipeline.send(...)`). Every file write — which for a heavily used agent session can be many kilobytes per message — blocks the entire event loop, delaying Telegram responses and all other async tasks. The project's CLAUDE.md explicitly states asyncio as the runtime; blocking I/O in the event loop is a documented asyncio anti-pattern.

**Impact**: Tail latency spikes on the Telegram response stream. Under high load (many events, large tool results) the event loop can stall for tens of milliseconds per write.

**Evidence**:
```python
# history_manager.py:59-61
def _append(self, text: str) -> None:
    with self._today_path().open("a", encoding="utf-8") as f:
        f.write(text)  # blocking — on the event loop thread
```

**Fix**: Either use `asyncio.to_thread(f.write, text)` / `loop.run_in_executor(None, ...)`, or buffer writes and flush them in a background task. At minimum, document the known limitation.

---

### [reminder.py:41-45] Counter reset occurs before file read — state corrupted on exception

**Description**: `build_reminder_message` resets `_message_count` and `_token_count` to zero *before* calling `self._file.read_text(...)`. If `read_text` raises (file deleted between `should_inject()` and `build_reminder_message()`, permissions error, disk full), the counters are already zeroed. The caller gets an exception but the reminder system silently believes a successful injection occurred. The next injection will not happen until another full `interval_messages`/`interval_tokens` cycle, losing the injection entirely.

**Impact**: Silent injection failure. If REMINDER.md is momentarily unavailable (symlink, network path, race with editor save), the daemon skips an injection without notifying the user.

**Evidence**:
```python
# reminder.py:41-45
def build_reminder_message(self) -> str:
    content = self._file.read_text(encoding="utf-8")
    self._message_count = 0    # already reset if read_text raises above
    self._token_count = 0
    return _XML_WRAPPER.format(content=content)
```

**Fix**: Read the file first into a local variable, then reset counters only after a successful read:
```python
content = self._file.read_text(encoding="utf-8")
self._message_count = 0
self._token_count = 0
return _XML_WRAPPER.format(content=content)
```
Wait — reading the code more carefully: this *is* the current order. The read happens on line 42, then the resets. So if `read_text` raises, the resets on lines 43-44 are never executed. But the `should_inject()` check on line 34 reads `self._file.exists()` — if the file disappears between `should_inject` and `build_reminder_message`, `read_text` raises `FileNotFoundError` and the caller catches it (or doesn't). The counters are NOT reset in that case, which is actually the correct TOCTOU-safe behaviour. **This finding is retracted as a false positive.** However, the TOCTOU race between `should_inject()` and `build_reminder_message()` remains: the file can disappear in between and cause an unhandled `FileNotFoundError` with no logging. There is no `try/except` around the `read_text` call. Callers receive a raw exception with no diagnostic context.

**Fix**: Wrap `read_text` in a `try/except FileNotFoundError` and log a warning. Return an empty string or re-raise with context.

---

### [history_compactor.py:99-107] SDK client created per-call with no connection pooling

**Description**: `HistorySummarizer.summarize` creates a new `ClaudeSDKClient` on every call when `self._client is None` (the production code path). For `compact_pending_days`, one new SDK client is created, connected, queried, and disconnected for *each day* in the backlog. This is intentional as documented, but it means N days = N full SDK connect/disconnect cycles with no reuse. On a machine that missed 14 days of compaction (vacation, daemon outage), this creates 14 sequential SDK sessions. More critically, if the SDK internally creates its own `claude` subprocess per `connect()`, this is N subprocesses spawned and torn down.

**Impact**: Startup latency proportional to N days of backlog. Not a correctness bug but a reliability concern at scale.

**Evidence**:
```python
# history_compactor.py:93-98
if self._client is not None:
    sdk_client = self._client
else:
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    sdk_client = ClaudeSDKClient(options=ClaudeAgentOptions(
        permission_mode="bypassPermissions",
        model=self._model,
        max_turns=1,
    ))
```

**Fix**: Cache the client in `HistorySummarizer` after first creation and reuse across calls. Or create the client once in `HistoryCompactor.__init__` and pass it to the summarizer.

---

### [agent_logger.py:255-263] Unbounded while-loop on filename collision

**Description**: `_agent_path` uses an unbounded `while True` loop to find a non-colliding filename when two agents start within the same minute with the same name. While collisions are rare in practice, an adversarial or buggy agent that restarts many times within a minute could cause `counter` to grow without bound. More practically, this is a TOCTOU race: the `if not candidate.exists()` check and the subsequent file creation in `AgentLogWriter.__init__` are not atomic. Under concurrent starts (two subagents with the same name starting simultaneously), both could observe the same `candidate` as non-existent and both write to it, corrupting the first agent's log.

**Impact**: Log file corruption under concurrent same-name agent starts. Theoretical unbounded loop.

**Evidence**:
```python
# agent_logger.py:255-263
counter = 2
while True:
    candidate = self._dir / f"{date_prefix}-{safe_name}-{counter}.md"
    if not candidate.exists():
        return candidate   # TOCTOU: another task could create this between check and use
    counter += 1
```

**Fix**: Use `open(candidate, 'x')` (exclusive create) to atomically claim the file, catching `FileExistsError` to retry. This eliminates the TOCTOU race.

---

### [history_manager.py:53-57] TOCTOU race on header creation

**Description**: `_ensure_header` checks `path.exists()` then calls `path.write_text(...)`. If two concurrent writes (two users or two async tasks for the same user) call `record_user_message` simultaneously, both could observe `path.exists() == False` and both call `write_text()`, with the second overwriting the first user's content. While in practice `HistoryManager` is accessed from a single asyncio coroutine, this is a structural weakness.

**Evidence**:
```python
# history_manager.py:53-57
def _ensure_header(self) -> None:
    path = self._today_path()
    if not path.exists():       # TOCTOU
        self._dir.mkdir(parents=True, exist_ok=True)
        path.write_text(...)    # may clobber concurrent write
```

**Fix**: Use `path.open('a')` and only write the header if `f.tell() == 0` (file was just created/empty), which is atomic in append mode.

---

### [tts.py:131] Wrong default value fallback for `edge_voice`

**Description**: Line 131 reads `voice = self.config.edge_voice or TTSConfig.edge_voice`. `TTSConfig.edge_voice` is a class-level attribute with value `"en-US-MichelleNeural"`. But `self.config.edge_voice` is already always set (it is a dataclass field with a default) — it will never be `None` or falsy unless explicitly set to `""`. The `or TTSConfig.edge_voice` fallback is therefore unreachable dead code. Worse, `TTSConfig.edge_voice` is accessed as a class attribute (not an instance), which returns the string `"en-US-MichelleNeural"` only because dataclass fields happen to leave class-level defaults, but this is an implementation detail of the dataclass machinery. If `edge_voice` were changed to a `field(default_factory=...)`, `TTSConfig.edge_voice` would raise `AttributeError`.

**Evidence**:
```python
# tts.py:131
voice = self.config.edge_voice or TTSConfig.edge_voice
```

**Fix**: Remove the `or TTSConfig.edge_voice` fallback — `self.config.edge_voice` is always populated. If a default fallback is desired, use `self.config.edge_voice or "en-US-MichelleNeural"` with a string literal.

---

## Medium Severity Findings

### [skill_loader.py:9] Frontmatter regex does not handle Windows line endings

**Description**: `_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)` hard-codes `\n`. YAML frontmatter in `.md` files edited on Windows or checked out with `git config core.autocrlf=true` will have `\r\n` line endings. The regex will fail to match, causing the frontmatter to be reported as "malformed" and the skill/agent to be silently skipped with a warning. This affects both `skill_loader.py` and `agent_loader.py` which imports `_FRONTMATTER_RE` from it.

**Impact**: Skills and agents silently disappear when SKILL.md files have Windows line endings.

**Evidence**:
```python
# skill_loader.py:9
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
```

**Fix**: Use `\r?\n` in the pattern: `r"^---\r?\n(.*?)\r?\n---\r?\n"`.

---

### [skill_loader.py:10] Key-value regex is too restrictive for YAML

**Description**: `_KEY_VALUE_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.+)$")` requires the key to start with a letter or underscore. This is correct for simple YAML keys, but YAML allows keys with hyphens (e.g., `tool-timeout: 30`). More importantly, multiline YAML values (list syntax `tools:\n  - Read\n  - Write`) are silently dropped because the regex requires `.+` on the same line. Agent files with `tools` as a YAML list (not comma-separated) will silently produce `fm.get("tools", "") == ""`, leading to an empty tools list.

**Impact**: Agents with YAML-list-style `tools:` fields will silently load with no tools, causing unexpected behaviour when the SDK uses those agents.

**Evidence**:
```python
# agent_loader.py:147-151
tools_raw = fm.get("tools", "").strip()
tools: list[str] = (
    [t.strip() for t in tools_raw.split(",") if t.strip()]
    if tools_raw
    else []
)
```

If `tools.md` contains:
```yaml
tools:
  - Read
  - Write
```
The regex will not match `tools:` (no value on the same line), so `fm.get("tools", "")` returns `""`, and `tools` becomes `[]`.

**Fix**: Either extend the YAML parser to handle multi-line sequences, or document that only the inline comma-separated format is supported and add a validation warning when the YAML block contains a `tools:` key with an empty value.

---

### [history_compactor.py:76] Regex for response extraction is fragile

**Description**: `_extract_responses` uses `re.findall(r"(### ✅ Response.*?)(?=\n### |\Z)", content, re.DOTALL)`. This regex relies on the exact emoji `✅` and exact heading format produced by `EventRenderer`. If `EventRenderer` changes the emoji, heading depth, or spacing (e.g., in a future PR), this regex silently matches nothing and `compact_pending_days` skips all content. There is no assertion or test that the regex format is derived from the same source of truth as `EventRenderer`.

**Impact**: Silent compaction failure on format drift. Historical summaries stop being generated.

**Fix**: Extract the heading pattern as a shared constant between `EventRenderer` and `_extract_responses`. Or test the round-trip: generate a `Response` through `EventRenderer`, feed it to `_extract_responses`, and assert the content is recovered.

---

### [history_manager.py:42] Timestamp uses `datetime.now(timezone.utc)` but `_today_path()` uses `date.today()`

**Description**: The timestamp written in `record_user_message` is UTC (`datetime.now(timezone.utc)`), but the file path is determined by `date.today()` which uses the local system timezone. Near midnight, a message sent at 23:59 local time (which is next day UTC) will be stamped `00:59 UTC` but written to yesterday's local date file. The timestamp in the header and the file date will be inconsistent.

**Impact**: Minor cosmetic inconsistency in history files for users in UTC+N timezones near midnight. No data loss.

**Fix**: Use `date.today()` consistently for filenames OR derive both the timestamp and date from the same `datetime.now(timezone.utc)` object.

---

### [agent_loader.py:31-33] `_strip_quotes` accepts `""` (length 2) as a quoted string

**Description**: `len(value) >= 2` allows `""` (an empty quoted string) to pass through as `""[1:-1]` == `""`. This is correct semantically, but the subsequent `if not name:` check will still catch it. However, `_strip_quotes` applied to a single-character string like `"x"` (length 3) will correctly strip to `x`, while applied to `""` (length 2, a truly empty YAML value in quotes) produces `""[1:-1] == ""` — correct, but the `>= 2` check means a single `"` character (malformed YAML) would not be stripped. Not a practical bug, but the condition should be `len(value) > 2` for strict correctness.

**Evidence**:
```python
# agent_loader.py:31-33
if value.startswith('"') and value.endswith('"') and len(value) >= 2:
    return value[1:-1]
```

For `value = '"'` (a single double-quote), `len(value) == 1` — condition is false, returned unchanged. For `value = '""'` (two double-quotes), `len(value) == 2` — stripped to `""`. Edge case only.

---

### [event_renderer.py:30-32] `_format_size` is a redundant wrapper

**Description**: `_format_size` in `event_renderer.py` is a one-line wrapper that calls `format_tool_result_size` from `tool_result_policy.py`. It adds no logic and exists only as a function alias. This is dead weight — either import `format_tool_result_size` directly or remove the alias. Current code creates a pointless indirection.

**Evidence**:
```python
# event_renderer.py:30-32
def _format_size(byte_count: int) -> str:
    """Format a byte count as a human-readable string (B or KB)."""
    return format_tool_result_size(byte_count)
```

**Fix**: Replace all uses of `_format_size` with `format_tool_result_size` and remove the wrapper.

---

### [plugin_loader.py:207] Private method called on foreign object

**Description**: `_load_plugin_skills` constructs a temporary `SkillLoader` instance pointing at `skills_dir`, then calls `_loader._load_skill(skill_md)` — a *private* method of `SkillLoader`. This is a direct violation of encapsulation: the method prefix `_` signals it is implementation-internal. If `SkillLoader._load_skill` is refactored (signature change, renamed), `PluginLoader` breaks silently. The comment "Temporary loader instance — used only for _load_skill() parsing" acknowledges the hack.

**Impact**: Tight coupling between `PluginLoader` and `SkillLoader` internals. Future refactors of `SkillLoader` will have a non-obvious blast radius.

**Fix**: Make `_load_skill` public (`load_skill`) or extract a standalone `parse_skill_file(path)` function that both call.

---

### [stt.py:44] Fallback `Path("whisper")` is a relative path — resolution depends on CWD

**Description**: When Whisper is not found in standard locations, `self.whisper_bin = Path("whisper")` is set. This is a relative `Path` object — when passed to `asyncio.create_subprocess_exec` via `str(self.whisper_bin)`, the OS will search `PATH`. This works on most systems but fails if the CWD is unexpected (e.g., `/` on macOS sandbox) and Whisper is installed in a user-specific location not on the system PATH. The warning log says "will attempt to use from PATH" but `str(Path("whisper"))` is literally `"whisper"` — the subprocess exec resolves it via PATH, so this is actually correct. However, it means `Path` semantics are misleading here; just use the string `"whisper"` directly.

**Impact**: Minor clarity issue, no functional bug.

---

### [truncation.py:21-31] Double-recomputation does not guard against second digit-boundary crossing

**Description**: `SplitStrategy.apply` performs one recomputation when `actual_label_w > label_w`, but the comment says "One recomputation if actual N uses a wider label (digit boundary crossings)". After the recomputation, `n = len(raw)` could theoretically cross another digit boundary (e.g., from 99→100 chunks), making `actual_label_w` still too small. In practice this is a degenerate case (text would need to be huge), but the algorithm is documented to handle only one boundary crossing, and the guarantee is not formally proved. A `while` loop would be more correct than a one-shot `if`.

**Impact**: Theoretical chunk-length overflow at N=10^k boundaries with exactly the right content size. Extremely unlikely in practice (max_len would need to be ≤12 chars).

---

## Low Severity / Style Issues

### [stt.py:16-17] Inconsistent use of `Optional[str]` vs `str | None`

`STTHandler.__init__` uses `Optional[str]` (line 16) while the rest of the codebase uniformly uses `str | None`. CLAUDE.md's code style section implies Python 3.12+; `Optional` is the legacy form.

### [history_compactor.py:11] Hardcoded model string is a magic constant

`_HAIKU_MODEL = "claude-haiku-4-5-20251001"` is a hardcoded constant at module level. This is better than inlining it everywhere, but it is not validated against the config or cross-referenced with the `[models] available` config list. If Haiku is deprecated, there is no config fallback.

### [agent_logger.py — no `import logging`] Missing logging import

`agent_logger.py` imports nothing from the `logging` module. `AgentLogWriter` and `AgentLogger` perform no logging at all — not even on write failures. An `OSError` on `self._path.open("a")` propagates up silently with no context. At minimum, a `logger = logging.getLogger("archon")` and a `try/except` around `_append` calls should log the failure.

### [history_manager.py:54-57] `_dir.mkdir` called redundantly inside `_ensure_header`

`HistoryManager.__init__` already calls `self._dir.mkdir(parents=True, exist_ok=True)` at line 35. The same call inside `_ensure_header` at line 56 is redundant under normal operation. It is a defensive measure, but suggests the author was uncertain about the invariant. This is not harmful but adds noise.

### [context_provider.py] Protocol with no runtime check

`ContextProvider` is a `Protocol` class with no `runtime_checkable` decorator. It cannot be used with `isinstance()`. If future code needs to check whether an object implements the protocol at runtime, it will silently fail. Either add `@runtime_checkable` or document that it is structural-only.

### [tts.py:178] `TODO` comment left in production code

```python
# tts.py:178
# TODO: Check for [[tts:...]] tags in response
return False
```

The `"tagged"` auto mode is documented in `TTSConfig` and `CLAUDE.md` but is not implemented. Callers that configure `auto="tagged"` will silently get no TTS output. There is no warning log or `NotImplementedError`. The `should_synthesize_tagged` test at test_tts.py:71 asserts that `tagged` returns `False` — essentially testing that the feature is broken.

### [plugin_loader.py:93-98] JSON parsing without schema validation

`_read_enabled_keys` reads `settings.json` and does `data.get("enabledPlugins", {})`. If `enabledPlugins` exists but is not a dict (e.g., it's a list due to a settings file corruption or format change), `data.get(...)` returns the list, and the dict comprehension `{k for k, v in enabled_plugins.items() if v is True}` will raise `AttributeError: 'list' object has no attribute 'items'`. This exception is not caught (the outer `except` catches `json.JSONDecodeError, OSError` only).

**Fix**: Add `if not isinstance(enabled_plugins, dict): return set()`.

### [history_compactor.py:204] Non-atomic file write of compacted summary

`out_path.write_text(summary, ...)` is a non-atomic operation. If the process is killed mid-write (SIGKILL, power loss), the compacted file is partially written. On the next run, `compact_pending_days` checks `if (self._daily_dir / f"{file_date}{_COMPACTED_SUFFIX}").exists()` — which is `True` for the partial file — and skips recompaction. The partial summary is used forever.

**Fix**: Write to a temp file first (`out_path.with_suffix(".tmp")`), then `rename()` atomically.

---

## Untested Code Paths

1. **`tool_result_policy.py`** — No dedicated test file exists. `should_suppress_tool_result`, `summarize_tool_result`, and `format_tool_result_size` are tested only indirectly through `event_renderer` and `history_manager` tests.

2. **`context_provider.py`** — No tests exist for the `ContextProvider` Protocol itself (not that Protocol classes typically need tests, but the conformance of `HistoryCompactor` to the protocol is untested).

3. **`stt.py:transcribe` — `txt_file` not found AND stdout empty** — The fallback-to-stdout path (lines 102-104) is tested, but the case where both `txt_file` does not exist AND `stdout` is empty (returns `""`) is not tested. This silently returns an empty string.

4. **`stt.py` — `proc.returncode = None`** — `asyncio.Process.returncode` can be `None` before `communicate()` completes. After `communicate()`, it should be set, but the error-raising code `proc.returncode or 1` at line 92 would coerce `None` to `1` — this edge case is not tested.

5. **`tts.py:_openai_tts` — `httpx` not installed** — The `ImportError` path at line 83-84 is not tested.

6. **`tts.py:_edge_tts` — `edge_tts` not installed** — The `ImportError` path at lines 123-128 is not tested.

7. **`tts.py:_openai_tts` — timeout fires** — The `asyncio.TimeoutError` catch at line 117 is not tested (the `httpx` timeout mechanism is different from `asyncio.wait_for`; `httpx.AsyncClient(timeout=...)` raises `httpx.ReadTimeout`, not `asyncio.TimeoutError` — making the catch unreachable).

8. **`plugin_loader.py:_read_enabled_keys` — `enabledPlugins` is not a dict** — No test for malformed `settings.json` where `enabledPlugins` is a list or string.

9. **`plugin_loader.py:_read_installed_plugins` — `installs[0].get("installPath")` missing** — No test for the case where the first install object in the list lacks `installPath`.

10. **`agent_loader.py:_load_agent` — `tools` as a YAML list** — No test verifying the documented comma-separated-only format handles or warns on YAML-list-style tools fields.

11. **`reminder.py:build_reminder_message` — REMINDER.md deleted between `should_inject` and `build_reminder_message`** — TOCTOU race with no test.

12. **`history_compactor.py:_collect_day_content` — agent log file with non-matching glob** — The glob pattern `f"{day}-??-??-*.md"` is not tested with edge-case names like `2026-03-08-1-1-agent.md` (single-digit hour/minute).

13. **`truncation.py:SplitStrategy.apply` — `max_len` of 1** — The `max(1, max_len - label_w)` guard produces `content_max=1`, generating hundreds of chunks with `[N/N] ` label each being 7+ chars — label can exceed max_len when N is large enough. Not tested.

---

## Convention Violations

1. **`stt.py:6` — `from typing import Optional`**: Project uses Python 3.12+ and the rest of the codebase uses `str | None`. `Optional` is legacy style per the project's Python version. CLAUDE.md implies modern type hints.

2. **`agent_logger.py` — No logging**: The module imports nothing from `logging` and logs nothing. CLAUDE.md states "All modules use `logging.getLogger("archon")` — no `print()`." `AgentLogger` silently discards all OS-level errors during file writes.

3. **`tts.py:178` — TODO for `tagged` mode**: The `auto="tagged"` mode is shipped in `TTSConfig` as a valid `Literal` value and documented in CLAUDE.md, but its implementation is a `return False` with a TODO comment. This is an incomplete feature presented as complete. The test suite even asserts `tagged → False` without marking it as `xfail`.

4. **`plugin_loader.py:215` — Calling private method `_load_skill` from foreign class**: Direct violation of encapsulation, specifically the Clean Code principle of "tell, don't ask" about internals. CLAUDE.md mandates SOLID principles.

5. **`history_compactor.py:204` — Non-atomic write**: CLAUDE.md's key constraints include file system correctness. A non-atomic write to a compaction output file violates data integrity under crash scenarios.

6. **`tts.py:asyncio.TimeoutError` catch is unreachable**: The `httpx.AsyncClient(timeout=timeout_sec)` raises `httpx.ReadTimeout` (a subclass of `httpx.HTTPError`), not `asyncio.TimeoutError`. The `except asyncio.TimeoutError` at line 117 is dead code. This dead catch is not caught by the existing tests.

---

## Overall Assessment

**Is this production-ready?** — **No**, not without fixing the three critical issues below.

The code shows good structural thinking and reasonable test coverage for happy paths. The TDD requirement is mostly met (each module has a corresponding test file). The SDK rule is correctly followed in `history_compactor.py` — it uses `ClaudeSDKClient`, not `anthropic.AsyncAnthropic`. The truncation algorithm, frontmatter parser, and compaction logic are sound.

However, the following **must be fixed before production**:

**Top 3 mandatory fixes:**

1. **`stt.py` — Kill the subprocess on timeout** (`transcribe_with_timeout`). This is a resource leak that accumulates zombie Whisper processes on every timeout. On a voice-heavy deployment this will eventually crash the host. Fix: `try/finally` with `proc.kill(); await proc.wait()` inside `transcribe`.

2. **`tts.py:107` — Move `output_path.write_bytes` inside the `async with` block**. The write currently happens after the HTTP client is closed. This is structurally wrong and a latent data-corruption bug.

3. **`history_manager.py` / `agent_logger.py` — Blocking file I/O on the event loop**. Every Telegram message response is delayed by synchronous disk writes. Wrap `_append` calls in `asyncio.to_thread` or migrate to a background write queue.
