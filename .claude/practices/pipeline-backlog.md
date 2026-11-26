# Pipeline backlog (open items)

## Standing lessons
- `wt.py remove` refuses a branch whose hand-made commit was squashed (`differs in X`): run `wt.py mark-merged` first. Hand merges use `wt.py merge` / `merge_wave.py`.
- A task that writes `.git/info/exclude` cannot be a dev job in a job worktree: plan it as a lead/script step.
- Tests must pass `dir=` to the code under test so a project `pipeline.json` in the cwd cannot leak in.
- Soft scope: failures caused by the dev's own change in any file are the dev's to fix and are reported under `Extra files:`. Watch the conflict rate per wave; many conflicts mean the task split is wrong.
- Lead does the merge to `master` (stage-branch squash, PR squash-merge without asking); the `vX.Y.Z` tag needs the owner's word. Push is possible via `gh`.
- Never wait with `until [ -z "$(pgrep -f X)" ]`: pgrep -f matches the waiting shell. Use a Monitor until-loop on a file/ls-remote result, or the backgrounded command's own notification.
- Retro carry-over: architect verifies every CLI, path and count in a plan with a scout; bulk-data briefs state a text pre-filter, never "all rows"; watcher scripts never pgrep/pkill the driver; the lead delegates debugging to dev/ops/scout; briefs name the interpreter/env and whether `-I` is allowed.
- `orch.py retry` needs a driver relaunch; stop the old Monitor first, or stale BLOCKED lines read as current.
- Node unit tests through `qrun.py` use the `unittest discover` form; scoped eslint uses the Test form. The audit mirror (`audit_run._mirror`) can hide worktree changes if its symlinks point at the wrong repo. jscpd lives in `/opt/jstools/node_modules/.bin`, `npx --no-install` cannot see it: use `audit_run.py <repo> --only jscpd`.
- The merger in conflict mode resolves by taking the dev side: review those merges.
- Artifact tool republish rules: read the page, then read each changed published file with `path`, in an earlier turn than the publish. After an "identical content" refusal on a deliberate replacement, publish again unchanged. The claude-artifacts MCP stays disconnected after `/login` (HTTP 404).
- A hand-dispatched dev leaves work uncommitted in the main checkout; the dirty preflight catches it at the next pipeline start.

## Status
Closed items live in git history, not here. Gate state: run `scripts/dev.sh check`, `ccn` and `test` for current numbers. Decision record: the guards (`review_guard.py`, `cmd_guard.py`) allow-list was widened on purpose to cut denials, so add no new denials without measured harm; if a reviewer or dev is seen damaging files outside its worktree, prefer a read-only mount over more deny rules.

## Open: needs real stages (evidence, not code)
- [ ] Local-search share of unknown-location searches, goal >= 30%. Last measured 2026-11-17: 299 local searches (`lp find|nav`) vs 2459 grep/glob = 11%; the lead alone stayed near 4% although the rules say find/nav first. Rules did not move it: try a lead-side nudge (hook note on a lead `grep`/`rg` Bash call pointing to `lp nav|find` and `scout`), then re-measure with `pipeline/lp_share.py`.
- [ ] After the next 2 real stages check together: reviewer rubric (short replies, no lint nits, no repeats) and `_lint_left` cost; no `cmd_guard` false denials; incident log (`.work/<stage>/patches/`); the `Skipped/unchecked:` line in dev reports; `merge-w1` refusal text names base, sha, `.merged`; the PreToolUse guard sees `agent_type` on a real reviewer run; `local-merge` live run; per-phase recall in nested repos; per-phase `retro.md`.
- [ ] Denials by cause: re-run `python3 -I .claude/tools/retro/denials.py --days 5 [--projects DIR]`. Baseline 2026-11-04..09: 20.9% of Bash calls denied (7.8% on 11-09 after the first fixes).
- [ ] Diagnose branch-cleanup (3 of 8 BLOCKED), release (2 of 5) and master-merged (50%) step failures on a real run; verify the CI-wait fix.
- [ ] Plan-context recall (bar 30%, last 32/102), the acceptance step, `cs-plan dryrun`, `_report_why` text.
- [ ] Retries drive wave cost: dev sessions per task were 2.9 to 3.0 (goal below 2). Check that the planning.md lessons and `each` in per-task gates cut them.
- [ ] Re-score the pipeline on the next real stage with `practices/score.md` (weakest: plan-context recall, lead load, guard false denials).

## Open: fixes
- [ ] Webtest and report tasks: the report path is an absolute untracked file in the main checkout `.work` (outside the job worktree), and it cites the commit it ran on, which goes stale with every fix commit (kata M25 task-24 failed 3 review rounds on this). Make a script write the report (command result, tip sha, date) instead of the dev, or give such a task a tracked `Outputs:` path inside the worktree; never let a model type a sha.
- [ ] Dev must not run the full test suite: add a check (script, not a rule for the model) that fails or warns when a dev or reviewer session runs the project's full gate. Devs use the unit or scoped set; full runs belong to the driver's gate and merge steps.
- [ ] Gate-time warnings per level: warn when one gate level runs past its limit, as is done for the full run. Defaults: unit 2 min, merge 5 min, full 9 min, all settings in `pipeline.json` (no literals in code). The warning shows in the driver status and the final report so the owner can act (split or speed up the gate). Part of the retro after M25.
- [ ] Research tasks: `Type: research` exists (`planning.md`, `architect.md`, `plan_research.py`). Check by a trial plan that planners can use it end to end (task, Outputs path, consuming task, plan check) and fix gaps in planner tooling or docs.
- [ ] Driver: a step added or retried in a phase already marked done is not run by `cs-wave run F` (only `cs-wave run F/phases/<dir>` runs it); `cs-orch next` by hand logs a dispatch the driver never runs. Document or guard it.
- [ ] Lead-side facts go stale across steps (a merge listed as pending after the driver did it). Add a post-exit check script to `run-feature` (PR state, stage branches, artifact date vs merge) so the lead reports from output.
- [ ] Orphaned sessions after a hard kill of the driver (SIGKILL, OOM): the dev keeps editing while `recover_unreported` starts a second one. Record process groups per dispatch and kill or refuse. Missing tests for SIGKILL recovery and concurrent merge.
- [ ] `wt.merge` and `merge_wave.py` run without the checkout lock (the driver holds it while the merge script runs, so a plain re-take deadlocks: needs a re-entrant or owner-aware lock); a nested repo can merge while the main repo conflicts (partial merge).
- [ ] The finish phase can re-plan a fix an earlier task already committed: the finish planner should read the git log and the skip/done events first.
- [ ] web-tester returns BLOCKED when it runs out of turns although nothing failed: return a partial PASS with the unchecked list, or raise its turn cap.
- [ ] A Haiku dev could not commit because `wt.py commit` needed approval: check it is allowed in in-place mode too.
- [ ] Audit rounds can end with nits and a coverage request no task can close: let the lead close a round with a named residual list.
- [ ] A build/validate step wrote an untracked lock file into the main checkout and blocked a merge: find the writers; such steps must run in a worktree or commit, never dirty the main checkout.
- [ ] A BLOCKED report that claims "master has no fix" is not checked against `wt.py show` base: verify branch-ancestry claims before writing a rework note.
- [ ] `/usr/local/bin/lp` is baked at image build; the `tools/local.py` shim is a bridge, remove it after the next image rebuild.
- [ ] Module refactor leftovers: `retro_numbers.py` data/format split (697 lines), one agent registry (none exists), function-level imports (66) and `sys.path` hacks (66), `feature_retro` wiring of stale findings (`stale_findings` is used only by `audit/scans.py`), checkout lock for nested repos, one `.merged` marker per repo of a multi-repo job.
- [ ] `gate.jsonl` is the only record of escalation-gate vetoes and has no reader: a retro check for false vetoes could use it.
- [ ] Small: `graph.decide` re-reads orch.jsonl and TASKS.md per step (hot path, not measured slow); `plan_rules._same` also matches by basename suffix (no harm shown); `link_private` keeps a link only for the `dir/*` ignore form; `--add-dir /tmp` is shared by all jobs (revisit if jobs collide); `gates_merge` cost (`premerge` isolation proof plus `smoke` at every merge gate: measure, trim if it dominates).
- [ ] Shared pipeline is project agnostic except the `Strata` naming (accepted: the local-model integration, profile dependent); `ctx_config.py` not checked.
