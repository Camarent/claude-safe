---
paths: [scripts/]
summary: host-side shell and Python helpers: dev checks, pre-PR gate, container test run, VM provisioning, config sync, agent rendering
---
# scripts

## Purpose
Host-side tools for the repo itself. Run from a checkout, never inside the container.
They check code, gate a branch before PR, provision an Ubuntu VM, and copy `.claude/` into `~/.claude`.

## Public surface
- `scripts/dev.sh` subcommands: `sync` (`scripts/dev.sh:19`), `check` (`:21`), `fix` (`:22`), `test` (`:23`), `pytest` (`:24`), `itest` (`:25`, needs `PIPELINE_PROJECT_DIR`), `cov` (`:27`), `covserial` (`:28`), `dead` (`:29`), `sec` (`:30`), `ccn` (`:31`), `clean` (`:32`). Dispatch: `scripts/dev.sh:18`.
- `scripts/release.sh` (no args; any arg prints usage, exit 2 at `:10`). Pre-PR gate: host checks, then container suite and gitleaks.
- `scripts/image_test.sh [IMAGE]` (`scripts/image_test.sh:5`, default `claude-safe:rust`). Runs the test suite in the image, repo mounted read-only.
- `scripts/sync-claude-config.sh [REF]`: sync `~/.claude` from a git ref; refuses while a driver holds `.work/*/wave.lock` (`scripts/sync-claude-config.sh:15`).
- `scripts/provision-dev-vm.sh` flags `--only`, `--skip`, `--dry-run`, `--list`, `-h`: `usage` at `scripts/provision-dev-vm.sh:474`, `parse_args` at `:524`, `main` at `:538`. Step table `STEPS` at `:54`.
- `scripts/host_agents.py [--home DIR] [--out DIR] [--prefix pl-] [--apply]`: `main` at `scripts/host_agents.py:114`, `render` at `:103`.

## Key files
- `scripts/dev.sh` - dev checks; all tool caches live in `./.uv` (`:10`), or `DEV_UV_DIR`.
- `scripts/release.sh` - refuses on uncommitted tracked changes (`:15`). Never merges or pushes.
- `scripts/provision-dev-vm.sh` - Ubuntu 26.04 VM setup without GPU or container. Rerunnable: each step checks first and prints `skip <step>`. Refuses root.
- `scripts/host_agents.py` - renders `.claude/agents/*.md` for the host: `lp` lines become Grep/Glob, `cs-*` names become wrapper paths. Dry run unless `--apply`.
- `scripts/sync-claude-config.sh` - runs `provision-dev-vm.sh --only claude-config` from a detached worktree.
- `scripts/image_test.sh`, `scripts/dev.sh` - the image and host tests share `.claude/tools/ptest.py`.

## Invariants and gotchas
- `dev.sh` uses `UV_PYTHON_PREFERENCE=only-system` (`scripts/dev.sh:13`): never downloads an interpreter.
- A worktree's `.uv` is a symlink to the main checkout's venv; `dev.sh sync` refuses there (`scripts/dev.sh:19`).
- `release.sh` scans a copy of tracked and untracked-but-not-ignored files, not the working dir, so ignored data is not walked.
- `provision-dev-vm.sh` pins copy the `ARG` lines of `Dockerfile` and `profiles/*.Dockerfile`. Keep them equal: `.claude/tools/tests/test_provision_script.py` compares them.
- `provision-dev-vm.sh` warns when not Ubuntu 26.04 (`check_os`), and needs sudo unless `--dry-run`.
- `host_agents.py` imports `.claude/tools/pipeline/cmdindex.py` (`scripts/host_agents.py:16`); the wrappers must exist (`gen-bin`) or it stops with an error.

## Depends on / used by
- Depends on: `.claude/tools/ptest.py`, `.claude/tools/tests/`, `.claude/tools/pipeline/cmdindex.py`, `Dockerfile`, `profiles/`, the `claude-safe:rust` image (docker), `uv`, `flock`, `git`.
- Used by: `README.md` (dev and release steps: `scripts/dev.sh`, `scripts/release.sh`, `scripts/sync-claude-config.sh`). `scripts/release.sh` header: merge waits for the GitHub `ci` workflow.
