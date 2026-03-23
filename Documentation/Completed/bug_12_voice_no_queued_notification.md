# Bug 12 — Voice messages give no "queued" notification when session is busy

Status: FIXED and VERIFIED

## Description

Found during DA review of Bug 04/05/06 fix.

`archon/chat/voice.py` handles voice/audio messages by transcribing and then calling `session.send()`. Unlike the text handler (`handler.py`), it does NOT check `session.is_processing` before calling `send()`. With the Bug 04/05/06 fix (lock in Pipeline.send()), voice messages will now queue silently behind the lock — the user sends a voice note while Claude is processing, and gets no feedback that their message is queued.

## Expected behavior
Voice messages should behave identically to text messages:
- If session is processing → notify user "⏳ Previous request still processing — your message is queued"
- Always send "⏳ Processing..." (or "⏳ Working..." in quiet mode) acknowledgment before calling send()

## Root cause
The `VoiceMessageHandler` in `archon/chat/voice.py` routes the transcribed text through the existing text handler or calls session.send() directly. The `is_processing` check and "queued" notification were added only to `handler.py:handle_message()`.

## Tasks
1. Read `archon/chat/voice.py` — understand how voice messages are processed
2. Read `archon/chat/handler.py` — understand the is_processing check pattern
3. Apply the same pattern to voice.py: check is_processing, send "queued" notification if True
4. Write test(s)
5. Verify: `uv run pytest -x -q --ignore=tests/integration 2>&1 | tail -8`

## AI Notes

### Verified findings
- `voice.py` calls `session.send(text)` directly in `_process_and_respond` — does NOT route through `handle_message()`. Bug is real.
- Fix applied to `archon/chat/voice.py`: added `is_processing` check + queued notification + `⏳ Processing...` ack in `_process_and_respond`, immediately after `session = await self.session_manager.get_or_create(user_id)`.
- `_mock_session()` in `tests/chat/test_voice.py` was updated to set `is_processing = False` by default (previously MagicMock auto-attribute was truthy — would have caused false positives).
- Pre-existing mypy error in `voice.py` (`bot: Bot | None` passed to `PlanExecutor`) was present before this fix and left unchanged.
- 4 new tests added; all 2306 tests pass; coverage 94.64%.

## DA Review (2026-03-11)

**Verdict: FIXED and VERIFIED** -- the fix is correct and mirrors handler.py faithfully.

### What holds up

1. **is_processing check mirrors handler.py exactly.** voice.py lines 188-198 match handler.py lines 311-321: same message text, same try/except swallowing pattern with warning log. Verified line-by-line.

2. **Queued notification failure is swallowed correctly.** The `except Exception` block at line 193 logs a warning and continues -- identical to handler.py. A Telegram outage will not prevent the voice message from being processed.

3. **Ack sent AFTER is_processing check.** Correct order: (1) check is_processing and notify if True (line 188), (2) send ack (line 204). The ack always fires regardless of whether the queued notification was sent.

4. **Quiet mode respected for ack.** Lines 200-202: `"Working..."` in quiet mode, `"Processing..."` otherwise. Matches handler.py lines 323-324.

5. **Ack failure also swallowed.** Lines 203-210: the ack `message.answer()` is wrapped in its own try/except, matching handler.py lines 354-361. AI processing proceeds even if the ack fails.

6. **Rate-limit test adjustment (call_count 2->3) is correct.** The new ack adds one `answer()` call before the first event reply. The test at line 789 correctly triggers on call 3 (preview=1, ack=2, first event=3).

7. **`_mock_session()` fix is correct.** Setting `is_processing = False` explicitly (line 44) prevents MagicMock's auto-attribute from being truthy. Without this, every existing test would see a truthy `is_processing` and send the "queued" notification, which would break `test_voice_no_queued_notification_when_session_is_idle` and could cause false positives in the rate-limit test's call counting.

### 4 new tests reviewed

- `test_voice_sends_queued_notification_when_session_is_processing` (line 812): Sets `is_processing = True`, asserts "queued" in answer calls. Correct.
- `test_voice_no_queued_notification_when_session_is_idle` (line 828): Sets `is_processing = False`, asserts no "queued". Correct negative test.
- `test_voice_sends_processing_ack_before_session_send` (line 844): Uses a capturing mock to verify the ack appears in the call sequence. Correct ordering test.
- `test_voice_queued_notification_failure_does_not_abort` (line 881): Raises Exception on "queued" text, asserts final response still delivered. Correct resilience test.

### Minor observations (not blockers)

1. **`_mock_session_error()` does not set `is_processing = False`.** The `MagicMock()` default creates a truthy auto-attribute, so `test_handle_voice_session_error` (line 259) will hit the queued notification path before the error. This is harmless -- the test only asserts "Error" appears in calls, which it does. But the test is now silently also testing the queued-then-error path rather than a clean error path.

2. **No test for queued notification via audio path.** All 4 new tests use `handle_voice_message`. Since `handle_audio_message` calls the same `_process_and_respond`, this is covered implicitly, but there is no explicit audio-specific queued notification test. Acceptable given the shared code path.

3. **UX: user sees up to 3 messages before any response.** When session is busy, the user sees: (1) transcription preview, (2) "queued" notification, (3) "Processing..." ack. This is potentially noisy but matches the text handler behavior exactly, which is the stated goal.

4. **No quiet-mode-specific queued notification test.** The queued notification text is the same in all modes (not gated by quiet mode), which matches handler.py. No issue -- just noting the test does not explicitly verify quiet mode + queued interaction.

### Conclusion

The fix is a faithful port of the handler.py pattern. All 4 tests verify the correct behavior. No critical or major issues found.
