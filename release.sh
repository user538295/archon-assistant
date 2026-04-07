#!/usr/bin/env bash
set -euo pipefail

# ─── helpers ────────────────────────────────────────────────────────────────

ok()   { echo "✔ $*"; }
fail() { echo "✖ $*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: release.sh [OPTIONS]

Cut a new release: bump version, commit, tag, and push.

The version is calculated automatically as YY.M.<commit-count+1>.

Requires GITHUB_TOKEN env var for creating GitHub releases.

Options:
  --dry-run, --dry   Print commands instead of executing them
  -h, --help         Show this help message and exit
USAGE
  exit 0
}

DRY_RUN=false
for arg in "$@"; do
  [[ "$arg" == "-h" || "$arg" == "--help" ]] && usage
  [[ "$arg" == "--dry-run" || "$arg" == "--dry" ]] && DRY_RUN=true
done

run() {
  if $DRY_RUN; then
    echo "  [dry-run] $*"
  else
    eval "$@"
  fi
}

# ─── guards ─────────────────────────────────────────────────────────────────

if ! $DRY_RUN; then
  [[ -n "${GITHUB_TOKEN:-}" ]] || fail "GITHUB_TOKEN not set. Required for creating GitHub releases."
else
  [[ -n "${GITHUB_TOKEN:-}" ]] || echo "  [dry-run] GITHUB_TOKEN not set (required for real release)"
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD)
[[ "$BRANCH" == "main" ]] || fail "Not on main branch (current: $BRANCH). Switch to main before releasing."
ok "On branch main"

if ! git diff --quiet || ! git diff --cached --quiet; then
  if $DRY_RUN; then
    echo "  [dry-run] WARNING: working tree is dirty (required to be clean for real release)"
  else
    fail "Working tree is dirty. Commit or stash your changes before releasing."
  fi
else
  ok "Working tree is clean"
fi

# ─── version calculation ─────────────────────────────────────────────────────

YEAR=$(date +%y)
MONTH=$(date +%-m)
COMMIT_COUNT=$(git rev-list --count HEAD)
NEXT_COUNT=$(( COMMIT_COUNT + 1 ))
VERSION="${YEAR}.${MONTH}.${NEXT_COUNT}"

echo "Calculated next version: v${VERSION}"

# ─── require RELEASE.md entry ────────────────────────────────────────────────

if grep -q "^## v${VERSION}" RELEASE.md; then
  ok "RELEASE.md entry found for v${VERSION}"
elif $DRY_RUN; then
  echo "  [dry-run] WARNING: no entry for v${VERSION} in RELEASE.md (required for real release)"
else
  fail "No entry for v${VERSION} in RELEASE.md. Add release notes before cutting a release."
fi

# ─── update install.py ───────────────────────────────────────────────────────

echo "Updating __version__ in install.py ..."
run "sed -i '' 's/__version__ = \"[^\"]*\"/__version__ = \"${VERSION}\"/' install.py"
ok "install.py updated"

# ─── update README.md ────────────────────────────────────────────────────────

echo "Updating installer URL in README.md ..."
run "sed -i '' 's|/v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*/install\.py|/v${VERSION}/install.py|g' README.md"
ok "README.md updated"

if ! $DRY_RUN; then
  # ─── sync AVAILABLE_MODELS from Anthropic API ───────────────────
  if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    echo "Syncing AVAILABLE_MODELS from Anthropic API..."
    MODELS_JSON=$(curl -s -f \
      -H "x-api-key: ${ANTHROPIC_API_KEY}" \
      -H "anthropic-version: 2023-06-01" \
      "https://api.anthropic.com/v1/models" 2>/dev/null) || MODELS_JSON=""
    if [ -n "$MODELS_JSON" ]; then
      echo "$MODELS_JSON" | python3 scripts/update_models.py \
        || echo "Warning: model update script failed — continuing"
      if ! git diff --quiet archon/ai/constants.py; then
        run "git add archon/ai/constants.py"
        ok "Staged updated constants.py"
      fi
      if ! git diff --quiet examples/config.toml.example; then
        run "git add examples/config.toml.example"
        ok "Staged updated config.toml.example"
      fi
    else
      echo "Warning: Anthropic API call failed — skipping AVAILABLE_MODELS sync"
    fi
  else
    echo "Warning: ANTHROPIC_API_KEY not set — skipping AVAILABLE_MODELS sync"
  fi
else
  echo "[dry-run] Skipping AVAILABLE_MODELS sync"
fi

# ─── git add ─────────────────────────────────────────────────────────────────

run "git add install.py README.md"
ok "Staged install.py and README.md"

# ─── git commit ──────────────────────────────────────────────────────────────

run "git commit -m \"chore: release v${VERSION}\""
ok "Committed release v${VERSION}"

# ─── git tag ─────────────────────────────────────────────────────────────────

run "git tag \"v${VERSION}\""
ok "Tagged v${VERSION}"

# ─── git push ────────────────────────────────────────────────────────────────

run "git push"
run "git push origin \"v${VERSION}\""
ok "Pushed branch and tag"

# ─── GitHub release ──────────────────────────────────────────────────────

REPO_URL=$(git remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/')

RELEASE_NOTES=$(python3 - <<PYEOF
import re, sys
version = "v${VERSION}"
try:
    text = open("RELEASE.md").read()
except FileNotFoundError:
    sys.exit(0)
m = re.search(r'^## ' + re.escape(version) + r'[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
print(m.group(1).strip() if m else "")
PYEOF
)

RELEASE_JSON_FILE=$(mktemp)
python3 - <<PYEOF > "$RELEASE_JSON_FILE"
import json, re
version = "v${VERSION}"
notes = """${RELEASE_NOTES}"""
data = {"tag_name": version, "name": version}
if notes.strip():
    data["body"] = notes.strip()
else:
    data["generate_release_notes"] = True
print(json.dumps(data))
PYEOF

if $DRY_RUN; then
  echo "  [dry-run] curl POST https://api.github.com/repos/${REPO_URL}/releases (body in ${RELEASE_JSON_FILE})"
else
  curl -sf -X POST "https://api.github.com/repos/${REPO_URL}/releases" \
    -H 'Accept: application/vnd.github+json' \
    -H "Authorization: Bearer ${GITHUB_TOKEN}" \
    -d "@${RELEASE_JSON_FILE}" \
    > /dev/null
fi
rm -f "$RELEASE_JSON_FILE"

if [[ -n "$RELEASE_NOTES" ]]; then
  ok "Created GitHub release v${VERSION} (notes from RELEASE.md)"
else
  ok "Created GitHub release v${VERSION} (auto-generated notes)"
fi

echo ""
ok "Release v${VERSION} complete!"
