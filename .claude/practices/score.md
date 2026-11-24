# Pipeline score (rubric, baseline, fix plan)

Evidence: the three newest real stages of one real project, measured 2026-11-06 from `.work/<stage>/metrics.jsonl`, `orch.jsonl`, retros (reports in the session scratchpad, numbers copied below). Scores are 0-10 per metric, a judgment against the stated thresholds; the total is the mean. Re-score after the next real stage with the same numbers.

## Rubric
| # | Metric | 10 = | 0 = |
|---|--------|------|-----|
| 1 | Active share: seconds a step runs / wall clock | >= 80% | <= 10% |
| 2 | Reliability: BLOCKED + FAIL + gate FAIL per task | 0 | >= 3 |
| 3 | First-try rate: tasks done in 1 dev try | all | none |
| 4 | Review waste: pre-review/review FAILs that changed nothing | 0 | > 1 per task |
| 5 | Lead load: asks + retries + skips per task | 0 | >= 2 |
| 6 | Plan context: recall of edited files, step within its time budget | >= 80%, in budget | < 30%, over |
| 7 | Cost visibility: share of real spend in metrics | 100% | < 30% |
| 8 | Token efficiency: all-in USD (pipeline + lead + lead-spawned agents) per merged task | <= $0.15 | >= $1.50 |

## Baseline
| Metric | stage 1 | stage 2 | stage 3 |
|---|---|---|---|
| Wall / active | 3h59 / 17% | 2h22 / 78% | 12h32 / 42% |
| Cost metered | $2.03 | $4.75 | $11.29 (real about 3-4x) |
| Dev BLOCKED / reviewer FAIL / gate FAIL | 6 / 4 / 5 | 5 / 6 / 15 | 9 / 9 / 13 (+36 pre-review FAIL) |
| Tasks with 1 dev try | 2 of 6 | 2 of 11 | 15 of 31 |
| Lead asks + retries + skips | 18 | 14 | 56 (27 asks, 18 retries, 11 skips) |
| Plan context recall | 39%, over budget | 5 of 18 files | 3 of 4 runs hit the cap |
| USD per task, metered / all-in estimate | $0.34 / about $1.0 | $0.43 / about $1.3 | $0.36 / about $1.2 |
| Opus share of metered spend | 34% | 12% | most of the 2 metered sessions |
| Scores (1..8) | 2, 3, 3, 5, 3, 3, 4, 4 | 7, 3, 2, 3, 4, 2, 4, 2 | 4, 3, 5, 3, 1, 3, 3, 2 |
| Total (sum / 8) | 3.4 | 3.4 | 3.0 |

Metric 8 was added after the first baseline (7 metrics gave 3.3, 3.6, 3.1). All-in USD is an estimate: the metered cost is multiplied by the 3-4x factor seen on optional-modules (retro: lead about $26 on top of $11.29 metered over 31 tasks); only a stage run with the new lead-cost logging gives a real figure. Linear scale: score = (1.5 - USD per task) / 1.35 * 10.

Baseline mean: about 3.3/10 (harsher than the owner's 5). Target 7-8/10 means roughly: active share >= 60%, under 1 failure per task, >= 60% first-try, pre-review waste near 0, lead load under 0.5 per task, plan recall >= 70%, cost visible.

## Fix groups (one dev each, ordered by gain)
- G1 review path: pre-review FAIL is not a dev try; pre-review never reviews a half-cut diff: over `PREREVIEW_DIFF_CHARS` (`.claude/tools/pipeline/configuration/limits.py:25`) it is skipped (the job diff for reviewers is cut at `JOB_DIFF_MAX_CHARS`, `.claude/tools/pipeline/configuration/limits.py:203`), a FAIL needs a file:line quote, skip the gate when the rework added no commit; reviewer may edit tests that the change breaks by design; ESCALATE with zero findings = PASS; scout tasks that write under `.work` (empty diff) get a real verdict; a session with no VERDICT line is a clean retry.
- G2 gates and merge: run all wave gates without stopping at the first red (`.claude/tools/audit/final_check.py`: `run_all` :153 runs each command through `_run_one` :112, which calls `_run_once` :126; groups come from `_units` :138); a red merge gets a fix-gate dev before the lead (`_fix_gate` in `.claude/tools/pipeline/graph.py:214`); a warning from the stage's own new rule is not a gate failure; detect an already-merged task before a re-merge; `_record_scope` extra vs edited (`.claude/tools/pipeline/worktree.py:782`); keep a longer push error tail (`.claude/tools/pipeline/pr_ops.py:88`); `orch.py retry` runs the Done check first (item 77).
- G3 briefs and plan: brief lint for `grep -c` exit 1, untracked predicted files, bad `jq` paths; protected insert-only gates named in briefs; task size cap (warn 15 files, fail 25); same-file tasks never share a wave; plan context step stays in budget and recalls more files.
- G4 cost, cleanup and lead load: log lead-spawned agents and a lead-session line in metrics and the retro; `cleanup` step without `ENV.md`; stage-branch handling after release; `orch.py retry|skip` resolves `.work` from the stage root; Opus sec-audit only when the diff touches security paths (default on); asks do not stop independent tasks.
- Then the structure items of the backlog (section C) and the Opus/Sonnet review round.

## After the fixes (2026-11-06, branch score merged into dev and master at b48244b)
Not re-scored: a score needs a new real stage. What is verified (tests, review, release gate) and what each fix should move:
| Metric | Fixes landed | Verified by | Still unproven |
|---|---|---|---|
| 1 Active share | manual/ask step no longer stops independent tasks; red merge gets fix-gate devs before the lead | unit tests, dry-run snapshots | real idle time (needs a stage) |
| 2 Reliability | gates run to the end, gate_warn_ok, already-merged check, retry done-check, brief lint, size cap | unit tests, 6 area reviews + Opus arch/sec audits, release.sh | BLOCKED/FAIL rate per task |
| 3 First-try rate | brief lint, protected-gate warning, oversized task starts on Sonnet | tests | tries per task |
| 4 Review waste | pre-review not a dev try, quote must exist in the diff, chunked review, ESCALATE 0/0/0 rule (security ESCALATE kept) | tests, reviewers | pre-review FAIL count |
| 5 Lead load | asks batched, cleanup script, stage_root, Revert subjects | tests | asks per task |
| 6 Plan context | explorer budget 240 s, error-run stop, seeding from lp/git (60 s deadline) | fixture tests only (no Strata run) | recall % and step time |
| 7 Cost visibility | lead-session and lead-spawned cost in the feature retro (`feature_retro.py --lead-log`, the lead must pass the log) | tests | one real retro |
Efficiency measured now: test suite 80 s -> 15 s host (critical path was test_orch.py), container 30 s; sec audit runs only on security paths. Re-score after the next two real stages with the numbers in the Baseline table.
