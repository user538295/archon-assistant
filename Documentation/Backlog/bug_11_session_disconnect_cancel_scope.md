# Bug 11 — Session disconnect fails with cancel scope error during eviction

Status: FIXED

## Description

From archon.log during session eviction:
```
2026-03-10 20:09:34,024 archon WARNING Session disconnect skipped: Attempted to exit cancel scope in a different task than it was entered in
2026-03-10 20:09:34,026 archon WARNING Session disconnect skipped: Attempted to exit cancel scope in a different task than it was entered in
2026-03-10 20:09:34,027 archon WARNING Session disconnect skipped: Attempted to exit cancel scope in a different task than it was entered in
2026-03-10 20:09:34,027 archon WARNING Session disconnect skipped: Attempted to exit cancel scope in a different task than it was entered in
```

This happened when the session was evicted due to inactivity (`Evicting inactive session for user 154643621`). The session had 4 SDK subprocesses (classifier, decomposer, orch_session, summary_session) and each one failed to disconnect with the cancel scope error.

## Root cause hypothesis

The Claude SDK client uses `anyio` cancel scopes internally. When `disconnect()` is called from the session eviction task, the cancel scope that was entered in the original task (where `connect()` was called) cannot be exited from a different task.

This is an anyio/asyncio task isolation issue: cancel scopes must be entered and exited from the same task.

## Impact

- Sessions are not properly cleaned up on eviction → potential resource leaks
- SDK subprocesses may not be terminated
- Repeated warnings in logs

## Tasks

1. Find where `disconnect()` is called during eviction (session_manager.py)
2. Understand the `ClaudeSession.disconnect()` and `ClaudeSDKClient.disconnect()` implementation
3. Determine how to properly disconnect from a different task (may need to signal the original task to disconnect, or use a different cleanup mechanism)
4. Fix: ensure session cleanup works correctly regardless of which task calls it
5. Write test for session eviction cleanup
6. Fix

## DA Review (2026-03-11)

### Verified

1. **`transport.close()` is called when `disconnect()` raises `RuntimeError`.** Confirmed at `claude_session.py` lines 375-385: `except RuntimeError` catches the cancel scope error, then `getattr(self._client, "_transport", None)` retrieves the transport, and `await transport.close()` is called. Verified against the SDK source: `SubprocessCLITransport.close()` (in `subprocess_cli.py` line 440) terminates the subprocess and cleans up stdin/stdout/stderr streams. This is the correct cleanup mechanism.

2. **Exception type check is appropriately scoped.** Only `RuntimeError` is caught (line 375), not a broad `Exception`. This is correct because the anyio cancel scope violation raises specifically `RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")`. Other errors from `disconnect()` (e.g., `OSError`, `ConnectionError`) would propagate normally, which is the right behavior -- those indicate genuine transport failures rather than a task-mismatch issue.

3. **`getattr(self._client, "_transport", None)` is safe.** Verified against `claude_agent_sdk/client.py` line 72: `_transport` is initialized to `None` in `ClaudeSDKClient.__init__()` and set to a `SubprocessCLITransport` instance in `connect()`. Since the `RuntimeError` occurs inside `Query.close()` (at `self._tg.cancel_scope.cancel()`, line 663 of `_internal/query.py`) BEFORE `disconnect()` reaches line 489 (`self._transport = None`), the `_transport` attribute still holds the active transport when the fallback code runs. The `getattr` with default `None` adds defense against future SDK refactors that might rename or remove the attribute.

4. **`_connected` is always set to `False`.** The `finally` block at line 386-387 ensures `self._connected = False` runs regardless of whether `disconnect()` succeeds, raises `RuntimeError`, or the transport fallback itself raises. This is correct.

5. **Edge cases are covered by tests:**
   - `test_stop_from_different_task_closes_transport` (line 2310): verifies `transport.close()` is called on `RuntimeError`. Uses a custom `_MockTransport` class with a real `async def close()` method, avoiding the `MagicMock` awaitable issue.
   - `test_stop_from_different_task_logs_warning` (line 2333): verifies the warning is logged.
   - `test_stop_transport_close_error_is_swallowed` (line 2353): verifies that if `transport.close()` itself raises (`OSError`), `stop()` still completes. Correct -- the `except Exception as transport_exc` at line 384 handles this.
   - `test_stop_without_transport_still_works` (line 2373): verifies behavior when `_transport` attribute doesn't exist (`del client._transport` on `MagicMock`). Since `getattr` with default `None` is used, `transport` is `None`, the `if transport is not None` guard (line 381) skips the close, and `stop()` completes cleanly.

6. **Root cause analysis is accurate.** Confirmed by reading the SDK source: `Query.close()` at `_internal/query.py` line 663 does `self._tg.cancel_scope.cancel()` which manipulates an anyio task group's cancel scope. When this is called from a different asyncio task (session eviction), anyio raises `RuntimeError`.

### Issues Found

1. **[MINOR] The bug status says "TODO" but the fix is already implemented.** Should be updated.

2. **[MINOR] Pre-existing tests at lines 884-906 now silently exercise the transport fallback path.** The `_make_mock_client()` helper returns a `MagicMock` where accessing `._transport` auto-creates a `MagicMock` child. When those tests set `disconnect` to raise `RuntimeError`, the Bug 11 fix now runs the transport fallback: `await MagicMock().close()` raises `TypeError` (MagicMock is not awaitable), which is caught by `except Exception` and logged at DEBUG. These tests still pass but now exercise an unintended error-within-error path rather than the clean "catch RuntimeError and move on" path they originally tested. Not a correctness issue, but a test hygiene concern. Consider updating `_make_mock_client` to explicitly set `client._transport = None` so those older tests exercise only the `disconnect-raises, transport-is-None` path.

3. **[MINOR] `disconnect()` sets `self._transport = None` (SDK client.py line 489), but this line is never reached.** When the cancel scope error occurs, `disconnect()` raises from `self._query.close()` at line 487. The `self._transport = None` on line 489 never executes. The Archon fix correctly reads `_transport` before it could be nullified. However, if a future SDK version moves the `_transport = None` assignment before `_query.close()`, the fallback would silently become a no-op (transport is `None`, guard skips close, subprocess leaks). This is a fragile coupling to SDK internals.

4. **[INFO] No integration-level test for session eviction.** The unit tests verify `ClaudeSession.stop()` behavior in isolation. The original bug occurred during session eviction via `SessionManager`, where `stop()` is called from a timer callback (different task context). No test verifies that the `SessionManager` eviction path handles the `RuntimeError` correctly end-to-end. The unit tests are sufficient for the `ClaudeSession` layer, but the system-level scenario is untested.

### Conclusion

The fix is correct and handles all identified edge cases. The approach of catching the specific `RuntimeError` from the cancel scope violation and falling back to direct `transport.close()` is the right pragmatic solution -- it terminates the subprocess without requiring the anyio cancel scope to be exited from the same task. The `getattr` pattern provides forward compatibility, and the `finally` block ensures `_connected` is always reset. Test coverage is thorough for the `ClaudeSession.stop()` layer. The only structural concern is the dependency on the SDK's internal `_transport` attribute, which could break on SDK upgrades without warning.

## AI Notes

### Fix applied (2026-03-11)

**Root cause confirmed**: `ClaudeSDKClient.disconnect()` internally calls `self._query.close()` which cancels an anyio `TaskGroup` cancel scope. When `disconnect()` is called from a different asyncio task (e.g., session eviction timer), anyio raises `RuntimeError("Attempted to exit cancel scope in a different task than it was entered in")`.

**Fix** in `archon/ai/claude_session.py` `stop()` method:
- Catch `RuntimeError` from `disconnect()` (existing behavior — already logged as warning)
- After catching, retrieve the transport via `getattr(self._client, "_transport", None)` and call `await transport.close()` directly
- `transport.close()` (`SubprocessCLITransport.close()`) terminates the subprocess and cleans up stdin/stdout/stderr
- If `transport.close()` itself raises, swallow via `except Exception` (logged at DEBUG)
- `_connected = False` set in `finally` block regardless of path

**2026-03-11 DA followup**: Updated `_make_mock_client()` in `tests/ai/test_claude_session.py` to explicitly set `client._transport = None` — prevents existing RuntimeError tests from silently exercising the transport-fallback path.

**Tests added** in `tests/ai/test_claude_session.py`:
- `test_stop_from_different_task_closes_transport`
- `test_stop_from_different_task_logs_warning`
- `test_stop_transport_close_error_is_swallowed`
- `test_stop_without_transport_still_works`

All tests pass.

