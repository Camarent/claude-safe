#!/usr/bin/env bash
# Install ~/.claude from the .claude/ dir of this checkout: the claude-config step, on its own.
# Running it is the whole step; it has no other provision steps.
# Rerunnable: a run that finds nothing to change prints "skip claude-config: already done".
#   scripts/install-claude-config.sh
set -euo pipefail

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 2; }

# claude-config: source is the .claude/ dir of this checkout, the one place the pipeline is changed. Every run
# re-syncs it: a file that differs from the checkout is replaced (the old copy goes to
# ~/.claude/.provision-backup/<UTC time>/), so a pipeline update reaches the VM by rerunning this step.
# Nothing is deleted: files that exist only on the VM (credentials, own skills, memory) stay, and so does a
# file the checkout removed or renamed (delete those by hand). settings.json is merged with existing keys
# winning. Copies keep /root/.claude paths on purpose: paths.json maps them per host.
SRC_CLAUDE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.claude"
CLAUDE_CONFIG_SKIP=(.work logs parked projects bin-host settings.json)
CLAUDE_CONFIG_EXCLUDE=(--exclude=__pycache__ --exclude='*.pyc')
CLAUDE_CONFIG_BACKUP_DIR=".provision-backup"
# claude_config_sync ITEM [rsync option...]: one item of the checkout into ~/.claude (extra options: -n -i for a dry run).
claude_config_sync() {
  local n="$1"
  shift
  rsync -a "${CLAUDE_CONFIG_EXCLUDE[@]}" "$@" "$SRC_CLAUDE/$n" "$HOME/.claude/"
}
# claude_config_drift: succeeds when some file of the checkout is missing or different in ~/.claude (`>f` = file to send).
claude_config_drift() {
  local n
  for n in $(claude_config_items); do
    claude_config_sync "$n" -n -i 2>/dev/null | grep -q '^>f' && return 0
  done
  return 1
}
claude_config_items() {
  local f n s
  for f in "$SRC_CLAUDE"/* "$SRC_CLAUDE"/.[!.]*; do
    [ -e "$f" ] || continue
    n="$(basename "$f")"
    for s in "${CLAUDE_CONFIG_SKIP[@]}"; do [ "$n" = "$s" ] && continue 2; done
    echo "$n"
  done
}
check_claude_config() {
  local n b
  [ -d "$SRC_CLAUDE" ] || return 1
  for n in $(claude_config_items); do [ -e "$HOME/.claude/$n" ] || return 1; done
  ! claude_config_drift || return 1
  for b in "$SRC_CLAUDE"/bin/*; do [ -e "$HOME/.local/bin/$(basename "$b")" ] || return 1; done
  [ -f "$HOME/.claude/settings.json" ] && jq -e '.hooks' "$HOME/.claude/settings.json" >/dev/null 2>&1 \
    && ! grep -qE '(^|[^/])cs-[a-z]+-guard' "$HOME/.claude/settings.json"
}
# Hooks call cs-*-guard by bare name and end in "|| exit 2", which blocks every tool call when ~/.local/bin is
# not on PATH (fresh VM before re-login). Rewrite them to absolute paths; idempotent (lookbehind skips done ones).
absolutize_hooks() {
  local f="$1" tmp
  tmp="$(mktemp)"
  jq '(.. | strings) |= gsub("(?<![/])(?<n>cs-[a-z]+-guard)"; "$HOME/.claude/bin/\(.n)")' "$f" >"$tmp" && cat "$tmp" >"$f"
  rm -f "$tmp"
}
step_claude_config() {
  local n b dst="$HOME/.claude"
  [ -d "$SRC_CLAUDE" ] || { say "error: $SRC_CLAUDE not found" >&2; return 1; }
  mkdir -p "$dst" "$HOME/.local/bin"
  if ! grep -qF "install-claude-config: local-bin" "$HOME/.profile" 2>/dev/null; then
    printf '%s\n' 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) PATH="$HOME/.local/bin:$PATH" ;; esac  # install-claude-config: local-bin' >>"$HOME/.profile"
  fi
  local backup="$dst/$CLAUDE_CONFIG_BACKUP_DIR/$(date -u +%Y%m%dT%H%M%SZ)"
  for n in $(claude_config_items); do
    claude_config_sync "$n" --backup --backup-dir="$backup"
  done
  [ ! -d "$backup" ] || say "replaced files were saved in $backup"
  for b in "$SRC_CLAUDE"/bin/*; do ln -sfn "$dst/bin/$(basename "$b")" "$HOME/.local/bin/$(basename "$b")"; done
  if [ ! -f "$dst/settings.json" ]; then
    cp "$SRC_CLAUDE/settings.json" "$dst/settings.json"
  else
    cp "$dst/settings.json" "$dst/settings.json.bak"
    jq -s '.[0] * .[1]' "$SRC_CLAUDE/settings.json" "$dst/settings.json.bak" >"$dst/settings.json"
  fi
  absolutize_hooks "$dst/settings.json"
  say "reminder: hooks in settings.json need snip (step snip) and ~/.local/bin on PATH; restart Claude Code to pick them up"
}

main() {
  [ "$(id -u)" != 0 ] || die "run as a normal user, not as root"
  if check_claude_config; then
    say "skip claude-config: already done"; return 0
  fi
  say "start claude-config"
  local rc
  set +e
  ( set -e; step_claude_config )
  rc=$?
  set -e
  if [ "$rc" = 0 ]; then say "done claude-config"
  else say "FAIL claude-config (exit $rc)"; return "$rc"; fi
}

main "$@"
