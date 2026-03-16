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

[[ -n "${GITHUB_TOKEN:-}" ]] || fail "GITHUB_TOKEN not set. Required for creating GitHub releases."

BRANCH=$(git rev-parse --abbrev-ref HEAD)
[[ "$BRANCH" == "main" ]] || fail "Not on main branch (current: $BRANCH). Switch to main before releasing."
ok "On branch main"

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "Working tree is dirty. Commit or stash your changes before releasing."
fi
ok "Working tree is clean"

# ─── version calculation ─────────────────────────────────────────────────────

YEAR=$(date +%y)
MONTH=$(date +%-m)
COMMIT_COUNT=$(git rev-list --count HEAD)
NEXT_COUNT=$(( COMMIT_COUNT + 1 ))
VERSION="${YEAR}.${MONTH}.${NEXT_COUNT}"

echo "Calculated next version: v${VERSION}"

# ─── update install.py ───────────────────────────────────────────────────────

echo "Updating __version__ in install.py ..."
run "sed -i '' 's/__version__ = \"[^\"]*\"/__version__ = \"${VERSION}\"/' install.py"
ok "install.py updated"

# ─── update README.md ────────────────────────────────────────────────────────

echo "Updating installer URL in README.md ..."
run "sed -i '' 's|/v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*/install\.py|/v${VERSION}/install.py|g' README.md"
ok "README.md updated"

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
run "curl -sf -X POST \"https://api.github.com/repos/${REPO_URL}/releases\" \
  -H 'Accept: application/vnd.github+json' \
  -H \"Authorization: Bearer \${GITHUB_TOKEN}\" \
  -d '{\"tag_name\":\"v${VERSION}\",\"name\":\"v${VERSION}\",\"generate_release_notes\":true}' \
  > /dev/null"
ok "Created GitHub release v${VERSION}"

echo ""
ok "Release v${VERSION} complete!"
