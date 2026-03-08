# Devil's Advocate Review — Chat & Telegram Layer
**Reviewer**: DA-3
**Date**: 2026-03-08
**Files reviewed**:
- `archon/chat/bot.py`
- `archon/chat/commands.py`
- `archon/chat/handler.py`
- `archon/chat/middleware.py`
- `archon/chat/telegram_delivery.py`
- `archon/chat/voice.py`
- `archon/chat/md_formatter.py`
- `archon/chat/__init__.py`

Cross-reference reads: `archon/gateway/gateway.py`, `CLAUDE.md`, test files under `tests/chat/`

---

## Executive Summary

The middleware is correctly structured and the whitelist logic is sound for the two registered event types. However, there are **35+ `message.answer()` calls in `commands.py` that omit `parse_mode="HTML"`** while sending HTML-tagged strings — any HTML entity in user-controlled data (skill names, model names, cron job output) will be rendered as literal tags or silently corrupt the message. The voice module contains a hard `assert` against `message.bot is None` that will crash the coroutine with an `AssertionError` (not a user-visible error) if the bot reference is somehow absent. There is a meaningful **reminder tracking asymmetry** between `handler.py` (no reminder tracking at all) and `voice.py` (tracks reminders), meaning sessions driven purely through text never have their reminder thresholds updated. The entire module has **zero rate-limit / FloodWait handling**: when Telegram throttles the bot during a multi-tool verbose run, every `message.answer()` silently swallows the `RetryAfter` exception and moves on, causing permanent message loss with no backoff. These issues make the layer unsafe to deploy without fixes to the parse_mode gaps and the missing FloodWait handling.

---

## Critical Findings (Severity: CRITICAL)

### [commands.py:122,131,178,274,315,396,406,416,428,447,467,488,501,569,574,576,586,592,647,692,705,713,773,778,805] HTML injected without parse_mode on 35+ answer() calls

**Description**: The bot is created with `DefaultBotProperties(parse_mode=ParseMode.HTML)` in `bot.py:84`. However, aiogram 3.x only applies the default parse mode to the `Bot.send_message()` family of methods — it does **not** cascade to `Message.answer()` unless `parse_mode` is passed explicitly or the `default` is picked up from the bot instance stored in `Message.bot`. In practice, most aiogram 3.x builds do propagate the default through `Message.answer()`, but relying on this implicit behavior creates two distinct failure modes:

1. **HTML injection when bot default is not propagated**: Any `message.answer(f"🤖 Model set to <code>{arg}</code>...")` call where the bot default does not apply will send the raw `<code>` tags as plaintext. This is a correctness bug.
2. **Unescaped user data in HTML mode**: `commands.py:467` (`/skills`) sends `skill.name` and `skill.description` directly into HTML context **without `html.escape()`**. If a skill name or description contains `<`, `>`, or `&`, Telegram will either reject the message with `TelegramBadRequest: can't parse entities` or silently interpret the character as HTML. This is an **XSS-class injection into the Telegram HTML renderer**.

**Impact**: Telegram rejects the message entirely, or arbitrary HTML tags from untrusted skill/plugin file content are rendered in the user's chat.

**Evidence**:
```python
# commands.py:455 — skill.name and skill.description not escaped
lines.append(f"• <b>{skill.name}</b>\n  {skill.description}")
# commands.py:463 — plugin.key and plugin.version not escaped
lines.append(f"<i>[{plugin.key} v{plugin.version}]</i>")
# commands.py:467 — answer() has no parse_mode argument
await message.answer("\n".join(lines))
```

Compare with the **correctly** escaped `/agents` output at line 667:
```python
f"• <b>{html.escape(agent.name)}</b>{model_str}\n  {html.escape(agent.description)}{tools_str}"
```

The `/skills` output is inconsistent with `/agents` — the same escaping discipline is missing.

**Fix**: Escape `skill.name`, `skill.description`, `plugin.key`, `plugin.version` with `html.escape()`. Add `parse_mode="HTML"` to every `message.answer()` call that sends HTML-tagged content (or set it globally once on the dispatcher's default and remove the inconsistency).

---

### [commands.py:274] `/context` sends HTML without parse_mode

**Description**: `_fmt_context()` (lines 197–250) constructs a string containing `<b>`, `<code>`, `<pre>`, and `<i>` HTML tags. The result is passed to `await message.answer(_fmt_context(stats, notifications))` at line 274 **without `parse_mode="HTML"`**. If the bot's default parse mode is not inherited, Telegram renders the raw tags as literal text to the user.

**Impact**: The context window display is completely garbled — all formatting tags appear as plaintext.

**Evidence**:
```python
# commands.py:274
await message.answer(_fmt_context(stats, notifications))
# _fmt_context returns: "📊 <b>Context Window</b>\n\n<code>[████░░░░░░░...]</code>..."
```

**Fix**: Add `parse_mode="HTML"` to this call.

---

### [commands.py:569,574,576,586,592] Model commands send HTML without parse_mode

**Description**: Lines 569, 574, 576, 586, and 592 all emit `<code>` or `<i>` tags inside `message.answer()` calls without an explicit `parse_mode="HTML"`. Only line 757 (`jobs_command`) explicitly passes `parse_mode="HTML"`, revealing that there was no systematic approach.

**Evidence**:
```python
# commands.py:574 — no parse_mode
await message.answer(f"🤖 Current model: <code>{current}</code>")
# commands.py:586 — no parse_mode
await message.answer("🤖 Model reset to <i>default (SDK)</i>. Session cleared.")
```

**Fix**: Pass `parse_mode="HTML"` on all answer calls that contain HTML tags.

---

## High Severity Findings

### [handler.py:337, voice.py:128, voice.py:168] `assert` used for runtime guard — raises AssertionError in production

**Description**: Three locations use `assert message.bot is not None` as a runtime guard. In Python, `assert` statements are stripped when running with the `-O` (optimize) flag (`python -O main.py`). Even without `-O`, when this assertion fires the coroutine raises `AssertionError` — an exception that is **not** a `TelegramAPIError`, so the general `except Exception` catch blocks will catch it and deliver `❌ Error: ` to the user with the internal message, leaking implementation details.

**Impact**: Under optimization builds or edge cases (e.g., polling update with no bot reference set), the typing indicator logic crashes with a non-descriptive internal error instead of being silently skipped.

**Evidence**:
```python
# handler.py:337
assert message.bot is not None
# voice.py:128
assert message.bot is not None
# voice.py:168
assert message.bot is not None
```

**Fix**: Replace with `if message.bot is None: return` (or `if message.bot is None: logger.warning(...); return`) — a proper guard, not an assertion.

---

### [handler.py, voice.py] No FloodWait / RetryAfter handling — permanent message loss under throttling

**Description**: Telegram imposes rate limits on bots. When the limit is exceeded, the API returns HTTP 429 with a `Retry-After` header; aiogram raises `TelegramRetryAfter` (a subclass of `TelegramAPIError`). All `message.answer()` calls in `handler.py:467–475` and `voice.py:234–237` catch the exception and log a warning, then **continue without retrying**. Under a verbose or debug run with many tool events, this means dozens of messages can be silently dropped with no indication to the user.

**Impact**: In verbose/debug mode during a multi-tool session, users may receive only 20–30% of the tool event stream. No retry means no recovery.

**Evidence**:
```python
# handler.py:469–475
except Exception as exc:
    # Telegram network flap — log and continue; don't abort Claude's work.
    logger.warning(
        "Failed to deliver event reply to user %d (%s) — continuing",
        user_id,
        type(exc).__name__,
    )
```

**Fix**: Catch `TelegramRetryAfter` specifically, `await asyncio.sleep(exc.retry_after)`, and retry the send once. A non-retryable error should still swallow and continue.

---

### [commands.py:142] `os.execv()` called without cleanup of asyncio tasks — potential resource leak on restart

**Description**: `restart_command` calls `os.execv()` to replace the process after `await session_manager.stop_all()`. However, `stop_all()` only stops sessions — it does not cancel or join any `asyncio.Task` objects that may be running (e.g., `plan-executor-*` tasks, beacon tasks, background agent tasks). `os.execv()` replaces the process image without running `finally` blocks of tasks in flight.

**Impact**: Any task holding an open file handle, temp directory, or external subprocess (TTS synthesis, Whisper CLI) will be killed without cleanup. Temp directories (from `tempfile.TemporaryDirectory`) will be leaked at OS level until the next restart.

**Evidence**:
```python
# commands.py:139–142
await session_manager.stop_all()
os.environ["ARCHON_RESTART_NOTIFY_CHAT_ID"] = str(chat_id)
logger.info("/restart: replacing process")
os.execv(sys.executable, [sys.executable] + sys.argv)
```

**Fix**: Cancel all running asyncio tasks (especially `plan-executor-*` and background agent tasks) before calling `os.execv()`. The gateway's `stop_all()` should be the canonical shutdown path.

---

### [voice.py:247–252] Reminder tracking is absent from `handler.py` — asymmetry breaks reminder injection

**Description**: `voice.py:247–252` updates `session.reminder` after each voice message:
```python
if session.reminder is not None:
    session.reminder.record_message()
    if session.usage_stats is not None:
        session.reminder.record_tokens(...)
```
`handler.py` — the text message path — contains **zero reminder tracking**. The `ReminderInjectedEvent` is handled correctly in `format_event()`, but the session's reminder counter is never incremented for text messages. This means:

- The reminder's `interval_messages` threshold is never reached via the text path.
- The reminder's `interval_tokens` threshold is only updated via voice messages.
- In a mixed voice+text session, the reminder fires at unpredictable intervals (only counting voice turns).

**Impact**: Reminder injection silently never fires for pure text sessions, defeating the feature entirely.

**Fix**: Add the same reminder tracking block to `handle_message()` in `handler.py` after the `async for event in session.send(...)` loop completes.

---

### [middleware.py:27–31] Non-Message/Non-CallbackQuery TelegramObjects pass through unconditionally — unintentional whitelist bypass

**Description**: The middleware only checks `from_user` for `Message` and `CallbackQuery` types. All other `TelegramObject` subtypes (e.g., `InlineQuery`, `ChosenInlineResult`, `ShippingQuery`, `PreCheckoutQuery`, `Poll`, `PollAnswer`) pass through without whitelist enforcement.

For Archon's current scope (private chats only), this is low risk today. However, if any of these update types are ever registered as handlers (e.g., inline queries for skill selection), they would bypass the whitelist entirely without any code change.

**Impact**: Future handler registrations for non-Message/CallbackQuery update types will bypass the whitelist silently.

**Evidence**:
```python
# middleware.py:27–31
if isinstance(event, (Message, CallbackQuery)):
    user_id = event.from_user.id if event.from_user else None
    if user_id not in self._allowed:
        ...
        return None
return await handler(event, data)  # all other types pass through
```

**Fix**: This is the documented design (CLAUDE.md specifies only Message and CallbackQuery). Add a comment to explicitly document that all other update types are intentionally allowed. If the bot is ever expanded, this needs to be revisited.

---

## Medium Severity Findings

### [commands.py:296–303] `notify` command integer parsing — negative beacon intervals accepted silently

**Description**: The `/notify quiet N` and `/quiet N` command parsers do not validate that the interval is non-negative. `int("−5")` succeeds and sets `notifications.interval_minutes = -5`. The beacon task in `handler.py:362–368` checks `> 0`, so a negative value silently disables the beacon — but the user has been told "Quiet mode — beacon every -5 min" (line 309), which is a false confirmation.

**Evidence**:
```python
# commands.py:302–305
try:
    notifications.interval_minutes = int(parts[2])
except ValueError:
    pass  # invalid number — keep current interval
```

**Fix**: Add `if int(parts[2]) >= 0:` guard before assignment.

---

### [commands.py:581–592] `/model` accepts arbitrary unchecked model name — no validation against `models_config.available`

**Description**: When the user runs `/model some-arbitrary-string`, `session_manager.set_model("some-arbitrary-string")` is called unconditionally without checking against `models_config.available`. The model name is persisted and used in subsequent SDK calls, which will fail with an API error at runtime rather than at command time.

**Evidence**:
```python
# commands.py:587–592
else:
    session_manager.set_model(arg)
    if session_manager.has_session(user_id):
        await session_manager.stop(user_id)
    logger.info("/model → %s for user %d", arg, user_id)
    await message.answer(f"🤖 Model set to <code>{arg}</code>. Session cleared.")
```

**Fix**: When `models_config.available` is non-empty, validate that `arg` is in the list before accepting it. Emit a helpful error if not.

---

### [handler.py:426] `assert message.bot is not None` inside PlanEvent handling — crash path in production

**Description**: Line 426 contains a third `assert message.bot is not None` inside the `PlanEvent` branch, guarding `PlanExecutor` instantiation. In addition to the `AssertionError` risk already noted, this particular assert is inside the event loop, so if it fires, the entire `async for` loop breaks and Claude's response stream is abandoned mid-flight with no completion message delivered to the user.

**Evidence**:
```python
# handler.py:425–427
if isinstance(event, PlanEvent) and background_agent_manager is not None:
    assert message.bot is not None
    executor = PlanExecutor(...)
```

**Fix**: Replace with `if message.bot is None: logger.error(...); continue`.

---

### [telegram_delivery.py:40] Binary search fallback emits only one character — not a graceful degradation

**Description**: When `render_split_messages` cannot find a valid budget (line 40), it falls back to:
```python
return [f"{prefix}{renderer(text[:1])}"]
```
This sends a single character of content to the user. While the comment says "this shouldn't happen in practice," it is the silent discard of all but one byte of a potentially important response. The user sees a one-character message with no indication of truncation.

**Fix**: Log an error and return a message indicating that the content was too large to display, rather than silently emitting a single character.

---

### [voice.py:131–134] `TemporaryDirectory` exits while STT transcription still holds reference to path

**Description**: The `with tempfile.TemporaryDirectory() as tmpdir:` block exits (deleting the directory) at line 137 — **after** `transcribe_with_timeout()` returns. This is correct for the happy path. However, `STTHandler.transcribe_with_timeout()` invokes a Whisper CLI subprocess. If the subprocess is killed mid-execution by `asyncio.TimeoutError`, the `TemporaryDirectory` context manager still exits normally, but the now-deleted path may be referenced by the still-running (zombie) Whisper process. On macOS, this causes a harmless ENOENT, but it is a subtle resource management issue.

**Impact**: Low in practice, but the temp directory scope should ideally outlive the subprocess.

**Evidence**:
```python
# voice.py:130–137
with tempfile.TemporaryDirectory() as tmpdir:
    audio_path = Path(tmpdir) / f"audio_{file_id}{ext}"
    ...
    await message.bot.download_file(file_info.file_path, audio_path)
    text = await self.stt.transcribe_with_timeout(audio_path, timeout_sec=timeout_sec)
# tmpdir deleted here — audio_path no longer exists
```

---

### [commands.py:142] `os.execv()` does not flush the Telegram bot's outgoing queue

**Description**: Aiogram buffers outgoing updates internally. When `os.execv()` replaces the process image, in-flight bot API calls (including the "♻️ Restarting..." confirmation at line 138) may not have been sent yet. The user may not receive the restart confirmation before the process is replaced.

**Fix**: Add a small `await asyncio.sleep(0.5)` after `await message.answer("♻️ Restarting...")` to allow the event loop to flush. Or use `await bot.session.close()` explicitly before `execv`.

---

### [md_formatter.py:58–64] `block_html` / `inline_html` — potential double-escaping or missed raw HTML pass-through

**Description**: `block_html()` escapes the raw HTML block, which is the correct behavior (prevent injected HTML from rendering in Telegram). However, if mistune ever emits a `block_html` token containing already-processed content (e.g., from a Markdown document that itself uses HTML comments), the resulting output will contain `&lt;!-- ... --&gt;` as visible text to the user rather than being silently stripped. This is minor but user-visible noise.

---

### [handler.py:373–377] Sub-agent event routing skips `history_manager.record_event()`

**Description**: When `event.source == "sub-agent"`, the code routes to `agent_logger` and `continue`s — skipping the `history_manager.record_event()` call at line 407. This means sub-agent events are logged to the per-agent log but **not** to the main session history. Whether this is intentional is unclear from the code, but there is no comment documenting the decision, and the voice path (which does the same at line 180–183) also has no comment.

**Fix**: Add an explicit comment: "Sub-agent events are intentionally excluded from session history — see AgentLogger." or add sub-agent events to session history if that is the desired behavior.

---

## Low Severity / Style Issues

### [bot.py:75] `message.from_user` is `None`-guarded with fallback "unknown" — inconsistent with commands.py default of 0

**Description**: `start_command` uses `"unknown"` as fallback for `user_id` (line 75), while all other handlers in `commands.py` use `0`. This is cosmetic but creates inconsistent log entries.

---

### [commands.py:185] `_CONTEXT_WINDOW_TOKENS = 200_000` is a hardcoded constant — will silently be wrong for future models

**Description**: The comment says "all current Claude models" but Claude 3.5 Haiku has a 200k context while new models may differ. This is a maintenance liability.

---

### [handler.py:48–69] `_BEACON_WORDS` tuple has 17 entries — `random.choice` is not seeded, always cryptographically random

**Description**: `random.choice(_BEACON_WORDS)` uses the standard `random` module which is seeded from OS entropy at startup. This is fine for a cosmetic beacon — but it is worth noting that if tests rely on deterministic beacon words, they will be flaky unless mocked.

---

### [commands.py:296] `notify` command arg parsing uses `maxsplit=2` — `/notify quiet 5 extra` silently ignores "extra"

**Description**: `parts = (message.text or "").split(maxsplit=2)` means `/notify quiet 5 extra` parses as `["notify", "quiet", "5 extra"]`, and `int("5 extra")` raises `ValueError`, silently falling back to the current interval. The user receives no feedback that their input was malformed.

---

### [voice.py:83–98] `handle_voice_message` and `handle_audio_message` share no common base method for the "no attachment" guard

**Description**: Both methods begin with a guard clause checking for the attachment attribute and sending an error. This is duplicated logic that could be extracted into a private helper, improving testability and maintainability.

---

### [md_formatter.py:19–33] `_strikethrough_telegram_plugin` uses `parse_strikethrough` from `mistune.plugins.formatting` — private API

**Description**: `parse_strikethrough` is imported from `mistune.plugins.formatting`, which is a private/internal module in mistune. The public API is the plugin itself (`strikethrough`), not individual parse functions. This will break silently on a mistune minor version upgrade that renames internals.

---

### [commands.py:710–716] `reload_jobs()` called on every `/jobs` invocation — potential I/O on the event loop

**Description**: `cron_scheduler.reload_jobs()` is called synchronously in the async command handler. If `reload_jobs()` involves file I/O (reading TOML files from disk), this blocks the event loop while Telegram waits for a response. This is a mild performance issue for a low-frequency command but violates the asyncio rule of no blocking I/O in coroutines.

---

### [handler.py:435] `asyncio.create_task` for plan-executor has no reference kept — task may be garbage collected

**Description**:
```python
asyncio.create_task(
    executor.execute(event.plan),
    name=f"plan-executor-{user_id}",
)
```
The task is created and immediately discarded. In CPython, the event loop holds a strong reference to running tasks, so this is safe in practice. However, Python's asyncio documentation explicitly warns that the caller must hold a reference to prevent garbage collection in some environments. The same pattern exists in `voice.py:204`.

**Fix**: Store the task in a set and discard it on completion, per the asyncio docs pattern.

---

## Untested Code Paths

1. **`/skills` with HTML-containing skill name or description** — the HTML injection path is not tested; tests only use clean ASCII skill names.
2. **`notify_callback` with an invalid mode** (e.g., `callback.data = "notify:hacked"`) — the `if mode in _VALID_MODES` guard is correct but the path where mode is invalid (silently ignored, keyboard updated with unchanged mode) is not explicitly tested.
3. **`cancel_agent_callback` when `callback.message` is `None`** — `callback.message.edit_reply_markup()` dereferences `callback.message` with a `# type: ignore[union-attr]` comment; the `except Exception: pass` at line 831 masks this, but there is no test for it.
4. **`_fmt_context` sub-session breakdown** (verbose/debug mode with `sessions` key) — tested implicitly but the `sessions.get(name)` returning `None` path (`or {}`) is not explicitly exercised.
5. **`render_split_messages` fallback path** (line 40, the single-character fallback) — no test exercises an `absurdly small max_len`.
6. **`_partial_update_task` cancellation** — the mid-query mode switch (quiet → normal) that cancels the beacon task is tested at a high level, but the `contextlib.suppress(asyncio.CancelledError)` await path is not directly asserted.
7. **`restart_command` with `stop_all()` raising an exception** — if `stop_all()` raises, `os.execv()` is never called. No test covers this.
8. **`model_callback` with `name` that contains a colon** (e.g., `callback.data = "model:foo:bar"`) — `removeprefix("model:")` leaves `"foo:bar"` which is then used as a model name; no test validates this edge case.
9. **`voice.py:247–252` reminder tracking** — the happy path where `session.reminder is not None` and `session.usage_stats is not None` is never tested in `test_voice.py` (the `# ── Reminder tracking` section is empty).
10. **`_download_and_transcribe` with `file_info.file_path is None`** — the `assert file_info.file_path is not None` at line 132 will raise `AssertionError` rather than a user-visible error; no test covers this.

---

## Convention Violations

1. **`commands.py:467`** — `skill.name` and `skill.description` used in HTML context without `html.escape()`. The project's `/agents` command is correctly escaped; `/skills` is not. Inconsistency within the same file.
2. **`handler.py:337`, `voice.py:128`, `voice.py:168`, `voice.py:132`** — `assert` used for runtime guards. CLAUDE.md Key Constraints: "Always use KISS as the first principle" — `assert` is not a KISS guard for production code; it is a debugging tool.
3. **`voice.py:247–252`** — reminder tracking present only in the voice path, absent from the text handler path. This is a feature parity bug that violates the single-responsibility expectation that `handle_message` is the canonical text-message handler.
4. **`commands.py`** — 35+ `message.answer()` calls without explicit `parse_mode="HTML"` while sending HTML-tagged strings. Only `jobs_command` (line 757) passes it explicitly. This inconsistency suggests the feature was added in multiple passes without a systematic approach.
5. **`telegram_delivery.py:40`** — silent degradation to single character on overflow. Clean Code principle: failures should be explicit, not silent.
6. **`handler.py:373–376`** — sub-agent routing decision is undocumented at the point of implementation. CLAUDE.md requires SOLID; the Single Responsibility implies such routing decisions need to be explicitly documented.

---

## Overall Assessment

**Is this production-ready?**
No, not without targeted fixes. The layer is functional for the common case, but has three classes of bugs that will manifest under real usage: (1) silent HTML injection via unescaped skill/plugin data, (2) no rate-limit backoff causing permanent message loss under load, and (3) reminder tracking asymmetry silently disabling a feature for all text-message sessions.

**Top 3 things that MUST be fixed before production:**

1. **HTML injection in `/skills`** (`commands.py:455,463`): Escape `skill.name`, `skill.description`, `plugin.key`, `plugin.version` with `html.escape()` and add `parse_mode="HTML"` to the `message.answer()` call. This is the only path where user-controlled (filesystem-sourced) strings are interpolated into HTML without escaping.

2. **FloodWait / RetryAfter handling** (`handler.py:467–475`, `voice.py:234–237`): Catch `TelegramRetryAfter` specifically and sleep for `exc.retry_after` seconds before retrying once. Without this, verbose/debug mode during a long tool-heavy session silently drops most of the output.

3. **Reminder tracking missing from `handler.py`** (`voice.py:247–252` has it; `handler.py` does not): Add the same `session.reminder.record_message()` / `session.reminder.record_tokens()` block to `handle_message()`. The reminder feature is completely inoperative for the dominant text-message path.
