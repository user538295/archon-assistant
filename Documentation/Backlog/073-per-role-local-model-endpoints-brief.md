# FEAT-016 — Per-role local model endpoint support
**Purpose**: Allow each internal session role (user session, orchestrator, classifier, compactor, background agents, scheduled jobs) to use a different model and/or a local inference server (LM Studio, Ollama, LiteLLM proxy) independently of one another, configured entirely in `config.toml`.
**Audience**: Power users who want to run some or all workloads on local hardware; developers experimenting with alternative models per role.
**Status**: To Do

---

## Background

The Claude Agent SDK spawns a `claude` CLI subprocess that reads `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` from the process environment.  LM Studio ≥ 0.4.1 ships an Anthropic-compatible `/v1/messages` endpoint, so pointing `ANTHROPIC_BASE_URL` at `http://localhost:1234` is sufficient to redirect any SDK call to a local model.

Today all sessions use the same `models.default` model and the global `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` env vars.  There is no way to send the classifier to a fast local model while keeping the orchestrator on Sonnet, or to route history compaction to a cheap local model without affecting the interactive session.

This feature introduces a `ModelEndpointConfig` dataclass and optional `[models.endpoints.<role>]` TOML sections so each role can independently override `model`, `base_url`, and `api_key`.  The env-var injection uses `_ENV_LOCK` defined in the new shared module `archon/ai/sdk_env.py` — the same lock is acquired by both `ClaudeSession.start()` and `HistorySummarizer._get_client()` so concurrent calls remain race-free.

---

## Goal

After this feature, a user can add any combination of `[models.endpoints.<role>]` blocks to `config.toml`.  Each block accepts `model`, `base_url`, and `api_key` — all optional.  When a role has a `base_url`, its sessions temporarily set `ANTHROPIC_BASE_URL` (and optionally `ANTHROPIC_API_KEY`) in `os.environ` under `_ENV_LOCK` during `connect()`, then restore the original values.  Roles without endpoint overrides continue to behave exactly as before.

---

## Scope

### In Scope
- `ModelEndpointConfig` dataclass: `model`, `base_url`, `api_key` (all optional)
- `ModelsEndpointsConfig` container with one field per role: `session`, `orch_session`, `classifier`, `compactor`, `background_agents`, `schedule`
- Config loader parsing for `[models.endpoints.*]` sections
- New shared module `archon/ai/sdk_env.py` containing `_ENV_LOCK`, `_get_env_lock()`, `_apply_endpoint_env`, `_restore_endpoint_env`
- `ClaudeSession` accepts and applies a `ModelEndpointConfig` (env injection under shared `_ENV_LOCK` from `sdk_env`)
- `HistorySummarizer` accepts and applies a `ModelEndpointConfig` (same env injection pattern, same shared lock)
- `Classifier`, `Decomposer`, `BackgroundAgentManager`, `JobScheduler` each wired to their role's endpoint config
- `Pipeline` distributes endpoint configs to `Classifier` and `Decomposer`
- `SessionManager` reads all endpoint configs from config and injects them into every consumer
- `config.toml.example` and `CLAUDE.md` updated
- Full TDD: all new logic covered before implementation

### Out of Scope
- Dynamic runtime switching of endpoints via Telegram commands (future work)
- Validation that the local model supports Claude's tool-use format
- Per-user endpoint overrides (single global config only)
- Windows platform differences (env patching is OS-agnostic here)

---

## Acceptance criteria
- [ ] `ModelEndpointConfig(model=None, base_url=None, api_key=None)` is importable from `archon.config`
- [ ] `[models.endpoints.session]` with `base_url = "http://localhost:1234"` causes only the main session to inject `ANTHROPIC_BASE_URL` during `connect()`; other roles are unaffected
- [ ] Each of the six roles can independently override `model`, `base_url`, and `api_key`
- [ ] Roles without an endpoint block fall back to existing behaviour (no regression)
- [ ] Two concurrent `ClaudeSession.start()` calls with different `base_url` values do not race (locked by shared `_ENV_LOCK` from `archon.ai.sdk_env`)
- [ ] Concurrent `ClaudeSession.start()` and `HistorySummarizer._get_client()` with different endpoints also serialize correctly under the same shared lock
- [ ] `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` are always restored to their pre-call values after `connect()`, even on exception
- [ ] `HistorySummarizer` applies its endpoint config using the same env-restore pattern
- [ ] When `base_url` is set and `api_key` is None, the existing `ANTHROPIC_API_KEY` is suppressed (popped) during `connect()` so it is not sent to the local server
- [ ] All existing tests pass without modification
- [ ] New tests achieve ≥ 85 % coverage on all changed files
- [ ] `config.toml.example` documents every `[models.endpoints.*]` section with inline comments
- [ ] The `CLAUDE.md` Configuration section documents that `endpoint.model` takes precedence over the `/model` runtime command for the configured role

---

## What does NOT change
- `ClaudeAgentOptions` — not modified (no `base_url` param added to it)
- `_ENV_LOCK` lazy-initialization pattern — preserved and moved to `archon/ai/sdk_env.py`; all callers use the same single instance via `_get_env_lock()`
- `CLAUDECODE` stripping logic — untouched
- `SessionManager._model` setter and `/model` Telegram command — untouched
- `models.available` / `models.default` semantics — unchanged
- All existing `ClaudeSession`, `Classifier`, `Decomposer`, `BackgroundAgentManager`, `JobScheduler` constructor signatures remain backward-compatible (new params are keyword-only with `None` default)

---

## Known limitations / accepted trade-offs
- Env injection is process-global during the `connect()` window despite the lock. If a future change bypasses `_ENV_LOCK`, two concurrent starts with different `base_url` values could interfere. Accepted: the lock pattern is already established in the codebase.
- No validation that the local model supports all Claude tools. If it doesn't, sessions will fail at runtime with SDK errors. Accepted: this is a power-user feature.
- `ModelEndpointConfig.api_key` is stored in plaintext in `config.toml`. For local models this is typically a dummy value (e.g. `"lmstudio"`), so the security risk is low. Accepted for MVP.
- When `api_key` is omitted but `base_url` is set, the existing `ANTHROPIC_API_KEY` is popped from the environment during `connect()` so it is not sent to the local server. This is intentional.
- The Decomposer's internal `_summary_session` always uses `_SUMMARIZER_MODEL` regardless of `orch_endpoint.model`. To override the summarizer model, users must wait for a future dedicated `[models.endpoints.decomposer_summary]` config role (out of scope for this feature).

---

## Architecture

### New module: `archon/config/model_endpoint.py`
Single-responsibility: the `ModelEndpointConfig` and `ModelsEndpointsConfig` dataclasses.

```python
@dataclass
class ModelEndpointConfig:
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

@dataclass
class ModelsEndpointsConfig:
    session: ModelEndpointConfig = field(default_factory=ModelEndpointConfig)
    orch_session: ModelEndpointConfig = field(default_factory=ModelEndpointConfig)
    classifier: ModelEndpointConfig = field(default_factory=ModelEndpointConfig)
    compactor: ModelEndpointConfig = field(default_factory=ModelEndpointConfig)
    background_agents: ModelEndpointConfig = field(default_factory=ModelEndpointConfig)
    schedule: ModelEndpointConfig = field(default_factory=ModelEndpointConfig)
```

### New module: `archon/ai/sdk_env.py`
Single-responsibility: the shared async lock and env-injection helpers used by all session types.

- `_ENV_LOCK: asyncio.Lock | None = None` — module-level lock variable (lazy, initially None)
- `_get_env_lock() -> asyncio.Lock` — Lazy accessor — creates `asyncio.Lock()` on first call, then caches it. This avoids `'bound to a different event loop'` errors across pytest runs where each test may get a fresh event loop. Matches the lazy pattern already used in the existing `claude_session.py`.
- `_apply_endpoint_env(ep: ModelEndpointConfig) -> dict[str, str | None]` — sets `ANTHROPIC_BASE_URL` and/or `ANTHROPIC_API_KEY` in `os.environ`; returns `{key: previous_value_or_None}` for restore. When `ep.base_url` is not None: saves and sets `ANTHROPIC_BASE_URL`. When `ep.api_key` is not None: saves and sets `ANTHROPIC_API_KEY`. When `ep.base_url` is not None but `ep.api_key` is None: **pops** the existing `ANTHROPIC_API_KEY` from env (saving it for restore) so the real Anthropic key is NOT sent to the local server — the local server receives no Authorization header from the SDK. Only modifies env vars when `ep.base_url` is not None (for `ANTHROPIC_BASE_URL`) or `ep.api_key` is not None (for `ANTHROPIC_API_KEY`). A model-only `ModelEndpointConfig` causes zero env mutations.
- `_restore_endpoint_env(saved: dict[str, str | None]) -> None` — restores previously saved env values (sets or deletes each key)
- Both `claude_session.py` and `history_compactor.py` import from this module — no circular dependency since `sdk_env.py` imports only from `archon.config`

### `archon/config/loader.py` changes
`ModelsConfig` gains:
```python
endpoints: ModelsEndpointsConfig = field(default_factory=ModelsEndpointsConfig)
```

New helper `_parse_endpoint(section: dict) -> ModelEndpointConfig` reads `model`, `base_url`, `api_key` from a TOML sub-table.

### `archon/ai/claude_session.py` changes
`ClaudeSession.__init__` gains keyword-only:
```python
endpoint: ModelEndpointConfig | None = None
```
`self._endpoint = endpoint or ModelEndpointConfig()`

`ClaudeSession.start()` — inside `async with _get_env_lock():` (imported from `archon.ai.sdk_env`), after popping `CLAUDECODE`, also calls `_apply_endpoint_env` and `_restore_endpoint_env`. The helpers `_apply_endpoint_env` and `_restore_endpoint_env` are imported from `archon.ai.sdk_env` (not defined in `claude_session.py`).

**Model resolution** — replace the existing `model=self._model` argument in the `ClaudeAgentOptions(...)` constructor call (currently at line 195 of `claude_session.py`, inside `start()` but before the lock acquisition) with `model=self._endpoint.model or self._model`. This is the only change needed for model override — it does not need to be inside the lock, since it reads instance attributes only.

**Note**: The `ClaudeAgentOptions` object is built before `async with _get_env_lock()`. The model resolution goes there, not inside the lock block.

### `archon/ai/history_compactor.py` changes
`HistoryCompactor.__init__` gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint`. When constructing `HistorySummarizer`, passes `endpoint=self._endpoint`. `HistoryCompactor` does not itself perform any env manipulation — it only forwards the config.

`HistorySummarizer.__init__` gains:
```python
endpoint: ModelEndpointConfig | None = None
```
`_get_client()` applies the same `_apply_endpoint_env` / `_restore_endpoint_env` pattern (imported from `archon.ai.sdk_env`) inside the existing `CLAUDECODE` try/finally. `HistorySummarizer._get_client()` must also acquire `_get_env_lock()` (imported from `archon.ai.sdk_env`) using `async with _get_env_lock():` wrapping the entire `CLAUDECODE` pop + endpoint env injection + `connect()` window.

### `archon/ai/classifier.py` changes
`Classifier.__init__` gains:
```python
endpoint: ModelEndpointConfig | None = None
```
Passed as `endpoint=self._endpoint` when constructing each `ClaudeSession`.

### `archon/ai/decomposer.py` changes
`Decomposer.__init__` gains two keyword-only params:
```python
session_endpoint: ModelEndpointConfig | None = None
orch_endpoint: ModelEndpointConfig | None = None
```
Internal `_summary_session` (lazy): uses `_SUMMARIZER_MODEL` for its model (not `orch_endpoint.model`), but inherits the same `base_url` and `api_key` from `orch_endpoint` so it routes to the same local server. Constructed with `endpoint=ModelEndpointConfig(base_url=orch_endpoint.base_url, api_key=orch_endpoint.api_key)` if `orch_endpoint` is not None, else `endpoint=None`.

This design ensures the summary session stays on the cheap fast model (`_SUMMARIZER_MODEL`) regardless of the orch model choice, while still routing to the same local inference server when one is configured.

Both `_reset_router_if_needed()` and `_reset_summary_if_needed()` null their respective sessions and rely on the lazy `_ensure_*` methods to recreate them. Since `self._orch_endpoint` is stored on the Decomposer instance, the recreated sessions automatically receive the correct endpoint — no additional reset-path changes needed.

### `archon/ai/background_agent_manager.py` changes
`BackgroundAgentManager.__init__` gains:
```python
endpoint: ModelEndpointConfig | None = None
```
Passed as `endpoint=self._endpoint` when constructing `ClaudeSession` in `_run_agent()`.

### `archon/ai/job_scheduler.py` changes
`JobScheduler.__init__` gains:
```python
endpoint: ModelEndpointConfig | None = None
```
Passed as `endpoint=self._endpoint` when constructing `ClaudeSession` in `_run_job()`.

### `archon/ai/pipeline.py` changes
`Pipeline.__init__` gains three keyword-only params (not four):
```python
classifier_endpoint: ModelEndpointConfig | None = None
session_endpoint: ModelEndpointConfig | None = None
orch_endpoint: ModelEndpointConfig | None = None
```
The Decomposer's internal `_summary_session` inherits only `base_url` and `api_key` from `orch_endpoint` (not the model), so `summary_endpoint` is not a separate parameter. Distributes `classifier_endpoint` to `Classifier` and `session_endpoint` + `orch_endpoint` to `Decomposer`.

### `archon/ai/session_manager.py` changes
Reads `config.models.endpoints` and passes endpoint configs to `Pipeline` (`classifier_endpoint`, `session_endpoint`, `orch_endpoint`), `BackgroundAgentManager`, and `JobScheduler`. The `compactor` endpoint is wired in `gateway.py` (see Task 5.2).

**Model precedence rule**: `endpoint.model` (from `config.toml`) takes priority over the runtime `/model` command. Specifically:
- `/model <name>` sets `SessionManager._model`, which flows to `Pipeline(model=...)` → `Decomposer(model=...)` → `ClaudeSession(model=self._model)`
- At `ClaudeSession.start()`, model resolution is `self._endpoint.model or self._model`, so endpoint model always wins if non-None
- Consequence: if `[models.endpoints.session] model = "local-llm"` is configured, the `/model` command has no effect on the main session
- This must be documented in the user manual (add to Task 6.2)

**Note**: verify whether `HistoryCompactor` is instantiated in `gateway.py` directly or through `SessionManager`. If instantiated in `gateway.py`, the endpoint must also be passed there. Task 5.2 must include wiring both construction sites.

### New config keys (`examples/config.toml.example`)
```toml
# Optional per-role endpoint overrides.
# Any field omitted falls back to the global default.
# Set base_url to route a role to a local inference server (LM Studio, Ollama, LiteLLM).

# [models.endpoints.session]
# model = "llama-3.2-3b-instruct"   # model name as registered in LM Studio
# base_url = "http://localhost:1234"
# api_key = "lmstudio"              # Set to any non-empty string for local servers; omit to suppress the real ANTHROPIC_API_KEY during this role's connect()

# [models.endpoints.orch_session]
# model = "claude-sonnet-4-6"       # keep orchestrator on Sonnet

# [models.endpoints.classifier]
# model = "qwen2.5-7b-instruct"
# base_url = "http://localhost:1234"
# api_key = "lmstudio"              # Set to any non-empty string for local servers; omit to suppress the real ANTHROPIC_API_KEY during this role's connect()

# [models.endpoints.compactor]
# model = "llama-3.2-3b-instruct"
# base_url = "http://localhost:1234"
# api_key = "lmstudio"              # Set to any non-empty string for local servers; omit to suppress the real ANTHROPIC_API_KEY during this role's connect()

# [models.endpoints.background_agents]
# model = "claude-sonnet-4-6"

# [models.endpoints.schedule]
# model = "llama-3.2-3b-instruct"
# base_url = "http://localhost:1234"
# api_key = "lmstudio"              # Set to any non-empty string for local servers; omit to suppress the real ANTHROPIC_API_KEY during this role's connect()
```

---

## Tests

- **test_model_endpoint_config_defaults** (unit): all fields default to None
- **test_models_endpoints_config_all_roles** (unit): all six role fields present and default to empty `ModelEndpointConfig`
- **test_parse_endpoint_full** (unit): `_parse_endpoint` with all three keys populated
- **test_parse_endpoint_partial** (unit): `_parse_endpoint` with only `base_url` set
- **test_parse_endpoint_empty** (unit): `_parse_endpoint` with empty dict returns default
- **test_loader_endpoints_section** (unit): `_load_models_config` parses `[models.endpoints.session]` into correct dataclass
- **test_loader_endpoints_missing** (unit): missing `[models.endpoints]` section yields default `ModelsEndpointsConfig`
- **test_apply_endpoint_env_sets_base_url** (unit): sets `ANTHROPIC_BASE_URL`; returns old value for restore
- **test_apply_endpoint_env_no_base_url** (unit): when `base_url=None`, env unchanged
- **test_apply_endpoint_env_pops_api_key_when_base_url_set_without_api_key** (unit): when `base_url` is set and `api_key` is None, the existing `ANTHROPIC_API_KEY` is removed from env during connect and restored after
- **test_restore_endpoint_env_restores** (unit): restores old value correctly
- **test_restore_endpoint_env_deletes_when_not_set** (unit): key deleted if it was absent before
- **test_restore_endpoint_env_empty_dict_noop** (unit): `_restore_endpoint_env({})` is a no-op; env unchanged
- **test_apply_endpoint_env_api_key_only_no_base_url** (unit): when only `api_key` is set, `ANTHROPIC_API_KEY` is set and `ANTHROPIC_BASE_URL` is untouched
- **test_claude_session_start_injects_base_url** (unit): start() sets env during connect then restores
- **test_claude_session_start_no_endpoint** (unit): start() without endpoint does not touch ANTHROPIC_BASE_URL
- **test_claude_session_endpoint_model_overrides** (unit): endpoint.model takes precedence over session model param
- **test_claude_session_start_restores_on_exception** (unit): env restored even when connect() raises
- **test_claude_session_model_only_endpoint_no_env_mutation** (unit): `ModelEndpointConfig(model="local-llm")` — env untouched, `ClaudeAgentOptions` receives the model
- **test_concurrent_start_different_base_urls** (integration): two concurrent start() calls serialise correctly under `_ENV_LOCK`
- **test_cross_component_concurrent_env_isolation** (integration): concurrent `ClaudeSession.start(endpoint_A)` + `HistorySummarizer._get_client(endpoint_B)` serialize under the shared lock without env leakage
- **test_history_summarizer_endpoint_env_injection** (unit): _get_client() applies endpoint env and restores
- **test_history_summarizer_no_endpoint** (unit): no env modification when endpoint is None
- **test_history_summarizer_external_client_ignores_endpoint** (unit): when an external client is provided, `_get_client()` returns it without env mutation, even if `endpoint.base_url` is set
- **test_history_compactor_forwards_endpoint_to_summarizer** (unit): HistorySummarizer constructed with correct endpoint
- **test_history_compactor_no_endpoint_default** (unit): no endpoint → HistorySummarizer with endpoint=None
- **test_classifier_passes_endpoint_to_session** (unit): ClaudeSession constructed with correct endpoint
- **test_classifier_reset_preserves_endpoint** (unit): after session reset, new ClaudeSession still has same endpoint
- **test_decomposer_session_endpoint** (unit): main session uses session_endpoint
- **test_decomposer_orch_endpoint** (unit): router session uses orch_endpoint
- **test_decomposer_summary_session_inherits_orch_connection_params** (unit): summary session receives `ModelEndpointConfig` with `base_url` and `api_key` from `orch_endpoint`, but `model=None` so `_SUMMARIZER_MODEL` is used
- **test_decomposer_summary_session_orch_model_not_inherited** (unit): when `orch_endpoint.model = "some-opus"`, summary session still uses `_SUMMARIZER_MODEL` (not "some-opus")
- **test_decomposer_orch_session_reset_preserves_endpoint** (unit): after `_reset_router_if_needed()` forces a reset, newly created router session still receives `orch_endpoint`
- **test_decomposer_summary_session_reset_preserves_orch_endpoint** (unit): after summary session reset, newly created summary session still receives `orch_endpoint`
- **test_background_agent_manager_passes_endpoint** (unit): spawned ClaudeSession has correct endpoint
- **test_job_scheduler_passes_endpoint** (unit): job session constructed with correct endpoint
- **test_pipeline_distributes_endpoints** (unit): correct endpoints forwarded to Classifier and Decomposer
- **test_session_manager_wires_all_endpoints** (integration): SessionManager reads config.endpoints and injects into Pipeline, BAM, and JobScheduler
- **test_gateway_wires_compactor_endpoint** (integration, `tests/gateway/test_gateway.py`): gateway constructs `HistoryCompactor` with `config.models.endpoints.compactor`
- **test_doctor_valid_base_url_passes** (unit): well-formed `base_url` produces no doctor warning
- **test_doctor_invalid_base_url_warns** (unit): malformed `base_url` produces a doctor warning
- **test_doctor_no_endpoints_configured_passes** (unit): all-default endpoints produce no doctor warning
- **test_doctor_multiple_invalid_base_urls_all_reported** (unit): two roles with bad URLs both appear in doctor output

---

## Documentation update
- [ ] `examples/config.toml.example`, section `[models]`: add all six commented `[models.endpoints.*]` blocks with inline explanations
- [ ] `CLAUDE.md`, section `Configuration`: add `[models.endpoints.<role>]` entry with field list and description
- [ ] `archon/cli/doctor.py`: add `base_url` format validation for all six endpoint roles

---

## Task breakdown

### Phase 1 — Config dataclasses and loader
> **Releasable**: after Task 1.3 — config can be loaded with endpoint sections present; all other code unchanged.

#### Task 1.1 — `ModelEndpointConfig` and `ModelsEndpointsConfig` dataclasses
- [ ] **File**: `archon/config/model_endpoint.py` *(new)*
- **Depends on**: nothing
- **Description**:
  - `ModelEndpointConfig(model: str | None = None, base_url: str | None = None, api_key: str | None = None)` — plain `@dataclass`
  - `ModelsEndpointsConfig` — `@dataclass` with six fields: `session`, `orch_session`, `classifier`, `compactor`, `background_agents`, `schedule`, each `ModelEndpointConfig` with `field(default_factory=ModelEndpointConfig)`
  - Both exported from `archon/config/__init__.py` alongside existing exports
  - No business logic — pure data containers
- **Releasable**: importable from `archon.config` after this task
- **Tests (TDD)** — `tests/config/test_model_endpoint.py`:
  - Unit: `test_model_endpoint_config_defaults` — all three fields default to None
  - Unit: `test_models_endpoints_config_has_all_roles` — verify all six role attributes present
  - Unit: `test_models_endpoints_config_role_defaults` — each role is a default `ModelEndpointConfig`
  - Unit: `test_model_endpoint_config_equality` — two default instances compare equal
  - Checkpoint: `uv run pytest tests/config/test_model_endpoint.py -v`

#### Task 1.2 — `_parse_endpoint` helper in config loader
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: Task 1.1
- **Description**:
  - Module-level private function `_parse_endpoint(section: dict[str, Any]) -> ModelEndpointConfig`
  - Reads keys `model`, `base_url`, `api_key` from dict; ignores unknown keys; all default to `None` if absent
  - No type coercion beyond `str | None`
  - Called by Task 1.3
- **Releasable**: helper callable; no external API yet
- **Tests (TDD)** — `tests/config/test_loader.py` (append to existing file):
  - Unit: `test_parse_endpoint_full` — all three keys present → all fields populated
  - Unit: `test_parse_endpoint_partial` — only `base_url` set → others None
  - Unit: `test_parse_endpoint_empty` — empty dict → `ModelEndpointConfig()` with all None
  - Unit: `test_parse_endpoint_ignores_unknown_keys` — extra keys do not raise
  - Checkpoint: `uv run pytest tests/config/test_loader.py -k "parse_endpoint" -v`

#### Task 1.3 — Wire `ModelsEndpointsConfig` into `ModelsConfig` and loader
- [ ] **File**: `archon/config/loader.py`
- **Depends on**: Task 1.1, Task 1.2
- **Description**:
  - Add `endpoints: ModelsEndpointsConfig = field(default_factory=ModelsEndpointsConfig)` to `ModelsConfig` dataclass
  - In `_load_models_config` (or equivalent), after loading `available` / `default`, look for `[models.endpoints]` TOML sub-table
  - For each of the six role names (`session`, `orch_session`, `classifier`, `compactor`, `background_agents`, `schedule`), call `_parse_endpoint(endpoints_table.get(role, {}))` and assign to `ModelsEndpointsConfig`
  - If `[models.endpoints]` section is absent entirely, `ModelsEndpointsConfig` is all-default (no change to existing behaviour)
- **Releasable**: `config.models.endpoints.session.base_url` accessible after loading
- **Tests (TDD)** — `tests/config/test_loader.py` (append):
  - Unit: `test_loader_endpoints_section_parsed` — TOML with `[models.endpoints.session]` block → correct `ModelEndpointConfig`
  - Unit: `test_loader_endpoints_partial_roles` — only `classifier` block present → only classifier populated, others default
  - Unit: `test_loader_endpoints_missing_entirely` — no `[models.endpoints]` → all roles default
  - Unit: `test_loader_endpoints_all_six_roles` — all six role blocks in TOML → all six populated correctly
  - Checkpoint: `uv run pytest tests/config/test_loader.py -k "endpoints" -v`

---

### Phase 2 — Env-injection helpers and ClaudeSession wiring
> **Releasable**: after Task 2.2 — `ClaudeSession` can be constructed with an endpoint override; start() injects env vars correctly.

#### Task 2.1 — `_apply_endpoint_env` / `_restore_endpoint_env` helpers and `_ENV_LOCK`
- [ ] **File**: `archon/ai/sdk_env.py` *(new)*
- **Depends on**: Task 1.1
- **Description**:
  - Module-level (private):
    ```python
    _ENV_LOCK: asyncio.Lock | None = None

    def _get_env_lock() -> asyncio.Lock:
        """Lazy accessor — creates asyncio.Lock() on first call, then caches it."""
        global _ENV_LOCK
        if _ENV_LOCK is None:
            _ENV_LOCK = asyncio.Lock()
        return _ENV_LOCK

    def _apply_endpoint_env(ep: ModelEndpointConfig) -> dict[str, str | None]:
        """Set ANTHROPIC_BASE_URL and/or ANTHROPIC_API_KEY in os.environ.
        Returns {key: previous_value_or_None} for restore."""

    def _restore_endpoint_env(saved: dict[str, str | None]) -> None:
        """Restore or delete keys saved by _apply_endpoint_env."""
    ```
  - `_apply_endpoint_env`: if `ep.base_url` is not None, saves current `os.environ.get("ANTHROPIC_BASE_URL")` and sets it. If `ep.api_key` is not None, saves and sets `ANTHROPIC_API_KEY`. If `ep.base_url` is not None and `ep.api_key` is None, **pops** `ANTHROPIC_API_KEY` (saving it for restore) so it is not sent to the local server. Returns dict of saved values. Only modifies env vars when `ep.base_url` is not None or `ep.api_key` is not None — a model-only `ModelEndpointConfig` causes zero env mutations.
  - `_restore_endpoint_env`: iterates saved dict; if saved value is None, deletes key from env (if present); otherwise sets it back.
  - Both are pure imperative helpers; no async, no logging.
  - Both `claude_session.py` and `history_compactor.py` import from this module.
- **Releasable**: helpers callable; not yet used in start()
- **Tests (TDD)** — `tests/ai/test_sdk_env.py` *(new)*:
  - Unit: `test_apply_endpoint_env_sets_base_url` — sets `ANTHROPIC_BASE_URL`, returns old value
  - Unit: `test_apply_endpoint_env_sets_api_key` — sets `ANTHROPIC_API_KEY`, returns old value
  - Unit: `test_apply_endpoint_env_no_base_url` — when `base_url=None`, `ANTHROPIC_BASE_URL` not touched
  - Unit: `test_apply_endpoint_env_returns_none_for_absent_key` — returns None for keys not originally present
  - Unit: `test_apply_endpoint_env_pops_api_key_when_base_url_set_without_api_key` — when `base_url` is set and `api_key` is None, existing `ANTHROPIC_API_KEY` is removed from env during connect and restored after
  - Unit: `test_restore_endpoint_env_restores_value` — key set back to old string
  - Unit: `test_restore_endpoint_env_deletes_absent_key` — key deleted from env when original was None
  - Unit: `test_restore_endpoint_env_empty_dict_noop` — `_restore_endpoint_env({})` is a no-op; env unchanged
  - Unit: `test_apply_endpoint_env_api_key_only_no_base_url` — when only `api_key` is set (no `base_url`), `ANTHROPIC_API_KEY` is set and `ANTHROPIC_BASE_URL` is untouched
  - Checkpoint: `uv run pytest tests/ai/test_sdk_env.py -v`

#### Task 2.2 — Wire endpoint into `ClaudeSession.__init__` and `start()`
- [ ] **File**: `archon/ai/claude_session.py`
- **Depends on**: Task 2.1
- **Description**:
  - Remove the existing `_ENV_LOCK` variable, `_get_env_lock()` function definition, and their associated comment block from `claude_session.py` (currently at lines 27-38). Replace with imports from `archon.ai.sdk_env`:
    ```python
    from archon.ai.sdk_env import _get_env_lock, _apply_endpoint_env, _restore_endpoint_env
    ```
    This ensures there is exactly ONE lock instance shared by all callers.
  - `__init__` signature gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint = endpoint or ModelEndpointConfig()`
  - In `start()`, inside `async with _get_env_lock():`, after saving and popping `claudecode`, apply endpoint env and restore in LIFO order:
    ```python
    async with _get_env_lock():
        claudecode = os.environ.pop("CLAUDECODE", None)
        _saved_ep = _apply_endpoint_env(self._endpoint)
        try:
            await self._client.connect()
        finally:
            _restore_endpoint_env(_saved_ep)   # restore endpoint first
            if claudecode is not None:
                os.environ["CLAUDECODE"] = claudecode  # then restore CLAUDECODE
    ```
  - Restore order is intentionally LIFO — endpoint vars restored before CLAUDECODE to avoid any window where CLAUDECODE is present but base_url is stale.
  - **Model resolution** — replace the existing `model=self._model` argument in the `ClaudeAgentOptions(...)` constructor call (currently at line 195 of `claude_session.py`, inside `start()` but before the lock acquisition) with `model=self._endpoint.model or self._model`. This is the only change needed for model override — it does not need to be inside the lock, since it reads instance attributes only.
  - **Note**: The `ClaudeAgentOptions` object is built before `async with _get_env_lock()`. The model resolution goes there, not inside the lock block.
  - The existing `ClaudeSDKClient(options=options)` line unchanged; only the model value passed to options may differ.
  - All existing constructor params unchanged (backward-compatible addition)
- **Releasable**: `ClaudeSession(endpoint=ModelEndpointConfig(base_url="http://localhost:1234"))` works end-to-end
- **Tests (TDD)** — `tests/ai/test_claude_session.py` (append):
  - Unit: `test_claude_session_start_injects_base_url` — mock connect(); assert env var set during call, restored after
  - Unit: `test_claude_session_start_no_endpoint` — env untouched when endpoint is None
  - Unit: `test_claude_session_endpoint_model_overrides_session_model` — endpoint.model takes precedence
  - Unit: `test_claude_session_start_restores_env_on_exception` — env restored even when connect() raises
  - Integration: `test_concurrent_start_different_base_urls` — two concurrent start() calls via asyncio.gather do not leak each other's base_url
  - Unit: `test_claude_session_model_only_endpoint_no_env_mutation` — `ModelEndpointConfig(model="local-llm")` passed to `ClaudeSession`; `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` are untouched during `start()`, while `ClaudeAgentOptions` receives `model="local-llm"`
  - Checkpoint: `uv run pytest tests/ai/test_claude_session.py -k "endpoint" -v`

---

### Phase 3 — HistorySummarizer endpoint support
> **Releasable**: after Task 3.1 (and Task 3.0) — history compaction can use a local model.

#### Task 3.0 — Add `endpoint` parameter to `HistoryCompactor.__init__`
- [ ] **File**: `archon/ai/history_compactor.py`
- **Depends on**: Task 1.1
- **Description**:
  - `HistoryCompactor.__init__` gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint`
  - When constructing `HistorySummarizer`, pass `endpoint=self._endpoint`
  - `HistoryCompactor` does not itself perform any env manipulation — it only forwards the config
- **Releasable**: `HistoryCompactor(model=..., endpoint=ModelEndpointConfig(base_url="..."))` correctly forwards endpoint to `HistorySummarizer`
- **Tests (TDD)** — `tests/ai/test_history_compactor.py`:
  - Unit: `test_history_compactor_forwards_endpoint_to_summarizer` — HistorySummarizer constructed with correct endpoint
  - Unit: `test_history_compactor_no_endpoint_default` — no endpoint → HistorySummarizer with endpoint=None
  - Checkpoint: `uv run pytest tests/ai/test_history_compactor.py -k "compactor" -v`

#### Task 3.1 — Wire endpoint into `HistorySummarizer`
- [ ] **File**: `archon/ai/history_compactor.py`
- **Depends on**: Task 1.1, Task 2.1, Task 3.0
- **Description**:
  - Import `_get_env_lock`, `_apply_endpoint_env`, `_restore_endpoint_env` from `archon.ai.sdk_env` (not from `archon.ai.claude_session`)
  - `HistorySummarizer.__init__` gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint = endpoint or ModelEndpointConfig()`
  - Model resolution: `model = self._endpoint.model or self._model`; used in `ClaudeAgentOptions(model=model, ...)`
  - In `_get_client()`, `HistorySummarizer._get_client()` must also acquire `_get_env_lock()` (imported from `archon.ai.sdk_env`) using `async with _get_env_lock():` wrapping the entire `CLAUDECODE` pop + endpoint env injection + `connect()` window:
    ```python
    async with _get_env_lock():
        claudecode = os.environ.pop("CLAUDECODE", None)
        _saved_ep = _apply_endpoint_env(self._endpoint)
        try:
            await self._cached_client.connect()
        except BaseException:
            try:
                await self._cached_client.disconnect()
            except Exception:
                pass
            self._cached_client = None
            raise
        finally:
            _restore_endpoint_env(_saved_ep)   # restore endpoint first (LIFO)
            if claudecode is not None:
                os.environ["CLAUDECODE"] = claudecode
    ```
  - The existing `except BaseException` cleanup block (disconnect + set `self._cached_client = None`) must be preserved inside the lock scope. Without it, a failed `connect()` leaves `_cached_client` in a broken non-None state, causing subsequent `_get_client()` calls to skip creation and return the disconnected client.
  - Restore is in `finally` so it runs even when connect() raises; existing error-cleanup path (`self._cached_client = None`) remains
  - **External client bypass**: When `HistorySummarizer` is constructed with an explicit `client` parameter (dependency injection for testing), `_get_client()` returns the external client immediately without acquiring the lock or calling `connect()`. In this case, the `endpoint` config is intentionally ignored — the external client is assumed to already be connected to the correct endpoint. This must be documented (not guarded with an error) since the test suite relies on injecting external mock clients.
- **Releasable**: `HistorySummarizer(model=..., endpoint=ModelEndpointConfig(base_url="..."))` routes compaction to local model
- **Tests (TDD)** — `tests/ai/test_history_compactor.py` (append):
  - Unit: `test_history_summarizer_endpoint_env_injected` — env set during connect, restored after
  - Unit: `test_history_summarizer_no_endpoint` — env not modified when endpoint=None
  - Unit: `test_history_summarizer_endpoint_model_override` — ClaudeAgentOptions receives endpoint.model
  - Unit: `test_history_summarizer_endpoint_restore_on_exception` — env restored when connect() raises
  - Unit: `test_history_summarizer_external_client_ignores_endpoint` — when an external client is provided, `_get_client()` returns it without env mutation, even if `endpoint.base_url` is set
  - Integration: `test_cross_component_concurrent_env_isolation` — concurrent `ClaudeSession.start(endpoint_A)` + `HistorySummarizer._get_client(endpoint_B)` serialize under the shared lock without env leakage
  - Checkpoint: `uv run pytest tests/ai/test_history_compactor.py -k "endpoint" -v`

---

### Phase 4 — Per-role wiring (Classifier, Decomposer, BAM, JobScheduler)
> **Releasable**: after Task 4.4 — all six roles accept independent endpoint configs.

#### Task 4.1 — Wire endpoint into `Classifier`
- [ ] **File**: `archon/ai/classifier.py`
- **Depends on**: Task 1.1, Task 2.2
- **Description**:
  - `Classifier.__init__` gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint`
  - Passed as `endpoint=self._endpoint` when constructing each `ClaudeSession(model=_CLASSIFIER_MODEL, endpoint=self._endpoint)`
  - Model resolution: if `self._endpoint.model` is set, it overrides `_CLASSIFIER_MODEL`; otherwise `_CLASSIFIER_MODEL` used (preserving existing default)
  - Session reset path (every 50 calls) also passes `endpoint=self._endpoint` to the new session
- **Releasable**: Classifier can use a local model independently
- **Tests (TDD)** — `tests/ai/test_classifier.py` (append):
  - Unit: `test_classifier_passes_endpoint_to_session` — ClaudeSession constructed with correct endpoint
  - Unit: `test_classifier_no_endpoint_uses_fast_model` — default behaviour preserved
  - Unit: `test_classifier_reset_preserves_endpoint` — new session after reset also has correct endpoint
  - Checkpoint: `uv run pytest tests/ai/test_classifier.py -k "endpoint" -v`

#### Task 4.2 — Wire two endpoints into `Decomposer`
- [ ] **File**: `archon/ai/decomposer.py`
- **Depends on**: Task 1.1, Task 2.2
- **Description**:
  - `Decomposer.__init__` gains two keyword-only params:
    - `session_endpoint: ModelEndpointConfig | None = None`
    - `orch_endpoint: ModelEndpointConfig | None = None`
  - Main `ClaudeSession` (line ~90-102): pass `endpoint=session_endpoint`
  - Router `ClaudeSession` (lazy, line ~190-233): pass `endpoint=orch_endpoint`
  - Summary `ClaudeSession` (lazy, line ~235-248): pass `endpoint=ModelEndpointConfig(base_url=orch_endpoint.base_url, api_key=orch_endpoint.api_key)` to inherit connection routing from orch_endpoint without overriding `_SUMMARIZER_MODEL`. If `orch_endpoint` is None, pass `endpoint=None`.
  - Both params default to `None` → existing behaviour preserved
  - Both `_reset_router_if_needed()` and `_reset_summary_if_needed()` null their respective sessions and rely on the lazy `_ensure_*` methods to recreate them. Since `self._orch_endpoint` is stored on the Decomposer instance, the recreated sessions automatically receive the correct endpoint — no additional reset-path changes needed.
- **Releasable**: `_session` independently configurable; `_orch_session` uses `orch_endpoint` in full; `_summary_session` inherits only `base_url`/`api_key` from `orch_endpoint`
- **Tests (TDD)** — `tests/ai/test_decomposer.py` (append):
  - Unit: `test_decomposer_main_session_endpoint` — session constructed with `session_endpoint`
  - Unit: `test_decomposer_orch_session_endpoint` — router session uses `orch_endpoint`
  - Unit: `test_decomposer_summary_session_inherits_orch_connection_params` — summary session receives `ModelEndpointConfig` with `base_url` and `api_key` from `orch_endpoint`, but `model=None` so `_SUMMARIZER_MODEL` is used
  - Unit: `test_decomposer_summary_session_orch_model_not_inherited` — when `orch_endpoint.model = "some-opus"`, summary session still uses `_SUMMARIZER_MODEL` (not "some-opus")
  - Unit: `test_decomposer_no_endpoints_defaults` — all sessions created without endpoint when params absent
  - Unit: `test_decomposer_orch_session_reset_preserves_endpoint` — after `_reset_router_if_needed()` forces a reset, newly created router session still receives `orch_endpoint`
  - Unit: `test_decomposer_summary_session_reset_preserves_orch_endpoint` — after summary session reset, newly created summary session still receives `orch_endpoint`
  - Checkpoint: `uv run pytest tests/ai/test_decomposer.py -k "endpoint" -v`

#### Task 4.3 — Wire endpoint into `BackgroundAgentManager`
- [ ] **File**: `archon/ai/background_agent_manager.py`
- **Depends on**: Task 1.1, Task 2.2
- **Description**:
  - `BackgroundAgentManager.__init__` gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint`
  - In `_run_agent()` (line ~343): `ClaudeSession(model=self._model, endpoint=self._endpoint, ...)`
- **Releasable**: background agents can use a local model independently
- **Tests (TDD)** — `tests/ai/test_background_agent_manager.py` (append):
  - Unit: `test_bam_passes_endpoint_to_session` — spawned ClaudeSession has `endpoint=self._endpoint`
  - Unit: `test_bam_no_endpoint_default` — no endpoint → ClaudeSession with `endpoint=None`
  - Checkpoint: `uv run pytest tests/ai/test_background_agent_manager.py -k "endpoint" -v`

#### Task 4.4 — Wire endpoint into `JobScheduler`
- [ ] **File**: `archon/ai/job_scheduler.py`
- **Depends on**: Task 1.1, Task 2.2
- **Description**:
  - `JobScheduler.__init__` gains keyword-only: `endpoint: ModelEndpointConfig | None = None`; stored as `self._endpoint`
  - In `_run_job()` (line ~484): `ClaudeSession(model=self._model, cwd=self._cwd, endpoint=self._endpoint)`
- **Releasable**: scheduled jobs can use a local model independently
- **Tests (TDD)** — `tests/schedule/test_job_scheduler.py` (append):
  - Unit: `test_scheduler_passes_endpoint_to_session` — job session has `endpoint=self._endpoint`
  - Unit: `test_scheduler_no_endpoint_default` — no endpoint → ClaudeSession with `endpoint=None`
  - Checkpoint: `uv run pytest tests/schedule/test_job_scheduler.py -k "endpoint" -v`

---

### Phase 5 — Pipeline and SessionManager integration
> **Releasable**: after Task 5.2 — full end-to-end wiring from `config.toml` to each session role.

#### Task 5.1 — Distribute endpoints through `Pipeline`
- [ ] **File**: `archon/ai/pipeline.py`
- **Depends on**: Task 4.1, Task 4.2
- **Description**:
  - `Pipeline.__init__` gains three keyword-only params:
    - `classifier_endpoint: ModelEndpointConfig | None = None`
    - `session_endpoint: ModelEndpointConfig | None = None`
    - `orch_endpoint: ModelEndpointConfig | None = None`
  - Pass `classifier_endpoint` to `Classifier(endpoint=...)`
  - Pass `session_endpoint` and `orch_endpoint` to `Decomposer(...)` — no `summary_endpoint` (Decomposer reuses `orch_endpoint` internally for `_summary_session`)
- **Releasable**: Pipeline can be constructed with per-role endpoints
- **Tests (TDD)** — `tests/ai/test_pipeline.py` (append):
  - Unit: `test_pipeline_forwards_classifier_endpoint` — Classifier constructed with correct endpoint
  - Unit: `test_pipeline_forwards_session_and_orch_endpoints` — Decomposer constructed with `session_endpoint` and `orch_endpoint`
  - Unit: `test_pipeline_no_endpoints_defaults` — existing tests unaffected
  - Checkpoint: `uv run pytest tests/ai/test_pipeline.py -k "endpoint" -v`

#### Task 5.2 — Wire all endpoints through `SessionManager`
- [ ] **Files**: `archon/ai/session_manager.py`, `archon/gateway/gateway.py`
- **Depends on**: Task 1.3, Task 3.0, Task 3.1, Task 4.3, Task 4.4, Task 5.1
- **Description**:
  - `SessionManager` reads `self._config.models.endpoints` once during initialisation (or on `start()`)
  - Passes `classifier_endpoint`, `session_endpoint`, `orch_endpoint` to `Pipeline`
  - Passes `background_agents` endpoint to `BackgroundAgentManager`
  - Passes `schedule` endpoint to `JobScheduler`
  - All pass-throughs pass the `ModelEndpointConfig` as-is (regardless of which fields are set). The env manipulation in `_apply_endpoint_env` only touches env vars when `base_url` or `api_key` is non-None — a model-only config passes the model through `ClaudeAgentOptions` without any env manipulation.
  - **`HistoryCompactor` is constructed in `gateway.py` line 512, not in `SessionManager`.** This task must modify `gateway.py` to pass `config.models.endpoints.compactor` to the `HistoryCompactor(...)` constructor. `SessionManager` receives `HistoryCompactor` as a dependency injection — it does not create it.
- **Releasable**: full end-to-end — set `[models.endpoints.classifier]` in config.toml and it flows to the Classifier session
- **Tests (TDD)** — `tests/ai/test_session_manager.py` (append) and `tests/gateway/test_gateway.py` (append):
  - Integration: `test_session_manager_wires_session_endpoint` — config with session endpoint → Pipeline receives it
  - Integration: `test_session_manager_wires_classifier_endpoint` — config with classifier endpoint → Classifier receives it
  - Integration: `test_session_manager_wires_bam_endpoint` — config with background_agents endpoint → BAM receives it
  - Integration: `test_session_manager_wires_schedule_endpoint` — config with schedule endpoint → JobScheduler receives it
  - Integration: `test_session_manager_no_endpoints_config` — missing `[models.endpoints]` → all defaults, no regression
  - Integration (`tests/gateway/test_gateway.py`): `test_gateway_wires_compactor_endpoint` — gateway constructs `HistoryCompactor` with `config.models.endpoints.compactor`
  - Checkpoint: `uv run pytest tests/ai/test_session_manager.py tests/gateway/test_gateway.py -k "endpoint" -v`

---

### Phase 6 — Documentation
> **Releasable**: after Task 6.2 — feature fully documented.

#### Task 6.1 — Update `config.toml.example`
- [ ] **File**: `examples/config.toml.example`
- **Depends on**: Task 5.2
- **Description**:
  - Add a new sub-section after `[models]` with all six commented `[models.endpoints.*]` blocks
  - Each block shows all three fields commented out with a one-line explanation
  - Add a header comment explaining the purpose: local LM Studio / Ollama / LiteLLM proxy usage
  - Include a "quick-start" comment: `# Set ANTHROPIC_BASE_URL globally instead to affect all roles at once`
- **Releasable**: users can copy examples from the template
- **Tests (TDD)**: none (docs only)
- Checkpoint: `grep -c 'models.endpoints' examples/config.toml.example`

#### Task 6.2 — Update `CLAUDE.md` configuration section
- [ ] **File**: `CLAUDE.md`
- **Depends on**: Task 6.1
- **Description**:
  - In the `## Configuration` section, add `[models.endpoints.<role>]` entry listing `model`, `base_url`, `api_key` fields and the six valid role names
  - Add one-sentence description: "Per-role endpoint overrides; set `base_url` to route a role to a local inference server."
  - Keep addition minimal — one bullet point per role is sufficient
  - Add a note to the Configuration section explaining the model precedence rule: `endpoint.model` (config) overrides the runtime `/model` command for its role.
- **Releasable**: CLAUDE.md reflects the new config keys
- **Tests (TDD)**: none (docs only)
- Checkpoint: `grep 'endpoints' CLAUDE.md`

#### Task 6.3 — Add `base_url` validation to `archon doctor`
- [ ] **File**: `archon/cli/doctor.py`
- **Depends on**: Task 1.3
- **Description**:
  - In the `doctor` pre-flight checks, add a new check: for each of the six roles in `config.models.endpoints`, if `base_url` is set, validate it is a well-formed URL (starts with `http://` or `https://`, parseable by `urllib.parse.urlparse` with a non-empty `netloc`)
  - On failure, emit a warning (not a hard error): `"[models.endpoints.<role>] base_url '{value}' does not look like a valid URL"`
  - Do NOT attempt to connect to the URL — validation is format-only at doctor time
  - Iterate all six roles; report all invalid values in a single pass
- **Releasable**: `archon doctor` catches `base_url = "htpp://localhost:1234"` typos at config-check time
- **Tests (TDD)** — `tests/cli/test_doctor.py` (append):
  - Unit: `test_doctor_valid_base_url_passes` — `base_url = "http://localhost:1234"` produces no warning
  - Unit: `test_doctor_invalid_base_url_warns` — `base_url = "htpp://localhost:1234"` produces a warning
  - Unit: `test_doctor_no_endpoints_configured_passes` — all-default `ModelsEndpointsConfig` produces no warning
  - Unit: `test_doctor_multiple_invalid_base_urls_all_reported` — two roles with bad URLs both appear in output
  - Checkpoint: `uv run pytest tests/cli/test_doctor.py -k "base_url" -v`
