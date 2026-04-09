# Archon Release Process

## How versioning works

Version format: `YY.M.<commit-count+1>`

`release.sh` calculates it automatically:
```bash
YEAR=$(date +%y)
MONTH=$(date +%-m)
COMMIT_COUNT=$(git rev-list --count HEAD)
VERSION="${YEAR}.${MONTH}.$((COMMIT_COUNT + 1))"
```

**Critical**: the version is calculated at the moment `release.sh` runs. Every commit you make before running it increases `COMMIT_COUNT` and therefore changes the version number.

---

## Step-by-step release procedure

### 1. Ensure main is clean and all tests pass

```bash
cd /Users/manczg/Documents/development/archon
git status           # must be clean
git log --oneline -5 # verify commits look right
uv run pytest --no-cov -q --tb=no 2>&1 | tail -3
```

### 2. Calculate the exact version that `release.sh` will use

**Wait for the full test run to finish before calculating.** If tests fail, commit the fix first, then calculate. Do this only after the working tree is clean and all tests pass:

```bash
COUNT=$(git rev-list --count HEAD)
echo "v$(date +%y).$(date +%-m).$((COUNT + 2))"
```

**Why `COUNT+2`**: the RELEASE.md commit you are about to make adds 1, and `release.sh` adds another 1 — so the final tag lands at `COUNT+2` from the current HEAD.

**This version is only valid if you make exactly one more commit** (the RELEASE.md commit).
If you make more commits between now and running `release.sh`, the version will drift — recalculate each time.

### 3. Add the RELEASE.md entry with the correct version

Edit `RELEASE.md` — insert a new section at the top (below `# Release Notes`):

```markdown
## v26.X.NNN

**Feature or fix title**
- Bullet point summary of changes
- ...

---
```

### 4. Commit RELEASE.md — this is the only commit you make

```bash
git add RELEASE.md
git commit -m "chore(release): update RELEASE.md for vYY.M.NNN"
```

### 5. Verify the version is still correct

After committing, re-check (at this point `release.sh` will add exactly 1 more commit, so use `COUNT+1`):
```bash
COUNT=$(git rev-list --count HEAD)
EXPECTED="v$(date +%y).$(date +%-m).$((COUNT + 1))"
echo "release.sh will use: $EXPECTED"
grep "^## $EXPECTED" RELEASE.md && echo "✔ version matches" || echo "✖ MISMATCH — fix RELEASE.md"
```

If there is a mismatch (e.g. you made an extra commit), **amend** the RELEASE.md commit (it's not pushed yet):

```bash
# Fix the version in RELEASE.md, then:
git add RELEASE.md
git commit --amend --no-edit
# Recalculate and verify again
```

### 6. Run the release script

Requires `GITHUB_TOKEN` in the environment:

```bash
bash release.sh
```

What `release.sh` does automatically:
- Validates clean working tree and correct branch
- Updates `__version__` in `install.py`
- Updates installer URL in `README.md`
- Syncs `AVAILABLE_MODELS` from Anthropic API (if `ANTHROPIC_API_KEY` is set) — **this overwrites any manual edits to `constants.py`**
- Commits all of the above as `chore: release vX.Y.Z`
- Tags the commit
- Pushes branch + tag
- Creates GitHub release with notes from RELEASE.md

---

## Common failure modes

### "No entry for vX.Y.Z in RELEASE.md"

Your RELEASE.md has the wrong version number. This happens when extra commits were made between calculating the version and running `release.sh`.

Fix:
1. Calculate the correct version: `COUNT=$(git rev-list --count HEAD); echo "v$(date +%y).$(date +%-m).$((COUNT+1))"` ← at this point RELEASE.md is already committed, so `COUNT+1` is correct (only `release.sh`'s commit remains)
2. Edit RELEASE.md to use the correct version
3. Amend the RELEASE.md commit: `git add RELEASE.md && git commit --amend --no-edit`
4. Re-run `release.sh`

### "Working tree is dirty"

You have uncommitted changes. Either commit them or stash before releasing.

### "Not on main branch"

Switch to main: `git checkout main` (from the primary repo, not a worktree).

### Version drift from the worktree

If working in a git worktree (e.g. `velvety-popping-cookie`), merge to `main` in the primary repository first, then run `release.sh` from `/Users/manczg/Documents/development/archon` (not from the worktree).

### AVAILABLE_MODELS reverted by release.sh

`release.sh` always re-syncs `AVAILABLE_MODELS` from the Anthropic API during the release commit. Manual edits to `constants.py` will be overwritten. This is intentional — the API is the authoritative source during releases.

If you need a manual fix to survive a release, edit `scripts/update_models.py` instead.

---

## Merge + release from a worktree (complete flow)

```bash
# 1. In the worktree: rebase onto main
cd /Users/manczg/Documents/development/archon/.claude/worktrees/<name>
git rebase main        # resolve any conflicts, then git rebase --continue

# 2. In the primary repo: fast-forward merge
cd /Users/manczg/Documents/development/archon
git merge --ff-only <worktree-branch>

# 3. Run tests
uv run pytest --no-cov -q --tb=no 2>&1 | tail -3

# 4. Calculate version (COUNT+2: RELEASE.md commit +1, release.sh commit +1)
COUNT=$(git rev-list --count HEAD)
echo "v$(date +%y).$(date +%-m).$((COUNT+2))"

# 5. Update RELEASE.md with that exact version
# (edit file, then:)
git add RELEASE.md
git commit -m "chore(release): update RELEASE.md for vYY.M.NNN"

# 6. Re-verify version
COUNT=$(git rev-list --count HEAD)
EXPECTED="v$(date +%y).$(date +%-m).$((COUNT + 1))"
grep "^## $EXPECTED" RELEASE.md && echo "✔" || echo "✖ MISMATCH"

# 7. Release
bash release.sh
```
