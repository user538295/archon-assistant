# FIX-026 — Preserve comments in config.toml on install update
**Purpose**: Fix the `--update` path in `install.py` that strips all TOML comments from `~/.archon/config.toml` when merging user IDs and working directory.
**Audience**: Users who run `uv run install.py --update` and expect their config comments to be preserved.
**Status**: To Do

---

## Background

`install.py` uses a PEP 723 inline-script with `tomli_w` as its declared dependency. On fresh install, `config.toml` is written by copying the `config.toml.example` template verbatim (comments preserved). On update (when `config.toml` already exists), the code parses the file with `tomllib` and re-serializes it with `tomli_w`. `tomllib` discards all comments on parse — they are not part of the TOML data model — so `tomli_w.dump()` produces a comment-free file.

`tomlkit` is already a runtime dependency in `pyproject.toml` (≥0.12). It round-trips comments, whitespace, and key ordering faithfully. The fix replaces `tomllib` + `tomli_w` with `tomlkit` in the update path only.

The `tomli_w` import is guarded by a `_HAS_TOMLI_W` flag with a string-regex fallback. Both branches are removed — `tomlkit` becomes the only update-path implementation.

---

## Goal

After this fix, running `uv run install.py --update` preserves all comments, blank lines, and user-added keys in `~/.archon/config.toml`. Only `allowed_user_ids` and `working_directory` are patched. The `tomli_w` optional dependency and its fallback branch are removed entirely.

---

## Scope

### In Scope
- Replace the `tomllib` + `tomli_w` update branch with `tomlkit`
- Remove the `_HAS_TOMLI_W` flag and the string-regex fallback branch
- Replace `tomli_w` with `tomlkit` in the PEP 723 inline-script `# dependencies` header only (see note below)
- Add regression tests: update path preserves comments, preserves user-added keys, only patches the two target fields

**Note**: `tomli-w` stays in `pyproject.toml` dev dependencies — it is still used by `tests/ai/test_archon_toolkit_schedule.py`. Only the PEP 723 `# dependencies` header in `install.py` and the import/update-path code in `install.py` are modified.

### Out of Scope
- Fresh install path (`config.toml` does not yet exist) — unchanged
- `_render_config_template()` — unchanged
- Any other fields beyond `allowed_user_ids` and `working_directory`
- `pyproject.toml` — not modified

---

## Acceptance criteria
- [ ] Running `write_config()` on an existing `config.toml` preserves all `#` comments
- [ ] User-added keys outside `[access]` and `[session]` are preserved
- [ ] Only `allowed_user_ids` and `working_directory` are modified
- [ ] `tomli_w` import and `_HAS_TOMLI_W` flag are gone from `install.py`
- [ ] PEP 723 `# dependencies` header lists `tomlkit` instead of `tomli_w`
- [ ] All existing installer tests pass
- [ ] The `[models]` section missing warning (`con.info(...)`) is preserved in the tomlkit path

---

## What does NOT change
- Fresh install path — `config.toml.example` template copy is unchanged
- `_render_config_template()` function
- `tomllib` (stdlib, read-only) remains for TOML parsing in `_rag_already_enabled()` and `_collect_credentials()`. Only `tomli_w` (write dependency) is removed from the PEP 723 header.
- `pyproject.toml` — `tomli-w` remains in dev dependencies (used by `tests/ai/test_archon_toolkit_schedule.py`)
- All other sections and behaviour of `install.py`

---

## Known limitations / accepted trade-offs
- `tomlkit` does not support multiline values in all edge cases, but neither `allowed_user_ids` nor `working_directory` ever spans multiple lines, so this is not a concern.
- `tomlkit` adds ~100 KB to the inline-script download; acceptable given it is already a declared project dependency.
- **Inline comments on patched keys are dropped**: when `tomlkit` replaces a key's value, any inline comment on that same line (e.g., `allowed_user_ids = [111]  # whitelist`) is dropped. This is a tomlkit limitation when writing values. Accepted: standalone section/block comments (the common case) are preserved; only inline comments on the exact two patched keys (`allowed_user_ids` and `working_directory`) are at risk. A test `test_write_config_update_inline_comments_on_patched_keys` documents this behavior by asserting the inline comment is dropped.
- **Non-atomic write**: the `write_text()` call is not atomic (no write-to-tmp + `os.replace()`). ADR-08 mandates atomic writes for tomlkit operations. `install.py` is exempt here: the installer runs as a one-shot script and the current `tomli_w` path was also non-atomic. Bringing this into ADR-08 compliance is a separate task.

---

## Architecture

**Modified file:** `install.py`

Changes:
1. PEP 723 header: `tomli_w` → `tomlkit`
2. Remove `try/except ImportError` block for `tomli_w` and `_HAS_TOMLI_W` flag
3. Add top-level `import tomlkit`
4. In `write_config()`, replace the `if _HAS_TOMLI_W: ... else: ...` branches with a single `tomlkit` block — see Task 1.2 for the canonical implementation. Note: use `encoding="utf-8"` on both the `read_text()` and `write_text()` calls.

No new modules, classes, or config keys are introduced.

> Note: `import tomllib` (line 25, stdlib) is **not removed** — it is still used by `_rag_already_enabled()` and `_collect_credentials()` elsewhere in `install.py`.

---

## Tests

- **test_write_config_update_preserves_comments** (unit): config contains a standalone `# section comment`, a `# standalone comment line`, and an inline comment on a **non-patched** key (e.g., `mode = "quiet"  # notification mode`); assert all three survive the update. Note: inline comments on the two patched keys (`allowed_user_ids`, `working_directory`) are dropped — that behavior is covered by `test_write_config_update_inline_comments_on_patched_keys`.
- **test_write_config_update_preserves_user_keys** (unit): user-added keys outside `[access]`/`[session]` survive the update
- **test_write_config_update_patches_only_target_fields** (unit): only `allowed_user_ids` and `working_directory` change; all other values are identical before and after
- **test_write_config_update_valid_toml_after_patch** (unit): output parses as valid TOML with `tomllib`
- **test_write_config_update_missing_access_section** (unit): existing config has NO `[access]` section — `write_config` creates it with the correct `allowed_user_ids`
- **test_write_config_update_missing_session_section** (unit): existing config has NO `[session]` section — `write_config` creates it with the correct `working_directory`
- **test_write_config_update_inline_comments_on_patched_keys** (unit): documents tomlkit limitation — asserts that an inline comment on `allowed_user_ids` (e.g., `# whitelist`) is dropped after the update; behavior is expected and documented
- **test_write_config_update_models_warning** (unit): existing config has no `[models]` section — asserts that `con.info()` is called with the "your config has no [models] section" message. This test is GREEN before Task 1.2 (the warning already exists in the current code's `if _HAS_TOMLI_W:` branch).

---

## Documentation update
- N/A — no user-visible behaviour change beyond bug fix; no doc update required

---

## Task breakdown

### Phase 1 — Replace tomli_w with tomlkit in update path
> **Releasable**: after Task 1.2 — the fix is complete and tested

#### Task 1.1 — Add regression tests for comment and key preservation
- [x] **File**: `tests/test_installer_py.py`
- **Depends on**: nothing
- **Description**:
  - Add 8 new test methods to the existing `TestWriteConfig` class (or the nearest write_config test class)
  - Each test sets up `archon_home` with an existing `config.toml` that contains comments, calls `write_config()`, then asserts on the output
  - `test_write_config_update_preserves_comments`: config contains a standalone `# section comment`, a `# standalone comment line`, and an inline comment on a **non-patched** key (e.g., `mode = "quiet"  # notification mode`); assert all three survive the update. Note: inline comments on the two patched keys (`allowed_user_ids`, `working_directory`) are dropped — that behavior is covered by `test_write_config_update_inline_comments_on_patched_keys`.
  - `test_write_config_update_preserves_user_keys`: config has `[logging]\nlog_level = "DEBUG"` (user-customised); assert still present after update
  - `test_write_config_update_patches_only_target_fields`: config has known values for all fields; after update only `allowed_user_ids` and `working_directory` differ
  - `test_write_config_update_valid_toml_after_patch`: re-parse output with `tomllib.loads()` — must not raise
  - `test_write_config_update_missing_access_section`: existing config has NO `[access]` section — `write_config` creates it with the correct `allowed_user_ids`
  - `test_write_config_update_missing_session_section`: existing config has NO `[session]` section — `write_config` creates it with the correct `working_directory`
  - `test_write_config_update_inline_comments_on_patched_keys`: config has `allowed_user_ids = [111]  # whitelist`; assert the inline comment is absent after update (documents tomlkit limitation)
  - `test_write_config_update_models_warning`: existing config has no `[models]` section — asserts that `con.info()` is called with the "your config has no [models] section" message
  - Only ONE test is expected to be RED before Task 1.2: `test_write_config_update_preserves_comments` — RED because the current `tomli_w` branch strips all comments. The remaining 7 are **characterization tests** — they verify existing behavior that must survive the migration: `test_write_config_update_preserves_user_keys` (GREEN: `tomli_w` preserves user keys), `test_write_config_update_patches_only_target_fields` (GREEN: `tomli_w` only modifies the two target fields), `test_write_config_update_valid_toml_after_patch` (GREEN: `tomli_w` output is valid TOML), `test_write_config_update_missing_access_section` (GREEN: current code uses `setdefault("access", {})` which handles missing sections), `test_write_config_update_missing_session_section` (GREEN: same reason), `test_write_config_update_inline_comments_on_patched_keys` (GREEN: `tomli_w` also drops inline comments, so the assertion "comment is absent" already passes), `test_write_config_update_models_warning` (GREEN: the warning already exists in the current `if _HAS_TOMLI_W:` branch).
- **Releasable**: The one RED test documents the bug to be fixed; the 7 GREEN tests lock in existing behavior that must not regress.
- **Tests (TDD)** — `tests/test_installer_py.py`:
  - [x] Unit: `test_write_config_update_preserves_comments` — asserts standalone section comments, standalone comment lines, and inline comments on non-patched keys survive
  - [x] Unit: `test_write_config_update_preserves_user_keys` — asserts user-added keys survive
  - [x] Unit: `test_write_config_update_patches_only_target_fields` — asserts only 2 fields change
  - [x] Unit: `test_write_config_update_valid_toml_after_patch` — asserts valid TOML output
  - [x] Unit: `test_write_config_update_missing_access_section` — asserts `[access]` section created when absent
  - [x] Unit: `test_write_config_update_missing_session_section` — asserts `[session]` section created when absent
  - [x] Unit: `test_write_config_update_inline_comments_on_patched_keys` — asserts inline comment on patched key is dropped (expected/documented behavior)
  - [x] Unit: `test_write_config_update_models_warning` — asserts `con.info()` called with [models] warning when section absent
  - Checkpoint: `uv run pytest tests/test_installer_py.py -k "update_preserves or update_patches or update_valid or update_missing or update_inline or update_models" -v`

#### Task 1.2 — Replace tomli_w with tomlkit in install.py
- [ ] **File**: `install.py`
- **Depends on**: Task 1.1
- **Description**:
  - Line 3 PEP 723 header: change `"tomli_w"` to `"tomlkit"` in the `# dependencies` list
  - Remove the `try/except ImportError` block (lines ~32–38) that imports `tomli_w` and sets `_HAS_TOMLI_W`
  - Add `import tomlkit` at the top-level imports
  - In `write_config()`, replace the entire `if _HAS_TOMLI_W: ... else: warnings.warn(...)` block with:
    ```python
    doc = tomlkit.parse(config_file.read_text(encoding="utf-8"))
    doc.setdefault("access", tomlkit.table())["allowed_user_ids"] = user_ids
    doc.setdefault("session", tomlkit.table())["working_directory"] = str(workspace_dir)
    if "models" not in doc:
        con.info(
            "Note: your config has no [models] section. Add one to enable the "
            "/models keyboard — see examples/config.toml.example for the format."
        )
    config_file.write_text(tomlkit.dumps(doc), encoding="utf-8")
    ```
  - Note: `import warnings` is a local import inside the `else` fallback branch — it is removed automatically when the branch is deleted. No top-level import needs to be removed.
  - All 8 new tests from Task 1.1 must now pass (green); all existing installer tests must continue to pass
- **Releasable**: after this task the fix is complete — update path preserves comments
- **Tests (TDD)** — `tests/test_installer_py.py`:
  - Unit: `test_write_config_update_preserves_comments` — now green (standalone comments and inline comments on non-patched keys preserved)
  - Unit: `test_write_config_update_preserves_user_keys` — now green
  - Unit: `test_write_config_update_patches_only_target_fields` — now green
  - Unit: `test_write_config_update_valid_toml_after_patch` — now green
  - Unit: `test_write_config_update_missing_access_section` — now green
  - Unit: `test_write_config_update_missing_session_section` — now green
  - Unit: `test_write_config_update_inline_comments_on_patched_keys` — now green (documents known limitation)
  - Unit: `test_write_config_update_models_warning` — now green (warning preserved in tomlkit path)
  - Checkpoint: `uv run pytest tests/test_installer_py.py -v`
