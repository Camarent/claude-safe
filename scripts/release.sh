#!/usr/bin/env bash
# Pre-PR gate: every check (host and container) on the current branch, then a verdict. Never merges, never pushes:
# merging is a squash-merge of a pull request once the GitHub ci workflow is green.
#   scripts/release.sh
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
image="${RELEASE_IMAGE:-claude-safe:rust}"   # needs node, cargo, rust-analyzer, pyright, gitleaks
[[ $# -eq 0 ]] || { sed -n '2,5p' "$0"; exit 2; }

step() { printf '\n== %s\n' "$1"; }
fail() { printf 'RELEASE BLOCKED: %s\n' "$1" >&2; exit 1; }

[[ -z "$(git status --porcelain --untracked-files=no)" ]] || fail "uncommitted changes"

uvdir="${DEV_UV_DIR:-$root/.uv}"
{
  logs="$(mktemp -d)"
  tracked="$(mktemp -d)"; trap 'rm -rf "$tracked" "$logs"' EXIT
  # Container suite and gitleaks run in the background beside the host steps; their output is printed after the host steps.
  docker run --rm --network none -e PYTHONDONTWRITEBYTECODE=1 -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/src -v "$root":/src:ro -w /src --entrypoint python3 "$image" -I /src/.claude/tools/ptest.py --dir /src/.claude/tools/tests >"$logs/suite" 2>&1 &
  suite_pid=$!
  (
    # Scan a copy of the tracked files only: the working dir holds ignored data that --no-git would walk.
    git ls-files -z --cached --others --exclude-standard | tar --null --ignore-failed-read -T - -cf - | tar -xf - -C "$tracked"
    docker run --rm --network none -v "$tracked":/src:ro --entrypoint gitleaks "$image" detect --source /src --no-git --no-banner --redact
    docker run --rm --network none -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=/src -v "$root":/src:ro --entrypoint gitleaks "$image" detect --source /src --no-banner --redact --log-opts="--all"
  ) >"$logs/leaks" 2>&1 &
  leaks_pid=$!

  step "host: check (ruff, format, mypy)";  scripts/dev.sh check
  step "host: tests with coverage floor";   scripts/dev.sh cov
  step "host: dead code and security";      scripts/dev.sh dead | tee /dev/stderr | wc -l | grep -qx 0 || fail "vulture findings"
  scripts/dev.sh sec
  step "host: complexity gate";             scripts/dev.sh ccn

  step "container ($image): full suite";    wait "$suite_pid" || { cat "$logs/suite"; fail "container suite"; }
  cat "$logs/suite"
  step "container: secrets in files and history"; wait "$leaks_pid" || { cat "$logs/leaks"; fail "gitleaks"; }
  cat "$logs/leaks"
}

printf '\nAll checks passed on %s (%s).\n' "$(git rev-parse --abbrev-ref HEAD)" "$(git rev-parse --short HEAD)"
