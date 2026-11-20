#!/usr/bin/env bash
# Dev checks for the pipeline tools. Everything lands in ./.uv (cache, venv): delete it and the host is clean.
#   scripts/dev.sh sync | check | fix | test | pytest | covserial | itest (needs PIPELINE_PROJECT_DIR) | cov | dead | sec | ccn | clean
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

# DEV_UV_DIR lets git worktrees share one venv and cache (default: ./.uv in this checkout)
uvdir="${DEV_UV_DIR:-$root/.uv}"
export UV_CACHE_DIR="$uvdir/cache"
export UV_PROJECT_ENVIRONMENT="$uvdir/venv"
export UV_PYTHON_PREFERENCE=only-system   # never download an interpreter
export UV_NO_PROGRESS=1

run() { uv run --frozen --no-sync "$@"; }

case "${1:-}" in
  sync)  uv sync --frozen 2>/dev/null || { uv lock && uv sync --frozen; } ;;
  check) run ruff check . && run ruff format --check . && run mypy ;;
  fix)   run ruff format . ; run ruff check --fix --unfixable F401 . ;;   # format first (check exits 1 while findings remain); F401 stays manual: some "unused" imports are re-exports
  test)  ORCH_AGENTS_DIR="$root/.claude/agents" run python .claude/tools/ptest.py "${@:2}" ;;   # parallel, one file per process (patterns optional)
  pytest) ORCH_AGENTS_DIR="$root/.claude/agents" run pytest -q "${@:2}" ;;   # serial runner, for -k / single tests
  itest) [ -n "${PIPELINE_PROJECT_DIR:-}" ] || { echo "itest needs PIPELINE_PROJECT_DIR=<project dir with pipeline.json>" >&2; exit 2; }
         PIPELINE_PROJECT_DIR="$PIPELINE_PROJECT_DIR" run pytest -q .claude/tools/tests/integration "${@:2}" ;;   # opt-in: real project settings, read-only
  cov)   ORCH_AGENTS_DIR="$root/.claude/agents" run python .claude/tools/ptest.py --cov "${@:2}" ;;   # parallel coverage (one file per process, data combined)
  covserial) ORCH_AGENTS_DIR="$root/.claude/agents" run pytest -q --cov --cov-report=term-missing:skip-covered "${@:2}" ;;
  dead)  run vulture .claude/tools scripts --min-confidence 80 ;;
  sec)   run bandit -q -r .claude/tools scripts -x .claude/tools/tests -s B404,B603,B606,B607 ;;   # B404/B603/B606/B607: the tools drive git, node, lp by design; B604/B608 are reviewed with an inline nosec + reason
  ccn)   run lizard .claude/tools -l python -C 15 -x "*/tests/*" -w ;;   # complexity limit: fails (exit 1) and lists every function over CCN 15; every function is under it since 2026-11-09, so there is no baseline
  clean) rm -rf "$uvdir" "$root/.ruff_cache" "$root/.mypy_cache" "$root/.pytest_cache" "$root/.coverage" ;;
  *)     sed -n '2,3p' "$0"; exit 2 ;;
esac
