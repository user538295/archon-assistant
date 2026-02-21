# Archon Assistant — Development Tasks

## Context

Read these files before working on any task:
- `high_level_concept.md` — concept and architecture decisions
- `prd.md` — full product requirements
- `stories.md` — all user stories with acceptance criteria
- `CLAUDE.md` — dev commands, architecture overview, constraints

Implementation order: `S0.1 → S0.2 → S4.1 → S1.1 → S1.2 → S1.3 → S1.4 → S2.1 → S2.2 → S2.3 → S2.4 → S3.1 → S3.2 → S4.2`

---

## Tasks

### Epic 0: Project Setup

- [x] **S0.1** — Initialize project structure (`stories.md` § S0.1)
- [x] **S0.2** — Config loader (`stories.md` § S0.2, `prd.md` § 5)

### Epic 4 (partial): Daemon — Logging

- [ ] **S4.1** — Logging: rotating file handler, configurable path/level, `logging.getLogger("archon")` in all modules (`stories.md` § S4.1, `prd.md` § 6)

### Epic 1: AI Module

- [ ] **S1.1** — PTY session (raw): spawn claude in PTY, `send()` / `read_stream()` / `stop()` / `is_alive` (`stories.md` § S1.1, `prd.md` § 3.2)
- [ ] **S1.2** — Output parser: parse raw PTY stream into typed event dataclasses (`stories.md` § S1.2, `prd.md` § 3.3)
- [ ] **S1.3** — Truncation strategy: `TruncationStrategy` ABC + `SplitStrategy` MVP (`stories.md` § S1.3, `prd.md` § 3.3)
- [ ] **S1.4** — Session manager: per-user PTY registry, inactivity timeout, `stop_all()` (`stories.md` § S1.4, `prd.md` § 3.2)

### Epic 2: Chat Module

- [ ] **S2.1** — Telegram bot bootstrap: aiogram 3.x, `/start` command (`stories.md` § S2.1, `prd.md` § 3.1)
- [ ] **S2.2** — Whitelist middleware: drop non-whitelisted users before handlers (`stories.md` § S2.2, `prd.md` § 3.1)
- [ ] **S2.3** — Message handler + event formatter: forward messages to session, send formatted events back (`stories.md` § S2.3, `prd.md` § 3.3)
- [ ] **S2.4** — Bot commands: `/status` and `/stop` (`stories.md` § S2.4, `prd.md` § 3.1)

### Epic 3: Gateway

- [ ] **S3.1** — Gateway core: wire bot + session manager in single asyncio loop, `main.py` entry point (`stories.md` § S3.1, `prd.md` § 3.4)
- [ ] **S3.2** — Graceful shutdown: SIGTERM/SIGINT → `stop_all()` → bot disconnect within 5s (`stories.md` § S3.2, `prd.md` § 3.4)

### Epic 4: Daemon — Service Install

- [ ] **S4.2** — launchd service (macOS): `make install/uninstall/logs`, plist with `KeepAlive` (`stories.md` § S4.2, `prd.md` § 3.5)
- [ ] **S4.3** *(bonus)* — systemd service (Linux): unit file, `make install-linux/uninstall-linux` (`stories.md` § S4.3)
