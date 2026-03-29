# FIX-024 — RAG install targets wrong virtualenv
**Purpose**: Fix `archon rag install` so RAG dependencies land in the Python environment the service actually runs under.
**Audience**: End users who install Archon from scratch and then run `archon rag install`.
**Status**: Done

---

## Background

After a fresh install via `uv run install.py`, the `archon` CLI is a `console_scripts` entry point
with a hardcoded shebang pointing to `~/.archon/app/.venv/bin/python3`. The RAG launchd/systemd
service is registered with the same Python (via `sys.executable` in `rag_service.register()`).

However, `RagInstaller.install_deps()` calls `uv pip install` **without `--python`**. Without
that flag, `uv pip` uses its own venv-discovery logic (walks up from CWD, checks `$VIRTUAL_ENV`).
When the user runs `archon rag install` from their development directory, `uv pip` finds the dev
repo's `.venv` and installs there instead of `~/.archon/app/.venv`. The service then crashes:

```
Stats unavailable — server may be writing (No module named 'lancedb')
```

The fix is a single targeted change: pass `--python sys.executable` to every `uv pip
install/uninstall` call in `install_deps()`, and add `import sys` which is currently absent from
the file. `sys.executable` is the correct source of truth because:
1. The installed `archon` CLI shebang is hardcoded to the app venv Python.
2. The existing `rag_service.register()` already uses `sys.executable` for the same purpose.

## Goal

After this fix, `archon rag install` (run as the installed CLI) always installs RAG packages
into the same Python environment the RAG service runs under, eliminating the
`No module named 'lancedb'` failure. Existing tests pass, and new tests lock in the
per-subprocess `--python` contract so the bug cannot silently regress.

---

## Scope

### In Scope
- Add `import sys` to `archon/rag/install.py`
- Add `--python sys.executable` to all four `subprocess.run` calls in `install_deps()`
- Extend `TestInstallDeps` with per-call `--python` assertions for all GPU paths

### Out of Scope
- `check_deps()` uses `importlib.import_module()` in the running interpreter. This is correct
  for the installed CLI scenario (same venv) and is a pre-existing design, not changed here.
- No shared `target_python()` helper is introduced (KISS — both `install_deps` and
  `rag_service.register()` already use `sys.executable` independently; no extra abstraction needed).
- Linux `rag_service.py` and Windows stubs are not modified — the `install_deps` fix is
  platform-agnostic and benefits Linux automatically.

---

## Acceptance criteria
- [ ] `archon rag install` (via installed CLI) installs lancedb into `~/.archon/app/.venv`
- [ ] `~/.archon/app/.venv/bin/python3 -c "import lancedb"` exits 0 after running the installer
- [ ] `archon rag status` no longer shows `No module named 'lancedb'`
- [x] All `TestInstallDeps` tests pass and include `--python sys.executable` assertions
- [x] All existing tests pass (`uv run pytest tests/rag/test_install.py --no-cov`)

---

## What does NOT change
- `check_deps()` — still uses `importlib.import_module()` in the current interpreter
- `rag_service.register()` — already uses `sys.executable`, unchanged
- The package list (`lancedb`, `docling`, `markitdown`, `trafilatura`, `chonkie`, `fastmcp`, `fastembed`)
- GPU-path branching logic (cuda vs. else) — structure unchanged, only `--python` added
- All other `RagInstaller` methods and the full `run()` / `run_uninstall()` flows

---

## Known limitations / accepted trade-offs
- If a developer runs `archon rag install` from a dev venv (not the installed CLI), `sys.executable`
  will point to the dev venv Python and packages install there. This is intentional: you're running
  the installer from a different Python, so that's your target. `check_deps()` will correctly probe
  the same dev venv, so the system remains self-consistent.
- `check_deps()` / `install_deps()` semantic mismatch (if packages exist in dev venv but not app
  venv, install is skipped) is a pre-existing design issue not addressed here.

---

## Architecture

No new modules, classes, or config keys are introduced.

**Change**: `archon/rag/install.py`
- Add `import sys` to the module-level import block (currently absent)
- `install_deps(self, gpu: GpuType) -> None`: capture `python = sys.executable` at the start,
  then pass `["--python", python]` as additional arguments to every `subprocess.run` call

Before (CUDA branch):
```python
subprocess.run(["uv", "pip", "uninstall", "fastembed", "-y"], check=False)
subprocess.run(["uv", "pip", "install", "fastembed-gpu>=0.8.0", "onnxruntime-gpu"], check=True)
```

After:
```python
python = sys.executable
subprocess.run(["uv", "pip", "uninstall", "--python", python, "fastembed", "-y"], check=False)
subprocess.run(["uv", "pip", "install", "--python", python, "fastembed-gpu>=0.8.0", "onnxruntime-gpu"], check=True)
```

The common-packages block and the non-cuda fastembed install receive the same treatment.

---

## Tests

New tests added to `TestInstallDeps` in `tests/rag/test_install.py`:
- **test_install_deps_cuda_passes_python_to_all_calls** (unit): all three subprocess commands in
  the CUDA path (uninstall + fastembed-gpu install + common install) each contain `--python` and
  `sys.executable` as adjacent elements
- **test_install_deps_cpu_passes_python_to_all_calls** (unit): both subprocess commands in
  the non-cuda path (fastembed install + common install) each contain `--python sys.executable`;
  parameterized over `gpu=["none", "apple_silicon"]` since both use the identical `else` branch
- **test_install_deps_cpu_common_packages_present** (unit): for gpu="none", verifies the common
  packages block (`lancedb`, `docling`, etc.) appears in captured subprocess calls, confirming
  it runs for non-CUDA paths
- **test_install_deps_dry_run_cuda_no_op** (unit): with `dry_run=True` and `gpu="cuda"`, confirms
  `subprocess.run` is never called (guards against accidentally scoping the dry_run guard inside
  the CUDA branch)
- *Existing tests retained unchanged*: `test_install_deps_cuda_still_installs_gpu_packages`,
  `test_install_deps_apple_silicon_installs_standard_fastembed`,
  `test_install_deps_none_installs_standard_fastembed`, `test_install_deps_dry_run_no_op`

Note: AC-1 through AC-3 in the acceptance criteria are manual live verification steps, not
automated test coverage.

---

## Documentation update
- N/A — this is a silent bugfix; no user-facing behavior changes beyond correct installation.

---

## Task breakdown

### Phase 1 — Fix and verify
> **Releasable**: after Task 1.1 — the bug is fixed and regression tests are in place.

#### Task 1.1 — TDD: write `--python` tests, implement fix in `install_deps()`
- [x] **Files**: `tests/rag/test_install.py`, `archon/rag/install.py`
- **Depends on**: nothing
- **Description**:
  **Step A — Write failing tests first (TDD red phase):**
  Add to `TestInstallDeps` in `tests/rag/test_install.py`:
  1. `test_install_deps_cuda_passes_python_to_all_calls` — captures subprocess calls for
     `gpu="cuda"`, asserts every `cmd` contains `"--python"` and
     `cmd[cmd.index("--python") + 1] == sys.executable`
  2. `test_install_deps_cpu_passes_python_to_all_calls` — parameterized over
     `@pytest.mark.parametrize("gpu", ["none", "apple_silicon"])`, same per-cmd assertion
  3. `test_install_deps_cpu_common_packages_present` — for `gpu="none"`, asserts `"lancedb"` and
     `"docling"` appear in the flat args of all captured commands (guards common block execution)
  4. `test_install_deps_dry_run_cuda_no_op` — `dry_run=True`, `gpu="cuda"`, asserts
     `subprocess.run` is never called

  **Step B — Implement the fix (TDD green phase):**
  In `archon/rag/install.py`:
  - Add `import sys` to the module-level imports block (after `import subprocess`, alphabetical: `s-u-b` < `s-y-s`)
  - In `install_deps(self, gpu: GpuType) -> None`, add `python = sys.executable` as the first
    statement after the `dry_run` guard
  - Add `"--python", python` to all four `subprocess.run` argument lists:
    1. `["uv", "pip", "uninstall", "--python", python, "fastembed", "-y"]` — CUDA, `check=False`
    2. `["uv", "pip", "install", "--python", python, "fastembed-gpu>=0.8.0", "onnxruntime-gpu"]` — CUDA, `check=True`
    3. `["uv", "pip", "install", "--python", python, "fastembed>=0.8.0"]` — else, `check=True`
    4. `["uv", "pip", "install", "--python", python, "lancedb", "docling", "markitdown", "trafilatura", "chonkie", "fastmcp"]` — common, `check=True`
  - Update the docstring to:
    `"""Install RAG dependencies into the same Python that runs this process. No-op when dry_run=True."""`
  - No other changes to the file
- **Releasable**: after this task, `archon rag install` installs into the correct venv and the regression is locked in by tests
- **Tests (TDD)** — `tests/rag/test_install.py`:
  - Unit: `test_install_deps_cuda_passes_python_to_all_calls` — all 3 CUDA-path cmds have `--python sys.executable`
  - Unit: `test_install_deps_cpu_passes_python_to_all_calls[none]` — parameterized, else-path cmds
  - Unit: `test_install_deps_cpu_passes_python_to_all_calls[apple_silicon]` — parameterized, same else-path
  - Unit: `test_install_deps_cpu_common_packages_present` — lancedb/docling present for non-CUDA
  - Unit: `test_install_deps_dry_run_cuda_no_op` — dry_run with CUDA GPU skips all subprocess calls
  - Checkpoint: `uv run pytest tests/rag/test_install.py --no-cov -v`
