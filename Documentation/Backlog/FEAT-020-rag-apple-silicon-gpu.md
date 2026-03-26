# FEAT-020 — RAG Apple Silicon GPU Acceleration
**Purpose**: Extend the RAG installer to automatically detect Apple Silicon (ARM64 macOS), configure `CoreMLExecutionProvider`, validate that acceleration actually works, and fall back gracefully to CPU if it does not.
**Audience**: Archon operators on Apple Silicon Macs (M1/M2/M3/M4) who want faster embedding and reranking without manual configuration.
**Status**: To Do

---

## Prerequisites

**Before implementation begins**, verify the foundational assumption: the standard `fastembed` wheel ships ONNX Runtime with CoreML support on macOS ARM64.

**Verification step** (run on an Apple Silicon Mac):
```bash
pip install fastembed==0.7.4 && python -c "import onnxruntime; print(onnxruntime.get_available_providers())"
```

Expected output must include `"CoreMLExecutionProvider"`. If it is absent, `install_deps("apple_silicon")` must install `onnxruntime-coreml` or a specific onnxruntime build — the "no extra packages needed" assumption is wrong and the scope of this feature must be revised before implementation.

This check must be performed before any code is written.

---

## Background

The current GPU detection in `archon/rag/install.py:detect_gpu()` runs `nvidia-smi` and returns a bool — it only handles NVIDIA CUDA. On Apple Silicon, `nvidia-smi` is absent so the installer silently uses CPU. fastembed (ONNX Runtime) supports `CoreMLExecutionProvider` on macOS ARM64, routing inference through the Apple Neural Engine and GPU — no extra packages needed. The standard `fastembed` wheel already ships ONNX Runtime with CoreML support on macOS (verified per Prerequisites above).

The `providers` field in `RagConfig` is already wired through `ModelEmbedder` and `ModelReranker` to `fastembed`'s `TextEmbedding(providers=...)`. Setting it to `["CoreMLExecutionProvider"]` is all the runtime needs. The gap is: (1) the installer never sets this for Apple Silicon, and (2) there is no validation that CoreML actually works on the user's OS version — it silently degrades to CPU if CoreML fails, which is fine at runtime but leaves the operator unaware.

"Fully supported" means: auto-detect → configure → validate → fallback if broken → report outcome clearly.

## Goal

When `archon rag install` runs on an Apple Silicon Mac, the installer detects the platform, validates that CoreML works, writes `providers = ["CoreMLExecutionProvider"]` only on confirmed success, and logs whether GPU acceleration is working. If validation fails, providers are NOT written to config (CPU remains the default) and a warning is logged. The user never needs to touch `config.toml` manually. Intel Macs, Linux, and Windows are unaffected.

---

## Scope

### In Scope
- `detect_gpu()` extended to return a `Literal["cuda", "apple_silicon", "none"]` typed string
- Apple Silicon detection placed in `archon/platform/` behind `PlatformRuntime.detect_gpu_type()`; `archon/rag/install.py` calls the platform layer — no `sys.platform` / `platform.machine()` checks in `archon/rag/`
- `configure_providers()` extended to write `CoreMLExecutionProvider` on Apple Silicon
- Post-configure validation: run a test embed with the configured providers; if CoreML is not the active provider or embed raises, fall back to CPU and warn
- `install_deps()` uses standard `fastembed>=0.7.4` for Apple Silicon (no new packages)
- Unit tests for all new detection, configuration, and validation paths
- `rag_guide.md` updated with an Apple Silicon section

### Out of Scope
- Metal Performance Shaders (MPS) via PyTorch — Archon uses ONNX Runtime, not PyTorch
- `onnxruntime-silicon` separate wheel — not required; CoreML is in the standard wheel
- Benchmarking or performance measurement
- Windows ARM support
- Validating specific ONNX operator coverage per model

---

## Acceptance criteria
- [x] `detect_gpu_type()` is added to `PlatformRuntime` ABC in `archon/platform/`; macOS implementation returns `"apple_silicon"` on ARM64; Linux/Windows return `"none"` or `"cuda"` as appropriate
- [x] `detect_gpu()` in `archon/rag/install.py` delegates to `get_runtime().detect_gpu_type()` — no `sys.platform` or `platform.machine()` calls in `archon/rag/`
- [x] `detect_gpu()` returns `"apple_silicon"` on macOS ARM64; `"cuda"` when `nvidia-smi` succeeds; `"none"` otherwise
- [ ] `configure_providers()` writes `providers = ["CoreMLExecutionProvider"]` for `"apple_silicon"` in `config.toml`
- [ ] Post-install validation confirms `CoreMLExecutionProvider` is in `onnxruntime.get_available_providers()` AND embeds one text without exception
- [ ] CoreML validation confirms the provider is active, not just that embedding succeeds
- [ ] On CoreML validation failure, providers are NOT written to config (validate-before-write) and a warning is logged
- [ ] `install_deps("apple_silicon")` installs `fastembed>=0.7.4` only — `fastembed-gpu` is never called
- [ ] CUDA path is unchanged: `nvidia-smi` success → `fastembed-gpu` + `CUDAExecutionProvider`
- [ ] CPU path is unchanged: `"none"` → standard `fastembed`, no providers key written
- [ ] All existing tests pass; new unit tests cover all Apple Silicon paths
- [ ] `rag_guide.md` documents Apple Silicon detection, validation, and the manual override

---

## What does NOT change
- `ModelEmbedder`, `ModelReranker` — `providers` is already passed through correctly
- `RagConfig.providers` field and config loading — already supports arbitrary provider lists
- CUDA install path behaviour
- MCP server, pipeline, store, chunker, parser, reranker

---

## Known limitations / accepted trade-offs
- CoreML validation uses the configured embedding model; first run downloads model weights (~150 MB). This is the same download that would happen at first real use — no extra cost.
- ONNX Runtime CoreML fallback to CPU is silent at runtime after install. If CoreML breaks on an OS update, the user won't notice until they check `archon rag status` or re-run the installer. Accepted: adding a runtime health check is a separate concern.
- macOS 12+ is required for full CoreML support. On macOS 11, CoreML may partially work or silently fall back. The validation step will catch failures, but the macOS version is not checked explicitly.
- **CoreML validation tests only the embedding model.** The reranker model (`TextCrossEncoder`) also uses `providers` but is not validated. If CoreML fails for the reranker model, it will silently fall back to CPU at runtime without a warning. Post-install validation embeds one text with the configured provider using the embedding model only; reranker CoreML support is not validated.
- **Pre-implementation verification required**: Confirm `onnxruntime.get_available_providers()` includes `"CoreMLExecutionProvider"` on macOS ARM64 with the exact `onnxruntime` version shipped by `fastembed>=0.7.4` (see Prerequisites section). If absent, additional packages are required and this spec must be updated.
- **Linux ARM64 gets no special treatment**: Linux ARM64 machines (Raspberry Pi, Graviton) use the Linux `detect_gpu_type()` path, which checks for CUDA only. CoreML is Apple-specific and cannot activate on Linux regardless of architecture. This is correct and intentional.
- **`onnxruntime` import may differ from fastembed's internal copy**: `validate_providers()` calls `import onnxruntime` and `onnxruntime.get_available_providers()`. If fastembed vendors or bundles its own onnxruntime, this import may resolve to a different installation than what fastembed uses internally, and `get_available_providers()` results may differ. If this is a concern, the verification step in Prerequisites will surface it.

---

## Architecture

### Platform layer: `detect_gpu_type()` in `archon/platform/`

Per project constraint, all platform-specific code must live in `archon/platform/` — no `sys.platform` or `platform.machine()` checks elsewhere. Apple Silicon detection is placed on the `PlatformRuntime` ABC:

```python
# archon/platform/types.py (alongside ServiceInfo)
from typing import Literal
GpuType = Literal["cuda", "apple_silicon", "none"]
```

```python
# archon/platform/__init__.py (ABC addition)
class PlatformRuntime(ABC):
    ...
    @abstractmethod
    def detect_gpu_type(self) -> GpuType:
        """Detect GPU type available on this platform."""
        ...
```

macOS implementation (`archon/platform/macos/runtime.py`):
```python
import platform
from typing import Literal

def detect_gpu_type(self) -> Literal["cuda", "apple_silicon", "none"]:
    # MacRuntime is only instantiated on darwin; no CUDA on macOS
    if platform.machine() == "arm64":
        return "apple_silicon"
    return "none"
```

Linux implementation: run `nvidia-smi` with `timeout=5`; catch `FileNotFoundError`, `subprocess.TimeoutExpired`, and non-zero returncode — return `"cuda"` or `"none"`.
Windows implementation: return `"none"` (service management not fully supported; GPU detection out of scope).

**Detection order rationale**: Apple Silicon is checked before CUDA. On an Apple Silicon Mac, CUDA cannot function even if `nvidia-smi` is somehow installed; returning `"cuda"` would trigger `fastembed-gpu` installation which will fail or produce broken behaviour.

### `detect_gpu() -> Literal["cuda", "apple_silicon", "none"]` in `archon/rag/install.py`

`archon/rag/install.py` delegates entirely to the platform layer:

```python
from typing import Literal
from archon.platform import get_runtime

GpuType = Literal["cuda", "apple_silicon", "none"]

def detect_gpu(self) -> GpuType:
    """Detect GPU type by delegating to the platform layer."""
    return get_runtime().detect_gpu_type()
```

No `sys.platform`, `platform.machine()`, or `subprocess` calls in `archon/rag/install.py`.

**API boundary note**: `detect_gpu()` returns one of three string literals. Callers must compare against string values explicitly (e.g., `if gpu == "apple_silicon":`). Do not use truthy/falsy checks — `"none"` is a non-empty string and is truthy in Python.

### `install_deps(gpu: Literal["cuda", "apple_silicon", "none"])` in `archon/rag/install.py`
- `"cuda"`: unchanged — uninstall `fastembed`, install `fastembed-gpu>=0.7.4` + `onnxruntime-gpu`
- `"apple_silicon"` and `"none"`: install standard `fastembed>=0.7.4` (identical)

### `configure_providers(gpu: Literal["cuda", "apple_silicon", "none"])` in `archon/rag/install.py`
- `"cuda"`: write `["CUDAExecutionProvider"]` — unchanged
- `"apple_silicon"`: write `["CoreMLExecutionProvider"]`
- `"none"`: no-op

**Replace semantics**: `configure_providers()` always writes a full replacement of the providers list (not an append). If the user previously had `["CUDAExecutionProvider"]` and detection now returns `"apple_silicon"`, the list is replaced with `["CoreMLExecutionProvider"]`. The idempotency guard only skips the write when the target provider is already the first/sole entry.

**Idempotency guard**: check whether the TARGET provider (based on gpu type) is already present in the providers list, not just whether `CUDAExecutionProvider` is set. For `"apple_silicon"`: skip if `"CoreMLExecutionProvider"` already present. For `"cuda"`: skip if `"CUDAExecutionProvider"` already present. The guard must not clobber user-extended provider chains (e.g., `["CoreMLExecutionProvider", "CPUExecutionProvider"]`).

### `validate_providers(providers: list[str]) -> bool` in `archon/rag/install.py`

New method. Checks two conditions:
1. `"CoreMLExecutionProvider"` (or the requested provider) is in `onnxruntime.get_available_providers()` — confirms ONNX Runtime actually loaded the provider, not just that no exception occurred.
2. A single short string embeds without exception.

ONNX Runtime silently falls back to `CPUExecutionProvider` if `CoreMLExecutionProvider` fails to initialize — no exception is raised. Checking only that embedding succeeds would make validation theater. The provider list check is the authoritative signal.

If called with an empty list, returns `True` — CPU mode is always available.

```python
def validate_providers(self, providers: list[str]) -> bool:
    """Return True if providers are active and a test embed succeeds, False otherwise."""
    if not providers:
        return True  # empty providers = CPU mode, always valid
    try:
        import onnxruntime
        available = onnxruntime.get_available_providers()
        for p in providers:
            if p != "CPUExecutionProvider" and p not in available:
                logger.warning("Provider %s not available in onnxruntime (available: %s)", p, available)
                return False
        from fastembed import TextEmbedding  # noqa: PLC0415
        model = TextEmbedding(self.cfg.embedding_model, providers=providers)
        list(model.embed(["archon rag test"]))
        return True
    except Exception as exc:
        logger.warning("Provider validation failed: %s", exc)
        return False
```

### Updated `run()` flow

Validate BEFORE writing config for the Apple Silicon path. This avoids leaving a broken CoreML provider in `config.toml` if validation fails or the process is interrupted during the model download:

1. `gpu = detect_gpu()`
2. `install_deps(gpu)`
3. If `gpu == "apple_silicon"`:
   a. Call `validate_providers(["CoreMLExecutionProvider"])`
   b. On success: call `configure_providers(gpu)` to write CoreML to config, then `logger.info("CoreML acceleration validated — GPU/Neural Engine active.")` (and print success message for interactive installer output)
   c. On failure: do NOT write providers (keep CPU default), `logger.warning("CoreML validation failed — falling back to CPU. macOS 12+ required.")`
4. Else: call `configure_providers(gpu)` as before

**Output mechanism**: `logger.warning` / `logger.info` for daemon-visible log output (consistent with project constraint: all modules use `logging.getLogger("archon")`, no `print()`). The installer may additionally print user-facing messages for interactive output — if the existing installer already uses `print()` for interactive output, keep `print()` for the success/failure messages but always also emit `logger.warning` for the detailed fallback warning.

This eliminates the need for a `clear_providers()` method — config is only written on confirmed success.

`dry_run=True`: skip validation (no fastembed installed yet in dry runs).

### No new config keys
`[rag] providers` already exists.

---

## Tests

- **`test_detect_gpu_type_returns_apple_silicon_on_arm64`** (unit, platform layer): mock `platform.machine="arm64"` in `MacRuntime` → `"apple_silicon"`
- **`test_detect_gpu_type_returns_none_on_intel_mac_via_mac_runtime`** (unit, platform layer): mock `platform.machine="x86_64"` in `MacRuntime` → `"none"` (no nvidia-smi on macOS)
- **`test_detect_gpu_type_returns_cuda_on_linux`** (unit, platform layer): mock `subprocess.run` returncode 0 on Linux → `"cuda"`
- **`test_detect_gpu_type_returns_none_when_nvidia_smi_not_found`** (unit, linux platform layer): mock `subprocess.run` raising `FileNotFoundError` on Linux → `"none"`. This is the most common real-world case on non-GPU machines. macOS no longer calls `nvidia-smi` so this test is Linux-only.
- **`test_detect_gpu_type_returns_none_when_nvidia_smi_fails_nonzero`** (unit, linux platform layer): mock `subprocess.run` returncode=1 on Linux → `"none"`. Distinct from `FileNotFoundError` (binary exists but fails) and timeout.
- **`test_detect_gpu_type_returns_none_when_nvidia_smi_times_out`** (unit, linux platform layer): mock `subprocess.run` raising `subprocess.TimeoutExpired` on Linux → `"none"`. Platform is explicitly mocked to Linux/x86_64; ARM64 check is not involved in this test.
- **`test_detect_gpu_type_returns_none_on_windows`** (unit, windows platform layer): instantiate `WindowsRuntime`, call `detect_gpu_type()`, assert `"none"` — maintaining platform test coverage parity.
- **`test_detect_gpu_delegates_to_platform_runtime`** (unit, rag layer): mock `get_runtime().detect_gpu_type()` → assert `detect_gpu()` returns the same value without calling `sys.platform` or `subprocess` directly
- **`test_detect_gpu_returns_cuda`** (unit): mock platform runtime returns `"cuda"` → `"cuda"`
- **`test_detect_gpu_returns_apple_silicon`** (unit): mock platform runtime returns `"apple_silicon"` → `"apple_silicon"`
- **`test_detect_gpu_returns_none_on_intel_mac`** (unit): mock platform runtime returns `"none"` → `"none"`
- **`test_detect_gpu_returns_none_on_linux_no_cuda`** (unit): mock platform runtime returns `"none"` → `"none"`
- **`test_install_deps_apple_silicon_installs_standard_fastembed`** (unit): assert `fastembed>=0.7.4` called; `fastembed-gpu` never called
- **`test_install_deps_cuda_still_installs_gpu_packages`** (unit): existing assertions pass with string `"cuda"`
- **`test_configure_providers_apple_silicon_writes_coreml`** (unit): config written with `["CoreMLExecutionProvider"]`
- **`test_configure_providers_cuda_unchanged`** (unit): `["CUDAExecutionProvider"]` for `"cuda"`
- **`test_configure_providers_none_is_noop`** (unit): no providers key written for `"none"`
- **`test_configure_providers_apple_silicon_idempotent_with_fallback_chain`** (unit): providers already set to `["CoreMLExecutionProvider", "CPUExecutionProvider"]` → verify no overwrite
- **`test_validate_providers_returns_true_on_empty_list`** (unit): call `validate_providers([])` → `True` (CPU mode always valid)
- **`test_validate_providers_returns_true_on_success`** (unit): mock `onnxruntime.get_available_providers()` returns list including `"CoreMLExecutionProvider"`, mock `TextEmbedding` + `embed` succeed → True
- **`test_validate_providers_returns_false_on_exception`** (unit): mock `TextEmbedding` raises → False
- **`test_validate_providers_returns_false_when_provider_not_in_available`** (unit): mock `onnxruntime.get_available_providers()` returns `["CPUExecutionProvider"]` only → False (CoreML not active)
- **`test_validate_providers_returns_false_when_onnxruntime_not_installed`** (unit): mock `import onnxruntime` raising `ModuleNotFoundError` → returns `False` (not an unhandled crash). Covers the case where onnxruntime is not yet installed when validation is called.
- **`test_validate_providers_passes_correct_providers_to_text_embedding`** (unit): assert `TextEmbedding` called with `providers=["CoreMLExecutionProvider"]`
- **`test_validate_providers_uses_configured_embedding_model`** (unit): assert `TextEmbedding` called with `self.cfg.embedding_model` as first arg
- **`test_run_flow_apple_silicon_validation_passes`** (integration): detect → `"apple_silicon"`, validate → True; assert CoreML written to config, no fallback
- **`test_run_flow_apple_silicon_validation_fails_falls_back`** (integration): detect → `"apple_silicon"`, validate → False; assert providers NOT written to config, assert `logger.warning` was called with a message matching `'CoreML'` — consistent with the project constraint that all modules use `logging.getLogger('archon')`, no `print()`
- **`test_run_flow_cuda_skips_validation`** (integration): detect → `"cuda"`, assert `validate_providers` is NOT called, `configure_providers("cuda")` is called — verifies the conditional branch is correct
- **`test_run_flow_no_gpu_unchanged`** (integration): detect → `"none"`, no validation called
- **`test_run_flow_apple_silicon_fallback_does_not_write_providers_to_config`** (integration): use a real temp `config.toml` file with no existing providers, run the fallback path, re-read the file, assert `providers` key is absent — verifying that no write occurred

---

## Documentation update
- [ ] `rag_guide.md`, section: Apple Silicon GPU Acceleration, path: `Documentation/UserManual/rag_guide.md`

---

## Task breakdown

### Phase 1 — Detection
> **Releasable only in conjunction with Phases 2 and 3** — releasing Task 1.1 alone breaks the installer because `"none"` is truthy in Python, and any caller of `install_deps(gpu: bool)` or `configure_providers(gpu: bool)` would treat `"none"` as `True`. Phases 1, 2, and 3 form an atomic release unit.

#### Task 1.1 — Add `detect_gpu_type()` to `PlatformRuntime` and extend `detect_gpu()`
- [x] **Files**: `archon/platform/types.py`, `archon/platform/__init__.py`, `archon/platform/macos/runtime.py`, `archon/platform/linux/runtime.py`, `archon/platform/windows/runtime.py`, `archon/rag/install.py`
- **Depends on**: nothing
- **Description**:
  - Add `GpuType = Literal["cuda", "apple_silicon", "none"]` to `archon/platform/types.py` (alongside `ServiceInfo`)
  - Add `detect_gpu_type(self) -> GpuType` abstract method to `PlatformRuntime` ABC
  - macOS implementation: `if platform.machine() == "arm64": return "apple_silicon"` then `return "none"` — no `nvidia-smi` call on macOS (CUDA not supported since macOS 10.14)
  - Linux implementation: check CUDA via `nvidia-smi` with `timeout=5`; catch `FileNotFoundError`, `subprocess.TimeoutExpired`, and non-zero returncode — return `"cuda"` or `"none"`
  - Windows implementation: return `"none"`
  - Change `detect_gpu(self) -> bool` to `detect_gpu(self) -> Literal["cuda", "apple_silicon", "none"]` in `archon/rag/install.py`; implementation delegates to `get_runtime().detect_gpu_type()` — no `sys.platform`, `platform.machine()`, or `subprocess` in `archon/rag/`
- **Tests (TDD)** — `tests/platform/macos/test_runtime.py`, `tests/platform/linux/test_runtime.py`, `tests/platform/windows/test_runtime.py`, `tests/rag/test_install.py`:
  - Unit: `test_detect_gpu_type_returns_apple_silicon_on_arm64`
  - Unit: `test_detect_gpu_type_returns_none_on_intel_mac_via_mac_runtime`
  - Unit: `test_detect_gpu_type_returns_none_when_nvidia_smi_not_found` (linux)
  - Unit: `test_detect_gpu_type_returns_none_when_nvidia_smi_fails_nonzero` (linux)
  - Unit: `test_detect_gpu_type_returns_none_when_nvidia_smi_times_out` (linux)
  - Unit: `test_detect_gpu_type_returns_none_on_windows`
  - Unit: `test_detect_gpu_delegates_to_platform_runtime`
  - Unit: `test_detect_gpu_returns_cuda`
  - Unit: `test_detect_gpu_returns_apple_silicon`
  - Unit: `test_detect_gpu_returns_none_on_intel_mac`
  - Unit: `test_detect_gpu_returns_none_on_linux_no_cuda`
  - Checkpoint: `uv run pytest tests/rag/test_install.py tests/platform/ -k "detect_gpu" -v`

### Phase 2 — Configuration
> **Releasable only in conjunction with Phase 3**: Tasks 2.1 and 2.2 write providers to config but do NOT yet include validation. Releasing Phase 2 alone writes `CoreMLExecutionProvider` to config without confirming it works. Phase 3 must be included in the same release. Phases 1, 2, and 3 form an atomic release unit.

#### Task 2.1 — Update `install_deps()` to accept string GPU type
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `install_deps(self, gpu: bool)` to `install_deps(self, gpu: Literal["cuda", "apple_silicon", "none"])`
  - `"cuda"`: unchanged path — uninstall `fastembed`, install `fastembed-gpu>=0.7.4` + `onnxruntime-gpu`
  - `"apple_silicon"` and `"none"`: install standard `fastembed>=0.7.4`
  - Callers must compare `gpu == "cuda"` explicitly — do not use truthy check
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_install_deps_apple_silicon_installs_standard_fastembed`
  - Unit: `test_install_deps_cuda_still_installs_gpu_packages`
  - Unit: `test_install_deps_none_installs_standard_fastembed`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "install_deps" -v`

#### Task 2.2 — Update `configure_providers()` to accept string GPU type
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `configure_providers(self, gpu: bool)` to `configure_providers(self, gpu: Literal["cuda", "apple_silicon", "none"])`
  - `"cuda"`: write `["CUDAExecutionProvider"]` (unchanged)
  - `"apple_silicon"`: write a REPLACEMENT providers list `["CoreMLExecutionProvider"]` (replaces any existing value)
  - `"none"`: no-op — do not write providers key
  - Idempotency guard: check whether the TARGET provider for the given gpu type is already present in the providers list. For `"apple_silicon"`: skip if `"CoreMLExecutionProvider"` already present. For `"cuda"`: skip if `"CUDAExecutionProvider"` already present. Do not clobber user-extended chains (e.g., `["CoreMLExecutionProvider", "CPUExecutionProvider"]`).
  - `dry_run=True`: no-op (unchanged)
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_configure_providers_apple_silicon_writes_coreml`
  - Unit: `test_configure_providers_cuda_unchanged`
  - Unit: `test_configure_providers_none_is_noop`
  - Unit: `test_configure_providers_idempotent_if_already_set`
  - Unit: `test_configure_providers_apple_silicon_idempotent_with_fallback_chain`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "configure_providers" -v`

### Phase 3 — Validation and fallback
> **Releasable**: after Task 3.2; install confirms GPU acceleration works or falls back cleanly

#### Task 3.1 — Add `validate_providers()` method
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: nothing (independent of Phase 2)
- **Description**:
  - `def validate_providers(self, providers: list[str]) -> bool`
  - First check: `onnxruntime.get_available_providers()` must include every non-CPU provider in `providers`. If any is absent, return `False` immediately — ONNX Runtime silently falls back to CPU and no exception is raised.
  - Second check: create `TextEmbedding(self.cfg.embedding_model, providers=providers)` and call `list(model.embed(["archon rag test"]))` — catches any runtime failure.
  - Returns `True` only if both checks pass.
  - Logs warning with detail on failure.
  - Never raises — caller handles fallback.
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_validate_providers_returns_true_on_empty_list`
  - Unit: `test_validate_providers_returns_true_on_success`
  - Unit: `test_validate_providers_returns_false_on_exception`
  - Unit: `test_validate_providers_returns_false_on_embed_exception`
  - Unit: `test_validate_providers_returns_false_when_provider_not_in_available`
  - Unit: `test_validate_providers_returns_false_when_onnxruntime_not_installed`
  - Unit: `test_validate_providers_passes_correct_providers_to_text_embedding`
  - Unit: `test_validate_providers_uses_configured_embedding_model`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "validate_providers" -v`

#### Task 3.2 — Wire validation-first flow into `run()`
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Task 2.1, Task 2.2, Task 3.1
- **Description**:
  - `run()` for Apple Silicon: validate BEFORE writing config (prevents broken config on interrupted install):
    1. `gpu = detect_gpu()`
    2. `install_deps(gpu)`
    3. If `gpu == "apple_silicon"`:
       a. Call `validate_providers(["CoreMLExecutionProvider"])`
       b. On success: call `configure_providers(gpu)`, print `"CoreML acceleration validated — GPU/Neural Engine active."`
       c. On failure: do NOT call `configure_providers()`, print `"Warning: CoreML validation failed — falling back to CPU. macOS 12+ required."`
    4. Else: call `configure_providers(gpu)` as before
  - No `clear_providers()` method needed — config is only written on confirmed success
  - Pass string gpu to `install_deps(gpu)` and `configure_providers(gpu)` (wires Tasks 2.1 and 2.2)
  - `dry_run=True`: skip validation (no fastembed installed yet in dry runs)
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Integration: `test_run_flow_apple_silicon_validation_passes`
  - Integration: `test_run_flow_apple_silicon_validation_fails_falls_back` — assert providers NOT written to config, assert `logger.warning` was called with a message matching `'CoreML'`
  - Integration: `test_run_flow_cuda_skips_validation`
  - Integration: `test_run_flow_no_gpu_unchanged`
  - Integration: `test_run_flow_apple_silicon_fallback_does_not_write_providers_to_config` — use a real temp `config.toml` file with no existing providers, run the fallback path, re-read the file, assert `providers` key is absent — verifying that no write occurred
  - Checkpoint: `uv run pytest tests/rag/test_install.py -v`

### Phase 4 — Documentation
> **Releasable**: after Task 4.1

#### Task 4.1 — Add Apple Silicon section to RAG user guide
- [ ] **File**: `Documentation/UserManual/rag_guide.md`
- **Depends on**: Task 3.2
- **Description**:
  - Add section "Apple Silicon GPU Acceleration" covering: auto-detection during install, validation output, what CoreML does, macOS 12+ requirement, manual override via `archon config set rag.providers '["CoreMLExecutionProvider"]'`
  - Note: CoreML falls back to CPU silently at runtime after install — re-run installer if acceleration is lost after an OS update
  - Note: validation tests the embedding model only; reranker CoreML support is not validated
- **Tests (TDD)**: N/A
- Checkpoint: `uv run pytest tests/rag/ -v`
