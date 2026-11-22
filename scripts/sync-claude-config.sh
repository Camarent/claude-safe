#!/usr/bin/env bash
# Sync ~/.claude from one git ref of this repo, without touching the checkout you run it from.
#   scripts/sync-claude-config.sh [REF]     (REF defaults to HEAD)
# Steps: refuse while a wave driver runs (.work/<stage>/wave.lock held), make a detached worktree of REF,
# run its provision-dev-vm.sh --only claude-config, print the agents rehosted by host_agents.py (dry run
# only: no write flag is ever passed), remove the worktree, print the commit and the restart reminder.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ref="${1:-HEAD}"

say() { printf '%s\n' "$*"; }

# driver_running: succeeds when a wave driver holds a stage lock (flock -n fails on a held lock).
driver_running() {
  local f
  for f in "$root"/.work/*/wave.lock; do
    [ -e "$f" ] || continue
    if ! flock -n "$f" true; then
      say "blocked: a wave driver is mid-step ($f); sync when it stops"
      return 0
    fi
  done
  return 1
}

if driver_running; then
  exit 1
fi

wt="$(mktemp -d)"
cleanup() {
  git -C "$root" worktree remove --force "$wt/tree" 2>/dev/null || true
  rm -rf "$wt"
}
trap cleanup EXIT

tree="$wt/tree"
git -C "$root" worktree add --detach "$tree" "$ref" >/dev/null
commit="$(git -C "$tree" rev-parse --short HEAD)"
say "syncing ~/.claude from $ref ($commit)"

bash "$tree/scripts/provision-dev-vm.sh" --only claude-config

say "agents rehosted by host_agents.py (dry run, nothing written):"
python3 -I "$tree/scripts/host_agents.py"

say "synced ~/.claude from commit $commit"
say "reminder: restart Claude Code to pick up the new config"
