# Architecture

## What it is
claude-safe runs Claude Code in a throwaway Docker container. Only the folder you mount is reachable from the host (`README.md:5-6`).
The repo builds the base image and optional toolchain layers, provides host-side dev and release scripts, and carries the pipeline under `.claude/` (agents, tools, practices; not an index module, see `README.md` and `docs/decisions.md`, whose "Cite verify" section records the orch.jsonl cite check).

## Modules
- [scripts](modules/scripts.md) - host-side dev checks, pre-PR gate, config install and sync, agent rendering.
- [profiles](modules/profiles.md) - two optional toolchain layers (`profiles/*.Dockerfile`) stacked on the base image.

## Flow
1. `Dockerfile` builds the base image (`FROM node:lts-trixie-slim`, `Dockerfile:6`).
2. `run.sh` builds the profile chain: `claude-safe` -> `claude-safe:<p1>` -> ... (`run.sh:163`, `run.sh:211`), then starts the container (`README.md:27-29`).
3. Inside the container, `entrypoint.sh` is the entry point (`Dockerfile:193`).
4. Host side, `scripts/dev.sh` runs lint, format, mypy and the test suite (`ptest.py`); `scripts/image_test.sh` runs the same suite in the image; `scripts/release.sh` runs both plus gitleaks as a pre-PR gate.
5. `scripts/sync-claude-config.sh` runs `scripts/install-claude-config.sh` to copy `.claude/` into `~/.claude`: a dry run by default (no flag; it passes `--dry-run` to `install-claude-config.sh`), which only lists the files it would overwrite; `--apply` writes. D6: after a phase merge the driver runs `pipeline_install_check` (`install_check` in `.claude/tools/pipeline/cli_feature.py`); this repo sets `scripts/sync-claude-config.sh --compare {work_branch}`, and a nonzero exit blocks before the next phase.
6. `scripts/host_agents.py` renders `.claude/agents/*.md` for the host (dry run unless `--apply`).

## Entry points
- `run.sh` - start a session. `--here` mounts `$PWD`; `--rebuild` rebuilds base and profiles (`README.md:27-29`).
- `scripts/dev.sh` - subcommands listed in `modules/scripts.md`; dispatch at `scripts/dev.sh:18`.
- `scripts/release.sh` - pre-PR gate; never merges or pushes (`scripts/release.sh:2-3`).
- `scripts/host_agents.py` - agent rendering; `main` at `scripts/host_agents.py:115`.
- `scripts/pipeline_auto.py` - starts a planned stage or feature in auto permission mode (opt-in); `main` at `scripts/pipeline_auto.py:45`.
- Stage gates: `docs_impact.py` (stale doc check) and `empty_tests.py` (assert-free test check) under `.claude/tools/pipeline/`; see `modules/scripts.md`.
- Shared entry helpers: `.claude/tools/pipeline/entry.py` holds the status-line formatter, the shared run flags, the open-phase resolver and the feature-level BLOCKED copy used by the pipeline entry points. For `run NAME`, `orch.py NAME` resolves its open phase through `entry.resolve_open_phase`; `cs-wave run NAME` picks the open phase through `plan_files.next_open_phase`.

## Build and test commands
- Build images: `./run.sh --rebuild` (`README.md:29`).
- Checks: `scripts/dev.sh check` (ruff, format, mypy); `scripts/dev.sh test` (parallel tests); `scripts/dev.sh cov` (coverage); `scripts/dev.sh sec`, `dead`, `ccn` (`scripts/dev.sh:3`).
- Container suite: `scripts/image_test.sh [IMAGE]`.
- Pre-PR gate: `scripts/release.sh`.
- Setup: `scripts/dev.sh sync` needs `uv` and a system Python 3.11+ (`README.md:201`).
- Tests of the pipeline live in `.claude/tools/tests/`.

## Pipeline model route and tool settings
- Model route: `.claude/tools/pipeline/e1.py:102` (`model_for`) decides every dev and reviewer model. Each dispatch starts on Haiku. Sonnet runs only when an E1 event holds (`.claude/tools/pipeline/e1.py:91`, `e1_event`): (a) the dev gate stayed red after the Haiku tries, (b) the dev reported ESCALATE, (c) a reviewer reported ESCALATE, (d) a fix-gate dev ended BLOCKED or its re-merge FAILed.
- Tool settings: `.claude/tools/pipeline/configuration/literals.py:80` (`_SETTINGS`) holds the named settings (timeouts, the usage-limit recheck slice `limit_recheck_s`, origin remote, price table), each with a default. An env override `CS_<NAME>` (`.claude/tools/pipeline/configuration/literals.py:15`) or the `tool_settings` object in `pipeline.json` (`.claude/tools/pipeline/configuration/literals.py:14`) replaces the default. Read them with `tool_setting` (`.claude/tools/pipeline/configuration/literals.py:100`).

## Pipeline module splits
Five pipeline modules hold code moved out of older modules. Each old module re-exports the moved names, so old import paths keep working.
- `.claude/tools/pipeline/worktree_base.py:24` (`git`): git helpers, manifest lock and write, link layout, base branch. `worktree.py` re-exports these names (`.claude/tools/pipeline/worktree.py:21`).
- `.claude/tools/pipeline/worktree_merge.py:35` (`_git_version`): git version and `merge-tree` checks, unmerged-file queries. `worktree.py` re-exports these names (`.claude/tools/pipeline/worktree.py:78`).
- `.claude/tools/pipeline/plan_check_task.py:38` (`_lint`): per-task plan checks (test refs, cites, commands, paths). `plan_check.py` re-exports them (`.claude/tools/pipeline/plan_check.py:72`).
- `.claude/tools/pipeline/steps_history.py:9` (`history`): event history, retry and report queries. `steps.py` re-exports them (`.claude/tools/pipeline/steps.py:57`).
- `.claude/tools/pipeline/pr_ops.py` (`_ready`, `_merge`): with `ci_available` false the pr-ready and master-merged steps skip the CI wait and merge on the green local gates.
- `.claude/tools/pipeline/pr_release.py:15` (`_release_level`): version bump, release tag and GitHub release of a stage branch. `pr_ops.py` re-exports them (`.claude/tools/pipeline/pr_ops.py:44`). Shared git and gh helpers sit in `.claude/tools/pipeline/pr_base.py`, which both import. `pr_ops.skipped_notes` lists the timed-out steps skipped with a note, for the PR body. `pr_release.py` imports only `pr_base` and `pr_mode`, never `pr_ops`, so there is no import cycle.

## Shared state
- Tool caches and venv: `./.uv` in the checkout, or `DEV_UV_DIR` (`scripts/dev.sh:10`).
- Pipeline stage state is runtime data, kept in an ignored work folder, not in the repo.
- Browser profile: `browser-profile/` on the host, mounted into the container (`README.md:43`).

## Docs
- Per-module contracts: `docs/modules/<slug>.md`; index: `docs/INDEX.md` (generated by `cs-docs-index`).
