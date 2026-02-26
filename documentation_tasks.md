# Documentation Audit — Task List

**Audited:** 2026-02-26
**Scope:** All docs + key source files cross-checked line-by-line
**Method:** Every task is backed by a specific source-file reference.

---

## Documentation Standard Compliance Tasks

> These tasks bring the project into compliance with the `documentation-standard` skill (`~/.claude/skills/documentation-standard/SKILL.md`). They are structural and organizational in nature — they do **not** overlap with the content-accuracy tasks above. Every gap against the standard has a corresponding task here.
>
> **Priority legend (structural):**
> - **P1** — Required structural element; the standard explicitly mandates it exists
> - **P2** — Required file or section is missing; must be created to comply
> - **P3** — Improvement / compliance refinement; not blocking but needed for full conformance

---

### 1. Directory Structure & File Organization

- [ ] **1.1 Rename `/docs/` to `/Documentation/`** — the standard mandates all documentation lives in `/Documentation/` (not `/docs/`). All internal cross-references and the README project-structure diagram must be updated after the rename. *(Ref: standard §Directory Structure: "All documentation lives in `/Documentation/`")* — **P1**

- [ ] **1.2 Create `Documentation/Architecture/` subdirectory** for all architecture documents using the `NNN_snake_case_name.md` numeric prefix convention. *(Ref: standard §Standard Subdirectories, §Architecture Documentation Structure)* — **P1**

- [ ] **1.3 Create `Documentation/ADRs/` subdirectory** for Architecture Decision Records. *(Ref: standard §Standard Subdirectories, §Architecture Decision Records)* — **P1**

- [ ] **1.4 Create `Documentation/Backlog/` subdirectory** for planned features and user stories. *(Ref: standard §Standard Subdirectories)* — **P1**

- [ ] **1.5 Create `Documentation/Completed/` subdirectory** for implementation documentation (completed stories, plan docs). *(Ref: standard §Standard Subdirectories)* — **P1**

- [ ] **1.6 Create `Documentation/UserManual/` subdirectory** and move `docs/USER_MANUAL.md` into it as `user_manual.md`. *(Ref: standard §Standard Subdirectories)* — **P1**

- [ ] **1.7 Create `Documentation/roadmap.md`** — a product roadmap file is required by the standard directory structure. Populate with current pending features (FR.005–FR.013, S16.1) and their status. *(Ref: standard §Standard Subdirectories: "roadmap.md — Product roadmap")* — **P2**

- [ ] **1.8 Create `Documentation/quick_start.md`** — developer onboarding entry point required by the standard. Cover: prerequisites, clone, `uv sync`, configure `.env`/`config.toml`, run, run tests. *(Ref: standard §Standard Subdirectories: "quick_start.md — Developer onboarding")* — **P2**

---

### 2. Required Root-Level Files

- [ ] **2.1 Create `contributing.md`** at the repository root — the standard lists this as a required root-level file ("must have"). Should cover: how to run tests, coding conventions (TDD, KISS, type safety), branch/PR workflow, and documentation update requirements. *(Ref: standard §Directory Structure: "contributing.md — Contribution guidelines, must have")* — **P1**

---

### 3. Architecture Documentation

All required Architecture files are absent. The existing `docs/high_level_concept.md` and `docs/prd.md` contain partial content that should be decomposed into the following properly-named files with metadata headers.

#### Foundation (000–099)

- [ ] **3.1 Create `Documentation/Architecture/000_introduction_and_guiding_principles.md`** — vision, philosophy, goals, and non-goals for Archon. Source material: `docs/high_level_concept.md` §Short App Definition and §Key Design Invariants, `README.md` intro. Must start with 3–5 guiding principles before details. *(Ref: standard §Foundation 000-099)* — **P2**

- [ ] **3.2 Create `Documentation/Architecture/010_engineering_principles_and_constraints.md`** — technical constraints and standards. Source material: `CLAUDE.md` §Key constraints (TDD mandatory, KISS, no `print()`, ≥85% coverage, 5-second shutdown, etc.). *(Ref: standard §Foundation 000-099)* — **P2**

#### System Design (100–199)

- [ ] **3.3 Create `Documentation/Architecture/100_system_architecture_overview.md`** — C4 diagrams (Context, Container, Component levels) in Mermaid format; overall architecture patterns; module layering. Currently only ASCII art diagrams exist in `README.md` and `docs/high_level_concept.md` without proper structure or metadata. *(Ref: standard §System Design 100-199)* — **P2**

- [ ] **3.4 Create `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md`** — inventory of all components (`ClaudeSession`, `EventMapper`, `SessionManager`, `BackgroundAgentManager`, `ArchonMCPServer`, `CronScheduler`, `HistoryManager`, `AgentLoader`, etc.) with their responsibilities and layer assignment. *(Ref: standard §System Design 100-199)* — **P2**

- [ ] **3.5 Create `Documentation/Architecture/120_services_and_integration_architecture.md`** — documents all sync and async integrations: Telegram Bot API (aiogram polling), Claude Agent SDK (MCP JSON-RPC), Archon MCP server (aiohttp), QMD MCP (optional), launchd/systemd. No dedicated document exists today. *(Ref: standard §System Design 100-199)* — **P2**

- [ ] **3.6 Create `Documentation/Architecture/130_data_architecture_and_persistence.md`** — config file structure (`.env` + `config.toml` + `config.toml.bak`), atomic write pattern, history file format (`~/.archon/history/YYYY-MM-DD.md`), per-agent log format, data flow, retention policy. *(Ref: standard §System Design 100-199)* — **P2**

- [ ] **3.7 Create `Documentation/Architecture/140_error_handling_strategy.md`** — documents fail-fast on startup (`ConfigError`), no-swallow policy for chat errors, `stop_all()` within 5 s, graceful shutdown pattern, Telegram network error recovery (aiogram polling auto-reconnect), session stuck-monitor strategy. *(Ref: standard §System Design 100-199)* — **P2**

- [ ] **3.8 Create `Documentation/Architecture/150_security_and_privacy_architecture.md`** — whitelist enforcement at middleware level (before any handler), no message content in logs (only `(N chars)`), error handler logs type only, atomic config write to prevent corruption, no cloud data transmission. Currently scattered across `CLAUDE.md` §Key constraints and `docs/prd.md` §6 NFR. *(Ref: standard §System Design 100-199)* — **P2**

- [ ] **3.9 Create `Documentation/Architecture/160_operational_readiness_monitoring_and_reliability.md`** — observability (daily-rotating log files, configurable log level), daemon auto-restart (`KeepAlive`/`Restart=on-failure`), graceful shutdown SLO (≤5 s), startup self-healing (config.toml.bak restore). *(Ref: standard §System Design 100-199)* — **P2**

#### Quality & Testing (200–299)

- [ ] **Create `Documentation/Architecture/200_testing_strategy.md`** — documents the full test pyramid: unit tests (no external deps, run by default), integration tests (SDK client boundary mocked), e2e tests (bot + SDK boundary mocked), live tests (`@pytest.mark.live`, real `claude` binary), `@pytest.mark.requires_telegram` tier. Coverage target ≥ 85% (actual ≥ 97%). Currently documented only inline per story in `docs/stories.md`, with no unified testing strategy document. *(Ref: standard §Quality & Testing 200-299)* — **P2**

#### Development & Operations (500–599)

- [ ] **Create `Documentation/Architecture/500_development_workflows_and_conventions.md`** — coding standards (TDD, KISS, Clean Code, no `print()`, `logging.getLogger("archon")`), type safety rules (mypy strict), branch and PR workflow, Definition of Done. Source material: `CLAUDE.md` §Key constraints. *(Ref: standard §Development & Operations 500-599)* — **P2**

- [ ] **Create `Documentation/Architecture/510_release_and_environment_strategy.md`** — environment configuration strategy (`.env` for secrets, `config.toml` for structure), versioning (currently none — note this gap), service installation (launchd macOS / systemd Linux), `make install/uninstall/logs` targets, planned Python installer (S16.1). *(Ref: standard §Development & Operations 500-599)* — **P2**

- [ ] **Create `Documentation/Architecture/530_technical_debt_refactoring_roadmap.md`** — debt register for pending items: Python installer replacement (S16.1), context compaction (FR.005), cron pipeline format change (FR.009), installer fix, plus any known code-quality debt. Source material: `docs/tasks.md` pending items. *(Ref: standard §Development & Operations 500-599)* — **P3**

#### Meta (900–999)

- [ ] **Create `Documentation/990_documentation_index_and_contribution_guide.md`** — top-level navigation index listing every document in `Documentation/` with a one-line purpose summary and a link. Include contribution guidelines: how to add a new doc, which subdirectory, naming convention, required metadata header, how to update the index, and the review cycle. This file must be updated whenever a new document is added. *(Ref: standard §Meta 900-999: "990_documentation_index_and_contribution_guide.md — Navigation and contribution instructions"; §Maintenance Workflows step 7: Update 990 index)* — **P1**

---

### 4. ADR Directory

The `docs/high_level_concept.md` §Architecture Decisions table contains 8 significant decisions captured as a flat table. Each must be converted into a standalone ADR file in `Documentation/ADRs/` following the standard format (Status, Date, Deciders, Context, Decision, Consequences, Alternatives Considered).

- [ ] **Create `Documentation/ADRs/01_use_claude_agent_sdk.md`** — documents the decision to use `claude-agent-sdk` (`ClaudeSDKClient`) over PTY/ANSI subprocess control. Alternatives: subprocess + PTY parsing, `pexpect`. *(Ref: standard §Architecture Decision Records format; source: `high_level_concept.md` row 1)* — **P2**

- [ ] **Create `Documentation/ADRs/02_logical_boundary_output_streaming.md`** — documents the decision to send each logical event (tool call, thinking, response) as a separate Telegram message rather than streaming raw characters. *(Ref: standard §ADR format; source: `high_level_concept.md` row 2)* — **P2**

- [ ] **Create `Documentation/ADRs/03_one_session_per_user.md`** — documents the decision to maintain one persistent `ClaudeSession` per Telegram user with full conversation context via SDK session resume. *(Ref: standard §ADR format; source: `high_level_concept.md` row 3)* — **P2**

- [ ] **Create `Documentation/ADRs/04_local_daemon_deployment.md`** — documents the decision to run as a local launchd/systemd daemon rather than a cloud service. Alternatives: cloud functions, Docker. *(Ref: standard §ADR format; source: `high_level_concept.md` row 4)* — **P2**

- [ ] **Create `Documentation/ADRs/05_whitelist_access_control.md`** — documents the decision to use a static Telegram user ID whitelist enforced at middleware level. Alternatives: bot password, OAuth. *(Ref: standard §ADR format; source: `high_level_concept.md` row 5)* — **P2**

- [ ] **Create `Documentation/ADRs/06_background_agents_via_mcp_http.md`** — documents the decision to expose `spawn_background_agent` via a local MCP HTTP server rather than using the native SDK `Task` tool (always disabled). Alternatives: SDK Task tool, subprocess. *(Ref: standard §ADR format; source: `high_level_concept.md` row 6)* — **P2**

- [ ] **Create `Documentation/ADRs/07_pluggable_truncation_abc.md`** — documents the decision to use an ABC (`TruncationStrategy`) so new truncation modes require no changes outside `archon/ai/`. *(Ref: standard §ADR format; source: `high_level_concept.md` row 7)* — **P2**

- [ ] **Create `Documentation/ADRs/08_tomlkit_config_write_back.md`** — documents the decision to use `tomlkit` over stdlib `tomllib`/`tomli` for config write-back so comments and formatting are preserved on runtime saves. *(Ref: standard §ADR format; source: `high_level_concept.md` row 8)* — **P2**

- [ ] **Create `Documentation/ADRs/09_qmd_compatible_history_format.md`** — documents the decision to use QMD-compatible Markdown (H2/H3 structure, Contextual Retrieval blockquote) for chat history files so Claude can search its own past conversations via QMD MCP tools. *(Ref: standard §ADR format; source: `high_level_concept.md` implied by §Tech Stack)* — **P2**

---

### 5. Backlog & Completed Structure

- [ ] **Reorganize `docs/stories.md` into individual files** in `Documentation/Backlog/` (for any pending stories, e.g. S16.1) and `Documentation/Completed/` (for all ✅ stories). Each file must use `NN_descriptive_name.md` naming and include the standard backlog item format (Status, Priority, Estimated effort, User Story, Acceptance Criteria, Technical Notes, Related Documents). *(Ref: standard §User Stories & Backlog Items format; §Naming Conventions §Backlog/Completed: NN_descriptive_name.md)* — **P2**

- [ ] **Move `docs/plan-FR.001.md` to `Documentation/Completed/`** with the naming convention applied (e.g. `10_fr001_human_readable_agent_names.md`). Add the standard metadata header. *(Ref: standard §Naming Conventions §Backlog/Completed: NN_descriptive_name.md)* — **P3**

- [ ] **Move `docs/plan-FR.014.md` to `Documentation/Completed/`** with the naming convention applied (e.g. `11_fr014_background_agent_execution.md`). Add the standard metadata header. *(Ref: standard §Naming Conventions §Backlog/Completed: NN_descriptive_name.md)* — **P3**

- [ ] **Move `PLUGIN_PLAN.md` to `Documentation/Completed/`** with proper naming (e.g. `12_plugin_support_implementation.md`) or, if the content is architectural rather than implementation-specific, consolidate into `Documentation/Architecture/120_services_and_integration_architecture.md`. *(Ref: standard §Naming Conventions; §Avoid Duplication)* — **P3**

---

### 6. Document Metadata Headers

Every document is missing the required 5-line metadata header. The standard states: **"Every document must include a 4-line header"** (Purpose, Audience, Status, Last reviewed, Next review). The review cycle is: Architecture docs = quarterly, technical specs = bi-annually, process docs = annually.

- [ ] **Add metadata header to `README.md`** (Purpose: project overview for new users and contributors; Audience: all; Status: Stable; Last reviewed / Next review). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `CLAUDE.md`** (Purpose: AI assistant operating instructions; Audience: Claude Code AI; Status: Stable). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `AGENTS.md`** (Purpose: module architecture and data flow reference; Audience: Backend engineers). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/high_level_concept.md`** (Purpose: high-level architecture decisions; Audience: Backend engineers; Status: Stable). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/prd.md`** (Purpose: product requirements; Audience: All developers; Status: Stable — all Phase 1 & 2 requirements complete). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/stories.md`** (Purpose: user stories with acceptance criteria; Audience: All developers; Status: Stable — all stories complete). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/tasks.md`** (Purpose: implementation task checklist; Audience: All developers; Status: Active — ongoing task tracking). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/USER_MANUAL.md`** (Purpose: end-user guide for Telegram bot commands; Audience: End users; Status: Stable). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/test_gap_report.md`** (Purpose: audit of test coverage gaps; Audience: Backend engineers; Status: Draft — gaps being addressed). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/plan-FR.001.md`** (Purpose: TDD implementation plan for FR.001 agent names; Audience: Backend engineers; Status: Deprecated — completed). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/plan-FR.014.md`** (Purpose: TDD implementation plan for FR.014 background agents; Audience: Backend engineers; Status: Deprecated — completed). *(Ref: standard §Document Metadata)* — **P2**

- [ ] **Add metadata header to `docs/native-app-wrapper.md`** (Purpose: options for macOS native app wrapper for TCC permissions; Audience: Backend engineers; Status: Draft — research only). *(Ref: standard §Document Metadata)* — **P2**

---

### 7. Naming Convention Fixes

The standard requires all non-root files to use `snake_case.md`. Only the four special root-level files (`readme.md`, `contributing.md`, `CLAUDE.md`, `constitution.md`) are exempt.

- [ ] **Rename `AGENTS.md` → `agents.md`** — uppercase filename violates the `snake_case.md` convention for non-root-special files. Update all cross-references. *(Ref: standard §Naming Conventions: "All other files: snake_case.md")* — **P1**

- [ ] **Rename `docs/USER_MANUAL.md` → `docs/user_manual.md`** (or `Documentation/UserManual/user_manual.md` after migration) — uppercase violates `snake_case.md`. Update the README project structure diagram. *(Ref: standard §Naming Conventions)* — **P1**

- [ ] **Rename `docs/native-app-wrapper.md` → `docs/native_app_wrapper.md`** — kebab-case violates the `snake_case.md` convention. *(Ref: standard §Naming Conventions: "All other files: snake_case.md")* — **P1**

- [ ] **Rename `docs/plan-FR.001.md` → snake_case + standard prefix** (e.g. `docs/plan_fr_001_agent_names.md` if kept in docs/, or `Documentation/Completed/10_fr001_agent_names.md` after migration). The dot in the filename and mixed case violate naming conventions. *(Ref: standard §Naming Conventions §Backlog/Completed: NN_descriptive_name.md)* — **P3**

- [ ] **Rename `docs/plan-FR.014.md` → snake_case + standard prefix** (e.g. `docs/plan_fr_014_background_agents.md` if kept in docs/, or `Documentation/Completed/11_fr014_background_agents.md` after migration). *(Ref: standard §Naming Conventions §Backlog/Completed: NN_descriptive_name.md)* — **P3**

---

### 8. Diagrams — Mermaid Conversion

The standard requires Mermaid format for all diagrams (flowcharts, sequence diagrams, etc.). Every diagram in the project is currently ASCII art. Each must be replaced with a ```` ```mermaid ```` fenced block.

- [ ] **Convert ASCII overview diagram in `README.md` (lines 7–11)** — the `You (Telegram) ──▶ Archon ──▶ Claude Agent SDK` block — to a Mermaid `flowchart LR`. *(Ref: standard §Diagrams: "Use Mermaid for: flowcharts")* — **P3**

- [ ] **Convert ASCII architecture diagram in `README.md` (lines 456–467)** — the multi-component diagram showing `Gateway → SessionManager → ClaudeSession`, `BackgroundAgentManager`, `CronScheduler`, `HistoryManager` — to Mermaid. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII architecture diagram in `CLAUDE.md` (lines 44–48)** — the `Telegram ──▶ Gateway ──▶ SessionManager ──▶ ClaudeSession` block — to a Mermaid `flowchart LR`. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII main conversation flow in `docs/high_level_concept.md` (lines 63–92)** — the detailed `User (Telegram) → WhitelistMiddleware → handle_message() → SessionManager → ClaudeSession → EventMapper → …` flow — to a Mermaid `sequenceDiagram` or `flowchart TD`. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII background agent flow in `docs/high_level_concept.md` (lines 94–113)** — the `ClaudeSession.send() → ArchonMCPServer → BackgroundAgentManager → _run_agent()` flow — to Mermaid. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII cron flow in `docs/high_level_concept.md` (lines 115–127)** — the `CronScheduler._loop() → _run_job()` pipeline — to Mermaid. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII architecture diagram in `docs/prd.md` (lines 72–81)** — the module tree showing `archon/chat/`, `archon/ai/`, `archon/gateway/`, `archon/config/` — to a Mermaid `flowchart` or `graph`. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII component map in `docs/plan-FR.014.md` (lines 99–121)** — the `Telegram user → handle_message → ClaudeSession → ArchonMCPServer → BackgroundAgentManager → Isolated ClaudeSession` flow — to Mermaid. *(Ref: standard §Diagrams)* — **P3**

- [ ] **Convert ASCII dependency flow in `PLUGIN_PLAN.md` (lines 31–38)** — the `Gateway._run() → SkillLoader / PluginLoader → SessionManager → ClaudeSession` hierarchy — to Mermaid. *(Ref: standard §Diagrams)* — **P3**

---

### 9. Cross-Linking

The standard principle is "Link related documents contextually rather than repeating content." Cross-references are currently minimal or absent.

- [ ] **Add cross-references from Architecture docs to their corresponding ADRs** — e.g., `100_system_architecture_overview.md` should link to `ADRs/01_use_claude_agent_sdk.md`; `150_security_and_privacy_architecture.md` should link to `ADRs/05_whitelist_access_control.md`. *(Ref: standard §Core Principles §4: "Cross-Linking Over Silos")* — **P3**

- [ ] **Add cross-references from `Documentation/UserManual/user_manual.md` to Architecture and config docs** — the user manual references configuration sections; each should link to the relevant Architecture doc (e.g., notification modes → `140_error_handling_strategy.md`, config resilience → `130_data_architecture_and_persistence.md`). *(Ref: standard §Content Standards §Cross-references)* — **P3**

- [ ] **Add cross-references from `CLAUDE.md` to Architecture docs** — once Architecture docs exist, `CLAUDE.md` should link to `000_introduction_and_guiding_principles.md` (for project context) and `500_development_workflows_and_conventions.md` (for coding standards). *(Ref: standard §Content Standards §Cross-references)* — **P3**

- [ ] **Add "Related Documents" sections to all Backlog/Completed story files** — each story must have a `## Related Documents` section linking to the relevant Architecture doc and ADR, per the standard User Stories format. *(Ref: standard §User Stories & Backlog Items: "## Related Documents" section)* — **P3**

- [ ] **Eliminate content duplication between `README.md` and `CLAUDE.md`** — both files contain an Architecture section with overlapping module descriptions and diagrams. Per the standard, information lives in exactly one place; the README should summarize and link to the Architecture docs; CLAUDE.md should contain only AI-assistant-specific context not found elsewhere. *(Ref: standard §Core Principles §2: "Avoid Duplication")* — **P3**

---

### 10. Documentation Index

- [ ] **Create `Documentation/990_documentation_index_and_contribution_guide.md`** — a complete navigation index of every file in `Documentation/` with one-line purpose summaries, links, and document type (Architecture/ADR/Backlog/Completed/UserManual). Include: how to add a new document (correct directory, naming convention, metadata header template, how to update this index), the review cycle table, and the Markdown linting command. This file must be updated every time a document is added or moved. *(Ref: standard §Meta 900-999; §Maintenance Workflows §Creating New Documents step 7; §Quality Checklist: "Listed in documentation index")* — **P1**

---

### 11. Content Standards

- [ ] **Audit all Architecture docs for "principles before details" structure** — per the standard, every Architecture document must open with 3–5 key rules/principles before diving into specifics. Existing docs (`high_level_concept.md`, `prd.md`) do not follow this pattern. Apply when creating new Architecture docs and when converting existing content. *(Ref: standard §Content Standards §Structure: "1. Start with principles")* — **P3**

- [ ] **Writing style audit: enforce active voice and present tense** across all documentation. Example fixes: "Input is validated by the middleware" → "The middleware validates input"; "The daemon will restart" → "The daemon restarts". *(Ref: standard §Writing Style: "Active voice … Present tense")* — **P3**

- [ ] **Verify all User Story files follow the standard backlog format** (Status, Priority, Estimated effort, User Story, Acceptance Criteria, Technical Notes, Related Documents) when reorganizing into `Documentation/Backlog/` and `Documentation/Completed/`. *(Ref: standard §User Stories & Backlog Items format)* — **P3**

---

### 12. Quality & Maintenance Workflow

- [ ] **Add `.markdownlint.json` configuration file** to the repository root, enabling consistent Markdown formatting validation across all `.md` files. Minimum rules: `MD013` (line length), `MD041` (first line heading), `MD022` (headings surrounded by blank lines). *(Ref: standard §Maintenance Workflows §Creating New Documents: "Validate Markdown linting (use markdownlint)")* — **P3**

- [ ] **Configure a pre-commit hook or `make lint-docs` Makefile target** that runs `markdownlint` (or `markdownlint-cli2`) on all `*.md` files, so documentation formatting is validated before commits. *(Ref: standard §Integration with Development Workflow: "Pre-commit: Validate Markdown linting")* — **P3**

- [ ] **Run markdownlint on all existing documentation files and fix formatting issues** — this is a one-time cleanup to establish the baseline. Expected issues: missing blank lines around headings, bare URLs, inconsistent list markers, trailing spaces. *(Ref: standard §Refactoring Documentation step 6: "Run Markdown linting")* — **P3**

- [ ] **Set `Next review` dates on all Architecture documents** following the review cycle defined in the standard: Architecture docs reviewed quarterly, technical specs bi-annually, process docs annually. The `Last reviewed` date should be set to the date the document was last substantively updated. *(Ref: standard §Document Metadata: "Review cycle: Architecture docs reviewed quarterly…")* — **P2**

- [ ] **Document the documentation maintenance workflow** in `contributing.md` or in `Documentation/990_documentation_index_and_contribution_guide.md` — covers when to update docs (every PR that changes behaviour must update relevant docs), the review cycle, and how to add a new architecture document (directory, naming, metadata, update index). *(Ref: standard §Maintenance Workflows; §Integration with Development Workflow: "PR reviews: Check documentation updates for code changes")* — **P3**

---

### Compliance Summary

| Category | P1 | P2 | P3 | Total |
|---|---|---|---|---|
| Directory Structure & File Organization | 6 | 2 | 0 | 8 |
| Required Root-Level Files | 1 | 0 | 0 | 1 |
| Architecture Documentation | 1 | 12 | 1 | 14 |
| ADR Directory | 1 | 9 | 0 | 10 |
| Backlog & Completed Structure | 0 | 1 | 3 | 4 |
| Document Metadata Headers | 0 | 12 | 0 | 12 |
| Naming Convention Fixes | 3 | 0 | 2 | 5 |
| Diagrams — Mermaid Conversion | 0 | 0 | 9 | 9 |
| Cross-Linking | 0 | 0 | 5 | 5 |
| Documentation Index | 1 | 0 | 0 | 1 |
| Content Standards | 0 | 0 | 3 | 3 |
| Quality & Maintenance Workflow | 0 | 1 | 4 | 5 |
| **Total** | **13** | **37** | **27** | **77** |

---
## Priority Legend

| Level | Meaning |
|---|---|
| **P1** | Factual error — doc states something false about the current implementation |
| **P2** | Missing critical content — important behaviour not documented anywhere |
| **P3** | Improvement — outdated, misleading, or incomplete but not wrong per se |

---

## README.md

### P1 — Factual Errors

- [ ] **Fix Background Agents feature claim about context injection.**
  Line 358: "On completion: Telegram `✅` notification + result injected into main session context."
  `inject_context()` is never called anywhere in `BackgroundAgentManager._run_agent()` — the method calls only `_notify_success()` after completion. The context-injection feature is not implemented.
  *Source: `archon/ai/background_agent_manager.py` lines 341–391 — no `inject_context` call exists.*

- [ ] **Correct the Output Events table — background agent spawn message.**
  Line 261: shows `Background agent started | 🤖 Agent <b>Name</b> started`.
  The actual Telegram message from `BackgroundAgentManager._notify_spawn()` is `🤖 Agent <b>{name}</b> spawned.` ("spawned", not "started"; period at end).
  *Source: `archon/ai/background_agent_manager.py` line 440.*

- [ ] **Correct the Output Events table — background agent completion message.**
  Line 262: shows `Background agent done | 🤖 Agent <b>Name</b> done`.
  The actual Telegram message from `BackgroundAgentManager._notify_success()` is `✅ 🤖 Agent <b>{name}</b> completed` (has ✅ prefix, "completed" not "done", and includes the 🤖 emoji).
  *Source: `archon/ai/background_agent_manager.py` line 463.*

### P2 — Missing Critical Content

- [ ] **Document the queued-message notification.**
  When a user sends a message while Claude is still processing, the handler replies immediately with `"⏳ Previous request still processing — your message is queued"`. This behaviour is not mentioned anywhere in the README.
  *Source: `archon/chat/handler.py` lines 222–229.*

- [ ] **Document `head_chars` and `tail_chars` in the `[output]` config section.**
  `OutputConfig` has `head_chars: int = 1500` and `tail_chars: int = 1500` fields parsed from `config.toml` (`output_data.get("head_chars", 1500)`, etc.) but these options are absent from the README configuration reference.
  *Source: `archon/config/loader.py` lines 34–35, 271–272.*

- [ ] **Clarify that `[background_agents] enabled` is not a real config key.**
  The existing note "The background agent MCP server always starts regardless of this config section" is accurate but does not explain why: `BackgroundAgentsConfig` has no `enabled` field and the gateway always instantiates `ArchonMCPServer` unconditionally. Add a sentence: "There is no `enabled` flag — the MCP server always starts."
  *Source: `archon/config/loader.py` lines 90–108; `archon/gateway/gateway.py` lines 244–248.*

### P3 — Improvements

- [ ] **Clarify the ToolStarted format in the Output Events table.**
  Line 257: shows `🔧 Tool [N]: <name>`. The `[N]` id tag only appears when `event.id != 0`; in practice it is always present but the format implies it is optional — add a note or show both forms.
  *Source: `archon/chat/handler.py` lines 160–163.*

- [ ] **Note that `install.sh` is the current installer; `install.py` (S16.1) is pending.**
  The Quick Start section describes `bash install.sh` as the installer. S16.1 plans to replace it with a Python installer. Add a one-line note pointing to `docs/tasks.md` S16.1.

---

## CLAUDE.md

### P2 — Missing Critical Content

- [ ] **Add `SubagentStarted` and `SubagentStopped` to the Output Event Model table.**
  The table (lines 78–83) lists five event types but omits `SubagentStarted → 🤖 Agent <b>Name</b> started` and `SubagentStopped → 🤖 Agent <b>Name</b> done`.
  *Source: `archon/chat/handler.py` lines 180–192.*

### P3 — Improvements

- [ ] **Update the Architecture diagram to include major subsystems.**
  Lines 44–48 show a minimal 3-node diagram. The real architecture adds: `BackgroundAgentManager`, `ArchonMCPServer`, `CronScheduler`, `HistoryManager`, `AgentLogger`. These are listed in the bullet points below but missing from the diagram, making the diagram misleading. Consider replacing with the full diagram from `high_level_concept.md`.
  *Source: `archon/gateway/gateway.py` lines 244–315.*

---

## docs/USER_MANUAL.md

### P1 — Factual Errors

- [ ] **Fix `/agents` command description: it reads filesystem files, not `config.toml`.**
  Line 231: "Lists all custom agent types defined in `config.toml`."
  The actual `agents_command` handler reads ONLY from `~/.claude/agents/*.md` via `AgentLoader` — it has no dependency on `AgentsConfig` from `config.toml`.
  *Source: `archon/chat/commands.py` lines 539–593.*

- [ ] **Fix `/agents` command output example: wrong header and format.**
  Lines 232–244: shows `🤖 Agent team:` with a flat list.
  Actual output uses `🤖 **Archon agents** (active in sessions):` for `-archon` agents and `🔍 **Other agents** (TUI-only, not injected):` for others. The flat format and "Agent team" header are both wrong.
  *Source: `archon/chat/commands.py` lines 563–587.*

- [ ] **Fix `/agents` command "no agents" reply text.**
  Line 245: "If no agents are configured: explains how to add `[agents]` definitions to `config.toml`."
  Actual reply is: `"ℹ️ No agent types configured.\n\nAdd <code>name-archon.md</code> files to <code>~/.claude/agents/</code>."` — no mention of config.toml.
  *Source: `archon/chat/commands.py` lines 555–560.*

- [ ] **Fix notification modes visibility matrix: remove "Thinking start" row, rename "Thinking content" row.**
  Lines 396–398: the matrix has two rows — "💭 Thinking start" and "💭 Thinking content" — implying two separate events.
  `ThinkingStarted` was removed (commit 31e176b); only `ThinkingResult` exists, producing a single `💭 Thinking complete:\n<content>` message. Replace both rows with one row: `💭 Thinking complete`.
  *Source: `archon/ai/event_mapper.py` lines 26–29, 118–119.*

### P2 — Missing Critical Content

- [ ] **Add `/running_agents` to the Quick Reference.**
  Lines 422–444: the quick-reference block lists every other command but omits `/running_agents`. It should appear after `/jobs`.
  *Source: `archon/chat/bot.py` line 53.*

- [ ] **Document the queued-message notification.**
  Same gap as in README.md — "⏳ Previous request still processing — your message is queued" is sent when Claude is busy but not explained anywhere in the user-facing manual.
  *Source: `archon/chat/handler.py` lines 222–229.*

### P3 — Improvements

- [ ] **Clarify `/restart` session behaviour.**
  Line 84: "Your conversation history is not lost — the next message resumes normally."
  `restart_command` calls `session_manager.stop_all()` before `os.execv()`, which destroys all in-memory session state. History files on disk (`~/.archon/history/`) are preserved, but the Claude conversation context is lost — the next message starts a new session. Rephrase to: "Session context is reset (new session on next message); conversation log files are preserved."
  *Source: `archon/chat/commands.py` lines 119–127.*

---

## docs/prd.md

### P1 — Factual Errors

- [ ] **Remove `enabled: bool = False` from the `BackgroundAgentsConfig` dataclass definition.**
  Lines 969–977: the prd shows `BackgroundAgentsConfig` with an `enabled` field.
  The actual dataclass in `loader.py` has no `enabled` field — `spawn_rule`, `max_parallel`, `host`, `port`, `beacon_interval_minutes` only.
  *Source: `archon/config/loader.py` lines 90–108.*

- [ ] **Fix S15.4 acceptance criteria: MCP server always starts.**
  Lines 1191–1192: "When `background_agents.enabled=False`: `BackgroundAgentManager` and `ArchonMCPServer` not instantiated; no port opened."
  This is false — the gateway always starts both unconditionally. The `enabled` key is not read.
  *Source: `archon/gateway/gateway.py` lines 239–254; `archon/config/loader.py` lines 352–360.*

- [ ] **Fix S7.11 (Background Agents) status: `inject_context()` is not called on completion.**
  Line 229: "✅ `inject_context()`: completed agent results prepended to next main session message."
  `BackgroundAgentManager._run_agent()` never calls `inject_context()` — it only calls `_notify_success()`. Revise to document actual behaviour or mark as not implemented.
  *Source: `archon/ai/background_agent_manager.py` lines 341–391.*

- [ ] **Correct the Agent lifecycle event format in Section 3.3 output table.**
  Line 47: "Agent started | 🤖 **Agent Name started**."
  The spawn notification (background agents) sends `🤖 Agent <b>Name</b> spawned.`; the completion sends `✅ 🤖 Agent <b>Name</b> completed`. The "started/done" wording comes from the (now unreachable) `SubagentStarted`/`SubagentStopped` format_event handlers, which only fire for SDK Task sub-agents — but Task is always disabled.
  *Source: `archon/ai/background_agent_manager.py` lines 440, 463.*

---

## docs/stories.md

### P1 — Factual Errors

- [ ] **Fix S1.2 SDK mapping: `ThinkingBlock → ThinkingResult` only (no `ThinkingStarted`).**
  Line 64: "`AssistantMessage` with `ThinkingBlock` → `ThinkingStarted` + `ThinkingResult(thinking)`."
  `ThinkingStarted` was removed. The mapper yields only `ThinkingResult`.
  *Source: `archon/ai/event_mapper.py` lines 118–119.*

- [ ] **Fix S1.2 event dataclasses list: remove `ThinkingStarted`.**
  Line 72: lists `ThinkingStarted` as a defined dataclass. It does not exist in `event_mapper.py`.
  *Source: `archon/ai/event_mapper.py` lines 22–88.*

- [ ] **Fix S12.1 acceptance criteria: `/agents` shows two sections, not three.**
  Line 882: "Accepts criteria: `/agents` lists archon agents (🤖), non-archon agents (🔍), and config agents (⚙️) in separate sections."
  The actual `agents_command` code only renders 🤖 (archon) and 🔍 (other) sections — there is no ⚙️ config section.
  *Source: `archon/chat/commands.py` lines 539–593.*

- [ ] **Fix S15.2 acceptance criteria: `inject_context()` is not called on successful completion.**
  Line 1093: "Successful run: … `inject_context()` called."
  `BackgroundAgentManager._run_agent()` does not call `inject_context()`.
  *Source: `archon/ai/background_agent_manager.py` lines 341–391.*

### P3 — Improvements

- [ ] **Update S5.1 acceptance criteria: remove `ThinkingStarted` from the required event list.**
  Line 302: "Tests cover: `ThinkingStarted`, `ThinkingResult`, …" — `ThinkingStarted` was removed.
  *Source: `archon/ai/event_mapper.py`.*

- [ ] **Update S7.1: "`ThinkingStarted` emits nothing" note is about a removed event.**
  Line 471: "`ThinkingStarted` emits nothing" — this note references a removed event. Remove or replace with: "There is no `ThinkingStarted` event; `ThinkingResult` produces `### 💭 Thought · HH:MM`."

- [ ] **Update S8.1 visibility matrix: replace two thinking rows with one.**
  Line 585: "verbose+: `💭 ThinkingStarted`, `💭 ThinkingResult` (truncated)."
  Only `ThinkingResult` exists now.

- [ ] **Update S2.6 acceptance criteria: count of commands is wrong.**
  Line 188: "All 7 commands (`start`, `status`, `stop`, `clear`, `restart`, `notify`, `settings`) appear in the command menu."
  There are now 18 commands in `BOT_COMMANDS`.
  *Source: `archon/chat/bot.py` lines 35–54.*

- [ ] **Update S11.2 formatting spec: remove colon from subagent format.**
  Line 741: "format `SubagentStarted` as `🤖 Agent: <b>{agent_type}</b> started`."
  Actual code is `🤖 Agent <b>{display}</b> started` (no colon, uses `agent_name` not `agent_type`).
  *Source: `archon/chat/handler.py` lines 180–186.*

- [ ] **Update S15.2 Telegram notification format spec.**
  Lines 1076–1079: spec shows `✅ Background agent **{name}** completed\n{result[:800]}` (Markdown bold, 800-char truncation).
  Actual code uses HTML `<b>` tags, includes 🤖 emoji (`✅ 🤖 Agent <b>{name}</b> completed`), and sends the full result split into ≤4000-char chunks — not capped at 800 chars.
  *Source: `archon/ai/background_agent_manager.py` lines 455–488.*

- [ ] **Mark S4.1 original logging spec as superseded by S4.4.**
  Lines 231–233: "Rotating file handler: max 10 MB per file, keep 5 backups" — this was the original spec but was replaced in S4.4 by `TimedRotatingFileHandler` with daily rotation and no size limit.
  *Source: `archon/log_setup.py`.*

---

## docs/high_level_concept.md

### P1 — Factual Errors

- [ ] **Remove `ThinkingStarted` from the data flow diagram.**
  Line 81: the main conversation flow diagram lists `ThinkingStarted` as a distinct event emitted by `EventMapper`. `ThinkingStarted` was removed — only `ThinkingResult` is emitted for thinking blocks.
  *Source: `archon/ai/event_mapper.py` lines 118–119.*

---

## docs/tasks.md

### P1 — Factual Errors

- [ ] **Remove garbled Bug.004 body text.**
  Lines 133–143: the Bug.004 entry contains accidentally pasted minified JavaScript / bundler output, making the actual bug description impossible to read after the first sentence. The only readable part is the first line ("Investigate this error log…"). Delete everything after the first sentence and preserve only the log snippet and the fix summary.

### P3 — Improvements

- [ ] **Add FR.005, FR.009, FR.010, FR.011, FR.012, FR.013 to a "Planned Features" section in prd.md.**
  Lines 151–168: several `[ ]` feature requests (FR.005 context compaction, FR.009 cron pipeline format change, FR.010 UTC timestamps, FR.011 compaction counter, FR.012 agent task brief, FR.013 thinking brief in normal mode) exist only in tasks.md and are not reflected in prd.md or stories.md as planned features. They should be promoted to proper requirements.

---

## Cross-Document Consistency Issues

### P1 — Factual Errors

- [ ] **Align all "SubagentStarted/Stopped always delivered" descriptions.**
  README notification matrix (line 142) and CLAUDE.md both correctly say SubagentStarted/Stopped are always sent. But USER_MANUAL visibility matrix (lines 387–399) has no row for agent lifecycle events at all — add a row `🤖 Agent start/stop` that shows ✓ in all four mode columns.
  *Source: `archon/chat/handler.py` lines 298–304; `archon/chat/handler.py` docstring lines 144–148.*

### P2 — Missing Critical Content

- [ ] **Add a note to README and USER_MANUAL explaining that SubagentStarted/SubagentStopped via SDK Task tool can never fire (Task always disabled).**
  The Output Events tables list "Background agent started/done" events, but format_event's SubagentStarted/SubagentStopped paths are for SDK Task sub-agents, which are permanently disabled. All background-agent Telegram messages come from `BackgroundAgentManager` directly. This distinction is undocumented and confusing.
  *Source: `archon/ai/claude_session.py` line 152; `archon/gateway/gateway.py` lines 239–254.*

- [ ] **Document the per-agent beacon message format (`🤖 Agent <b>Name</b> is working...`) in README and USER_MANUAL.**
  The FR.15 beacon (spawn notification edited in-place with live tool/thinking counts) is mentioned as a feature in README features list (line 25) but the actual message format is never shown. Add an example: `🤖 Agent <b>Atlas</b> is working... (3 tools, 1 thinking)`.
  *Source: `archon/ai/background_agent_manager.py` lines 65–85.*

- [ ] **Document `BackgroundAgentManager.spawn()` adds "spawned" notification before the beacon.**
  Users see `🤖 Agent <b>Name</b> spawned.` as the first message, which is then edited in-place. Neither README nor USER_MANUAL describes this two-phase (spawned → is working...) notification pattern.
  *Source: `archon/ai/background_agent_manager.py` lines 434–453.*

---

## Summary Statistics

| Priority | Count | Documents Affected |
|---|---|---|
| P1 — Factual errors | 14 | README, USER_MANUAL, prd.md, stories.md, high_level_concept.md, tasks.md |
| P2 — Missing content | 9 | README, CLAUDE.md, USER_MANUAL, prd.md, cross-doc |
| P3 — Improvements | 11 | stories.md, README, CLAUDE.md, USER_MANUAL, tasks.md |
| **Total** | **34** | |

---

