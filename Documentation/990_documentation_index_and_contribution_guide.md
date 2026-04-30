**Purpose**: Navigation index and contribution guide for all project documentation
**Audience**: All contributors
**Status**: Active
**Last reviewed**: 2026-04-30
**Next review**: 2026-07-30

# Documentation Index and Contribution Guide

## Introduction

This file is the single entry point for all documentation in the `Documentation/` directory. Use the **Documentation Map** below to find any document by file path, type, or purpose. When you add, move, or rename a document, update this index as the final step.

Start here if you are:
- Looking for a specific topic — scan the "One-line purpose" column in the map.
- Adding a new document — follow the **Adding a New Document** instructions.
- Reviewing existing docs — consult the **Review Schedule** to check what is due.

---

## Documentation Map

| File path | Type | One-line purpose |
|---|---|---|
| `Documentation/quick_start.md` | Architecture | Gets a developer from zero to a running Archon daemon in under 10 minutes |
| `Documentation/roadmap.md` | Architecture | Tracks the product roadmap — completed phases and pending features with exact task IDs |
| `Documentation/tasks.md` | Reference | Development task checklist with implementation order |
| `Documentation/installer_resilience_plan.md` | Reference | Installer robustness and resilience plan |
| `Documentation/Architecture/000_introduction_and_guiding_principles.md` | Architecture | Establishes the vision, philosophy, and guiding principles of the Archon Assistant project |
| `Documentation/Architecture/010_engineering_principles_and_constraints.md` | Architecture | Defines the technical constraints and standards every contributor must follow |
| `Documentation/Architecture/100_system_architecture_overview.md` | Architecture | Describes Archon's runtime architecture using C4-style context, container, and component diagrams |
| `Documentation/Architecture/110_component_catalog_and_layer_breakdown.md` | Architecture | Catalogs every component, assigns it to an architectural layer, and documents its public interface and dependencies |
| `Documentation/Architecture/120_services_and_integration_architecture.md` | Architecture | Documents every external integration — protocol, direction, authentication, and error handling |
| `Documentation/Architecture/130_data_architecture_and_persistence.md` | Architecture | Documents every persistent data artefact — file paths, formats, write patterns, and retention policy |
| `Documentation/Architecture/140_error_handling_strategy.md` | Architecture | Documents every error handling pattern — startup failures, message processing errors, session faults, and graceful shutdown |
| `Documentation/Architecture/150_security_and_privacy_architecture.md` | Architecture | Documents every security control and privacy measure enforced by Archon |
| `Documentation/Architecture/160_operational_readiness_monitoring_and_reliability.md` | Architecture | Documents observability, daemon lifecycle, graceful shutdown, startup self-healing, and the operational runbook |
| `Documentation/Architecture/170_voice_integration.md` | Architecture | Documents voice message integration — STT/TTS modules, VoiceMessageHandler, configuration, and data flow |
| `Documentation/Architecture/180_search_architecture.md` | Architecture | Documents the Search subsystem — components, data flow, interfaces, and gateway integration |
| `Documentation/Architecture/200_testing_strategy.md` | Architecture | Defines the test pyramid, markers, coverage targets, and commands for running Archon's test suite |
| `Documentation/Architecture/500_development_workflows_and_conventions.md` | Architecture | Documents Archon's coding standards, development workflow, type-checking configuration, and Definition of Done |
| `Documentation/Architecture/510_release_and_environment_strategy.md` | Architecture | Documents how Archon is configured, installed, versioned, and run as a system daemon on macOS and Linux |
| `Documentation/Architecture/530_technical_debt_refactoring_roadmap.md` | Architecture | Registers all known technical debt, pending feature gaps, and test coverage deficiencies with prioritisation guidance |
| `Documentation/ADRs/01_use_claude_agent_sdk.md` | ADR | Chooses `claude-agent-sdk` (`ClaudeSDKClient`) over PTY/subprocess for controlled Claude Code interaction |
| `Documentation/ADRs/02_logical_boundary_output_streaming.md` | ADR | Decides to send each logical event (tool call, thinking, response) as a separate Telegram message |
| `Documentation/ADRs/03_one_session_per_user.md` | ADR | Decides to maintain one persistent `ClaudeSession` per Telegram user with SDK-based context resume |
| `Documentation/ADRs/04_local_daemon_deployment.md` | ADR | Documents the decision to run Archon as a local user-space daemon rather than a cloud-hosted service |
| `Documentation/ADRs/05_whitelist_access_control.md` | ADR | Documents the decision to use a static Telegram user ID whitelist enforced at the middleware layer |
| `Documentation/ADRs/06_background_agents_via_mcp_http.md` | ADR | Documents the decision to expose background agent spawning as an MCP tool via a local aiohttp HTTP server |
| `Documentation/ADRs/07_pluggable_truncation_abc.md` | ADR | Architecture decision record for the `TruncationStrategy` ABC pattern |
| `Documentation/ADRs/08_tomlkit_config_write_back.md` | ADR | Architecture decision record for using `tomlkit` over stdlib `tomllib` for config write-back |
| `Documentation/ADRs/09_search_history_format.md` | ADR | Architecture decision record for the Search integration replacing QMD |
| `Documentation/Backlog/01_s16_1_python_installer.md` | Backlog | Backlog item for replacing the bash installer with a maintainable Python module |
| `Documentation/Backlog/02_macos_tcc_native_app_wrapper.md` | Backlog | macOS native app wrapper for TCC permissions |
| `Documentation/Backlog/03_multi-agent-architecture.md` | Backlog | Multi-agent orchestration system architecture specification |
| `Documentation/Backlog/03_multi-agent-discussion-notes.md` | Backlog | Multi-agent architecture discussion notes |
| `Documentation/Backlog/03_multi-agent-implementation-plan.md` | Backlog | Multi-agent pipeline phase 1 implementation plan |
| `Documentation/Backlog/03_multi-agent-phase2-plan.md` | Backlog | Multi-agent pipeline phase 2 implementation plan |
| `Documentation/Backlog/04_add_conversation_context_to_orchestration_session.md` | Backlog | Plan to add conversation context to orchestration session |
| `Documentation/Backlog/05_periodic_context_reminder_injection.md` | Backlog | Periodic context reminder injection feature spec |
| `Documentation/Backlog/06_orchestration_session_full_redesign.md` | Backlog | Orchestration session full redesign plan |
| `Documentation/Backlog/07_redesign_bugs.md` | Backlog | Bug list from orchestration redesign |
| `Documentation/Backlog/08_context_injection_improvement.md` | Backlog | Context injection improvement — REMINDER.md for orchestrator and agents |
| `Documentation/Backlog/09_platform_strategy_refactoring.md` | Backlog | Platform strategy refactoring plan |
| `Documentation/Backlog/10_file_attachment_support.md` | Backlog | File attachment support (inbound and outbound) |
| `Documentation/Backlog/10_startup_notification.md` | Backlog | Startup notification feature spec |
| `Documentation/Backlog/11_archon_control_plane_mcp_tools.md` | Backlog | Archon control plane MCP tools |
| `Documentation/Backlog/13_reduce_routing_lock_hold_time.md` | Backlog | Reduce routing lock hold time |
| `Documentation/Backlog/FEAT-037-search-competitive-analysis-field.md` | Backlog | Competitive analysis of Archon Search versus major local and self-hosted search/RAG systems |
| `Documentation/Backlog/FEAT-037-search-competitive-analysis-marveen.md` | Backlog | Comparative analysis of Archon Search versus Marveen's memory/search subsystem |
| `Documentation/Backlog/FEAT-037-search-world-class-roadmap.md` | Backlog | Priority-ordered roadmap for turning Search into a standalone world-class product |
| `Documentation/Backlog/bug_01_status_version_display.md` | Backlog/Bug | /status command shows unexpected version string |
| `Documentation/Backlog/bug_02_reminder_md_wrong_path.md` | Backlog/Bug | REMINDER.md read from wrong path at session start |
| `Documentation/Backlog/bug_03_sdk_19min_hang.md` | Backlog/Bug | SDK took 19 minutes to respond to simple chat message |
| `Documentation/Backlog/bug_04_05_06_concurrent_message_handling.md` | Backlog/Bug | Concurrent messages cause shifted responses, stuck processing, and silent processing |
| `Documentation/Backlog/bug_07_routing_check_timeout.md` | Backlog/Bug | Routing check timeout message shown to user |
| `Documentation/Backlog/bug_08_agent_spawn_message_order.md` | Backlog/Bug | Wrong message order when promoting task to background agent |
| `Documentation/Backlog/bug_09_model_warnings.md` | Backlog/Bug | Repeated model warnings in logs for unlisted models |
| `Documentation/Backlog/bug_10_generator_drain_timeout.md` | Backlog/Bug | Generator drain timed out after 5s warning |
| `Documentation/Backlog/bug_11_session_disconnect_cancel_scope.md` | Backlog/Bug | Session disconnect fails with cancel scope error during eviction |
| `Documentation/Backlog/bug_12_voice_no_queued_notification.md` | Backlog/Bug | Voice messages give no queued notification when session is busy |
| `Documentation/Backlog/bug_13_send_lock_cancelled_error.md` | Backlog/Bug | Send lock permanently held when CancelledError occurs during drain |
| `Documentation/Backlog/bug_14_cron_stop_orphaned_tasks.md` | Backlog/Bug | JobScheduler.stop() orphans running scheduled job tasks |
| `Documentation/Backlog/bug_15_16_session_manager_stop_all.md` | Backlog/Bug | SessionManager.stop_all() sequential stops and eviction race |
| `Documentation/Backlog/bug_17_classifier_session_unbounded_growth.md` | Backlog/Bug | Classifier session never recycled — unbounded SDK history accumulation |
| `Documentation/Backlog/bug_18_bam_failure_handler_unprotected.md` | Backlog/Bug | BAM failure handler unprotected history_manager.record_event() call |
| `Documentation/Backlog/bug_19_cost_lost_on_session_reset.md` | Backlog/Bug | Cost data silently lost on session resets |
| `Documentation/Backlog/bug_20_message_shifting_response_misalignment.md` | Backlog/Bug | Message shifting and response misalignment |
| `Documentation/Backlog/bug_21_beacon_double_send.md` | Backlog/Bug | Multiple beacons per interval from parallel agents |
| `Documentation/Backlog/bug_22_pipeline_empty_model_field.md` | Backlog/Bug | Pipeline routing log shows empty model field |
| `Documentation/Completed/00_completed_stories_index.md` | Completed | Navigation index of all completed user stories in the Archon project |
| `Documentation/Completed/10_fr001_human_readable_agent_names.md` | Completed | TDD implementation plan for FR.001 human-readable agent names |
| `Documentation/Completed/11_fr014_background_agent_execution.md` | Completed | TDD implementation plan for FR.014 background agent execution |
| `Documentation/Completed/12_plugin_support_implementation.md` | Completed | Plugin support implementation plan for Archon |
| `Documentation/Completed/12_tool_visibility_in_session_history_and_chat.md` | Completed | Tool visibility in session history and chat |
| `Documentation/Completed/13_epic0_project_setup.md` | Completed | Epic 0 — initial project scaffolding and config loading |
| `Documentation/Completed/14_epic1_ai_module.md` | Completed | Epic 1 — Claude SDK session, event mapping, truncation, and session management |
| `Documentation/Completed/15_epic2_chat_module.md` | Completed | Epic 2 — Telegram bot, whitelist, message handler, commands, and command menu |
| `Documentation/Completed/16_epic3_gateway.md` | Completed | Epic 3 — gateway orchestration and graceful shutdown |
| `Documentation/Completed/17_epic4_daemon.md` | Completed | Epic 4 — logging, daemon deployment, and daily log rotation |
| `Documentation/Completed/18_epic5_integration_tests.md` | Completed | Epic 5 — integration and end-to-end tests across all layers |
| `Documentation/Completed/19_epic6_skills.md` | Completed | Epic 6 — skills integration and live skill loader test |
| `Documentation/Completed/20_epic7_history.md` | Completed | Epic 7 — chat history persistence in structured Markdown files |
| `Documentation/Completed/21_epic8_notification_modes.md` | Completed | Epic 8 — notification mode redesign with four verbosity levels |
| `Documentation/Completed/22_epic9_model_management.md` | Completed | Epic 9 — runtime model selection via /model command |
| `Documentation/Completed/23_epic11_context_subagents.md` | Completed | Epic 11 — context window tracking, sub-agent team configuration, and per-agent notification control |
| `Documentation/Completed/24_epic12_agent_loader.md` | Completed | Epic 12 — filesystem-based agent loader reading ~/.claude/agents/*.md |
| `Documentation/Completed/25_epic14_session_diagnostics.md` | Completed | Epic 14 — session state tracking, diagnostics, and enhanced /status command |
| `Documentation/Completed/09_qmd_compatible_history_format.md` | Completed | Archived ADR — QMD-compatible history format (superseded by ADR 09 RAG) |
| `Documentation/Completed/26_search_integration_research.md` | Completed | FEAT-019 RAG integration research and implementation plan |
| `Documentation/UserManual/user_manual.md` | UserManual | End-user guide for Telegram bot commands and features |
| `Documentation/UserManual/cli_reference.md` | UserManual | Reference guide for the `archon` CLI management tool |
| `Documentation/UserManual/schedule_guide.md` | UserManual | Comprehensive guide to Archon's scheduled jobs feature |
| `Documentation/UserManual/search_guide.md` | UserManual | Comprehensive guide to Search — installation, configuration, collections, and CLI reference |

---

## Adding a New Document

Follow these five steps every time you add a document to `Documentation/`.

### Step 1 — Choose the right directory

| Content type | Directory |
|---|---|
| System design, architecture diagrams, operational guides | `Documentation/Architecture/` |
| Architecture Decision Records | `Documentation/ADRs/` |
| Planned features and user stories not yet implemented | `Documentation/Backlog/` |
| Completed implementation plans and finished stories | `Documentation/Completed/` |
| End-user-facing guides and command references | `Documentation/UserManual/` |
| Developer onboarding and product roadmap | `Documentation/` (root) |

### Step 2 — Apply the naming convention

| Directory | Pattern | Example |
|---|---|---|
| `Architecture/` | `NNN_snake_case_name.md` | `180_caching_strategy.md` |
| `ADRs/` | `NN_descriptive_name.md` | `10_use_redis_for_cache.md` |
| `Backlog/` | `NN_descriptive_name.md` | `02_s17_1_context_compaction.md` |
| `Completed/` | `NN_descriptive_name.md` | `13_fr005_context_compaction.md` |
| `UserManual/` | `snake_case.md` | `admin_guide.md` |
| Root | `snake_case.md` | `changelog.md` |

Choose the next available number prefix within the directory. Check existing files before assigning a number to avoid collisions.

### Step 3 — Add the required metadata header

Every document must open with this 5-line block (no heading before it):

```markdown
**Purpose**: [One sentence describing what this document covers]
**Audience**: [Who should read this — e.g., Backend engineers, All developers]
**Status**: [Draft | Stable | Deprecated | Active]
**Last reviewed**: YYYY-MM-DD
**Next review**: YYYY-MM-DD
```

Set `Last reviewed` to today's date. Set `Next review` according to the review cycle in the **Review Schedule** section below.

### Step 4 — Update this index

Add a row to the **Documentation Map** table above. Keep rows in the same order: root files first, then `Architecture/` in numeric order, then `ADRs/`, `Backlog/`, `Completed/`, `UserManual/`. Update `Last reviewed` and `Next review` on this file's metadata header too.

### Step 5 — Follow content standards

- Start Architecture documents with 3–5 key principles before diving into details.
- Use Mermaid for all diagrams (`flowchart`, `sequenceDiagram`, `classDiagram`, `erDiagram`).
- Cross-reference related documents contextually — link to them instead of repeating content.
- Target medior developers (2–5 years experience) as the default audience.
- Use active voice and present tense throughout.

---

## Review Schedule

| Document type | Review frequency | Next review due |
|---|---|---|
| Architecture docs (`Architecture/`) | Quarterly | 2026-05-26 |
| ADRs (`ADRs/`) | As needed (when superseded or revisited) | — |
| Technical specs, backlog items | Bi-annually | 2026-08-26 |
| Process docs, user manual | Annually | 2027-02-26 |
| This index (`990_...`) | Quarterly (with Architecture docs) | 2026-05-26 |

During each review:

1. Verify the content still reflects the current implementation.
2. Update the `Last reviewed` and `Next review` dates on the document.
3. Check all cross-references for broken links.
4. Remove or deprecate content that no longer applies.
5. Update this index if the file was moved, renamed, or its purpose changed.
