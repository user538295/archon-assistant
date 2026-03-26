# FEAT-020 — RAG Apple Silicon GPU Acceleration
**Purpose**: Extend the RAG installer to automatically detect Apple Silicon (ARM64 macOS), configure `CoreMLExecutionProvider`, validate that acceleration actually works, and fall back gracefully to CPU if it does not.
**Audience**: Archon operators on Apple Silicon Macs (M1/M2/M3/M4) who want faster embedding and reranking without manual configuration.
**Status**: To Do

---

## Background

The current GPU detection in `archon/rag/install.py:detect_gpu()` runs `nvidia-smi` and returns a bool — it only handles NVIDIA CUDA. On Apple Silicon, `nvidia-smi` is absent so the installer silently uses CPU. fastembed (ONNX Runtime) supports `CoreMLExecutionProvider` on macOS ARM64, routing inference through the Apple Neural Engine and GPU — no extra packages needed. The standard `fastembed` wheel already ships ONNX Runtime with CoreML support on macOS.

The `providers` field in `RagConfig` is already wired through `ModelEmbedder` and `ModelReranker` to `fastembed`'s `TextEmbedding(providers=...)`. Setting it to `["CoreMLExecutionProvider"]` is all the runtime needs. The gap is: (1) the installer never sets this for Apple Silicon, and (2) there is no validation that CoreML actually works on the user's OS version — it silently degrades to CPU if CoreML fails, which is fine at runtime but leaves the operator unaware.

"Fully supported" means: auto-detect → configure → validate → fallback if broken → report outcome clearly.

## Goal

When `archon rag install` runs on an Apple Silicon Mac, the installer detects the platform, sets `providers = ["CoreMLExecutionProvider"]`, runs a single-text validation embed to confirm CoreML is active, and prints whether GPU acceleration is working. If validation fails, it falls back to CPU (clears providers) with a clear warning. The user never needs to touch `config.toml` manually. Intel Macs, Linux, and Windows are unaffected.

---

## Scope

### In Scope
- `detect_gpu()` extended to return a typed string: `"cuda"`, `"apple_silicon"`, or `"none"`
- Apple Silicon detection: `sys.platform == "darwin"` and `platform.machine() == "arm64"`
- `configure_providers()` extended to write `CoreMLExecutionProvider` on Apple Silicon
- Post-configure validation: run a test embed with the configured providers; if it raises, fall back to CPU and warn
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
- [ ] `detect_gpu()` returns `"apple_silicon"` on macOS ARM64; `"cuda"` when `nvidia-smi` succeeds; `"none"` otherwise
- [ ] `configure_providers()` writes `providers = ["CoreMLExecutionProvider"]` for `"apple_silicon"` in `config.toml`
- [ ] Post-install validation embeds one text with the configured provider; success is reported, failure falls back to CPU
- [ ] On CoreML validation failure, `providers` key is cleared from config and a warning is printed
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

---

## Architecture

### `detect_gpu() -> str` in `archon/rag/install.py`

```python
import sys
import platform

def detect_gpu(self) -> str:
    """Detect GPU type. Returns 'cuda', 'apple_silicon', or 'none'."""
    try:
        if subprocess.run(["nvidia-smi"], capture_output=True).returncode == 0:
            return "cuda"
    except FileNotFoundError:
        pass
    if sys.platform == "darwin" and platform.machine() == "arm64":
        return "apple_silicon"
    return "none"
```

### `install_deps(gpu: str)` in `archon/rag/install.py`
- `"cuda"`: unchanged — uninstall `fastembed`, install `fastembed-gpu>=0.7.4` + `onnxruntime-gpu`
- `"apple_silicon"` and `"none"`: install standard `fastembed>=0.7.4` (identical)

### `configure_providers(gpu: str)` in `archon/rag/install.py`
- `"cuda"`: write `["CUDAExecutionProvider"]` — unchanged
- `"apple_silicon"`: write `["CoreMLExecutionProvider"]`
- `"none"`: no-op

### `validate_providers(providers: list[str]) -> bool` in `archon/rag/install.py`
New method. Attempts to embed a single short string (`"archon rag test"`) using the configured providers. Returns `True` on success, `False` on any exception.

```python
def validate_providers(self, providers: list[str]) -> bool:
    """Return True if providers work for a test embed, False otherwise."""
    try:
        from fastembed import TextEmbedding  # noqa: PLC0415
        model = TextEmbedding(self.cfg.embedding_model, providers=providers)
        list(model.embed(["archon rag test"]))
        return True
    except Exception as exc:
        logger.warning("Provider validation failed: %s", exc)
        return False
```

### Updated `run()` flow
After `configure_providers(gpu)`, call `validate_providers()`. On failure:
1. Clear `providers = []` in `config.toml` (write empty list or remove key)
2. Print: `"Warning: CoreML acceleration failed validation — falling back to CPU. Check macOS version (≥12 required)."`

### No new config keys
`[rag] providers` already exists.

---

## Tests

- **`test_detect_gpu_returns_cuda`** (unit): mock `subprocess.run` returncode 0 → `"cuda"`
- **`test_detect_gpu_returns_apple_silicon`** (unit): mock FileNotFoundError + `sys.platform="darwin"` + `platform.machine="arm64"` → `"apple_silicon"`
- **`test_detect_gpu_returns_none_on_intel_mac`** (unit): mock `sys.platform="darwin"` + `platform.machine="x86_64"` → `"none"`
- **`test_detect_gpu_returns_none_on_linux_no_cuda`** (unit): `sys.platform="linux"` + FileNotFoundError → `"none"`
- **`test_install_deps_apple_silicon_installs_standard_fastembed`** (unit): assert `fastembed>=0.7.4` called; `fastembed-gpu` never called
- **`test_install_deps_cuda_still_installs_gpu_packages`** (unit): existing assertions pass with string `"cuda"`
- **`test_configure_providers_apple_silicon_writes_coreml`** (unit): config written with `["CoreMLExecutionProvider"]`
- **`test_configure_providers_cuda_unchanged`** (unit): `["CUDAExecutionProvider"]` for `"cuda"`
- **`test_configure_providers_none_is_noop`** (unit): no providers key written for `"none"`
- **`test_validate_providers_returns_true_on_success`** (unit): mock `TextEmbedding` + `embed` succeed → True
- **`test_validate_providers_returns_false_on_exception`** (unit): mock `TextEmbedding` raises → False
- **`test_run_flow_apple_silicon_validation_passes`** (integration): detect → `"apple_silicon"`, validate → True; assert CoreML written, no fallback
- **`test_run_flow_apple_silicon_validation_fails_falls_back`** (integration): detect → `"apple_silicon"`, validate → False; assert providers cleared, warning printed

---

## Documentation update
- [ ] `rag_guide.md`, section: Apple Silicon GPU Acceleration, path: `Documentation/UserManual/rag_guide.md`

---

## Task breakdown

### Phase 1 — Detection
> **Releasable**: after Task 1.1; `detect_gpu()` correctly identifies all platform types

#### Task 1.1 — Extend `detect_gpu()` to return a typed string
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: nothing
- **Description**:
  - Change `detect_gpu(self) -> bool` to `detect_gpu(self) -> str`
  - Return `"cuda"` when `nvidia-smi` exits 0
  - Return `"apple_silicon"` when `sys.platform == "darwin"` and `platform.machine() == "arm64"`
  - Return `"none"` otherwise
  - Add `import sys` and `import platform` (stdlib, no new deps)
- **Releasable**: `detect_gpu()` callable and correct on all platforms
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_detect_gpu_returns_cuda`
  - Unit: `test_detect_gpu_returns_apple_silicon`
  - Unit: `test_detect_gpu_returns_none_on_intel_mac`
  - Unit: `test_detect_gpu_returns_none_on_linux_no_cuda`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "detect_gpu" -v`

### Phase 2 — Configuration
> **Releasable**: after Task 2.2; installer correctly configures providers on all platforms

#### Task 2.1 — Update `install_deps()` to accept string GPU type
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `install_deps(self, gpu: bool)` to `install_deps(self, gpu: str)`
  - `"cuda"`: unchanged path — uninstall `fastembed`, install `fastembed-gpu>=0.7.4` + `onnxruntime-gpu`
  - `"apple_silicon"` and `"none"`: install standard `fastembed>=0.7.4`
- **Releasable**: correct wheel installed per GPU type
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_install_deps_apple_silicon_installs_standard_fastembed`
  - Unit: `test_install_deps_cuda_still_installs_gpu_packages`
  - Unit: `test_install_deps_none_installs_standard_fastembed`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "install_deps" -v`

#### Task 2.2 — Update `configure_providers()` to accept string GPU type
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Task 1.1
- **Description**:
  - Change `configure_providers(self, gpu: bool)` to `configure_providers(self, gpu: str)`
  - `"cuda"`: write `["CUDAExecutionProvider"]` (unchanged)
  - `"apple_silicon"`: write `["CoreMLExecutionProvider"]`
  - `"none"`: no-op — do not write providers key
  - Guard: if provider already set to the correct value, skip (idempotent)
  - `dry_run=True`: no-op (unchanged)
- **Releasable**: `configure_providers("apple_silicon")` writes CoreML to `config.toml`
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_configure_providers_apple_silicon_writes_coreml`
  - Unit: `test_configure_providers_cuda_unchanged`
  - Unit: `test_configure_providers_none_is_noop`
  - Unit: `test_configure_providers_idempotent_if_already_set`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "configure_providers" -v`

### Phase 3 — Validation and fallback
> **Releasable**: after Task 3.2; install confirms GPU acceleration works or falls back cleanly

#### Task 3.1 — Add `validate_providers()` method
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: nothing (independent of Phase 2)
- **Description**:
  - `def validate_providers(self, providers: list[str]) -> bool`
  - Imports `fastembed.TextEmbedding` inside the method (already installed by this point)
  - Creates `TextEmbedding(self.cfg.embedding_model, providers=providers)` and calls `list(model.embed(["archon rag test"]))`
  - Returns `True` on success, `False` on any exception
  - Logs warning with exception detail on failure
  - Never raises — caller handles fallback
- **Releasable**: validation can be called after `configure_providers()`
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_validate_providers_returns_true_on_success` — mock TextEmbedding + embed succeed
  - Unit: `test_validate_providers_returns_false_on_exception` — mock TextEmbedding raises
  - Unit: `test_validate_providers_returns_false_on_embed_exception` — model created but embed raises
  - Checkpoint: `uv run pytest tests/rag/test_install.py -k "validate_providers" -v`

#### Task 3.2 — Wire validation and fallback into `run()`
- [ ] **File**: `archon/rag/install.py`
- **Depends on**: Task 2.1, Task 2.2, Task 3.1
- **Description**:
  - `run()`: after `configure_providers(gpu)`, if `gpu == "apple_silicon"`:
    1. Call `validate_providers(["CoreMLExecutionProvider"])`
    2. On success: print `"CoreML acceleration validated — GPU/Neural Engine active."`
    3. On failure: call `clear_providers()` (new private method: writes `providers = []` to config.toml), print warning: `"Warning: CoreML validation failed — falling back to CPU. macOS 12+ required."`
  - `clear_providers()`: writes empty list for `providers` key using tomlkit (same pattern as `configure_providers`)
  - `dry_run=True`: skip validation (no fastembed installed yet in dry runs)
  - Pass string gpu to `install_deps(gpu)` and `configure_providers(gpu)` (wires Tasks 2.1 and 2.2)
- **Releasable**: full install flow works end-to-end on all platforms with validation
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Integration: `test_run_flow_apple_silicon_validation_passes`
  - Integration: `test_run_flow_apple_silicon_validation_fails_falls_back`
  - Integration: `test_run_flow_no_gpu_unchanged`
  - Checkpoint: `uv run pytest tests/rag/test_install.py -v`

### Phase 4 — Documentation
> **Releasable**: after Task 4.1

#### Task 4.1 — Add Apple Silicon section to RAG user guide
- [ ] **File**: `Documentation/UserManual/rag_guide.md`
- **Depends on**: Task 3.2
- **Description**:
  - Add section "Apple Silicon GPU Acceleration" covering: auto-detection during install, validation output, what CoreML does, macOS 12+ requirement, manual override via `archon config set rag.providers '["CoreMLExecutionProvider"]'`
  - Note: CoreML falls back to CPU silently at runtime after install — re-run installer if acceleration is lost after an OS update
- **Tests (TDD)**: N/A
- Checkpoint: `uv run pytest tests/rag/ -v`
