# Key & Secret Storage Plan for Archon
_Created: 2026-03-01_

---

## 1. Situation Assessment

### What exists today

| File | Location | Role |
|---|---|---|
| `~/.archon/.env` | Runtime | Holds `TELEGRAM_BOT_TOKEN` |
| `~/.archon/config.toml` | Runtime | All non-secret structured config |
| `examples/.env.example` | Repo | Template — currently only 2 lines |
| `archon/config/loader.py` | Source | `load_config()` — loads both files into typed dataclasses |

### How loading works today

1. `Gateway._run()` calls `load_config(env_file="~/.archon/.env", config_file="~/.archon/config.toml")`.
2. `load_config()` calls `load_dotenv(env_file)` (python-dotenv default: does **not** clobber already-set env vars — correct).
3. `os.environ.get("TELEGRAM_BOT_TOKEN")` is read; missing → `ConfigError` at startup.
4. The token is stored in `Config.telegram_bot_token`.

### Identified gaps

| ID | Gap | Severity |
|---|---|---|
| G1 | `OPENAI_API_KEY` is undocumented — not in `.env.example`, not wired through `loader.py`. Users enabling OpenAI TTS have no guided place to put it. | Medium |
| G2 | `ANTHROPIC_API_KEY` is never mentioned anywhere — the SDK picks it up from the ambient env automatically, but there is no hint for users. | Low |
| G3 | `gateway.py` builds `TTSConfig` without an `openai_api_key` argument; TTS relies 100% on whatever is in the ambient shell environment. | Medium |
| G4 | `TELEGRAM_LIVE_CHAT_ID` in `.env.example` is test-only but looks like a required secret to new users. | Low |

---

## 2. Design Principles

1. **Env var wins, `.env` is a local shortcut.** `load_dotenv()` already implements this correctly (no `override=True`). Never break this contract.
2. **Secrets only in `.env` / environment — never in `config.toml`.** The two-file split is correct and must be preserved.
3. **Central loading, once.** All secrets flow through `load_config()` into `Config`. No module calls `os.getenv("SOME_KEY")` independently for secrets that should be user-configurable.
4. **Fail loud at startup.** Required keys raise `ConfigError` immediately. Optional keys are `None` by default and checked at use-site with a clear error.
5. **No over-engineering.** We are a Python daemon with a small, fixed set of secrets. `python-dotenv + os.environ` is the right tool — no `pydantic-settings` needed.

---

## 3. Precedence Model

```
Runtime env vars  →  ~/.archon/.env  →  code defaults
   (highest)                                (lowest)
```

This is already implemented correctly via `load_dotenv()` without `override=True`. Nothing changes here.

---

## 4. Secret Inventory (complete)

| Variable | Required? | Consumer | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ Required | `archon/chat/` via `Config.telegram_bot_token` | Bot will not start without this |
| `ANTHROPIC_API_KEY` | ✅ Required | Claude Code SDK (auto-picked from env) | SDK reads it directly; Archon never touches it |
| `OPENAI_API_KEY` | Optional | `archon/ai/tts.py` `TTSHandler` | Only needed when `[voice] enabled = true` and `[voice.tts] provider = "openai"` |

---

## 5. Target Architecture (no structural change)

The existing two-file split (`config.toml` for structure, `.env` for secrets) is correct and stays. The only changes are:

1. **`loader.py`** — add `openai_api_key` to `Config` so it flows through the central loading path.
2. **`gateway.py`** — pass `cfg.openai_api_key` into `TTSConfig` instead of relying on the ambient environment at TTS init time.
3. **`examples/.env.example`** — document all three secrets with clear comments.
4. **`examples/config.toml.example`** — no changes needed (API keys do not belong there).

### Updated `Config` dataclass (conceptual)

```python
@dataclass
class Config:
    telegram_bot_token: str     # required — from TELEGRAM_BOT_TOKEN
    openai_api_key: str | None  # optional — from OPENAI_API_KEY; None if not set
    access: AccessConfig
    session: SessionConfig
    # ... rest unchanged
```

### Updated `load_config()` (conceptual)

```python
def load_config(...) -> Config:
    load_dotenv(Path(env_file).expanduser())   # .env file; env vars already set take precedence

    # Required secret — fail fast
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN is missing. Set it as an environment variable or in ~/.archon/.env"
        )

    # Optional secret — log presence, never log value
    openai_api_key = os.environ.get("OPENAI_API_KEY") or None
    if openai_api_key:
        logger.debug("OPENAI_API_KEY: set")
    else:
        logger.debug("OPENAI_API_KEY: not set (OpenAI TTS will be unavailable)")

    # ... rest of TOML loading unchanged ...

    return Config(
        telegram_bot_token=token,
        openai_api_key=openai_api_key,
        # ... rest unchanged
    )
```

### Updated `gateway.py` TTSConfig construction (conceptual)

```python
tts_cfg = TTSConfig(
    provider=cfg.voice.tts.provider,
    model=cfg.voice.tts.model,
    voice=cfg.voice.tts.voice,
    auto=cfg.voice.tts.auto,
    max_text_length=cfg.voice.tts.max_text_length,
    edge_voice=cfg.voice.tts.edge_voice,
    openai_api_key=cfg.openai_api_key,   # ← add this line
)
```

`TTSHandler.__init__` already has `openai_api_key = config.openai_api_key or os.getenv("OPENAI_API_KEY")`, so passing it explicitly through config is strictly additive — the env fallback remains as a safety net.

### Updated `examples/.env.example`

```dotenv
# Archon secrets — copy to ~/.archon/.env and fill in your values.
# Environment variables take precedence over this file if set in the shell.

# Required: your Telegram bot token (from @BotFather)
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Required: your Anthropic API key (used by the Claude Code SDK directly)
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Optional: OpenAI API key — only needed when [voice] is enabled and
# [voice.tts] provider = "openai"
# OPENAI_API_KEY=your_openai_api_key_here

# The following is only used by live integration tests, not by Archon itself:
# TELEGRAM_LIVE_CHAT_ID=your_telegram_user_id_here
```

---

## 6. Security Guardrails (existing + new)

| Rule | Status |
|---|---|
| `.env` never committed to git | ✅ Already enforced (`.gitignore`) |
| Secrets never appear in `config.toml` | ✅ Existing design |
| `load_dotenv()` without `override=True` | ✅ Current code is correct |
| Fail fast at startup for required secrets | ✅ `TELEGRAM_BOT_TOKEN` already does this; `ANTHROPIC_API_KEY` is handled by the SDK |
| Never log secret values | ✅ Current code never logs tokens. New code must follow same rule: log `bool(key)` not the value |
| No `os.getenv("SECRET")` scattered across modules | ⚠️ `tts.py` has a stray `os.getenv("OPENAI_API_KEY")` — fixed by routing through `Config` |

---

## 7. Concrete Refactoring Checklist

### File: `archon/config/loader.py`

- [ ] Add `openai_api_key: str | None = None` field to `Config` dataclass (optional, default `None`).
- [ ] In `load_config()`, after `load_dotenv(...)`, read `OPENAI_API_KEY` from `os.environ` and assign to a local variable.
- [ ] Log its presence at `DEBUG` level (never log its value).
- [ ] Pass it into the `Config(...)` constructor call at the bottom of `load_config()`.

### File: `archon/gateway/gateway.py`

- [ ] In the `TTSConfig(...)` construction block (around line 157), add `openai_api_key=cfg.openai_api_key`.

### File: `examples/.env.example`

- [ ] Replace current 2-line file with the annotated 4-secret version shown in Section 5 above.
- [ ] Keep `TELEGRAM_LIVE_CHAT_ID` commented out with a note that it is test-only.

### File: `archon/ai/tts.py`

- [ ] **No changes required.** `TTSHandler.__init__` already uses `config.openai_api_key or os.getenv("OPENAI_API_KEY")`. Once the config layer passes the key through, the `os.getenv` fallback becomes an invisible safety net, which is fine.

### Files: no changes needed

- `config.toml.example` — API keys do not belong there; no change.
- `archon/chat/` — does not deal with secrets directly; no change.
- `archon/ai/claude_session.py` — `ANTHROPIC_API_KEY` is consumed by the SDK internally; no change.

---

## 8. What We Are Deliberately NOT Doing

- **No `pydantic-settings`** — adds a new dependency for no real gain on a small, fixed secret set.
- **No secrets directory / file-per-secret** — overkill for a local daemon.
- **No vault / cloud secrets manager** — out of scope for a personal macOS/Linux daemon.
- **No layered `.env.shared` / `.env.local`** — single-environment daemon; unnecessary complexity.
- **No CLI flag for env-file path** — the hard-coded `~/.archon/.env` default is fine; the function signature already accepts an override if ever needed.

---

## 9. Summary

The existing architecture is fundamentally sound. The two required code changes are small and surgical:

1. Thread `OPENAI_API_KEY` through `Config` (3 lines in `loader.py`, 1 line in `gateway.py`).
2. Update `examples/.env.example` to document all three secrets.

Everything else — the precedence model, the two-file split, the fail-fast validation, the dotenv approach — is already correct and stays as-is.
