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
- `scripts/pipeline_auto.py run NAME` (or `feature NAME`): execs `cs-wave <args>` with the full default flags and permission mode `auto` (opt-in). Args pass through unchanged; old aliases `feature --feature F` and `run --stage S` still accepted. `auto_flags` at `scripts/pipeline_auto.py:28`, `plan` at `:36`, `main` at `:45`. First argument other than `feature` or `run` exits 2 (`:51`).

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
- `.claude/tools/pipeline/gate_slots.py` - the shared gate-slot cap: `held(slots)` takes one of N flock slot files under the temp dir, so gate commands from every driver on the host run at most N at once. N is the `gate_slots` setting (default 3 in `.claude/tools/pipeline/configuration/defaults.py`; 0 = no cap). `testrun.py` `run` runs each Test line inside a slot (`_run_line`).
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
- `testcmd:` (error, was a warning): a task `Test:` line runs bare `pytest` or `python -m pytest` while the project names its test command (`test_command_hint`). Example: `testcmd: T4: Test runs bare \`pytest\` but the project's test command is \`<hint>\`: use that, or the runner the project notes name`. Checked by `plan_testcmd.py`.
- `wiring:` (error): a new module under a `wiring_dirs` folder (pipeline.json key `wiring_dirs`, a list of repo folders; `[]` = off) has no task that mounts it. Example: `wiring: T4: src/widgets/new.ts is a new module under wiring dir src/widgets with no mount: list a tracked mount point of the task, or add a dependent task with \`Mounts: T4\``. Checked by `plan_wiring.py`.
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

## Depends on / used by
- Depends on: `.claude/tools/ptest.py`, `.claude/tools/tests/`, `.claude/tools/pipeline/cmdindex.py`, `Dockerfile`, `profiles/`, the `claude-safe:rust` image (docker), `uv`, `flock`, `git`.
- Used by: `README.md` (dev and release steps: `scripts/dev.sh`, `scripts/release.sh`, `scripts/sync-claude-config.sh`). `scripts/release.sh` header: merge waits for the GitHub `ci` workflow.
