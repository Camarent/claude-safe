---
paths: [scripts/]
summary: host-side shell and Python helpers: dev checks, pre-PR gate, container test run, config install, config sync, agent rendering
---
# scripts

## Purpose
Host-side tools for the repo itself. Run from a checkout, never inside the container.
They check code, gate a branch before PR, and copy `.claude/` into `~/.claude`.

## Public surface
- `scripts/dev.sh` subcommands: `sync` (`scripts/dev.sh:28`), `check` (`:30`), `fix` (`:31`), `test` (alias `ptest`, `:32`), `pytest` (`:33`), `itest` (`:34`, needs `PIPELINE_PROJECT_DIR`), `cov` (`:36`), `covserial` (`:37`), `dead` (`:38`), `sec` (`:39`), `ccn` (`:40`, `ccn_check` at `:20`: `-C N` and PATHs override the default 15 and `.claude/tools`), `clean` (`:47`), `gate [--ext|--pytest]` (`:41`: `--ext` = check, dead, sec, ccn; `--pytest` = unit suite; no flag = both). Dispatch: `scripts/dev.sh:27`. Gate run times are checked against `gate_time_warn_s` (pipeline.json key; default in `.claude/tools/pipeline/configuration/defaults.py`), merge-only test files come from `merge_only_tests`.
- `scripts/release.sh` (no args; any arg prints usage, exit 2 at `:10`). Pre-PR gate: host checks, then container suite and gitleaks.
- `scripts/image_test.sh [IMAGE]` (`scripts/image_test.sh:5`, default `$RELEASE_IMAGE`, then `claude-safe:rust`). Runs the test suite in the image, repo mounted read-only.
- `scripts/sync-claude-config.sh [--apply|--compare] [REF]`: sync `~/.claude` from a git ref; dry run by default (lists the files that would be overwritten, writes nothing), `--apply` writes; `--compare [REF]` writes nothing and exits 0 when `~/.claude` matches REF (no file would be overwritten, or the install prints `skip claude-config: already done`), 10 when a file would be overwritten (the driver's `pipeline_install_check`; it runs even while a driver holds the lock); refuses while a driver holds `.work/*/wave.lock` (`driver_running` at `scripts/sync-claude-config.sh:40`, guard at `:52`).
- `scripts/host_agents.py [--home DIR] [--out DIR] [--prefix pl-] [--apply]`: `main` at `scripts/host_agents.py:115`, `render` at `:104`.
- `scripts/pipeline_auto.py run NAME` (or `feature NAME`): execs `cs-wave <args>` with the full default flags and permission mode `auto` (opt-in). Args pass through unchanged; old aliases `feature --feature F` and `run --stage S` still accepted. `auto_flags` at `scripts/pipeline_auto.py:28`, `plan` at `:36`, `main` at `:45`. First argument other than `feature` or `run` exits 2 (`:51`). This repo names it as its `launcher` in `pipeline.json`: `cs-wave run` without the auto flags in `WAVE_CLAUDE_FLAGS` is refused (exit 2, the launcher named on stderr; `.claude/tools/pipeline/launcher_guard.py` `check`). Preview runs (`--dry-run`), `status` and `metrics` pass.

## Key files
- `scripts/dev.sh` - dev checks; all tool caches live in `./.uv` (`:11`), or `DEV_UV_DIR`.
- `scripts/release.sh` - refuses on uncommitted tracked changes (`:15`). Never merges or pushes.
- `scripts/host_agents.py` - renders `.claude/agents/*.md` for the host: `lp` lines become Grep/Glob, `cs-*` names become wrapper paths. Dry run unless `--apply`.
- `scripts/install-claude-config.sh` - installs `~/.claude` from this checkout's `.claude/` dir; rerunnable, prints `skip claude-config: already done` when nothing changes.
- `scripts/sync-claude-config.sh` - runs `install-claude-config.sh` from a detached worktree of a git ref: dry run by default (no flag), `--apply` to write.
- `scripts/pipeline_auto.py` - starts a planned stage or feature in auto permission mode; imports `pipeline.configuration` from `.claude/tools` (`scripts/pipeline_auto.py:17`).
- `scripts/image_test.sh`, `scripts/dev.sh` - the image and host tests share `.claude/tools/ptest.py`.
- Ledger figures (`cs-ledger`, `.claude/tools/retro/ledger.py`): the lead part covers a session only when its transcript has a `subagents/` dir next to it. Headless driver sessions have no `subagents/` dir: they are counted only with `--metrics`, which takes their ids from `metrics.jsonl`; without it they are left out. The `LEAD-COST` lines cover the lead session by stage window. Log layout and session ids: `docs/headless-session-logs.md`.
- `.claude/tools/pipeline/docs_impact.py` - stage gate: a doc that cites a file or top-level def the job diff changed must change too; `check` at `.claude/tools/pipeline/docs_impact.py:91`.
- `.claude/tools/pipeline/entry.py` - shared entry helpers: the status-line formatter, the shared run flags, the open-phase resolver and the feature-level BLOCKED copy; `write_blocked` writes it.
- `.claude/tools/pipeline/gate_slots.py` - the shared gate-slot cap: `held(slots)` takes one of N flock slot files under the temp dir, so gate commands from every driver on the host run at most N at once. N is the `gate_slots` setting (default 3 in `.claude/tools/pipeline/configuration/defaults.py`; 0 = no cap). `testrun.py` `run` (the Test-line runner; not `plan_run.py` `run`) runs each Test line inside a slot (`_run_line`).
- `.claude/tools/pipeline/gitx.py` - `gitx.ignored(repo, paths)`: the paths git ignores (one `check-ignore` call per path; tracked and outside-repo paths are not reported). Shared by the plan checks and the callers that used to copy it.
- `.claude/tools/pipeline/task_text.py` - the shared task-file field parser: `field(text, name)` and `lines(text, *names)` read `NAME:` lines, skipping fenced code blocks. Used by brieflint and the plan checks.
- `.claude/tools/pipeline/plan_problem.py` - `Problem(kind, level, text)`: a str equal to its text that also carries its `kind:` token and level. `KIND_LEVELS` maps each kind to `ERROR` or `WARN`; an unknown kind is an error.
- `.claude/tools/pipeline/empty_tests.py` - stage gate: lists test functions in a Python test file that assert nothing; `find` at `.claude/tools/pipeline/empty_tests.py:46`.

## Invariants and gotchas
- `dev.sh` uses `UV_PYTHON_PREFERENCE=only-system` (`scripts/dev.sh:14`): never downloads an interpreter.
- A worktree's `.uv` is a symlink to the main checkout's venv; `dev.sh sync` refuses there (`scripts/dev.sh:28`).
- `release.sh` scans a copy of tracked and untracked-but-not-ignored files, not the working dir, so ignored data is not walked.
- `host_agents.py` imports `.claude/tools/pipeline/cmdindex.py` (`scripts/host_agents.py:16`); the wrappers must exist (`gen-bin`) or it stops with an error.

## Plan check kinds (stricter checks, M25 retro)
Each problem line starts with its kind token. The level comes from `KIND_LEVELS` in `plan_problem.py`: `error` fails `cs-plan check`, `warn` never does. Examples:
- `ignored-done:` (error): a path on a task's Done, Goal or Test line is git-ignored, so the job never commits it. Example: `ignored-done: T3: path \`build/out.log\` on a Done, Goal or Test line is git-ignored`. Checked by `plan_outputs.py` via `gitx.ignored`.
- `cite-short:` (error): a bare file name cite (no directory) resolves to a file in a subdirectory or to 2+ files of the tree. Example: `cite-short: loader.py has no directory and the tree has pipeline/loader.py, tools/loader.py; cite the full path`. Checked by `plan_check_task.py`.
- `research-hint:` (warn): a task's Goal or Done names a fact the repo may lack and the task depends on no research task. Example: `research-hint: T7: its Goal or Done needs a fact the repo may lack: add a \`Type: research\` task that finds it and depend on it`. Checked by `plan_research_hint.py`.
- `carried-notes:` (error): a bullet in the later-notes file names a path of this stage but no `- task-N ...` or not-needed line in `## Carried`. Example: `carried-notes: \`pipeline/loader.py\` names a path of this stage: add a \`- task-N ...\` or \`- <not-needed> reason\` line to \`## Carried\``. Checked by `plan_check.py`.
- `publish-recipe:` (error): a URL of a `PUBLISH:` line in the stage TASKS.md has no `publish` entry in pipeline.json, so the republish step cannot build it. Example: ``publish-recipe: PUBLISH: https://example.test/x has no `publish` recipe in pipeline.json: add the entry, or drop the line``. Checked by `plan_check.py` (`publish_recipe_problems`).
- `testcmd:` (error, was a warning): a task `Test:` line runs bare `pytest` or `python -m pytest` while the project names its test command (`test_command_hint`). Example: `testcmd: T4: Test runs bare \`pytest\` but the project's test command is \`<hint>\`: use that, or the runner the project notes name`. Checked by `plan_testcmd.py`.
- `wiring:` (error): a new module under a `wiring_dirs` folder (pipeline.json key `wiring_dirs`, a list of repo folders; `[]` = off) has no task that mounts it. Example: `wiring: T4: src/widgets/new.ts is a new module under wiring dir src/widgets with no mount: list a tracked mount point of the task, or add a dependent task with \`Mounts: T4\``. Checked by `plan_wiring.py`.
- `testroot:` (error): a vitest `Test:` line names a path that repeats the vitest root of its runner (pipeline.json key `test_roots`, list of `{runner, root}`). Example: `testroot: T3: Test path \`web/src/a.test.ts\` repeats the test root \`web\`: drop the \`web/\` prefix, the runner runs inside its root`. Checked by `plan_testroot.py`.
- `reach:` (warning): a task with a `Mounts:` header has no Done line naming an entry proof (entry, bundle, built, cli, route or e2e). Example: `reach: T4: its \`Mounts:\` header needs a Done line naming an entry proof (entry, bundle, built, cli, route or e2e), not only a harness, mock or fake`. Checked by `plan_wiring.py`.
- `tool-block:` (error): a Test or Done line runs a JS tool (madge, knip, eslint) whose smoke probe fails. Example: `tool-block: T6 uses knip: <probe reason>`. Checked by `plan_toolblock.py` through `tool_probe.py`.
- `knip-prod:` (error): a Test or Done line runs `knip --production` while the knip entry config does not allow it. Example: `knip-prod: T6 runs <KNIP_PROD_CMD>: <reason>`. Checked by `plan_toolblock.py`.
- `quote-missing:` (error): a Done line quotes a message (12+ characters after warning, message, text or prints) that no tracked file holds and the task text does not create. Example: `quote-missing: T7: Done quotes '<quoted text>' but no tracked file has it and the task does not create it`. Checked by `plan_quotes.py`.

The tools check (`xtools.py check`, the `tools-check` audit step) warns `warning (not used by this project, install failed): <tool>` for a tool that failed to install and that AUDIT.md does not need; it never fails the check. The `--needed-by` and `--stage-dir` flags decide that.

Other pieces of the wiring checks:
- `plan_wiring_orphans.py` (`.claude/tools/pipeline/plan_wiring_orphans.py`): the orphan check, run by the audit phase. It lists each source file added since the base under a `wiring_dirs` folder that no other tracked source file imports, in `.work/<stage>/audits/orphans.md`. It always exits 0: it warns, never blocks. Its folders come from pipeline.json `wiring_dirs`. Example: `python3 -I .claude/tools/pipeline/plan_wiring_orphans.py --workspace . --base main --out .work/<stage>/audits/orphans.md`.
- `tool_probe.py` (`.claude/tools/pipeline/tool_probe.py`): smoke probe for madge, knip and eslint: runs `<tool> --version`, checks the peer range against the project's typescript and the knip entry config. `probe(tool, proj)` at `.claude/tools/pipeline/tool_probe.py:95`. Used by the `tool-block:` check.
- `webtest_report.py` (`.claude/tools/pipeline/webtest_report.py`): writes the browser-check report stamped with the branch HEAD (`HEAD: <sha>` line). `stale(text, head)` at `.claude/tools/pipeline/webtest_report.py:46` returns a `stale webtest:` reason when that HEAD line is missing or differs from the branch HEAD. Example line: `HEAD: 3f2a9c1`.

The brieflint pre-flight (`.claude/tools/brieflint.py`) also rejects a `Test:` line with `;` or `|`, a `&&` chain whose later segment is not `cs-qrun --` or a read-only command, or a first segment that is not `cs-qrun --`.

## Followup behaviour (retro-m25-followup)
Each paragraph names the code file that holds the change.

- Plan check `ptest_selector_problems` (`.claude/tools/pipeline/plan_ptest.py`): a `Test:` line whose ptest selector matches no test file fails `cs-plan check`.
- Plan size and gate rules (`.claude/tools/pipeline/plan_rules.py`): the task size cap and the protected gates a task may not drop are checked at plan time.
- Bare cite rewrite (`.claude/tools/pipeline/cite_rewrite.py`, `rewrite_bare_cites`): a bare module-name cite in a dev report is rewritten to its full repo-relative path before the cite check runs.
- Feature install check (`.claude/tools/pipeline/cli_feature.py`, `install_check`): after each phase merge, while another phase is open, the driver runs the install check; a nonzero exit blocks before the next phase and the phase stays done.
- Settings (`.claude/tools/pipeline/configuration/project.py`): `pipeline.json` is overlaid by `pipeline.local.json` in the workspace, and the `PIPELINE_OVERRIDES` env JSON is overlaid last.
- qrun summary (`.claude/tools/qrun.py`, `_ruff_summary`): a ruff failure prints a one-line `ruff: <findings>` summary before the excerpts.
- Acceptance order (`.claude/tools/pipeline/phases.py`, `_acceptance`): the acceptance items run in the order the plan lists them, after the planned tasks and before the docs step.
- Merged PR closes the stage out (`.claude/tools/pipeline/pr_ops.py`, `stage_pr_merged`, used by `cli_wave.py` `_closed_by_merged_pr`): a stage run whose work branch PR is MERGED prints the close-out line and returns before the plan is read; no gh, no PR or a bad gh reply keeps the old run.
- PR update-branch retry (`.claude/tools/pipeline/pr_ops.py`): a merge refused because the head is behind its base gets one `gh pr update-branch` and one more merge try.
- Gate flake in the pr-ready gates (`.claude/tools/pipeline/pr_ops.py`, `_settles_alone`, used by `_gates_gate`): a red gate whose failing test files pass when rerun alone is counted green and logged as a flake (`gate_record`, `GATE_KIND_FLAKE`); a red still red alone stays red.
- Nit-only PASS (`.claude/tools/pipeline/verdicts.py`, `is_nit_only_fail`; used by `.claude/tools/pipeline/steps_review.py`, `_nit_pass`, which `.claude/tools/pipeline/steps.py` re-exports): a reviewer FAIL whose counts are wrong 0, risky 0, nit above 0 is read as nit-only; after the failed rounds the task gets a PASS with the nits file as its notes.
- Stage branch (`.claude/tools/pipeline/branches.py`): the main checkout's dirty guard and the switch to the stage or feature branch both use the stage name.
- Gate slots (`.claude/tools/pipeline/gate_slots.py`, `held`): the host-wide gate cap is the `gate_slots` setting; `.claude/tools/testrun.py` runs each Test line inside one slot.
- Worktree requirements (`.claude/tools/pipeline/worktree.py`, `_requirement_steps`, `_run_setup`; defaults in `.claude/tools/pipeline/configuration/defaults.py`): the `worktree_requirements` globs are pip-installed into each new job worktree's venv after the `worktree_setup` commands; a missing file is skipped and a failing install removes the worktree.
- Retry refresh rebase skip (`.claude/tools/pipeline/worktree.py`, `refresh`): job commits already on the stage branch under another sha (patch-id test, `rev-list --cherry-pick --right-only`) are not replayed; a job with no commit left off the stage is not rebased.
- Retry refresh (`.claude/tools/pipeline/steps_history.py`, `_after_retry`): a task's history after a retry marker keeps only the events after that marker, so a reset starts the review rounds again.
- Final-test flake rerun (`.claude/tools/pipeline/steps_finaltest.py`, `triage`, `record_flake`): a final-test FAIL whose failing test files pass when rerun alone is logged as a flake (gate record kind `GATE_KIND_FLAKE`, `.claude/tools/pipeline/gate_record.py`) and gets no fix plan; a red still red alone goes on to the fix plan.
- Plan check `testroot:` (`.claude/tools/pipeline/plan_testroot.py`, `check_vitest_root`): a vitest `Test:` line whose path repeats the runner root (pipeline.json `test_roots`) fails `cs-plan check`.
- Plan check `testwrap:` (`.claude/tools/pipeline/plan_testwrap.py`, `check_test_wrap`): a `Test:` line that matches a pipeline.json `test_wrap` rule but lacks its required wrapper text fails `cs-plan check`; the message names the wrapper to use.
- Plan check `docs-impact:` (`.claude/tools/pipeline/plan_docs_impact.py`, `check_docs_impact`): a doc that cites a Predicted path of a task must be in that task's Predicted paths, or a doc-only task must list it.
- Plan check `plan-gap:` for `Needs:` lines (`.claude/tools/pipeline/plan_needs.py`, `check_needs`): a task's `Needs: <repo path> :: <literal>` line fails the plan when the path is missing or lacks the literal, unless a task it depends on lists that path in its Predicted paths.
- Settings defaults (`.claude/tools/pipeline/configuration/defaults.py`): `test_roots`, `test_wrap` and `roadmap_file` default to empty, which means off; `worktree_requirements` defaults to an empty list.
- Retry note lint (`.claude/tools/pipeline/retry_note.py`): a retry NOTE that follows a dev PLAN GAP report gets a warning while the task file is unchanged since that report.
- Merge gate skip (`.claude/tools/merge_wave.py`, `_passed_gates`): a merge gate command with a green qrun record on the merged tree is skipped and logged as skipped (its `gate_cmd` record has `skipped: true`, `seconds: 0`; `_log_skipped`). Every `gate_cmd` record carries `mode` (the gate mode flag, `gate_mode.mode_of`), `concurrent` (gate commands running at once when the command started; 1 when alone) and `skipped`.
- Full gate record (`.claude/tools/audit/final_check.py`, `main`): with `--stage S`, each command of the final run is logged to `.work/S/metrics.jsonl` as a `gate_cmd` record with `kind` `full` (the run carries `PIPELINE_GATE_KIND=full`, level `full`) and checked against the `full` limit of `gate_time_warn_s`; an over-limit run prints a warning line to stderr, and the report and exit code stay unchanged. Without `--stage` nothing is written.
- Roadmap audit pass (`.claude/tools/pipeline/cli_orch.py`, `_remit_verdict`): an auditor FIXES list whose severe lines are all should-fix items repeating an open `roadmap_file` item becomes CLEAN.
- Open plan-review gaps warning (`.claude/tools/pipeline/plan_gaps_open.py`): the stage or feature folder's plan-review gaps still open are read at stage start and shown to the lead as a warning.

## Depends on / used by
- Depends on: `.claude/tools/ptest.py`, `.claude/tools/tests/`, `.claude/tools/pipeline/cmdindex.py`, `Dockerfile`, `profiles/`, the `claude-safe:rust` image (docker), `uv`, `flock`, `git`.
- Used by: `README.md` (dev and release steps: `scripts/dev.sh`, `scripts/release.sh`, `scripts/sync-claude-config.sh`). `scripts/release.sh` header: merge waits for the GitHub `ci` workflow.
