#!/usr/bin/env bash
# Sync ~/.claude from one git ref of this repo, without touching the checkout you run it from.
#   scripts/sync-claude-config.sh [REF]           dry run (default, REF defaults to HEAD): lists the ~/.claude files
#                                                 that would be overwritten; nothing is written
#   scripts/sync-claude-config.sh --apply [REF]   writes ~/.claude (replaced files are saved in
#                                                 ~/.claude/.provision-backup/<UTC time>/)
#   scripts/sync-claude-config.sh --compare [REF] writes nothing; exit 0 when ~/.claude matches REF, 10 when a file
#                                                 would be overwritten (the driver's pipeline_install_check)
# Steps: refuse while a wave driver runs (.work/<stage>/wave.lock held), make a detached worktree of REF,
# run its scripts/install-claude-config.sh (with --dry-run unless --apply), print the agents rehosted by
# host_agents.py (dry run only: no write flag is ever passed), remove the worktree, print the commit.
# --compare skips the driver guard (the driver itself runs it) and host_agents.py.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ref="HEAD"
apply=0
compare=0

say() { printf '%s\n' "$*"; }
usage() {
  say "usage: scripts/sync-claude-config.sh [--apply|--compare] [REF]"
  say "  default: dry run, lists the ~/.claude files that would be overwritten; nothing is written"
  say "  --apply: write ~/.claude from REF; replaced files are saved in ~/.claude/.provision-backup/<UTC time>/"
  say "  --compare: nothing written; exit 0 when ~/.claude matches REF, 10 when it differs"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) apply=1 ;;
    --compare) compare=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; exit 2 ;;
    *) ref="$1" ;;
  esac
  shift
done

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

if [ "$compare" = 0 ] && driver_running; then
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
if [ "$compare" = 1 ]; then
  out="$(bash "$tree/scripts/install-claude-config.sh" --dry-run)"
  say "$out"
  # "already done" = the install check passed (checksums match), the same as "none: no file would be overwritten"
  if grep -qE 'none: no file would be overwritten|skip claude-config: already done' <<<"$out"; then
    say "compare: ~/.claude matches commit $commit"
    exit 0
  fi
  say "compare: ~/.claude differs from commit $commit: run scripts/sync-claude-config.sh --apply"
  exit 10
fi

say "syncing ~/.claude from $ref ($commit)"

if [ "$apply" = 1 ]; then
  bash "$tree/scripts/install-claude-config.sh"
else
  bash "$tree/scripts/install-claude-config.sh" --dry-run
fi

say "agents rehosted by host_agents.py (dry run, nothing written):"
python3 -I "$tree/scripts/host_agents.py"

if [ "$apply" = 1 ]; then
  say "synced ~/.claude from commit $commit"
  say "reminder: restart Claude Code to pick up the new config"
else
  say "dry run of ~/.claude from commit $commit: nothing written; rerun with --apply to write"
fi
