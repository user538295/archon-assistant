# Archon Assistant — Development Tasks

## Context

Read these files before working on any task:
- `high_level_concept.md` — concept and architecture decisions
- `prd.md` — full product requirements
- `stories.md` — all user stories with acceptance criteria
- `CLAUDE.md` — dev commands, architecture overview, constraints

Implementation order: `S0.1 → S0.2 → S5.7 → S4.1 → S1.1 → S1.2 → S1.3 → S5.1 → S5.5 → S1.4 → S2.1 → S2.2 → S2.3 → S2.4 → S5.2 → S3.1 → S5.3 → S3.2 → S5.4 → S4.2 → S5.6`

---

## Tasks

### Epic 0: Project Setup

- [x] **S0.1** — Initialize project structure (`stories.md` § S0.1)
- [x] **S0.2** — Config loader (`stories.md` § S0.2, `prd.md` § 5)
- [x] **S5.7** — Live unit test: config loader — real tmp files, no mocks, `@pytest.mark.live` (`stories.md` § S5.7)

### Epic 4 (partial): Daemon — Logging

- [x] **S4.1** — Logging: rotating file handler, configurable path/level, `logging.getLogger("archon")` in all modules (`stories.md` § S4.1, `prd.md` § 6)

### Epic 1: AI Module

- [x] **S1.1** — Claude session (SDK): `ClaudeSession` wrapping `ClaudeSDKClient`, `start()` / `send()` / `stop()` / `is_alive` (`stories.md` § S1.1, `prd.md` § 3.2)
- [x] **S1.2** — Event mapper: translate SDK messages to archon event dataclasses (`stories.md` § S1.2, `prd.md` § 3.3)
- [x] **S1.3** — Truncation strategy: `TruncationStrategy` ABC + `SplitStrategy` MVP (`stories.md` § S1.3, `prd.md` § 3.3)
- [x] **S5.1** — AI pipeline integration: `FakeClaudeClient` (SDK message stream) → `EventMapper` → all 6 event types + truncation, no internal mocks (`stories.md` § S5.1)
- [x] **S5.5** — Live Claude Agent SDK test (`@pytest.mark.live`): real `claude` binary + SDK, trivial prompt, verify `Response` event within 30s (`stories.md` § S5.5)
- [ ] **S1.4** — Session manager: per-user `ClaudeSession` registry, inactivity timeout, `stop_all()` (`stories.md` § S1.4, `prd.md` § 3.2)

### Epic 2: Chat Module

- [ ] **S2.1** — Telegram bot bootstrap: aiogram 3.x, `/start` command (`stories.md` § S2.1, `prd.md` § 3.1)
- [ ] **S2.2** — Whitelist middleware: drop non-whitelisted users before handlers (`stories.md` § S2.2, `prd.md` § 3.1)
- [ ] **S2.3** — Message handler + event formatter: `async for event in session.send(text):` → formatted Telegram messages (`stories.md` § S2.3, `prd.md` § 3.3)
- [ ] **S2.4** — Bot commands: `/status` and `/stop` (`stories.md` § S2.4, `prd.md` § 3.1)
- [ ] **S5.2** — Chat + AI integration: aiogram `Dispatcher` + `WhitelistMiddleware` + message handler + `SessionManager` + mock `ClaudeSession` (`stories.md` § S5.2)

### Epic 3: Gateway

- [ ] **S3.1** — Gateway core: wire bot + session manager in single asyncio loop, `main.py` entry point (`stories.md` § S3.1, `prd.md` § 3.4)
- [ ] **S5.3** — Full message flow e2e: gateway with stubbed bot + scripted SDK client, verify exact Telegram reply sequence and log output (`stories.md` § S5.3)
- [ ] **S3.2** — Graceful shutdown: SIGTERM/SIGINT → `stop_all()` → bot disconnect within 5s (`stories.md` § S3.2, `prd.md` § 3.4)
- [ ] **S5.4** — Graceful shutdown e2e: SIGINT → `stop_all()` → bot disconnect within 5s, verify log messages (`stories.md` § S5.4)

### Epic 4: Daemon — Service Install

- [ ] **S4.2** — launchd service (macOS): `make install/uninstall/logs`, plist with `KeepAlive` (`stories.md` § S4.2, `prd.md` § 3.5)
- [ ] **S4.3** *(bonus)* — systemd service (Linux): unit file, `make install-linux/uninstall-linux` (`stories.md` § S4.3)
- [ ] **S5.6** — Live full-stack e2e (`@pytest.mark.live @pytest.mark.requires_telegram`): real Gateway + real Telegram API + real Claude Agent SDK, verify `✅ Response:` delivered to `TELEGRAM_LIVE_CHAT_ID` within 60s (`stories.md` § S5.6)
