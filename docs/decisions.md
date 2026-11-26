# Pipeline hardening decisions

One section per problem of the stage.

## Problem 1

Reason: Gate lists derive from the CI workflow and `ci-drift` is an error, so the stage gates run what CI runs (ccn, bandit, format check, cov, markers). Tests needing a binary skip with a reason; CI records the binary.

## Problem 2

Reason: A guard in ptest records the main checkout branch before and after a run. The driver restores the branch, logs once and does not block the merge.

## Problem 3

Reason: The `write_guard` PreToolUse hook denies a Write/Edit to a main-checkout path while the guard env is set for the session. The dev session's cwd is its job worktree (see Problem 15).

## Problem 4

Reason: `flags: docs` with no docs root is reported at plan time. A retry conflict is recorded as a blocked step with the conflict text (`.claude/tools/pipeline/cli_orch.py:450`).

## Problem 5

Reason: `cs-plan check` rejects a done_when line that is neither a runnable command nor `MANUAL:`. A reviewer agent checks each MANUAL item and writes one verdict line each.

## Problem 6

Reason: The acceptance run keeps the failing test log in the stage reports folder. `test_driver_script.py` no longer depends on the environment.

## Problem 7

Reason: `test_lsp_e2e.py` states an explicit skip reason and has a 20-run stability check.

## Problem 8

Reason: `master-merged` polls CI with named timeout and interval settings and merges on green. It blocks only on red or timeout.

## Problem 9

Reason: `orch.py retry --verify` runs the Test and Done checks on the existing job commit and writes the report. No dev run happens.

## Problem 10

Reason: The architect step fills `Affected tests:` from the importers of the planned paths. The plan reviewer checks it.

## Problem 11

Reason: The task brief includes the import header of the sibling test file named in `Pattern:`, so new tests do not fail on a missing `sys.path` line.

## Problem 12

Reason: A cleanup step in the finish phase removes merged or gone job worktrees and run temp dirs. A list-only `--dry-run` mode changes nothing.

## Problem 13

Reason: `scripts/sync-claude-config.sh` syncs `~/.claude` in one command. It is a dry run by default (lists the files it would overwrite, writes nothing); `--apply` writes. It refuses while a driver is mid-step and never runs `host_agents.py --apply`.

## Problem 14

Reason: main-dirt (a dev's write into the main checkout) is BLOCKED, not FAIL, because the dev cannot fix it from its worktree and a FAIL retry would only repeat the write. The driver names the paths, deletes nothing and clears by itself once the paths are gone; the lead decides after `git -C <main> status` and runs `orch.py retry`.

## Problem 15

Reason: A dev agent runs with cwd = its job worktree, so its edits land in the tree it reviews. The main checkout and the scratch folder are added with `--add-dir`; reviewer and read-only roles keep the project cwd. The `write_guard` hook (`cs-write-guard`) reads the five `PIPELINE_GUARD_*` env names, and every denied write is recorded in the guard log, which is how attribution is checked.

## Pipeline tuning

Owner decisions (from the stage request):

- Gates: `cov` replaces `test`. Reason: `cov` runs the same tests with coverage, so one gate less; a failing test makes `cov` exit non-zero (task-2).
- Haiku rework first: after a Haiku review FAIL, a Haiku rework runs before Sonnet. Reason: cheaper; the reviewer still checks the same lines (task-14).
- Docs-only diffs skip the task gate only. Reason: merge gate and CI still check them (task-17).
- Opus and Fable stay out of the dev, review and rework paths. Reason: owner cost decision (task-14, task-24).

Stage decisions, one per task. Reason = the report item it fixes.

- task-1: every new named setting lives in `configuration/tuning.py`. Reason: one owner for settings (no literals).
- task-2: separate `test` gate dropped; `cov` must fail on a failing test; `dev.sh sync` refuses a symlinked `.uv`. Reason: time 6, stability 11.
- task-3: review_guard allows the report file by main path; shared denial log helper. Reason: stability 5, 9.
- task-4: cmd_guard `cd_git_why` fixes; deny rows go to the denial log. Reason: stability 9, 10.
- task-5: claude.py SIGHUP kills sessions; a stopped child is no report; pgid file. Reason: stability 3a, 3c.
- task-6: cli_orch auto retry ignores rc=-N reasons; one shared unreported helper. Reason: stability 3b, 20.
- task-7: cli_wave stale re-run limit from setting; kill live pgid before re-run; squash-residue reset. Reason: stability 3c, 3d, 7, 20.
- task-8: temp leaks: cleanup prunes merged `job/*` branches, `qrun-tmp-*`, `ptest-*`; qrun log cap 100. Reason: stability 12a, 14 (qrun log cap, `qrun-tmp-*` and `ptest-*` prune only; LSP script part dropped), time 13.
- task-9: skip generated-index regeneration in parallel waves; log seconds per worktree_setup command. Reason: stability 4, time 8.
- task-10: ptest branch guard compares non-job branches only; test base asserts refs unchanged. Reason: stability 12b, 13.
- task-11: zero-count ESCALATE with no security keyword is PASS with notes. Reason: stability 15.
- task-12: env failures classified; worktree_setup reruns once before asking. Reason: stability 1, 16.
- task-13: dev BLOCKED with committed work and an env/test reason runs the verify path. Reason: stability 2.
- task-14: rework ladder: Haiku rework first, Sonnet only after it fails; the complexity review route starts on Haiku. Reason: tokens 3, 5.
- task-15: final-test FAIL reruns failing files alone, then on the preflight head. Reason: stability 6.
- task-16: third round of a repeating docs-only finding becomes a script step; auditor retry is the default. Reason: stability 16, time 11.
- task-17: task gate skips docs-only diffs; linkguard flags line-number cites. Reason: time 3, 4.
- task-18: merge dedup by tree hash; red after clean merge goes to the task dev; merger gets conflicted hunks. Reason: time 2, 9, tokens 14.
- task-19: briefs: slice cap 3000 with Pattern; verbatim findings; reviewer gets diff and green gate inline. Reason: time 4, tokens 8, 11, 12.
- task-20: agent files split; dev batches reads; reviewer PASS report is counts only. Reason: tokens 2, 4, 9; time 5.
- task-21: cs-plan batch: several tasks in one call. Reason: tokens 7.
- task-22: plan_check errors when the Goal asks for a file or function that already exists. Reason: stability 8.
- task-23: BASE_FLAGS adds `--disable-slash-commands --strict-mcp-config --exclude-dynamic-system-prompt-sections`. Reason: tokens 1.
- task-24: ready steps ordered by model; audits run together on the first pass; retro-digest after PR ready. Reason: time 10, tokens 10.
- task-25: audit and plan-fix briefs slice to the block paths; 1 KB digest for fix architects. Reason: tokens 6, 13.
- task-26: guidelines digest of 60 lines for the changed file types. Reason: tokens 15.
- task-27: CI poll every 10 s with one log line per poll; `statusCheckRollup` used only in `_sweep`. Reason: time 12 (poll part), stability 17 (poll log line part only); also the guard test that stability 18 suggests (item 18 itself is dropped, see the map).
- task-28: `wt.py merge` honours the driver lock by owner pid. Reason: stability 22.
- task-29: post-exit summary script for the lead. Reason: stability 23.
- task-30: this section, `test_image_files.py`, image suite run by the lead.

Item map. Report, item, task id or drop reason:

| report | item | task |
|---|---|---|
| stability | 1 | task-12 |
| stability | 2 | task-13 |
| stability | 3 (3a-3d) | task-5, task-6, task-7 |
| stability | 4 | task-9 |
| stability | 5 | task-3 |
| stability | 6 | task-15 |
| stability | 7 | task-7 |
| stability | 8 | task-22 |
| stability | 9 | task-3, task-4 |
| stability | 10 | task-4 |
| stability | 11 | task-2 |
| stability | 12 (12a, 12b) | task-8, task-10 |
| stability | 13 | task-10 |
| stability | 14 | task-8 (qrun log cap, `qrun-tmp-*`, `ptest-*` prune); LSP script part dropped: the script is untracked `.work` scratch, its ptest dirs are pruned by task-8 |
| stability | 15 | task-11 |
| stability | 16 | task-12, task-16 |
| stability | 17 | task-27 (log line per CI poll); gitleaks ignore and live-run parts dropped: they need a live CI run |
| stability | 18 | dropped: `gh` token cannot read check runs is known and handled in code (`pr_ops._sweep` falls back to workflow runs); out of scope as "known, handled". task-27 adds only the suggested guard test |
| stability | 19 | dropped: local model never answers, the gate judge is inert; the request puts the inert local gate judge out of scope |
| stability | 20 | task-6, task-7 |
| stability | 21 | dropped: manual `retry --from review` role write is known and already fixed in hardening phase 03 (`cli_orch.cmd_reset` writes `role: reviewer`); listed only as closed |
| stability | 22 | task-28 |
| stability | 23 | task-29 |
| time | 1 | dropped: project-specific (other repo, check-all.sh) |
| time | 2 | task-18 |
| time | 3 | task-17 |
| time | 4 | task-17, task-19 |
| time | 5 | task-20 |
| time | 6 | task-2 |
| time | 7 | dropped: project-specific |
| time | 8 | dropped: project-specific; only setup timing kept in task-9 |
| time | 9 | task-18 |
| time | 10 | task-24 |
| time | 11 | task-16 |
| time | 12 | dropped: concurrency (project-specific ci.yml; image jobs must finish); CI poll part in task-27 |
| time | 13 | task-8 |
| tokens | 1 | task-23 |
| tokens | 2 | task-20 |
| tokens | 3 | task-14 |
| tokens | 4 | task-20 |
| tokens | 5 | task-14 |
| tokens | 6 | task-25 |
| tokens | 7 | task-21 |
| tokens | 8 | task-19 |
| tokens | 9 | task-20 |
| tokens | 10 | task-24 |
| tokens | 11 | task-19 |
| tokens | 12 | task-19 |
| tokens | 13 | task-25 |
| tokens | 14 | task-18 (merger hunks) |
| tokens | 15 | task-26 |

## Stage pipeline-pr-ready (2026-11-23)

Owner decisions per task. Reason = the report item it fixes.

- task-1 to task-12: the pr-ready CI-line rules are one list. Lines that need a GitHub-only variable (`RUNNER_*`, `GITHUB_*`), a secret or a `${{ }}` expression, and bare shell settings (`set ...`, `cd X`, `export X`, `VAR=value`) are never run locally. The same list feeds drift and `ci_commands`. Source: `.claude/tools/pipeline/configuration/pr_gates.py:14`, `.claude/tools/pipeline/ci_drift.py:3`.
- Hint instead of auto-skip: a red CI-only line gets a `ci_drift_ignore` hint with its leading words; the pipeline never skips it on its own. Source: `.claude/tools/pipeline/pr_ops.py:252`.
- Environment-only wording: the pr-ready report words a CI-only red as environment-only, not as a code fault.
- `ci_clean`: snapshots the worktree, removes only the untracked files that are new since the snapshot, and never runs `git clean`. It also removes a stray `requirements-ci.txt`. Reason: no loss of owner files.
- Stub reviewer report: a reviewer report that is only a stub counts as unclear. The driver runs one automatic Haiku re-run before it asks the owner.
- Cite check: runs inside `testrun.py` after the Test lines are green. A wrong `path:line` cite is FAIL. The `cite_check` key in `pipeline.json` is the switch; default on. Source: `.claude/tools/testrun.py:176`, `.claude/tools/pipeline/configuration/cite_check.py:29`.
- Docs impact: runs in `testrun.py` after the cite check. A file the job diff changes, or a top-level def or class it changes (column-0 line or hunk context; indented methods and names under 3 characters are not read), that a doc cites fails the task unless that doc is changed too. A doc cites a file by its word-bounded path or by a `path:line` cite, a def by its name in backticks. `docs_impact_gate` in pipeline.json is the switch, default on; `docs_impact_exempt` lists the globs never flagged, default `docs/INDEX.md`, `docs/decisions.md`, `docs/retro-lessons.md`. Source: `.claude/tools/testrun.py:200`, `.claude/tools/pipeline/docs_impact.py:72`.
- `INDEX.md` reviewer/dev rule: the reviewer and dev briefs follow the `INDEX.md` rule. `docs/INDEX.md` itself is regenerated by the lead.
- Skip-gate warning and marker rule: a gate that skips a test is warned about. Opt-in tests use a registered marker that the conftest deselects; `skipif` and `pytest.skip` are not used. Reason: `cs-qrun` exits 3 on any skipped test.
- Retry-note rule: a retry note names the file, the line and the replacement text (task-12; `cli_orch.py`, `ORCHESTRATOR.md`).
- Orphan job branch prune: branch-cleanup (phase `branch-cleanup`, `.claude/tools/pipeline/phases.py:230`, `pr_ops._cleanup` in `.claude/tools/pipeline/pr_ops.py:644`) deletes merged orphan `job/*` branches itself. Unmerged ones are kept and named in the step note and by `cleanup.py` main.
- `ci_drift_ignore` is described in `.claude/practices/orch-pipeline.md` (CI drift warning). The cite check and its `cite_check` switch are described there too.
- main-dirt: dirt in the main checkout ends the step BLOCKED, not FAIL. Reason: the cause is outside the job (a file the lead or another process left), no dev retry can fix it, and a FAIL would burn the retry ladder; BLOCKED waits for the lead, auto-clears when the dirt is gone, and the driver never deletes user files.

## Stage pipeline-fixes

- task-10: public docs and CI reads are documented in `practices/pipeline-dev.md` and `ORCHESTRATOR.md`. `public_forbidden_words` is a per-project list; an empty list or a private repo skips the scan, so it never fails on private docs. Source: `.claude/tools/pipeline/configuration/public.py:3`, `.claude/tools/ops_steps.py:170`.

## Stage packaging-safety

Owner decisions for the packaging and safety task (task-9). Reason = the report item it fixes.

- `settings.json allows` keeps `git add`, `git commit` and `git merge`. The pipeline needs them to commit job work and merge stage branches. No change to `.claude/settings.json`. Source: the allow list in `.claude/settings.json`.
- Pins policy: the base image pins its Node base by digest (`FROM ...@sha256:` in `Dockerfile`). Extra tools are pinned and vetted in `.claude/EXTRA-TOOLS.md`. A new unpinned image or tool is a review item.
- Token opt-in: host tokens are not forwarded by default. `--with-gh-token` and `--with-cloudflare-token` in `run.sh` forward one token each, for the session only. Reason: a forwarded token is readable by anything in the session.
- Auto mode is opt-in by script: `scripts/pipeline_auto.py` starts a stage in auto permission mode (`PERMISSION_MODE_AUTO`). The default run keeps the allowlist prompt. Reason: auto mode removes the prompt, so it needs a deliberate start.
- Packaging: `package = false` in `pyproject.toml`. The supported install is the `.claude/bin` wrappers on PATH; the project metadata documents the entry points only. Reason: the tools have no runtime dependencies and the wrappers are the install path.

## E1 dispatch and old logs

Reason: the model route is one rule now: a Sonnet dev or reviewer needs an E1 event, logged as `E1(a|b|c)` (`pipeline/e1.py`). Old logs with a ccn or extra-round why still parse: the task state reads the why as text and no parser drops it. A running stage whose task sits on Sonnet without an E1 event gets Haiku at its next dispatch, because the model is read from the events, not from a flag. Reason for the change: owner cost decision, fewer Sonnet dispatches.

## Strict typing: disallow_untyped_defs (task-13)

Reason: the modules below have every def annotated, so mypy runs them with `disallow_untyped_defs = true` and `disallow_incomplete_defs = true` (the `[[tool.mypy.overrides]]` block in `pyproject.toml`). Found by running mypy with that flag and keeping the modules with no error line; no source was edited for this. Other modules stay on the looser default until their defs are annotated.

Modules (24, `pipeline` package):

- `pipeline`, `pipeline.agents`, `pipeline.plan_review`, `pipeline.run`, `pipeline.steps_docfix`, `pipeline.verify_retry`, `pipeline.webskip`, `pipeline.worktrees`
- `pipeline.configuration`, `pipeline.configuration.ci_poll`, `pipeline.configuration.cite_check`, `pipeline.configuration.cleanup`, `pipeline.configuration.defaults`, `pipeline.configuration.env`, `pipeline.configuration.errors`, `pipeline.configuration.layout`, `pipeline.configuration.limits`, `pipeline.configuration.planning`, `pipeline.configuration.pr_gates`, `pipeline.configuration.public`, `pipeline.configuration.qrun`, `pipeline.configuration.serve`, `pipeline.configuration.tuning`, `pipeline.configuration.verdicts`

## Plan checks and brief rules (phase 01-small-fixes)

Reason: the small pipeline fixes of this phase, each named by its behaviour. Fix-gate route: a fix-gate dev that ends BLOCKED, or whose re-merge FAILs, is a Sonnet dev only through E1(d) (`pipeline/e1.py`). Plan check `qrun-op:`: a `Test:` line with a shell operator right after `cs-qrun --` fails `cs-plan check`, because cs-qrun runs a program, not a shell. Plan check `pins:`: a task that edits a data or setting file a test pins must list that test. Plan check `docs-count:`: a docs task that asks a hand-written doc for a machine count or a line reference gets this warning; the generated docs index holds those. The cite check skips the line checks of a file that the task splits, and its failure note names full paths. The reviewer brief treats the generated INDEX.md diff from the commit as expected, never a finding. Brief notes: dev never cites line numbers of a file it splits or moves code out of (cites the symbol); the lizard note (split the function, do not raise a limit); the ci_drift_ignore note (change only that list in pipeline.json); the public-words note (old hits in the baseline are skipped and counted once); the merger takes an add/add conflict on `__init__.py` as a merge step. Reason for the change: fewer Sonnet dispatches, fewer false cite and reviewer findings, and plans that fail early instead of at dispatch.

## Task kinds, output rule and ops preflight (phase 02-task-kinds)

Reason: the three decisions of this phase, each named by its behaviour.

- D1 research task kind: `Type: research` is a read-only question. A scout on Haiku answers it, one tracked `Outputs:` file holds the answer, and a later task consumes that file. Mechanism: `research` is in `TASK_TYPES` and `task_outputs` reads the path (`pipeline/plan_files.py`); the plan check is `pipeline/plan_research.py` (prefix `research:`); the commit is `research_apply.py`; the steps are `_next_research` in `pipeline/steps.py`. Reason: the answer is a reviewed, committed file, not chat text lost when the run ends.
- D2 tracked-only outputs: a code task whose `Predicted` or `Outputs` path is git-ignored or under the work folder fails `cs-plan check`. Such a file is never committed, so the merge loses it. `Type: report` tasks keep the work-folder rule, because their file is outside any repo by design. Mechanism: `check_output_paths` in `pipeline/plan_outputs.py` (prefix `outputs:`).
- D3 lead-step inputs up front: a missing `Inputs:` path must match an `ops-N` manual row that the task depends on (plan check, `pipeline/plan_report.py`). The driver blocks once, before any dispatch, on every open `ops-N` step that no task depends on (`pipeline/ops_preflight.py`, called from `pipeline/cli_wave.py`). Reason: the lead knows its steps before the run starts, so the driver asks for them before agents are dispatched, not in the middle of a wave.
- Docs cite check at every stage end: script step `docs-cite-final` runs `steps_finaltest.py --doc-cites` on every `*.md` under `docs_dir`, changed or not; a `path:line` cite that does not hold fails the stage (fenced blocks and generated docs are skipped). Switch `docs_cite_final` in pipeline.json. The step runs after the docs step; when the docs writer rides beside the audits, it runs right after the first audit batch (the docs writer's batch), so it sees the writer's output. Reason: a stale cite in a doc the stage did not touch was never checked.
