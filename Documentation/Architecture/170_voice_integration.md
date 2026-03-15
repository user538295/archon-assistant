**Purpose**: Documents the voice message integration in Archon — STT/TTS modules, VoiceMessageHandler, configuration schema, data flow, and operational requirements.
**Audience**: Backend engineers and operators adding or maintaining voice support
**Status**: Stable
**Last reviewed**: 2026-02-28
**Next review**: 2026-05-28

# Voice Integration

## Principles

1. **Voice is a thin adapter, not a new pipeline.** Incoming voice messages are transcribed to text and injected into the existing `Pipeline` flow unchanged. No separate Claude session, no special prompt, no new event types for the core AI path.
2. **STT and TTS are decoupled.** `STTHandler` (inbound) and `TTSHandler` (outbound) are independent modules. Enabling TTS does not require enabling STT and vice versa — though the primary use case combines both.
3. **Feature-flagged.** Voice support is disabled by default (`[voice] enabled = false`). When disabled, no Whisper binary, OpenAI key, or `httpx` dependency is required.
4. **Graceful degradation.** STT transcription failure (timeout, Whisper not installed, empty result) sends an error message to the user but does not crash the daemon or affect text-only message handling.
5. **Temporary file hygiene.** All audio files (downloaded from Telegram, synthesized for reply) are written to `tempfile.TemporaryDirectory()` contexts and deleted automatically on exit — nothing is persisted to disk.

---

## Overview

When a user sends a Telegram voice note or audio file, Archon transcribes it to text using OpenAI Whisper (local CLI), processes the transcribed text through the normal Claude pipeline, and optionally synthesizes the response back into a voice note using OpenAI TTS or Edge TTS.

```
User (voice note) → Telegram → VoiceMessageHandler
    ├─ STTHandler.transcribe_with_timeout()   [Whisper CLI subprocess]
    ├─ text_handler(transcribed_text)          [existing Pipeline flow]
    └─ TTSHandler.synthesize() + answer_voice() [optional, mode-dependent]
```

---

## Components

### `archon/ai/stt.py` — `STTHandler`

Speech-to-text transcription using the local Whisper CLI.

| Interface | Description |
|---|---|
| `STTHandler(model, language)` | `model`: Whisper model size (default `"medium"`). `language`: ISO code (`"en"`, `"hu"`) or `None` for auto-detect |
| `async transcribe(audio_path: Path) -> str` | Runs `whisper <path> --model <model> --output_format txt --output_dir <dir>`; reads the `.txt` file Whisper creates; falls back to stdout if file missing; raises `CalledProcessError` on non-zero exit |
| `async transcribe_with_timeout(audio_path, timeout_sec) -> str` | Wraps `transcribe()` in `asyncio.wait_for(timeout=timeout_sec)` |

**Binary discovery**: Delegates to `get_runtime().find_binary("whisper")`, which checks in order:
1. `shutil.which("whisper")` — finds the binary if it is on `$PATH`
2. Platform-specific fallback paths:
   - **macOS**: `/opt/homebrew/bin/whisper`, `/usr/local/bin/whisper`
   - **Linux**: `~/.local/bin/whisper`, `/usr/local/bin/whisper`
3. If none found, falls back to bare `"whisper"` and logs a warning

**Supported audio formats**: `.mp3`, `.wav`, `.m4a`, `.ogg`, `.opus`, `.flac`, `.webm`

Telegram voice notes are delivered as `.ogg` (Opus codec), which is in the supported set. Unsupported formats log a warning but still attempt transcription.

**Whisper model sizes**:

| Model | Size | Speed | Accuracy |
|---|---|---|---|
| `tiny` | 39 MB | Fastest | Lowest |
| `base` | 140 MB | Fast | Moderate |
| `small` | 244 MB | Medium | Good |
| `medium` | 1.5 GB | Slow | Very good (recommended default) |
| `large` | 2.9 GB | Slowest | Best |

---

### `archon/ai/tts.py` — `TTSHandler` / `TTSConfig`

Text-to-speech synthesis with two provider backends.

#### `TTSConfig` dataclass

| Field | Type | Default | Description |
|---|---|---|---|
| `provider` | `"openai"` \| `"edge"` | `"openai"` | TTS backend |
| `model` | `str` | `"tts-1"` | OpenAI model (`"tts-1"` fast, `"tts-1-hd"` high quality) |
| `voice` | `str` | `"nova"` | OpenAI voice (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`) |
| `auto` | `"always"` \| `"inbound"` \| `"tagged"` \| `"off"` | `"inbound"` | When to synthesize responses. Note: `"tagged"` is declared in the `Literal` type but raises `NotImplementedError` at runtime |
| `max_text_length` | `int` | `3000` | Characters of response text to synthesize (truncated if longer) |
| `timeout_ms` | `int` | `30000` | API/subprocess timeout in milliseconds |
| `openai_api_key` | `str \| None` | `None` | Overrides `OPENAI_API_KEY` env var |
| `edge_voice` | `str` | `"en-US-MichelleNeural"` | Edge TTS voice |
| `edge_output_format` | `str` | `"audio-24khz-48kbitrate-mono-mp3"` | Edge TTS output format |
| `edge_rate` | `str` | `"+0%"` | Edge TTS speech rate adjustment |
| `edge_pitch` | `str` | `"+0Hz"` | Edge TTS pitch adjustment |

**Note**: `TTSConfig` (in `archon/ai/tts.py`) is the runtime dataclass with all fields including `timeout_ms`, `openai_api_key`, `edge_output_format`, `edge_rate`, and `edge_pitch`. `VoiceTTSConfig` (in `archon/config/loader.py`) is the config-loader dataclass with a subset of fields (`provider`, `model`, `voice`, `auto`, `max_text_length`, `edge_voice`). The gateway maps `VoiceTTSConfig` values into a `TTSConfig` instance at startup.

#### `TTSHandler` interface

| Interface | Description |
|---|---|
| `async synthesize(text, output_path) -> Path` | Generates audio at `output_path`; dispatches to `_openai_tts()` or `_edge_tts()` |
| `is_enabled() -> bool` | Returns `True` when `config.auto != "off"` |
| `should_synthesize(message_has_voice: bool) -> bool` | `"always"` → always `True`; `"inbound"` → `True` only when `message_has_voice=True`; `"off"` → always `False` |

**OpenAI TTS provider**: Uses `httpx.AsyncClient` to `POST https://api.openai.com/v1/audio/speech` with `response_format: "opus"`. Opus output renders as a round-bubble voice note in Telegram (not a file icon). Requires `httpx` package and `OPENAI_API_KEY`.

**Edge TTS provider**: Uses the `edge_tts` Python library (`edge_tts.Communicate`). Free, no API key needed. Output is MP3, which Telegram renders as an audio file icon (not a round bubble). Optional dependency — install with `uv add edge-tts`.

---

### `archon/chat/voice.py` — `VoiceMessageHandler`

Orchestrates the full voice message lifecycle.

| Interface | Description |
|---|---|
| `VoiceMessageHandler(session_manager, stt_config=None, tts_config=None, truncation=None, max_len=4000, notifications=None, cwd="", history_manager=None, agent_logger=None, background_agent_manager=None)` | `stt_config`: optional dict with `model` and `language` keys. `tts_config`: `TTSConfig` instance or `None` (defaults to `TTSConfig(auto="off")`). Other params wire up truncation, notifications, history, and background agent support |
| `async handle_voice_message(message: Message) -> None` | Handles `message.voice`: downloads OGG, transcribes, shows `"🎤 …"` preview, processes through Claude pipeline, optionally replies with TTS |
| `async handle_audio_message(message: Message) -> None` | Handles `message.audio`: same flow, extension derived from MIME type |
| `async _send_tts_response(message: Message, text: str) -> None` | Internal method: synthesizes voice note via `TTSHandler` and sends it with `message.answer_voice()` |

**Dispatcher registration** (in `gateway.py` when `config.voice.enabled = true`):
```python
dp.message.register(vmh.handle_voice_message, F.voice)
dp.message.register(vmh.handle_audio_message, F.audio)
```

---

## Configuration

Voice support is configured under the `[voice]` section in `~/.archon/config.toml`.

### `config.toml` reference

```toml
[voice]
enabled = false             # Must be true to activate voice handlers

[voice.stt]
model = "medium"            # Whisper model: tiny, base, small, medium, large
language = null             # Optional language hint: "en", "hu", etc.

[voice.tts]
provider = "openai"         # "openai" (Opus/round bubble) or "edge" (MP3/file icon)
model = "tts-1"             # "tts-1" (fast) or "tts-1-hd" (high quality)
voice = "nova"              # OpenAI voices: alloy, echo, fable, onyx, nova, shimmer
auto = "inbound"            # "always" | "inbound" (voice→voice) | "off"
max_text_length = 3000      # Max characters to synthesize
edge_voice = null           # Edge TTS voice, e.g. "en-US-MichelleNeural"
```

### Config dataclasses (`archon/config/loader.py`)

| Class | Fields |
|---|---|
| `VoiceConfig` | `enabled: bool = False`, `stt: VoiceSTTConfig`, `tts: VoiceTTSConfig` |
| `VoiceSTTConfig` | `model: str = "medium"`, `language: str \| None = None` |
| `VoiceTTSConfig` | `provider: str = "openai"`, `model: str = "tts-1"`, `voice: str = "nova"`, `auto: str = "inbound"`, `max_text_length: int = 3000`, `edge_voice: str = "en-US-MichelleNeural"` |

`VoiceConfig` is a field on the top-level `Config` dataclass: `voice: VoiceConfig = field(default_factory=VoiceConfig)`.

---

## Data Flow

### Inbound (voice → text)

```
1. User sends voice note in Telegram app
2. aiogram Dispatcher receives Voice update
3. WhitelistMiddleware checks user ID (same as text messages)
4. VoiceMessageHandler.handle_voice_message()
5.   bot.get_file(voice.file_id) → file_info
6.   bot.download_file(file_info.file_path, audio_path)  [to temp dir]
7.   STTHandler.transcribe_with_timeout(audio_path, timeout_sec=60)
8.     → whisper <path> --model medium --output_format txt --output_dir <dir>  [subprocess]
9.     → reads .txt output file → returns transcribed_text
10.  message.answer("🎤 Transcribed: …")  [preview shown to user]
11.  message.text = transcribed_text
12.  text_handler(message)  [routes into normal Pipeline flow]
13.    → SessionManager → Pipeline → Classifier → Decomposer → events
14.    → handle_message() formats events → Telegram messages
```

### Outbound (text → voice reply, TTS `"inbound"` mode)

```
15.  Claude Response event arrives in _process_and_respond()
16.  response_text captured; after event loop completes:
17.    should_synthesize(message_has_voice=True) → True
18.    VoiceMessageHandler._send_tts_response(message, response_text)
19.      TTSHandler.synthesize(response_text, output_path)  [to temp dir]
20.      message.answer_voice(voice=FSInputFile(output_path))
21.    [temp dir cleaned up automatically on context manager exit]
```

### Error paths

| Failure | Behavior |
|---|---|
| Whisper binary not found | Warning logged at init; `CalledProcessError` at transcription time → user sees `"❌ Error processing voice message: …"` |
| Transcription timeout | `asyncio.TimeoutError` caught → user sees `"❌ Voice transcription timed out"` |
| Empty transcription result | `VoiceMessageHandler` checks for empty string → user sees `"❌ Could not transcribe voice message"` |
| `OPENAI_API_KEY` missing | `ValueError` raised in `_openai_tts()` → caught in `_send_tts_response()`; logged, text response still delivered |
| TTS synthesis timeout / HTTP error | `RuntimeError` raised → caught in `_send_tts_response()`; logged, text response still delivered |

---

## Dependencies

### Required (always, even when `voice.enabled = false`)

None — voice modules are only imported when `config.voice.enabled = true` in `gateway.py`.

### Required when `voice.enabled = true`

| Dependency | Purpose | Install |
|---|---|---|
| **Whisper CLI** | STT transcription | `brew install openai-whisper` (macOS) or `pip install openai-whisper` (Linux) |
| `OPENAI_API_KEY` env var | OpenAI TTS API authentication | Set in `~/.archon/.env` or shell environment |

### Optional

| Dependency | Purpose | Install |
|---|---|---|
| `httpx` | OpenAI TTS API HTTP client | `pip install httpx>=0.24` (or `uv add httpx`) |
| `edge-tts` | Free Edge TTS alternative (Python library) | `uv add edge-tts` |

`httpx` is imported with a try/except guard in `tts.py`; if missing, attempting to use the `"openai"` provider raises `ImportError` with an installation hint.

---

## Operational Notes

### Whisper model download

Whisper downloads model weights on first use and caches them locally (`~/.cache/whisper/`). The `medium` model is ~1.4 GB. For CI/CD or restricted environments, pre-download the model:
```bash
whisper --model medium /dev/null 2>&1 | head -5
```

### OpenAI TTS cost

| Model | Cost per 1 000 characters |
|---|---|
| `tts-1` (fast) | ~$0.015 |
| `tts-1-hd` (HD) | ~$0.030 |

With `auto = "inbound"`, TTS only runs when the user sends voice messages — minimizing cost for text-only users sharing a bot instance.

### Telegram voice note format

Telegram renders Opus-encoded audio files as round-bubble voice notes. MP3 or other formats appear as file icons. The OpenAI TTS backend requests `response_format: "opus"` to ensure round-bubble rendering. Edge TTS produces MP3 by default.

### Logging

Voice handler operations log at `INFO` and `DEBUG` levels under the `archon` logger:
```
INFO  | Voice message from user 123456, duration: 8s, file_id: …
INFO  | Transcribed voice_<id>.ogg: 42 characters
INFO  | OpenAI TTS generated audio: response.ogg (18432 bytes)
INFO  | Voice response sent successfully
```

Errors log at `ERROR` with full traceback (`exc_info=True`).

---

## Related Documents

- [110 Component Catalog](110_component_catalog_and_layer_breakdown.md) — `STTHandler`, `TTSHandler`, `VoiceMessageHandler` component entries
- [100 System Architecture Overview](100_system_architecture_overview.md) — voice message flow in the container diagram and data flow section
- [160 Operational Readiness](160_operational_readiness_monitoring_and_reliability.md) — logging, startup checks, graceful shutdown

---

## Related Decisions

- [ADR-01: Use Claude Agent SDK](../ADRs/01_use_claude_agent_sdk.md) — voice transcription feeds the same `ClaudeSession` as text messages; no special voice session type
