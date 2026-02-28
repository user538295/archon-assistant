**Purpose**: Documents every error handling pattern in Archon — startup failures, message processing errors, session faults, graceful shutdown, and Telegram network resilience.
**Audience**: Backend engineers debugging or extending Archon.
**Status**: Stable
**Last reviewed**: 2026-02-28
**Next review**: 2026-05-28

# Error Handling Strategy

## Principles

1. **Fail fast at startup.** Missing or invalid configuration raises `ConfigError` immediately, aborting the daemon before any user traffic is processed.
2. **Protect the user loop.** Errors during message processing are caught, reported to the user as `❌ Error: …`, and the handler exits cleanly — the bot stays alive.
3. **Telegram errors never abort AI work.** Network flaps when delivering event messages are logged as warnings and swallowed; Claude's processing continues uninterrupted.
4. **Background agents are isolated.** One agent's failure does not affect other running agents or the main session.
5. **Shutdown completes within 5 s.** `session_manager.stop_all()` is bounded by a `_SHUTDOWN_TIMEOUT = 5.0` second `asyncio.wait_for`; a timeout logs a warning and continues the shutdown sequence.

---

## Error categories

```mermaid
graph TD
    subgraph Startup["Startup errors (fatal)"]
        CE["ConfigError<br/>missing token, bad TOML,<br/>missing required key"]
        TU["Unknown truncation_strategy<br/>(also ConfigError)"]
    end

    subgraph MessageLoop["Message loop errors (non-fatal)"]
        ME["Session error<br/>during send()"]
        TE["Telegram delivery error<br/>per-event send failure"]
        QE["Queued notification error<br/>send_message flap"]
    end

    subgraph ClassificationErrors["Classification errors (graceful degradation)"]
        CF["Classifier crash/timeout<br/>defaults to task intent"]
        CJ["Malformed JSON response<br/>defaults to task intent"]
        CS_["Classifier stop() failure<br/>logged, Decomposer still stopped"]
    end

    subgraph AgentErrors["Background agent errors (isolated)"]
        AF["Agent Exception<br/>status=failed, _notify_failure()"]
        AC["asyncio.CancelledError<br/>status=cancelled, re-raised"]
        AB["Beacon send error<br/>logged, loop continues"]
    end

    subgraph Shutdown["Graceful shutdown"]
        ST["session_manager.stop_all()<br/>timeout=5.0 s"]
        TO["TimeoutError<br/>logged as WARNING, shutdown continues"]
    end

    CE -->|"propagates to asyncio.run()"| ProcessExit["Process exits"]
    TU -->|"propagates to asyncio.run()"| ProcessExit

    CF -->|"logger.error, default Classification"| DecomposerContinues["Decomposer continues"]
    CJ -->|"logger.warning, default Classification"| DecomposerContinues
    CS_ -->|"logger.error, Decomposer stopped"| CleanExit2["Shutdown continues"]

    ME -->|"logger.error + answer(❌)"| UserNotified["User notified"]
    TE -->|"logger.warning, continue"| AIWorkContinues["AI work continues"]

    AF -->|"_notify_failure()"| UserNotified
    AC --> CleanExit["Task ends cleanly"]
    AB --> BeaconLoopContinues["Beacon loop continues"]

    ST -->|"5 s exceeded"| TO
```

---

## Startup errors

### Configuration loading

`Gateway._run()` calls `load_config()` as its first operation, before `setup_logging()`. If `load_config()` raises, the exception propagates to `asyncio.run()`, which prints the traceback and exits the process.

**Conditions that raise `ConfigError`:**

| Condition | Error message |
|---|---|
| `TELEGRAM_BOT_TOKEN` absent from environment and `.env` | `"TELEGRAM_BOT_TOKEN is missing from environment or .env file"` |
| `config.toml` does not exist | `"Config file not found: {path}"` |
| `config.toml` is corrupt TOML and no `.bak` exists | `"config.toml is corrupt ({exc}) and no backup exists at {backup_path}"` |
| Required key `access.allowed_user_ids` or `session.working_directory` missing | `"Missing required config key: {key}"` |
| `allowed_user_ids` is an empty list | `"allowed_user_ids must not be empty"` |
| `inactivity_timeout_seconds ≤ 0` | `"inactivity_timeout_seconds must be > 0"` |
| `session.working_directory` does not exist on disk | `"working_directory does not exist: {path}"` |
| `max_message_length ≤ 0` | `"max_message_length must be > 0"` |

**Corruption auto-recovery:** when `tomllib.TOMLDecodeError` is raised and `config.toml.bak` exists, `load_config()` logs a warning, copies the backup back to `config.toml`, and retries parsing. The daemon starts normally. No `ConfigError` is raised in this path.

### Truncation strategy validation

`_make_truncation()` in `gateway.py` raises `ConfigError` if `output.truncation_strategy` is not `"split"`. This is called during dispatcher setup, before `dp.start_polling()`. The exception propagates identically to a config load failure.

### Non-fatal startup conditions

| Condition | Behaviour |
|---|---|
| `qmd.enabled = true` but `qmd` binary not in PATH | `logger.warning` — QMD disabled for session; daemon starts normally |
| QMD daemon fails to start (non-zero exit, timeout, or exception) | `logger.warning` — QMD disabled; `qmd_url = None` |
| `ARCHON_RESTART_NOTIFY_CHAT_ID` set but `send_message` fails | `logger.warning` with `exc_info=True`; startup continues |

---

## Message processing errors

### Session error during `send()`

The outer `try/except` in `handle_message` catches any exception raised while streaming events from Claude:

```python
try:
    async for event in session.send(message.text):
        ...
except Exception as exc:
    logger.error("Error processing message for user %d (%s)", user_id, type(exc).__name__)
    try:
        await message.answer(f"❌ Error: {html.escape(str(exc))}")
    except Exception:
        logger.warning(
            "Failed to send error notification to user %d",
            user_id,
            exc_info=True,
        )
```

The error is reported to the user as `❌ Error: {message}`. If that notification also fails (e.g. second Telegram flap), the failure is logged at `WARNING` with a full traceback and silently swallowed. The handler then exits normally.

### Per-event Telegram delivery errors

Each formatted event message is sent inside its own `try/except`:

```python
try:
    await message.answer(text)
except Exception as exc:
    logger.warning(
        "Failed to deliver event reply to user %d (%s) — continuing",
        user_id, type(exc).__name__,
    )
```

A single failed send does not interrupt the event loop. Claude's remaining events continue to be processed and sent.

### Ancillary notification errors

All secondary Telegram calls in `handle_message` follow the same swallow-and-warn pattern:

| Call | On error |
|---|---|
| Typing indicator (`send_chat_action`) | `logger.warning` — rate-limited internally, silently swallowed |
| "⏳ Working..." acknowledgement (quiet mode) | `logger.warning` — processing continues |
| "⏳ Previous request still processing" notification | `logger.warning` — processing continues |

---

## Session errors (`ClaudeSession`)

### Not-started guard

`ClaudeSession.send()` raises `RuntimeError("Session not started")` if called before `start()`. This propagates to `handle_message`'s outer `try/except` and is reported to the user as an `❌ Error`.

### Concurrency guard

`ClaudeSession` protects against concurrent `send()` calls with `_send_lock: asyncio.Lock`. A second `send()` call while the first is in flight **waits** for the lock rather than raising an error. This ensures messages are processed sequentially without losing the second request. The `is_processing` property reflects the lock state for the queued-message notification in `handle_message`.

### Disconnect errors

`ClaudeSession.stop()` catches only `RuntimeError` from `client.disconnect()`:

```python
try:
    await self._client.disconnect()
except RuntimeError as exc:
    logger.warning("Session disconnect skipped: %s", exc)
```

This handles the anyio cancel-scope edge case that arises when `stop()` is called from a different task during shutdown. Other exception types propagate normally.

### No automatic reconnect

`ClaudeSession` does not attempt to reconnect after a failed `send()`. If the SDK raises during streaming, the exception propagates to `handle_message`, which reports it to the user. The session object remains connected; the next message from the user creates a new `send()` call using the existing SDK connection.

---

## Classification errors (`Pipeline`)

The `Pipeline` routes each user message through a Classifier (Haiku) before the Decomposer. All classification failures apply graceful degradation — the Pipeline defaults to `Classification(intent="task", confidence=0.0)` and continues processing normally.

### Classifier crash or timeout

If the Classifier `ClaudeSession.send()` raises any exception (SDK error, timeout, process crash), the Pipeline catches it, logs at `ERROR` with `exc_info=True`, and proceeds with the default classification:

```python
try:
    async for event in self._classifier.send(prompt):
        if isinstance(event, Response):
            classifier_response = event.content
except Exception:
    logger.error("Classifier failed — defaulting to task intent", exc_info=True)
```

### Malformed classification JSON

`parse_classification()` handles all JSON parsing failures:

| Condition | Behaviour |
|---|---|
| Not valid JSON (`JSONDecodeError`, `TypeError`) | `logger.warning`, returns default |
| JSON is not a dict | `logger.warning`, returns default |
| `intent` not in `("chat", "task")` | `logger.warning`, returns default |
| `confidence` is `None` | `logger.warning`, returns default |
| Valid JSON with valid fields | `Classification(intent=..., confidence=...)` with confidence clamped to 0.0–1.0 |

In all failure cases, the default `Classification(intent="task", confidence=0.0)` is returned. The user never sees a classification error — the Decomposer receives the prompt and handles it as a task.

### Pipeline `stop()` resilience

`Pipeline.stop()` wraps the Classifier's `stop()` call in a `try/except` so that a Classifier disconnect failure does not prevent the Decomposer from being stopped:

```python
try:
    await self._classifier.stop()
except Exception:
    logger.error("Classifier stop failed", exc_info=True)
await self._decomposer.stop()
```

---

## Graceful shutdown

`_SHUTDOWN_TIMEOUT = 5.0` seconds (defined at module level in `gateway.py`).

The `Gateway._run()` `finally` block executes this sequence regardless of how `dp.start_polling()` exits:

```mermaid
sequenceDiagram
    participant G as Gateway._run() finally
    participant CS as CronScheduler
    participant BM as BackgroundAgentManager
    participant MS as ArchonMCPServer
    participant SM as SessionManager
    participant B as Bot

    G->>CS: await cron_scheduler.stop()
    G->>BM: await bg_manager.stop_all()
    Note over BM: Cancels all running agent tasks<br/>await asyncio.gather(*tasks, return_exceptions=True)
    G->>MS: await bg_mcp_server.stop()
    G->>SM: asyncio.wait_for(stop_all(), timeout=5.0)
    alt Completes within 5 s
        SM-->>G: done
    else Times out
        G->>G: logger.warning("Session cleanup timed out after 5s")
    end
    G->>B: await bot.session.close()
    G->>G: logger.info("Archon shutdown complete")
```

**Key behaviours:**
- `bg_manager.stop_all()` cancels every running agent task and calls `asyncio.gather(..., return_exceptions=True)` — individual agent errors during cancellation do not block shutdown.
- The 5 s SLO applies only to `session_manager.stop_all()`. If it times out, the warning is logged and `bot.session.close()` still executes.
- `cron_scheduler.stop()`, `bg_mcp_server.stop()`, and `bot.session.close()` are not time-bounded — they are expected to complete quickly.

---

## Background agent failure handling

Each background agent runs in `BackgroundAgentManager._run_agent()`. Three distinct exit paths exist:

### Success

```python
run.status = "completed"
run.result = result
await self._notify_success(run)
```

The full result is sent to the user. If the combined header + result exceeds 4000 characters, the header is sent first, then the result is split into labelled `[1/N]` chunks.

### Cancellation (`asyncio.CancelledError`)

```python
run.status = "cancelled"
self._release_name(run.name)
logger.info("Background agent %r cancelled (user=%d)", run.name, run.user_id)
# cancel beacon task, call session.stop()
raise  # CancelledError must be re-raised
```

`CancelledError` is always re-raised. No Telegram notification is sent for user-initiated cancellations.

### Failure (`Exception`)

```python
run.status = "failed"
run.error = str(exc)
self._release_name(run.name)
logger.exception("Background agent %r failed (user=%d)", run.name, run.user_id)
# cancel beacon task, call session.stop()
await self._notify_failure(run)
```

`_notify_failure()` sends `❌ Agent {name} failed\n{error[:400]}` to the user. The 400-character truncation prevents excessively long error messages from hitting Telegram's message limit.

All Telegram calls in `_notify_failure()` go through `_send_notification()`, which catches `Exception`, logs a `WARNING`, and swallows it — a Telegram flap during failure notification does not propagate.

### Beacon errors

The `_agent_beacon_task` loop swallows Telegram errors at each fire:

```python
try:
    await self._bot.send_message(chat_id, text, parse_mode="HTML")
except Exception as exc:
    logger.warning("Agent beacon update failed for %r (user=%d): %s", ...)
```

A single failed beacon send does not cancel the beacon loop. The agent continues running and the next beacon fires after the next interval.

---

## Telegram network errors (polling layer)

aiogram 3.x's `dp.start_polling(bot)` handles reconnection transparently. The polling loop catches network-level exceptions internally and retries with back-off. No custom reconnect logic is needed in Archon.

A clean shutdown signal (SIGINT, SIGTERM) causes `start_polling()` to exit its loop, which triggers the `finally` block in `Gateway._run()`.

---

## Error log levels summary

| Situation | Level | Location |
|---|---|---|
| Session error during message processing | `ERROR` | `handler.py` |
| Failed to notify user of error | `WARNING` | `handler.py` |
| Failed to deliver event reply | `WARNING` | `handler.py` |
| Typing indicator send failure | `WARNING` | `handler.py` |
| Classifier crash/timeout during classification | `ERROR` (with traceback via `exc_info=True`) | `pipeline.py` |
| Classification JSON parse failure | `WARNING` | `classification.py` |
| Classifier `stop()` failure | `ERROR` (with traceback via `exc_info=True`) | `pipeline.py` |
| Session disconnect RuntimeError | `WARNING` | `claude_session.py` |
| Background agent unhandled exception | `ERROR` (with traceback via `logger.exception`) | `background_agent_manager.py` |
| Background agent cancelled | `INFO` | `background_agent_manager.py` |
| Agent beacon send failure | `WARNING` | `background_agent_manager.py` |
| Notification send failure (spawn/success/failure) | `WARNING` | `background_agent_manager.py` |
| Session cleanup timeout at shutdown | `WARNING` | `gateway.py` |
| QMD daemon failure at startup | `WARNING` | `gateway.py` |
| config.toml corruption (backup present) | `WARNING` | `loader.py` |
| config.toml corruption (no backup) | `ConfigError` → fatal | `loader.py` |
| Restart notification failure | `WARNING` | `gateway.py` |

---

## Related documents

- [130 — Data Architecture and Persistence](130_data_architecture_and_persistence.md) — `ConfigError` conditions tied to config file format and backup mechanism
- [120 — Services and Integration Architecture](120_services_and_integration_architecture.md) — session lifecycle and SDK interaction that feeds into session error paths
- [160 — Operational Readiness](160_operational_readiness_monitoring_and_reliability.md) — log-based alerting on `ERROR` and `WARNING` lines
