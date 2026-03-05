# Installer Robustness and Resilience Plan

## Objective
Implement a transactional installer/update flow that prioritizes service continuity.

Target behavior:
- New install either completes cleanly or leaves no partial active state.
- Update never destroys the currently working installation on transient errors.
- Version cutover happens only after candidate verification.
- Failed cutover triggers automatic rollback to the previously working version.
- User-facing output is actionable and recovery-oriented (not only fatal exit).

## Scope
In scope:
- `install.py` transactional update/install orchestration.
- Health-gated activation using `GET /health`.
- Auto-rollback on failed activation.
- Retry/backoff for transient network/dependency failures.
- Expanded installer unit tests in `tests/test_installer_py.py`.

Out of scope (for this iteration):
- Remote telemetry or automatic bug report uploads.
- Full daemon lifecycle redesign.
- Cross-platform service managers beyond current macOS launchd support.

## Current Problems (Verified)
1. Update path can remove active app on `uv sync` failure.
2. Install/update stages are not transactional.
3. Failure handling is primarily "exit with error" without continuity guarantees.
4. Rollback model is implicit/incomplete (no explicit previous-version restoration step).

## Design Principles
1. Continuity first: keep current service running until candidate is proven healthy.
2. Transactional cutover: stage -> verify -> activate -> confirm.
3. Automatic rollback: on activation failure, restore last-known-good version.
4. Idempotent operations: rerunning installer should converge to safe state.
5. Clear recovery messaging: always show next actions and log location.
6. KISS: minimal moving parts, explicit filesystem states.

## Proposed Filesystem State Model
Under `~/.archon/`:
- `app/` -> active version currently used by service.
- `app.candidate/` -> staging area for install/update preparation.
- `app.previous/` -> backup of prior active version during cutover.
- `archon.log` -> service log.

Optional metadata file (recommended):
- `install_state.toml` with fields like:
  - `active_version`
  - `candidate_version`
  - `last_operation`
  - `last_error`
  - `updated_at`

Note: metadata is helpful but not required for transactional correctness.

## End-to-End Flow

### A) Fresh Install
1. Validate prerequisites.
2. Collect config (interactive/non-interactive).
3. Prepare directories.
4. Stage code into `app.candidate/` (clone pinned tag).
5. Run dependency installation in `app.candidate/` with retries.
6. Render and validate launchd plist using `app.candidate/` paths.
7. Stop existing service only right before activation (if any).
8. Activate atomically:
   - if `app/` exists, move to `app.previous/`.
   - move `app.candidate/` -> `app/`.
9. Register/load service.
10. Health-check with retries.
11. If healthy:
   - remove `app.previous/`.
   - emit success summary.
12. If unhealthy:
   - rollback to `app.previous/` (if exists).
   - restart service and verify health.
   - emit degraded success (rollback completed) or explicit manual recovery path.

### B) Update
1. Validate prerequisites.
2. Read existing config.
3. Stage update into `app.candidate/`:
   - clone target tag into candidate (preferred simple path), or
   - copy active app then fetch/checkout target tag.
4. Run dependency installation in candidate with retries.
5. Preflight candidate sanity checks.
6. Perform atomic cutover + health verification.
7. Auto-rollback on failure.
8. Keep prior active app intact if candidate preparation fails.

## Atomicity and Rollback Mechanics
Use filesystem rename semantics on same filesystem:
- `app` -> `app.previous`
- `app.candidate` -> `app`

Rules:
1. Never delete `app` during candidate preparation failure.
2. During activation, keep `app.previous` until health passes.
3. On rollback, reverse rename:
   - stop service
   - remove broken `app`
   - `app.previous` -> `app`
   - start service
4. Cleanup `app.candidate` and stale `app.previous` only after successful stabilization.

## Failure Taxonomy and Handling

### Candidate preparation failures
Examples:
- clone/fetch failure
- `uv sync` failure
- plist/template validation failure

Handling:
- Preserve active `app` and service state.
- Report: "Update not applied; existing version remains active."
- Print logs/diagnostics and retry guidance.

### Activation failures
Examples:
- `launchctl load` failure
- health endpoint timeout/non-200

Handling:
- Trigger automatic rollback.
- Retry service start once during rollback path.
- If rollback succeeds: report safe fallback to previous version.
- If rollback fails: keep artifacts and print explicit manual recovery commands.

### Unrecoverable filesystem failures
Examples:
- rename permission issues
- partial moves due to external interference

Handling:
- Stop mutation immediately.
- Emit exact filesystem state and required manual remediation steps.

## Retry and Backoff Strategy
Apply bounded retries for transient operations:
- git network operations
- `uv sync`
- health checks

Defaults:
- attempts: 3
- initial delay: 1s
- multiplier: 2x
- max delay: 8s

Do not retry deterministic validation failures (invalid tag format, malformed env input).

## User-Facing Output Contract
For each failure class, output:
1. Stage that failed.
2. What was preserved (e.g., existing service untouched).
3. What was rolled back (if applicable).
4. Exact next command(s) user can run.
5. Log file path.

Example update failure message:
- "Dependency installation failed in candidate. Existing Archon version is still running."
- "Inspect logs: tail -f ~/.archon/archon.log"
- "Retry update: uv run <installer> --update --tag X.Y.Z"

## Implementation Plan (Phased)

### Phase 1: Transaction Core (minimum safe baseline)
1. Add path helpers for `app`, `app.candidate`, `app.previous`.
2. Refactor install/update orchestration to always stage in candidate.
3. Replace destructive rollback (`rmtree(app)`) with state-aware handling.
4. Implement atomic activation function with rollback support.
5. Preserve current active app on any pre-activation failure.

Acceptance criteria:
- Update path never deletes active app when candidate build fails.
- Successful install/update ends with healthy active app.

### Phase 2: Health-Gated Cutover + Auto-Rollback
1. Add explicit `activate_candidate()` + `rollback_previous()` functions.
2. Integrate health verification after service load.
3. Auto-rollback on failed health.
4. Emit degraded success when rollback succeeds.

Acceptance criteria:
- Forced health failure restores previous version automatically.
- Final user message clearly indicates active version source (new vs previous).

### Phase 3: Retries + Diagnostics
1. Add retry wrapper utility with exponential backoff.
2. Apply to git, uv sync, and health checks.
3. Standardize error summaries with stage and remediation commands.

Acceptance criteria:
- Transient failures recover without user intervention when possible.
- Error outputs are actionable and specific.

### Phase 4: Optional Metadata/Recovery Helpers
1. Add `install_state.toml` updates at each stage.
2. Add startup reconciliation for stale states (`app.candidate` leftover, etc.).

Acceptance criteria:
- Interrupted installer runs can be resumed or safely cleaned up.

## Test Plan (TDD)
Add/adjust tests in `tests/test_installer_py.py`.

Core tests:
1. Update + `uv sync` failure keeps `app/` intact.
2. Fresh install + candidate `uv sync` failure does not activate broken app.
3. Activation health failure triggers rollback to previous app.
4. Rollback path failure yields explicit remediation output.
5. Candidate success + health success promotes candidate to active app.
6. Retries are attempted for transient failures and stop at max attempts.
7. Cleanup rules: `app.previous` removed only after successful health check.

Test style:
- Mock `subprocess.run`, `verify_running`, and filesystem side effects via `tmp_path`.
- Assert filesystem state transitions explicitly (`app`, `app.candidate`, `app.previous`).
- Assert user-facing messages for continuity guarantees.

## Refactor Outline (Functions)
Proposed internal functions in `install.py`:
- `_paths(archon_home) -> dataclass` with app dirs.
- `_prepare_candidate(tag, paths, ...)`
- `_sync_candidate(paths, ...)`
- `_activate_candidate(paths, ...)`
- `_verify_service_health(...)`
- `_rollback_activation(paths, ...)`
- `_cleanup_post_success(paths, ...)`
- `_run_with_retry(callable, policy, stage_name)`

## Risks and Mitigations
1. Risk: rename semantics differ across filesystems.
- Mitigation: keep all dirs under same `~/.archon` root.

2. Risk: launchctl state drift if load/unload races.
- Mitigation: explicit unload/load sequence, check return codes, bounded retries.

3. Risk: stale candidate/previous from interrupted runs.
- Mitigation: startup reconciliation rules and explicit cleanup policy.

4. Risk: broader refactor introduces regressions.
- Mitigation: phase-by-phase delivery with tests first for each behavior change.

## Rollout Strategy
1. Implement Phase 1 + tests.
2. Implement Phase 2 + tests.
3. Run targeted installer tests.
4. Run full test suite to satisfy project quality gates.
5. Ship with concise release notes documenting rollback semantics.

## Definition of Done
1. No destructive update behavior on transient failures.
2. Automatic rollback on failed activation/health checks.
3. Installer messages always include continuity status and next actions.
4. New tests cover major failure paths and pass.
5. Existing relevant tests continue to pass.
