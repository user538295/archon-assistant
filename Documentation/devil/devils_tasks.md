# Archon — Devil's Advocate Fix List
**Generated**: 2026-03-08
**Source**: DA-1 through DA-6 reports
**False positives excluded per DA-6 meta-review**

---

## Quick Wins (High impact, low effort — fix these first)

- [ ] **[pipeline.py:112-113]** Classifier error does not abort routing — add `return` after `yield ErrorEvent(...)` so the user never sees an error banner followed by a normal response.
- [ ] **[decomposer.py:96-100]** `_orch_session` has no `tools=[]` restriction — add `tools=[]` to the orchestration session constructor (one parameter, mirrors `_summary_session` at line 104).
- [ ] **[session_manager.py:121-122]** `_locks` TOCTOU on creation — replace `if user_id not in self._locks: self._locks[user_id] = asyncio.Lock()` with `self._locks.setdefault(user_id, asyncio.Lock())`.
- [ ] **[session_manager.py:171,181]** `_locks` leaked on `stop()` / `stop_all()` — add `self._locks.pop(user_id, None)` in `stop()` and `self._locks.clear()` in `stop_all()`.
- [ ] **[cron_scheduler.py:364]** `asyncio.get_event_loop()` deprecated in Python 3.10+ — replace with `asyncio.get_running_loop()` or `await asyncio.to_thread(job_file.read_text)`.
- [ ] **[commands.py:455,463]** `/skills` sends `skill.name`, `skill.description`, `plugin.key`, `plugin.version` in HTML context without `html.escape()` — wrap each with `html.escape()`.
- [ ] **[tts.py:131]** Dead fallback `or TTSConfig.edge_voice` is unreachable; remove it and use `self.config.edge_voice or "en-US-MichelleNeural"`.
- [ ] **[classification.py:63-66]** Backslash consumed outside strings in JSON extractor — add `if in_string:` guard before `escape_next = True`.
- [ ] **[archon_mcp_server.py:149]** `except (json.JSONDecodeError, Exception)` is redundant — simplify to `except Exception`.
- [ ] **[decomposer.py:167]** Empty `context` still injects `"\n\n"` spuriously — change fallback to `""`.

---

## CRITICAL

- [ ] **[tests/gateway/test_full_flow.py:97-119]** Five committed test failures — filter in `_run()` strips `⏳` ack messages, but assertions still expect them present (asserts `len==5` and `texts[0].startswith("⏳")`).
  **Impact**: CI fails on every run; no developer can trust the test suite.
  **Fix**: Either remove the `⏳` filter from `_run()` (keep ack, length=5, assert texts[0] is ack), or adjust all assertions to post-filter world (length=4, first element is thinking result).

- [ ] **[archon/ai/stt.py:81-87,107-125]** Whisper subprocess orphaned on timeout — `proc` is a local variable; `asyncio.wait_for` cancels at `await proc.communicate()` leaving the subprocess running indefinitely.
  **Impact**: Repeated voice-message timeouts accumulate zombie Whisper processes consuming 4-8 GB RAM each; eventually exhausts process/memory limits.
  **Fix**: Add `try/finally` inside `transcribe()` that calls `proc.kill(); await proc.wait()` on `asyncio.CancelledError`, or promote `proc` to an instance variable so it is reachable from `transcribe_with_timeout`.

- [ ] **[archon/gateway/gateway.py:416-418]** `cron_scheduler.stop()`, `bg_manager.stop_all()`, and `bg_mcp_server.stop()` have no `asyncio.wait_for()` timeout — only `session_manager.stop_all()` is guarded by the 5-second SLA.
  **Impact**: A hung SDK subprocess (background agent mid-call on SIGTERM) stalls shutdown indefinitely; launchd eventually SIGKILLs the process, potentially corrupting state.
  **Fix**: Wrap each of the three unguarded `await` calls in `asyncio.wait_for(..., timeout=_SHUTDOWN_TIMEOUT)` with appropriate fallback logging.

- [ ] **[archon/gateway/gateway.py:388]** No signal handlers registered — SIGTERM/SIGINT handling relies on aiogram internal behavior; double-SIGTERM from launchd bypasses the `finally` block.
  **Impact**: Unclean shutdown under launchd; final writes and SDK disconnects may not complete.
  **Fix**: Register `loop.add_signal_handler(signal.SIGTERM, ...)` and `loop.add_signal_handler(signal.SIGINT, ...)` in `_run()` before polling starts.

- [ ] **[archon/ai/cron_scheduler.py:200-202]** Fire-and-forget `asyncio.create_task()` for cron jobs — no reference retained, exceptions after the `finally` block are silently sent to stderr instead of the configured log file.
  **Impact**: Cron job panics are completely invisible in logs; operators have no way to know jobs are failing.
  **Fix**: Retain a reference to the task and attach a `done_callback` that logs any exception: `task.add_done_callback(lambda t: t.exception() and logger.error(..., exc_info=t.exception()))`.

---

## HIGH

- [ ] **[archon/ai/archon_mcp_server.py:140-144]** No `user_id` whitelist validation — any process on localhost can POST to `/mcp/{user_id}` and spawn agents for any user, bypassing the Telegram whitelist entirely.
  **Impact**: Localhost privilege escalation; crafted task prompts can execute arbitrary code in agent sessions for any user ID.
  **Fix**: Construct `ArchonMCPServer` with `allowed_user_ids: list[int]` and reject requests where the path `user_id` is not in that list before dispatching to `_handle_tools_call`.

- [ ] **[archon/ai/pipeline.py:104-123]** Classifier error does not abort routing — after yielding `ErrorEvent`, execution continues to `ClassificationEvent` and full decomposer routing with the default (`task, 0.0`) classification.
  **Impact**: Users see an error banner immediately followed by a normal response — contradictory UX that destroys trust.
  **Fix**: Add `return` immediately after `yield ErrorEvent(message=result.error, source="pipeline")`.

- [ ] **[archon/ai/decomposer.py:96-100]** `_orch_session` has full tool access — orchestration calls (JSON generation for routing/review) can invoke filesystem tools silently with no user-visible event and no output.
  **Impact**: Silent filesystem side-effects during classification; produces empty `raw_response` if tools run; no recovery path.
  **Fix**: Add `tools=[]` to the `_orch_session = ClaudeSession(...)` constructor, matching `_summary_session` at line 104.

- [ ] **[archon/version.py:22]** `get_version()` runs `git rev-list` subprocess at module import time — every `import archon` (daemon start, CLI invocation, test collection) spawns a `git` process.
  **Impact**: Measurable startup latency; crashes silently to version `"0"` in non-git environments (Docker, CI without git); test for `__version__` is effectively untestable.
  **Fix**: Use `functools.lru_cache`, `importlib.metadata.version()`, or a `__getattr__`-based lazy computation.

- [ ] **[archon/chat/commands.py:139-145]** `/restart` calls only `session_manager.stop_all()` before `os.execv()` — `bg_manager`, `cron_scheduler`, and `bg_mcp_server` are never stopped; background agent Claude subprocesses are orphaned.
  **Impact**: Orphaned subprocesses accumulate on each restart; the gateway `finally` shutdown path (the canonical sequence) is bypassed entirely.
  **Fix**: Call the full shutdown sequence — `cron_scheduler.stop()`, `bg_manager.stop_all()`, `bg_mcp_server.stop()`, then `session_manager.stop_all()` — before `os.execv()`.

- [ ] **[archon/ai/claude_session.py:182-187]** `os.environ.pop("CLAUDECODE")` is a process-global mutation with no serialization — two different users starting sessions concurrently race on `os.environ`.
  **Impact**: Second concurrent `start()` may inherit or lose `CLAUDECODE` non-deterministically; on multi-user deployments this corrupts child process environments.
  **Fix**: Use a module-level `asyncio.Lock` to serialize all `start()` calls that mutate `os.environ`, or pass env-var suppression through SDK subprocess options instead of process-global state.

- [ ] **[archon/ai/background_agent_manager.py:378]** `session.stop()` outside inner `try/finally` — if `session.stop()` raises on the success path, the second `session.stop()` attempt in `except Exception` may also raise, leaving the SDK subprocess alive.
  **Impact**: Under sustained agent load, leaked SDK subprocesses eventually exhaust file descriptors.
  **Fix**: Restructure `_run_agent()` so the outermost `try/finally` unconditionally calls `session.stop()`, rather than relying on the branching `except` handlers.

- [ ] **[archon/ai/background_agent_manager.py:418-421]** `except Exception: pass` silently swallows `session.stop()` errors during cancellation.
  **Impact**: Cancelled agent may leave a zombie Claude subprocess with no log trace; violates project convention of no silent exception swallowing.
  **Fix**: Replace `pass` with `logger.warning("session.stop() failed during cancellation of agent %r", run.name, exc_info=True)`.

- [ ] **[archon/ai/plan_executor.py:102-105]** Indefinite `asyncio.gather` wait on `done` events — no timeout; if a spawned agent never sets `done` (e.g., `_run_agent` panic in its own `finally`), the wave waits forever.
  **Impact**: PlanExecutor hangs indefinitely, holding the user's session with no feedback or completion notification.
  **Fix**: Replace with `asyncio.wait_for(asyncio.gather(...), timeout=configurable_max)` and send a timeout notification if exceeded.

- [ ] **[archon/ai/plan_executor.py:85-93]** Unhandled `RuntimeError` from `spawn()` inside the wave loop aborts the plan silently — already-running wave agents are orphaned with no cleanup.
  **Impact**: Partial plan execution with generic error message; users cannot tell which agents ran and which were skipped.
  **Fix**: Wrap `self._bam.spawn()` in `try/except RuntimeError` inside the wave loop; add failed tasks to `failed_ids` and continue the remaining wave.

- [ ] **[archon/ai/history_manager.py:59-61 / archon/ai/agent_logger.py:169-171]** Blocking file I/O (`f.write()`, `f.open()`) runs synchronously on the asyncio event loop in the hot path of the Telegram message handler.
  **Impact**: Every file write stalls the event loop; tail latency spikes during heavy agent sessions with many events.
  **Fix**: Wrap `_append` calls in `asyncio.to_thread(...)` or implement a background write queue that drains on a dedicated task.

- [ ] **[archon/ai/cron_scheduler.py:189-203]** `_loop()` has no `try/except` around the tick body — any unexpected exception kills the entire scheduler loop silently.
  **Impact**: After a single transient error in `_should_fire()` or `asyncio.create_task()`, all cron jobs stop running permanently with no operator notification.
  **Fix**: Wrap the tick body in `try/except Exception: logger.exception("Scheduler tick failed")` to prevent transient errors from killing the loop.

- [ ] **[archon/config/loader.py:302-309]** `load_cron_jobs` accesses `job_data["schedule"]` without a try/except — a missing `schedule` key raises unguarded `KeyError`.
  **Impact**: A misconfigured cron TOML file produces a cryptic traceback at startup instead of a helpful `ConfigError`.
  **Fix**: Wrap in `try/except KeyError` and raise `ConfigError(f"cron job '{name}' is missing required 'schedule' field")`.

- [ ] **[archon/ai/cron_scheduler.py:291-326]** `_run_tool` uses `shlex.split(command)` but has no allowlist validation — any process with write access to the TOML jobs directory can execute arbitrary binaries as the daemon user.
  **Impact**: Privilege escalation if jobs directory permissions are not correctly set; no documentation of the assumption.
  **Fix**: Add a permissions check on `jobs_dir` at load time and document the security model explicitly; at minimum log a WARNING if the directory is world-writable.

- [ ] **[archon/ai/cron_scheduler.py:205-238]** `_should_fire` timezone comparison may hit `TypeError` if `croniter.get_prev()` returns a naive datetime for a timezone-aware input — broad `except Exception` silently returns `False`.
  **Impact**: All jobs with `timezone` settings permanently stop firing without any notification.
  **Fix**: After `get_prev()`, assert `tzinfo` is set: `if prev_aware.tzinfo is None: prev_aware = prev_aware.replace(tzinfo=tz)`.

- [ ] **[archon/ai/agent_logger.py:255-263]** TOCTOU race in `_agent_path()` — `if not candidate.exists()` check and file creation are not atomic; concurrent same-name agent starts can both claim the same filename.
  **Impact**: Log file corruption when two subagents with the same name start within the same minute.
  **Fix**: Use `open(candidate, 'x')` (exclusive create) and catch `FileExistsError` to retry atomically.

- [ ] **[archon/gateway/gateway.py:249-260]** `_midnight_compaction_loop` uses `datetime.now()` (local naive time) — DST spring-forward runs compaction twice in one day; fall-back skips a day.
  **Impact**: Duplicate or missing compaction around DST transitions; history summary gaps or duplicates.
  **Fix**: Use `datetime.now(timezone.utc)` to schedule against UTC midnight, or derive sleep duration from monotonic time.

- [ ] **[archon/ai/voice.py:247-252]** Duplicate reminder tracking — `voice.py` calls `session.reminder.record_message()` and `session.reminder.record_tokens()` after `session.send()`, which already does the same in its `finally` block; voice messages count twice.
  **Impact**: Reminders fire at half the configured interval for voice-heavy sessions; timing becomes unpredictable.
  **Fix**: Remove lines 247-252 from `voice.py`; reminder tracking inside `ClaudeSession.send()` is authoritative for all paths.

- [ ] **[archon/cli/config_cmd.py:116]** `_run_set` writes config non-atomically with `_CONFIG_PATH.write_text()` — crash between open/truncate and write completion corrupts the config file.
  **Impact**: Config corruption on power loss or SIGKILL mid-write; daemon cannot start.
  **Fix**: Replace with the existing `atomic_write()` from `loader.py` (already used in `save_notifications_config` at `loader.py:591`).

- [ ] **[archon/log_setup.py:95-96]** Race condition in `_rotate_on_startup` — `log_path.rename(dated_path)` has no `try/except OSError`; two rapid restarts race on rename, second raises `FileNotFoundError`, preventing logging setup entirely.
  **Impact**: Daemon fails to start or starts with broken logging after rapid restart.
  **Fix**: Wrap `log_path.rename(dated_path)` in `try/except OSError: pass`.

- [ ] **[archon/cli/doctor.py:70-74]** `_check_env_file` checks only `"TELEGRAM_BOT_TOKEN" in content` — a commented-out or empty-value line passes as healthy.
  **Impact**: `archon doctor` reports green but daemon immediately fails with `ConfigError` on startup.
  **Fix**: Parse with `re.search(r'^TELEGRAM_BOT_TOKEN=\S+', content, re.MULTILINE)` to require a non-empty value.

---

## MEDIUM

- [ ] **[archon/ai/stt.py:94-98]** `txt_file.unlink()` has no `try/except` — permissions error or read-only filesystem propagates as `OSError` even though transcription already succeeded; stale `.txt` files also cause next run to return stale content.
  **Impact**: Unnecessary transcription failures; stale result correctness bug on crash-interrupted runs.
  **Fix**: Wrap `unlink()` in `try/except OSError: logger.warning(...)`. Also add `encoding="utf-8"` to `txt_file.read_text()` to prevent silent corruption of non-ASCII transcriptions.

- [ ] **[archon/ai/history_compactor.py:204]** `out_path.write_text(summary)` is non-atomic — partial write on SIGKILL leaves a corrupt compacted file that is never re-generated (existence check treats it as complete).
  **Impact**: Permanently corrupted compaction output for the affected day; summary gaps that are unrecoverable without manual deletion.
  **Fix**: Write to `out_path.with_suffix(".tmp")` then rename atomically.

- [ ] **[archon/ai/background_agent_manager.py:209-213]** `self._runs[run_id] = run` registered before `asyncio.create_task()` — if `create_task()` raises (event loop closing), an orphaned run with `_task_ref = None` remains in `_runs` permanently.
  **Impact**: `list_running()` returns a phantom run that never completes; `max_parallel` check is permanently skewed.
  **Fix**: Assign `_task_ref` first, then insert into `_runs`.

- [ ] **[archon/ai/agent_plan.py:85-89]** `queue.pop(0)` in `validate_dependency_graph` is O(n) — makes Kahn's algorithm O(n²); also duplicates the graph traversal already implemented in `topological_sort`.
  **Impact**: Performance degradation for large plans (>50 agents); maintenance burden of two graph-traversal implementations.
  **Fix**: Use `collections.deque` with `popleft()`; better: remove `validate_dependency_graph` from `_execute_plan` and wrap `topological_sort` in `try/except ValueError` with a user-facing message.

- [ ] **[archon/ai/plan_executor.py:59-67]** Double cycle-detection — `validate_dependency_graph` and `topological_sort` both detect cycles independently; the `ValueError` from `topological_sort` is dead code in this usage.
  **Impact**: Inconsistent user-facing error messages depending on which path detects the cycle; maintenance confusion.
  **Fix**: Remove the `validate_dependency_graph` call from `_execute_plan`; catch `topological_sort`'s `ValueError` directly for the user-visible message.

- [ ] **[archon/ai/skill_loader.py:9]** Frontmatter regex `^---\n` does not match Windows line endings `\r\n` — affects `agent_loader.py` which imports `_FRONTMATTER_RE`.
  **Impact**: Skills and agents silently disappear when `.md` files have Windows line endings (git `autocrlf=true`, Windows editors).
  **Fix**: Change to `r"^---\r?\n(.*?)\r?\n---\r?\n"`.

- [ ] **[archon/ai/agent_loader.py:147-151]** YAML-list-style `tools:` fields silently produce empty tools list — key-value regex requires value on same line; multiline YAML sequences are dropped silently.
  **Impact**: Agents with `tools:\n  - Read\n  - Write` load with zero tools; unexpected SDK behavior with no error.
  **Fix**: Extend parser to handle multi-line sequences, or add a validation warning when `tools:` is present but parsed as empty.

- [ ] **[archon/ai/history_compactor.py:76]** Response-extraction regex hard-codes the `✅` emoji and heading format from `EventRenderer` — any format change silently breaks compaction.
  **Impact**: Silent compaction failure on format drift; historical summaries stop being generated.
  **Fix**: Extract the heading format as a shared constant used by both `EventRenderer` and `_extract_responses`, and add a round-trip test.

- [ ] **[archon/ai/history_manager.py:42]** UTC timestamp in records but `date.today()` (local time) for file path — near-midnight messages in UTC+ zones land in the wrong file.
  **Impact**: Minor cosmetic inconsistency; messages appear in "yesterday's" history file with "today's" UTC timestamp.
  **Fix**: Derive both the record timestamp and the file date from the same `datetime.now(timezone.utc)` object.

- [ ] **[archon/ai/plugin_loader.py:207]** `_load_plugin_skills` calls `SkillLoader._load_skill()` — a private method of a foreign class.
  **Impact**: Any internal refactor of `SkillLoader` silently breaks `PluginLoader`; tight coupling that defeats SOLID.
  **Fix**: Make `_load_skill` public (`load_skill`) or extract a standalone `parse_skill_file(path)` function.

- [ ] **[archon/ai/plugin_loader.py:93-98]** `_read_enabled_keys` does not validate that `enabledPlugins` is a dict — if `settings.json` has `"enabledPlugins": [...]`, `AttributeError` on `.items()` is raised; the `except` only catches `json.JSONDecodeError, OSError`.
  **Impact**: Malformed `settings.json` crashes plugin loading with an uncaught exception.
  **Fix**: Add `if not isinstance(enabled_plugins, dict): return set()`.

- [ ] **[archon/chat/commands.py:581-592]** `/model` accepts arbitrary unchecked model name — no validation against `models_config.available`; SDK call fails at runtime rather than at command time.
  **Impact**: Confusing delayed failure; user is told "Model set to `bad-model`" then gets an API error on next message.
  **Fix**: When `models_config.available` is non-empty, validate `arg` is in the list and emit a helpful error if not.

- [ ] **[archon/chat/commands.py:296-303]** `/notify quiet N` accepts negative intervals silently — confirmation message says "every -5 min" while beacon is silently disabled.
  **Impact**: False confirmation misleads the user about the actual beacon behavior.
  **Fix**: Add `if int(parts[2]) >= 0:` guard before assigning `interval_minutes`.

- [ ] **[archon/chat/handler.py:337 / archon/chat/voice.py:128,168]** `assert message.bot is not None` used as production guard — stripped under `python -O`; raises `AssertionError` (not user-visible) rather than a graceful skip.
  **Impact**: Under optimize builds or edge cases, the coroutine crashes with a non-descriptive internal error that leaks implementation details.
  **Fix**: Replace each `assert` with `if message.bot is None: logger.warning(...); return`.

- [ ] **[archon/chat/handler.py:426]** Same `assert message.bot is not None` inside PlanEvent handling — if it fires, the entire `async for` event loop breaks; Claude's response stream is abandoned mid-flight.
  **Impact**: No completion message is ever delivered to the user; session appears to hang.
  **Fix**: Replace with `if message.bot is None: logger.error(...); continue`.

- [ ] **[archon/config/loader.py:393-407]** Notification mode accepts any string — `mode = "typo"` passes validation silently; handler falls through to debug behavior.
  **Impact**: Silent misconfiguration with no error at startup; unexpected verbose output.
  **Fix**: Validate against `("quiet", "normal", "verbose", "debug")` and raise `ConfigError` on unknown value.

- [ ] **[archon/config/loader.py:106-109]** `VoiceTTSConfig.auto` accepts any string — `auto = "typo"` passes silently.
  **Impact**: TTS silently behaves as if configured incorrectly; no startup warning.
  **Fix**: Validate against `("always", "inbound", "off")` in `load_config` and raise `ConfigError`.

- [ ] **[archon/config/loader.py:357-366]** `allowed_user_ids` elements not type-validated; `int()` coercions for `max_parallel` and `port` raise `ValueError` (not `ConfigError`) on bad input.
  **Impact**: Cryptic startup crash instead of a helpful config error message.
  **Fix**: Validate element types in `allowed_user_ids`; wrap all `int()` conversions in `try/except ValueError` and raise `ConfigError`.

- [ ] **[archon/chat/telegram_delivery.py:40]** Binary-search fallback on overflow emits a single character with no indication of truncation.
  **Impact**: User receives a one-character message with no context; a critical response may be silently discarded.
  **Fix**: Log an error and return a message indicating content was too large to display rather than silently emitting one character.

- [ ] **[archon/ai/cron_scheduler.py:329-349]** `_run_prompt` creates `ClaudeSession(model=self._model)` without `cwd` — cron prompt sessions lack the working directory that background agent sessions always receive.
  **Impact**: Tool calls inside cron prompt steps fail or produce incorrect results when the task involves project files.
  **Fix**: Pass `cwd=self._cwd` to `ClaudeSession` in `_run_prompt`.

- [ ] **[archon/chat/handler.py, archon/chat/voice.py]** No `TelegramRetryAfter` handling — rate-limit exceptions are caught, logged, and silently dropped with no retry.
  **Impact**: In verbose/debug mode during heavy tool sessions, Telegram throttling causes permanent message loss with no user notification.
  **Fix**: Catch `TelegramRetryAfter` specifically, `await asyncio.sleep(exc.retry_after)`, and retry the send once before logging and continuing.

- [ ] **[archon/ai/decomposer.py:401-402]** `_pending_turns.popleft()` pop-count relies on an undocumented asyncio single-task ordering invariant — any future reentrant refactor of `_refresh_summary` breaks this silently.
  **Impact**: Turns could be incorrectly over- or under-popped on refactor; data correctness bug that would be hard to trace.
  **Fix**: Document the invariant with a comment, or capture the exact turns to remove by identity in `snapshot` and remove by reference rather than by count.

- [ ] **[archon/ai/decomposer.py:120-135]** `_inject_workspace_agents()` calls `agents_path.read_text()` synchronously inside an async method.
  **Impact**: Blocks the event loop on network-mounted or slow filesystems; violates asyncio best practices.
  **Fix**: Use `await asyncio.to_thread(agents_path.read_text, encoding="utf-8")`.

- [ ] **[archon/gateway/gateway.py:385-388]** `bg_mcp_server._manager = bg_manager` mutates a private attribute post-construction to break a circular dependency — object is in an invalid state between construction and line 388.
  **Impact**: Any call to MCP server methods before line 388 would NPE; defeats type checking (`# type: ignore`).
  **Fix**: Restructure construction order: create `BackgroundAgentManager` first, pass it to `ArchonMCPServer` at construction, or use a factory pattern.

- [ ] **[archon/ai/plan_executor.py:97-99]** `WaveStarted` event recorded after all agents in the wave are spawned — in history logs, `WaveStarted` appears after `SubagentStarted` events, inverting the expected timeline.
  **Impact**: Minor ordering issue in history and operator logs; confusing timeline for debugging.
  **Fix**: Move `self._record_event(WaveStarted(...))` to before the wave's `spawn()` loop begins.

- [ ] **[archon/ai/plan_executor.py:121-133]** Final plan summary does not count `cancelled` status — `succeeded + failed + skipped < total` with no explanation when an agent is cancelled mid-plan.
  **Impact**: Misleading summary; users cannot account for all agents in the plan.
  **Fix**: Add `cancelled = sum(1 for r in runs.values() if r.status == "cancelled")` and include it in the summary message.

- [ ] **[archon/ai/history_compactor.py:99-107]** SDK client created per-call with no reuse — `compact_pending_days` creates, connects, queries, and disconnects one `ClaudeSDKClient` per day of backlog.
  **Impact**: N days of missed compaction = N sequential SDK process spawn/teardown cycles at startup; noticeable latency after a long outage.
  **Fix**: Cache the client in `HistorySummarizer` after first creation and reuse across calls.

- [ ] **[archon/cli/update.py:38-47]** `subprocess.run(cmd)` not wrapped in `try/except FileNotFoundError` — crashes with a traceback if `uv` is not in PATH.
  **Impact**: CLI crash with confusing traceback instead of a clean error message.
  **Fix**: Wrap in `except FileNotFoundError: print("uv not found in PATH"); return 1`.

---

## LOW / Style

- [ ] **[archon/ai/claude_session.py:242]** `ClaudeSession` accesses `self._reminder._message_count` (private attribute) — breaks encapsulation; rename of `_message_count` in `ContextReminder` silently breaks `ClaudeSession`.
  **Fix**: Add a `message_count` property to `ContextReminder`.

- [ ] **[archon/ai/claude_session.py:260]** `self._reminder._config.notify` — second private attribute access on `ContextReminder`.
  **Fix**: Add a `notify` property to `ContextReminder` returning `self._config.notify`.

- [ ] **[archon/ai/event_mapper.py:203-211]** `_tool_id_map` and `_tool_name_map` grow unbounded across turns for long-lived sessions — minor slow memory leak for heavy background agents.
  **Fix**: Use an LRU cache or reset the maps per `query()` round.

- [ ] **[archon/ai/event_mapper.py:231-232]** `TextBlock` in `AssistantMessage` is silently discarded with `pass` — no debug log; if the SDK changes delivery mechanism, the silence makes debugging very hard.
  **Fix**: Add `logger.debug("TextBlock in AssistantMessage discarded (arrives via ResultMessage)")`.

- [ ] **[archon/ai/prompts/__init__.py:8-14]** `load_prompt()` raises bare `FileNotFoundError` with no context — at startup a missing prompt file produces a confusing traceback with no hint it's a required resource.
  **Fix**: Catch `FileNotFoundError` and re-raise with `raise FileNotFoundError(f"Required prompt '{name}.md' not found in {_PROMPTS_DIR}") from exc`.

- [ ] **[archon/ai/session_manager.py:209]** `hasattr(session, "track_context")` duck-type check couples `SessionManager` to concrete implementation details.
  **Fix**: Define a Protocol or ABC specifying the duck-typed interface.

- [ ] **[archon/ai/decomposer.py:233-235]** `estimated_tools` has no `max(0, ...)` guard — LLM returning `-1` propagates as a negative integer.
  **Fix**: Apply `estimated_tools = max(0, int(estimated_tools))` mirroring `classification.py:133`.

- [ ] **[archon/ai/classifier.py:17 / archon/ai/decomposer.py:28-29 / archon/ai/history_compactor.py:11]** Hardcoded `"claude-haiku-4-5-20251001"` in three modules — not validated against `[models] available`; model deprecation causes cascading classifier/decomposer/compactor failures.
  **Fix**: Extract to a single shared constant or derive from config; log a WARNING at startup if the constant model is not in `available`.

- [ ] **[archon/ai/decomposer.py:333,453]** Deferred `from archon.ai...` imports inside method bodies — dependency tracking is harder and IDE support is degraded.
  **Fix**: Move both imports to module-level (runtime imports, not `TYPE_CHECKING`).

- [ ] **[archon/ai/agent_plan.py:12-16]** `AgentTask.depends_on: list[str]` is mutable despite the dataclass being `frozen=True` — the slot can't be reassigned but the list contents can.
  **Fix**: Use `tuple[str, ...]` for a truly immutable dependency list.

- [ ] **[archon/ai/stt.py:16-17]** `Optional[str]` used instead of `str | None` — inconsistent with the rest of the Python 3.12+ codebase.
  **Fix**: Change to `str | None`.

- [ ] **[archon/ai/tts.py:178]** `TODO: Check for [[tts:...]] tags` — the `"tagged"` auto mode is documented as a valid `Literal` value but the implementation returns `False` with no warning or `NotImplementedError`; test suite asserts the broken behavior without `xfail`.
  **Fix**: Either implement the feature or replace `return False` with `raise NotImplementedError("tagged TTS mode is not yet implemented")` and mark the test as `xfail`.

- [ ] **[archon/ai/tts.py:117]** `except asyncio.TimeoutError` in `_openai_tts` is dead code — `httpx.AsyncClient(timeout=...)` raises `httpx.ReadTimeout`, not `asyncio.TimeoutError`.
  **Fix**: Change to `except httpx.ReadTimeout` or `except httpx.TimeoutException`.

- [ ] **[archon/ai/event_renderer.py:30-32]** `_format_size` is a one-line alias for `format_tool_result_size` with no added logic — unnecessary indirection.
  **Fix**: Replace all `_format_size` calls with direct `format_tool_result_size` calls and remove the wrapper.

- [ ] **[archon/ai/agent_logger.py]** Module has no `import logging` and no `logger = logging.getLogger("archon")` — `OSError` from file writes propagates silently with no context; violates project convention.
  **Fix**: Add `import logging` and `logger = logging.getLogger("archon")`; wrap `_append` in `try/except OSError: logger.warning(...)`.

- [ ] **[archon/chat/commands.py:185]** `_CONTEXT_WINDOW_TOKENS = 200_000` hardcoded — silently wrong for models with different context sizes.
  **Fix**: Derive from model configuration or document as an approximation with a comment.

- [ ] **[archon/chat/handler.py:435 / archon/chat/voice.py:204]** `asyncio.create_task()` result not stored — Python asyncio docs warn that the caller must hold a reference to prevent garbage collection in some environments.
  **Fix**: Store in a `set`; add a `done_callback` that removes the task on completion.

- [ ] **[archon/gateway/__init__.py:1]** `_setup_dp` (private, underscore-prefixed) is exported in `__all__` — contradicts naming convention; makes internal function part of the public API.
  **Fix**: Remove `_setup_dp` from `__all__` and the `__init__.py` import.

- [ ] **[archon/chat/handler.py:373-377]** Sub-agent event routing skips `history_manager.record_event()` with no documenting comment — intent is unclear.
  **Fix**: Add an explicit comment: "Sub-agent events are intentionally excluded from session history — logged via AgentLogger only."

- [ ] **[archon/cli/config_cmd.py:28-34]** `archon config show` prints raw TOML to stdout with no redaction — pattern is unsafe in shell scripts that log stdout.
  **Fix**: At minimum document that the command prints raw TOML; better: redact any key named `token`, `password`, `secret`, or `key`.

- [ ] **[archon/ai/context_provider.py]** `ContextProvider` Protocol has no `@runtime_checkable` decorator — cannot be used with `isinstance()` checks.
  **Fix**: Add `@runtime_checkable` or document structural-only usage explicitly.

- [ ] **[archon/ai/reminder.py]** TOCTOU between `should_inject()` and `build_reminder_message()` — file can be deleted between calls; `read_text()` raises unhandled `FileNotFoundError`.
  **Fix**: Wrap `read_text()` in `try/except FileNotFoundError: logger.warning(...); return ""`.

- [ ] **[archon/cli/doctor.py:128-129]** `print()` used in CLI modules — violates the project convention "no `print()`" unless scoped as a CLI exception.
  **Fix**: Document explicitly in `CLAUDE.md` that CLI modules are exempt from the `print()` prohibition (already a sensible exception; needs to be codified).

---

## Totals
- Critical: 5
- High: 22
- Medium: 26
- Low: 25
- **Total tasks: 78**
