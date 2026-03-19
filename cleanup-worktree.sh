#!/bin/sh

DRY_RUN=0
if [ "$1" = "--dry-run" ]; then
  DRY_RUN=1
  echo "[dry-run] No changes will be made."
fi

# Remove all agent worktrees
for wt in $(git worktree list --porcelain | grep "^worktree " | grep "\.claude/worktrees" | sed 's/^worktree //'); do
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] Would remove worktree: $wt"
  else
    git worktree remove --force "$wt"
  fi
done

if [ "$DRY_RUN" = "0" ]; then
  git worktree prune
fi

# Delete orphan branches
for branch in $(git branch | grep worktree-agent | sed 's/^[ *]*//'); do
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] Would delete branch: $branch"
  else
    git branch -D "$branch"
  fi
done

# Remove worktrees directory
if [ "$DRY_RUN" = "1" ]; then
  if [ -d ".claude/worktrees" ]; then
    echo "[dry-run] Would delete directory: .claude/worktrees/"
    find .claude/worktrees -mindepth 1 -maxdepth 1 -type d | while read dir; do
      echo "[dry-run]   $dir"
    done
  else
    echo "[dry-run] Directory .claude/worktrees/ does not exist."
  fi
else
  rm -rf .claude/worktrees/
fi
