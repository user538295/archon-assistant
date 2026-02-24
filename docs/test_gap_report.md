# Test Gap Report — Archon

**Generated:** 2026-02-24
**Scope:** Full audit of unit, integration, and e2e test coverage across all source modules.

---

## Executive Summary

The archon codebase has strong test coverage overall, following TDD discipline with comprehensive suites for every module. However, a deep audit reveals **20 high-priority gaps** and **10 medium-priority gaps** across 7 modules. The most critical blind spots are: untested `PluginsConfig` loading in the config loader, completely absent `get_model()`/`set_model()` tests in `SessionManager`, missing `CallbackQuery` path in `WhitelistMiddleware`, and several untested gateway wiring paths.

---

## Module-by-Module Analysis

### 1. `archon/ai/claude_session.py` — `ClaudeSession`

**Coverage:** Good. Lifecycle, skills, usage tracking all well tested.

| Gap | Priority | Description |
|-----|----------|-------------|
| `model` property | Medium | The `model` property is never read back in any test; only `session._model` is implicitly set via `ClaudeAgentOptions`. |
| `plugins` parameter | High | The `plugins` parameter passed to `ClaudeAgentOptions` is never verified — no test asserts that `options.plugins` contains the expected SDK plugin configs. |
| `stop()` swallows RuntimeError | Medium | The `except RuntimeError` branch in `stop()` (anyio cancel scope) is never triggered in tests; the warning log path is untested. |
| CLAUDECODE absent from env | Low | `test_start_strips_claudecode_before_connect` only tests when `CLAUDECODE` is present. The branch where it was never set (so no pop/restore needed) has no dedicated test. |
| Concurrent `send()` calls | Low | No test verifies behavior when `send()` is called concurrently on the same session. |

---

### 2. `archon/ai/event_mapper.py` — `EventMapper`

**Coverage:** Excellent. All six event types, ID allocation, edge cases all covered.

| Gap | Priority | Description |
|-----|----------|-------------|
| `_tool_input_text` with `None` input | Low | The `_tool_input_text` helper is not called with a `None` dict directly; empty dict is tested but no explicit test for `None` being guarded. |

---

### 3. `archon/ai/history_manager.py` — `HistoryManager`

**Coverage:** Good. All event types, date rotation, cwd tagging covered.

| Gap | Priority | Description |
|-----|----------|-------------|
| Response with no prior question | High | `_render` for `Response` does `self._last_question.get(user_id, "")`. If no prior `record_user_message` was called for that `user_id`, `q` is `""` and the `q_ctx` blockquote is skipped. This path (`q == ""`) is never exercised. |
| Multi-user isolation | Medium | No test verifies that messages from user A and user B are correctly interleaved in the same file with their respective user IDs. |
| File write failure (IOError) | Low | The `_append` method uses a plain `open()` with no error handling. No test exercises what happens if the file is unwritable. |

---

### 4. `archon/ai/plugin_loader.py` — `PluginLoader`

**Coverage:** Good for happy paths and most edge cases.

| Gap | Priority | Description |
|-----|----------|-------------|
| Corrupt JSON in `installed_plugins.json` | High | `_read_installed_plugins` catches `json.JSONDecodeError` and logs a warning. No test triggers this path. |
| Corrupt JSON in `settings.json` | High | `_read_enabled_keys` catches `json.JSONDecodeError` and logs a warning. No test triggers this path. |
| Missing/malformed `plugin.json` manifest | Medium | `_load_plugin` has a `try/except` around manifest reading; if it fails, `version` stays `"unknown"`. No test exercises this fallback. |
| Unrecognized `installed_plugins.json` format | Medium | The final `logger.warning("installed_plugins.json has an unrecognised format")` branch (neither dict nor list) is never triggered in tests. |
| v2 installs-as-dict (not list) | Low | The `elif isinstance(installs, dict)` branch in `_read_installed_plugins` has no test. |
| Multiple plugins with multiple skills ordering | Low | No test verifies that skills from multiple plugins are returned in a consistent order. |

---

### 5. `archon/ai/session_manager.py` — `SessionManager`

**Coverage:** Good for lifecycle and concurrency, but model management is completely absent.

| Gap | Priority | Description |
|-----|----------|-------------|
| `get_model()` / `set_model()` | **Critical** | These two public methods have **zero tests**. `set_model` is central to the `/model` command behavior — it affects every new session created after it's called. |
| Default factory path (with `skill_loader` / `plugin_loader`) | High | All `SessionManager` tests use a custom `session_factory` to inject mocks. The real default factory that calls `skill_loader.load_all()`, `plugin_loader.get_skills()`, and `plugin_loader.get_sdk_configs()` is completely untested. |
| `_evict_after` timer self-cleanup | Low | The timer removal before calling `stop()` (to avoid self-cancellation) is a subtle invariant that is only tested indirectly; no test verifies `self._timers.pop(user_id, None)` removes the timer before calling stop. |

---

### 6. `archon/ai/skill_loader.py` — `SkillLoader`

**Coverage:** Excellent. All error cases and caching tested.

| Gap | Priority | Description |
|-----|----------|-------------|
| Non-directory entries at `skills_dir` root | Low | The `if not entry.is_dir(): continue` guard is never tested with a file at the top level of the skills directory. |
| `_parse_frontmatter` edge cases | Low | Module-level helper tested only indirectly; values containing colons (e.g., `description: a: b`) are not tested. |

---

### 7. `archon/ai/truncation.py` — `SplitStrategy`

**Coverage:** Good. Digit boundary, label format, reconstruction all tested.

| Gap | Priority | Description |
|-----|----------|-------------|
| Empty string input | Medium | `SplitStrategy().apply("", max_len=100)` — the `len(text) <= max_len` short-circuit returns `[""]`, but this is never asserted. |
| `max_len=1` | Low | Near-boundary behavior with an extremely small max_len (where label width may dominate) is not tested. |

---

### 8. `archon/chat/bot.py`

**Coverage:** Partial. Only 3 of 15 commands are verified in `create_dispatcher`.

| Gap | Priority | Description |
|-----|----------|-------------|
| 12 commands not verified in dispatcher | High | Only `start_command`, `clear_command`, and `model_command` are asserted to be registered. The following are never checked: `status_command`, `context_command`, `stop_command`, `restart_command`, `notify_command`, `quiet_command`, `normal_command`, `verbose_command`, `debug_command`, `settings_command`, `skills_command`, `skill_command`. |
| Callback handlers not verified | High | `notify_callback` and `model_callback` registrations (`dp.callback_query.register(...)`) are never asserted. |
| `BOT_COMMANDS` completeness | Medium | Only the presence of `"model"` in `BOT_COMMANDS` is tested. No test verifies that all 15 commands are present, or that command count matches the dispatch table. |

---

### 9. `archon/chat/commands.py`

**Coverage:** Very good. Most branches and edge cases covered.

| Gap | Priority | Description |
|-----|----------|-------------|
| `skills_command` with `plugin_loader` | High | `skills_command` accepts an optional `plugin_loader` parameter and renders a "Plugin skills" section. No test passes a `plugin_loader` mock with actual plugins — the plugin-skills rendering path is completely untested. |
| `_fmt_context` duration ≥ 60 seconds | Medium | The `dur_str = f"{dur_s / 60:.1f}m"` branch (triggered when `dur_s >= 60`) has no test. Only the seconds path (`dur_s < 60`) is exercised. |
| `notify_callback` with unrecognized data | Medium | If `callback.data` doesn't start with a valid mode (e.g., `"notify:invalid"`), the callback still calls `edit_reply_markup` and `answer()` without setting mode. This silent no-op path is untested. |
| `_notify_keyboard` beacon label in button text | Medium | The quiet-mode button with beacon interval shows `"🔇 Quiet 🔦Nm ✓"`. No test verifies the actual button text content in this case. |
| `model_command` with active session cleared | Low | `model_command` calls `session_manager.stop(user_id)` when there's an active session. `test_model_set_via_text_arg` uses `active=False` so the stop branch is never exercised via `model_command` (only via `model_callback`). |
| `_model_keyboard` with odd number of models | Low | The `if row: rows.append(row)` remnant-row path in `_model_keyboard` (odd number of models leaving one button unpaired) has no test. |

---

### 10. `archon/chat/handler.py`

**Coverage:** Excellent. The most thoroughly tested module.

| Gap | Priority | Description |
|-----|----------|-------------|
| `message.bot is None` assertion | Medium | `handle_message` has `assert message.bot is not None`. Tests always provide `msg.bot = MagicMock()`. The `AssertionError` path is never triggered. |
| `_keep_typing` inner loop | Low | The `_keep_typing` coroutine is cancelled before it can loop. The second (and subsequent) `send_chat_action` calls from the loop body are never observed in tests. |
| `_partial_update_task` when mode changes cancel it | Low | The beacon-cancel-on-mode-change test (`test_handle_message_quiet_beacon_cancelled_on_mode_change`) exercises the cancel logic, but it does not verify the task is `done()` after cancellation. |

---

### 11. `archon/chat/md_formatter.py`

**Coverage:** Excellent. 30+ tests covering all formatting rules and HTML safety.

| Gap | Priority | Description |
|-----|----------|-------------|
| h4+ headings not converted | Low | The regex `^#{1,3} +` stops at h3. `#### Heading 4` should pass through unchanged. No test asserts this boundary behavior. |
| `_inline` function directly | Low | The internal `_inline` helper is only tested via `md_to_html`. Its behavior on segments already containing `\x00` placeholders is implicitly safe but unverified. |

---

### 12. `archon/chat/middleware.py` — `WhitelistMiddleware`

**Coverage:** Message events well-tested; other event types untested.

| Gap | Priority | Description |
|-----|----------|-------------|
| `CallbackQuery` events | **Critical** | `WhitelistMiddleware` explicitly handles `CallbackQuery` in the `isinstance(event, (Message, CallbackQuery))` check. **No test passes a `CallbackQuery` to the middleware.** The whitelist filter for button taps is completely untested. |
| Non-Message/CallbackQuery pass-through | Medium | Any other `TelegramObject` type should pass through to the handler unconditionally (no user check). No test verifies this fallback. |

---

### 13. `archon/config/loader.py`

**Coverage:** Comprehensive for all sections except `PluginsConfig`.

| Gap | Priority | Description |
|-----|----------|-------------|
| `PluginsConfig` loading | **Critical** | The `[plugins]` section in `config.toml` is never tested in `test_loader.py`. The three fields — `enabled`, `plugins_dir`, and `settings_path` — have no test verifying they load correctly, default correctly, or that `enabled=false` disables the plugin loader. |
| `save_notifications_config` on write failure | Low | No test exercises what happens if the config file is read-only when `save_notifications_config` is called. |
| `LoggingConfig` custom values from config | Low | Only defaults for `LoggingConfig` are verified via the optional-defaults test. A config with custom `log_file` and `log_level` is never loaded and checked (only `/tmp/test_archon.log` in the live test). |

---

### 14. `archon/gateway/gateway.py` — `Gateway`

**Coverage:** Shutdown, wiring, restart notification all tested. Entry point and some wiring paths missing.

| Gap | Priority | Description |
|-----|----------|-------------|
| `_make_truncation` with unknown strategy | High | `_make_truncation("headtail")` raises `ConfigError`. This branch is completely untested — there is no test for an unknown `truncation_strategy` value. |
| `_setup_dp` `skill_loader` injection | Medium | `_setup_dp` either uses the provided `skill_loader` or creates a default `SkillLoader()`. Tests pass `None`, never verifying that a provided `skill_loader` object ends up in `dp["skill_loader"]`. |
| `_setup_dp` `plugin_loader` wiring | Medium | The `dp["plugin_loader"] = plugin_loader` path (when plugin_loader is not None) is tested via `test_setup_dp_injects_session_manager` (which uses `_mock_session_manager()`) but `dp["plugin_loader"]` is never asserted. |
| `Gateway._run()` with `cfg.models.default` set | High | When `cfg.models.default` is not `None`, `_run()` calls `session_manager.set_model(cfg.models.default)`. No test covers this startup path. |
| `Gateway._run()` with `cfg.plugins.enabled = False` | Medium | The `plugin_loader = None` path (when plugins are disabled) in `_run()` is never exercised. |
| `Gateway.start()` entry point | Low | `Gateway.start()` calls `asyncio.run(cls._run())`. No test covers the synchronous entry point. |

---

### 15. `archon/log_setup.py`

**Coverage:** Excellent. All rotation behaviors and handler wiring fully tested.

No significant gaps found.

---

### 16. `main.py`

**Coverage:** None.

| Gap | Priority | Description |
|-----|----------|-------------|
| `main()` function | Low | `main()` simply calls `Gateway.start()`. A smoke test asserting `main` is importable and callable would be sufficient. |

---

## Gaps by Test Type

### Missing Unit Tests

| Module | Missing Unit Test |
|--------|------------------|
| `session_manager.py` | `get_model()`, `set_model()` |
| `session_manager.py` | Default factory with skill_loader + plugin_loader |
| `commands.py` | `skills_command` with plugin_loader |
| `commands.py` | `_fmt_context` with duration ≥ 60s |
| `commands.py` | `notify_callback` invalid mode |
| `config/loader.py` | All `PluginsConfig` fields |
| `gateway/gateway.py` | `_make_truncation` unknown strategy |
| `middleware.py` | `CallbackQuery` whitelist enforcement |
| `middleware.py` | Non-Message/CallbackQuery pass-through |
| `claude_session.py` | `plugins` parameter in options |
| `claude_session.py` | `model` property |
| `history_manager.py` | Response with no prior question |
| `plugin_loader.py` | Corrupt JSON in registry/settings |
| `bot.py` | All 15 command registrations |
| `bot.py` | Callback handler registrations |
| `truncation.py` | Empty string input |

### Missing Integration Tests

| What is Missing | Suggested Location |
|----------------|--------------------|
| Full `/skills` command with real plugin skills | `tests/chat/test_commands.py` |
| Gateway startup with `models.default` from config | `tests/gateway/test_gateway.py` |
| Gateway startup with `plugins.enabled = false` | `tests/gateway/test_gateway.py` |
| `WhitelistMiddleware` filtering a `CallbackQuery` tap | `tests/chat/test_middleware.py` |
| `SessionManager` model inheritance to new sessions | `tests/ai/test_session_manager.py` |

### Missing E2E Tests

| Scenario | Suggested Location |
|----------|--------------------|
| `/model <name>` command clears session, new session uses new model | `tests/gateway/test_full_flow.py` |
| Full `/context` command flow: send message → receive stats → /context | `tests/gateway/test_full_flow.py` |
| Plugin-loaded skill appears in `/skills` list in a full dispatch flow | `tests/gateway/test_full_flow.py` |

---

## Priority Matrix

### 🔴 Critical (must fix — real feature bugs can hide here)

1. `WhitelistMiddleware` — `CallbackQuery` never tested; inline keyboard taps bypass whitelist check in tests
2. `config/loader.py` — `PluginsConfig` loading completely untested
3. `session_manager.py` — `get_model()` / `set_model()` zero tests

### 🟠 High (significant blind spots in tested logic)

4. `session_manager.py` — Default factory path (skill_loader + plugin_loader integration)
5. `commands.py` — `skills_command` with plugin_loader (plugin skill section untested)
6. `bot.py` — 12 commands not verified in dispatcher registration
7. `bot.py` — `notify_callback`, `model_callback` not verified in dispatcher
8. `gateway/gateway.py` — `_make_truncation` unknown strategy (ConfigError path)
9. `gateway/gateway.py` — `_run()` with `cfg.models.default` set
10. `plugin_loader.py` — Corrupt JSON in registry/settings files
11. `history_manager.py` — Response with no prior `record_user_message` for that user
12. `claude_session.py` — `plugins` parameter not verified in options

### 🟡 Medium (edge cases that matter at runtime)

13. `middleware.py` — Non-Message/CallbackQuery pass-through
14. `commands.py` — `_fmt_context` duration ≥ 60s
15. `commands.py` — `notify_callback` with invalid mode data
16. `plugin_loader.py` — Corrupt/missing `plugin.json` manifest
17. `plugin_loader.py` — Unrecognized `installed_plugins.json` format
18. `gateway/gateway.py` — `_run()` with plugins disabled
19. `truncation.py` — Empty string input
20. `handler.py` — `message.bot is None` assertion path

### 🟢 Low (nice-to-have, unlikely to cause bugs)

21. `claude_session.py` — `model` property read-back
22. `claude_session.py` — `stop()` RuntimeError swallowed path
23. `history_manager.py` — Multi-user interleaving in same file
24. `bot.py` — `BOT_COMMANDS` count completeness
25. `md_formatter.py` — h4+ headings pass-through boundary
26. `gateway/gateway.py` — `Gateway.start()` entry point
27. `skill_loader.py` — Non-directory entries at `skills_dir` root
28. `main.py` — `main()` importable + callable smoke test

---

## Recommended Test File Additions

```
tests/
├── ai/
│   ├── test_session_manager.py          # Add: get_model/set_model, default factory
│   ├── test_claude_session.py           # Add: plugins param, model property, RuntimeError stop
│   ├── test_history_manager.py          # Add: response-no-prior-question, multi-user
│   └── test_plugin_loader.py            # Add: corrupt JSON, bad manifest, unknown format
├── chat/
│   ├── test_middleware.py               # Add: CallbackQuery, non-Message pass-through
│   ├── test_commands.py                 # Add: skills+plugins, fmt_context minutes, notify_callback invalid
│   └── test_bot.py                      # Add: all 15 commands, callback registrations
├── config/
│   └── test_loader.py                   # Add: PluginsConfig loading (all 3 fields)
└── gateway/
    └── test_gateway.py                  # Add: _make_truncation unknown, _run with models.default, plugins disabled
```
