**Purpose**: Registers all known technical debt, pending feature gaps, and test coverage deficiencies, with prioritisation guidance for planning.
**Audience**: All developers contributing to Archon.
**Status**: Stable
**Last reviewed**: 2026-02-26
**Next review**: 2026-05-26

# Technical Debt and Refactoring Roadmap

## Principles

1. **Register debt before fixing it** — every known gap lives here so nothing is silently deferred or forgotten.
2. **Impact over age** — items are prioritised by the damage they cause today, not how long they have existed.
3. **Test debt is product debt** — an untested code path is an undiscovered bug; test gaps carry the same priority scale as feature gaps.
4. **Small items compound** — low-effort, low-priority items are scheduled in batches to prevent accumulation.
5. **Debt resolves through stories** — each item must have a corresponding story or task entry before work begins.

---

## Debt register

The table below lists every open item from `Documentation/tasks.md`. Items are drawn directly from that source file; no items are inferred or invented.

| ID | Category | Description | Impact | Effort | Priority |
|---|---|---|---|---|---|
| S16.1 | Distribution | Replace `install.sh` with a PEP 723 Python installer (`install.py`) supporting `--dry-run`, `--uninstall`, `--update`, `--non-interactive` flags and `pytest` unit tests | High — `install.sh` has a broken install path and is hard to test | M | 🔴 High |
| Installer-fix | Distribution | All installed files must land under `~/.archon/`; current `install.sh` puts some files in the wrong location | High — broken installations for new users | S | 🔴 High |
| FR.005 | AI | Context compaction: watch context window after each response, generate a summary before compaction, `/clear` the session, reload the summary, and resume — full TDD suite required | High — without compaction, long sessions silently lose context | L | 🔴 High |
| FR.011 | AI | Count compaction events in the session and expose the count in the `/context` command — TDD required | Medium — visibility gap for users debugging context loss | S | 🟠 Medium |
| FR.012 | Chat | When an agent starts, include a brief of its task in the spawn notification (e.g., "Agent Nova started: Summarise xyz.txt") | Medium — users cannot tell what a background agent is doing | XS | 🟠 Medium |
| FR.013 | Chat | In Normal notification mode, show a short thought brief (trim after two sentences or before the first `\n`), consistent with existing tool-result trimming | Medium — Normal mode is the default; users miss reasoning context | XS | 🟠 Medium |
| FR.009 | Config | Cron pipeline format mismatch: current TOML uses `[[pipeline]]` array-of-tables; spec requires `pipeline = [{"tool": "..."}, {"prompt": "..."}]` inline array — TDD required | Medium — cron job files written to spec will silently fail to parse | M | 🟠 Medium |
| FR.010 | Logging | UTC timestamp ambiguity: every log and history entry carries a time value but the UTC label appears only at the start; each timestamp should carry its timezone label | Low — confusion when reviewing historical logs | XS | 🟢 Low |
| FR.006 | Distribution | Installer: add an interactive option to install additional plugins, agents, and skills (claude-mem and QMD already handled; remaining ecosystem components not yet covered) | Low — manual post-install steps required | S | 🟢 Low |
| UI-disable | Chat | The Telegram question-UI (interactive prompts from Claude Code) does not work via the Agent SDK; the feature must be added to the disallowed tools list | Low — affects only Claude tools that present choice prompts | XS | 🟢 Low |
| Status-ext | Chat | The `/status` command does not report the state of optional third-party components (e.g., QMD running/stopped); extend the output to include these | Low — minor observability gap | XS | 🟢 Low |
| FR.007 | Research | Investigate whether the Claude browser plugin is accessible from Archon and document how it could be used — requires reading official documentation | Unknown — exploratory | M | 🟢 Low |
| FR.008 | Docs | Missing end-user documentation: installation through configuration and use of third-party components (QMD, etc.) — needs a world-class user guide | High for new users | L | 🟠 Medium |
| Agent-kill-beacon | Chat/AI | After a background agent is cancelled via SIGTERM (exit code -15), the per-agent beacon task continues sending `🤖 Agent is working…` updates — it should stop immediately on cancel. Additionally, hard-killing agents should be the last resort; the cancel flow should prefer graceful asyncio task cancellation before escalating to OS signals. | Medium — confusing UX; stale beacon messages after cancellation | S | 🟠 Medium |
| Smart-heartbeat | AI/Config | Add a smart heartbeat mechanism: a list of cron-job-like definitions that Claude can read and update at runtime. When the heartbeat fires, Claude processes the job list, executes triggered items, and removes completed ones. When the list is empty the heartbeat sleeps until a new job is added. Distinct from the existing `CronScheduler` (which uses static TOML config) — this heartbeat list is AI-editable at runtime. | Low — exploratory feature; depends on stable cron infrastructure | L | 🟢 Low |

---

## Test coverage debt

The items below were originally drawn from `docs/test_gap_report.md` (generated 2026-02-24, since removed). Task **H4** (test coverage gap closure) resolved all Critical, all High, and almost all Medium gaps. The remaining open item is listed below.

### Resolved — all Critical and High gaps closed by H4

All three Critical gaps, all eight High gaps, and seven of eight Medium gaps listed in the original report have been fixed and verified with dedicated tests. No action required.

### Medium — remaining open item

| Module | Gap |
|---|---|
| `archon/chat/handler.py` | `message.bot is None` assertion path never triggered |

---

## Prioritisation matrix

The diagram maps every open item across two axes: business/user impact (vertical) and implementation effort (horizontal). Items in the top-left quadrant ship first.

```mermaid
quadrantChart
    title Debt prioritisation — impact vs effort
    x-axis Low Effort --> High Effort
    y-axis Low Impact --> High Impact
    quadrant-1 Ship next sprint
    quadrant-2 Plan carefully
    quadrant-3 Batch with others
    quadrant-4 Avoid unless forced

    Installer-fix: [0.1, 0.92]
    FR.013: [0.12, 0.6]
    FR.012: [0.15, 0.58]
    UI-disable: [0.1, 0.35]
    Status-ext: [0.12, 0.3]
    FR.010: [0.1, 0.28]
    FR.011: [0.2, 0.62]
    FR.009: [0.35, 0.65]
    S16.1: [0.55, 0.88]
    FR.005: [0.75, 0.95]
    FR.008: [0.72, 0.72]
    FR.006: [0.4, 0.38]
    FR.007: [0.5, 0.25]
    Agent-kill-beacon: [0.25, 0.55]
    Smart-heartbeat: [0.7, 0.3]
```

---

## Implementation order (recommendation)

Based on the matrix above and dependency relationships:

1. **Installer-fix** — unblocks clean new installations; low effort, high blast radius.
2. **S16.1** — replaces `install.sh` with a testable Python installer; fixes the installer-fix permanently.
3. **FR.013 + FR.012** — tiny UX improvements; ship together in a single PR.
4. **FR.009** — cron pipeline format correctness; medium effort, needed before any cron expansion.
5. **FR.011** — compaction counter in `/context`; enables FR.005 observability.
6. **FR.005** — context compaction; largest AI feature; depends on FR.011 for visibility.
7. **Test debt (remaining)** — all critical and high gaps already closed by H4; one medium gap remains (`handler.py` `message.bot is None` path).
8. **FR.008** — user guide; schedule as a documentation sprint after FR.005 stabilises.
9. **FR.010, Status-ext, UI-disable, FR.006, FR.007** — low-priority; batch into a maintenance sprint.
10. **Agent-kill-beacon** — medium-priority UX fix; address after the installer and core feature work is stable.
11. **Smart-heartbeat** — exploratory, low-priority; requires stable cron infrastructure first.

---

## Related documents

- [`510_release_and_environment_strategy.md`](510_release_and_environment_strategy.md) — S16.1 installer detail, environment layout
- [`200_testing_strategy.md`](200_testing_strategy.md) — test pyramid and coverage policy
- [`160_operational_readiness_monitoring_and_reliability.md`](160_operational_readiness_monitoring_and_reliability.md) — logging and observability
