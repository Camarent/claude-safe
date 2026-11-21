# Execution graph: what runs, in which order

Read `orch-pipeline.md` for the rules and `pipeline-dev.md` for working on the tools. This file is the map: three levels, from the entry point down to one task. Every box is a tool call (`script`, no model) or an agent session (`agent: model`). Source of truth is the code; to see the real sequence of a stage without model calls run `cs-wave run --stage S --dir D --dry-run` (the fixtures in `tools/tests/support/project.py` do the same for the tests).

## 1. Entry: `cs-plan run --feature F --prompt-file P`
```
cs-plan run
 |- plan_context.goal_context (script + Strata, optional)  -> file digest, plan-context.md   [skipped, not failed, when Strata or the index is down]
 |   `- ensure_index (script; first build of a big or nested repo can take minutes)
 |- model: sonnet first; opus only when the architect replies ESCALATE
 |- architect (agent: sonnet|opus), ONE session:
 |     decides SMALL (<= 25 tasks, no gate)  -> cs-plan stage-init + task files      = one stage  .work/F/
 |     or BIG                                -> cs-plan plan-init + phase entries    = Plan.md    .work/F/Plan.md
 |     may reply VERDICT: ESCALATE (on sonnet) -> run repeats planning once on opus
 |- cs-plan check (script) until OK: task graph, 30-task cap, phase gates, one-phase plan = stage, combine hints
 `- metrics.jsonl: context event, session lines, estimates
        |
        |-- small: run the stage        `/run-feature F`  = cs-wave run --stage F        (level 2)
        `-- big:   cs-wave feature --feature F                                        (level 3, below)
```

## 2. One stage: `cs-wave run --stage S`
`orch.py` walks this list and decides the next step from the event log (`orch.jsonl`); it makes no model call. Steps in `[ ]` exist only with the flag or config named.

```
for each wave of the task graph (waves run in order, tasks inside a wave in parallel):
   per task          (level 4)
   merge-w<N>: script `merge_wave.py` in worktree mode (agent `merger`, haiku, only in in-place mode)   squash-merge the wave's branches into the stage branch `stage/<feature>`, run gates and `each`
   [pr-push-w<N> (script)    push the stage branch after each wave merge, only with an open PR]
        `- on FAIL: merge-fix-w<N> (agent `merger`, sonnet, CONFLICT-RESOLVE MODE) fixes once, merge-w<N>-2 runs again, then the lead is asked
   [index-w<N> (script)      refresh the code index, only with flag `index`]
        BLOCKED "done task(s) ... still have an unmerged worktree branch" (graph `_unmerged_done`): a done task whose job branch has commits ahead of the stage branch, or uncommitted work, asks the lead. A done task with no commits ahead and nothing uncommitted (e.g. a retried scout that wrote only files outside git) has its job worktree and branch removed by the driver and does not block.
before the first wave (feature level): deps-sweep (merge green Dependabot PRs); the draft PR opens at the first `pr-push-w<N>` (no `pr-open` step any more)
final phases, in this order:
   final-test        script `final_check.py` when `gates` are set, else tester (agent: haiku); red result on a stage head that moved since the run -> one automatic final-test-rerun; still red -> one bounded fix round -> final-test-2
   [final-webtest    web-tester (agent: sonnet, one step per batch of 4 UI tasks), flag `ui`; fix round + re-check of FAIL/UNVERIFIED batches; fix tasks checked in their own batch]
   [docs             docs-writer (agent: haiku), flag `docs`]
   audits, only when flag `audit` is on and, for a middle phase of a big plan, enough tasks piled up (`audit_every_tasks`, default 25; the last phase is always audited):
      tools-check          script   xtools.py: needed tools present
      [audit-run, audit-enrich, audit-context, plan-vs-changes]  scripts, only when AUDIT.md has an `audit-tools` block
           audit-context = lp outlines + refs + "Related, unchanged" (Strata, optional); plan-vs-changes = live recall of the plan vs the diff
      [ux-shots            script, flags `ui` and a `ux` block in AUDIT.md]
      audit-sec (agent: sec-auditor)  ||  [audit-ux (agent: ux-reviewer)]     in parallel
      audit-arch (agent: arch-auditor)                                         after them, sees their reports
      on FIXES: fix round (architect writes fix tasks -> dev, reviewer, merge as in level 4/2 -> re-audit `-2` of the kinds that said FIXES); still FIXES: a second fix round and re-audit `-3`; FIXES there goes to the lead
   [pr-ready           script: mark the PR ready, wait for green CI; red = BLOCKED to the lead]
   [local-merge        script: squash the stage branch into the release branch in each local-only repo (main repo without `origin`, nested repos); dirty tree or conflict = BLOCKED to the lead; not with `PRE-APPROVED: master merge`]
   master-merged       script step `pr_steps.py merge` squashes a pushed repo's ready, green, mergeable PR (BLOCKED otherwise: the lead merges by hand); local repos were already squashed by `local-merge`, the lead only confirms and runs `orch.py skip`. The stage PAUSES (ask_main) unless TASKS.md says `PRE-APPROVED: master merge` (then branch-cleanup is skipped too)
   [branch-cleanup     script: delete the stage branch; refuses unless the PR is MERGED or the branch adds nothing to master]
   [push-master        ops (agent: haiku), flags `push` and `merge`; push of master needs the owner's word]
   [publish, live check  per `PUBLISH:` line and flag `live`]
   [index-refresh      script, flag `index`, after the master merge]
   cleanup             delete finished plan and task files
   retro-numbers       script (transcript numbers), then a haiku cost digest; a failure here never stops the stage
```

## 3. A big plan: `cs-wave feature --feature F`
```
loop over the phases of .work/F/Plan.md in dependency order (_pick_phase):
   if the phase has no TASKS.md yet:
      [phase context  script (Strata, optional): goal, paths, plan-notes decisions, earlier phases' diff stat -> phases/<dir>/plan-context.md]   
      architect (agent: sonnet|opus)   plans ONE phase: stage-init + tasks, `cs-plan check --stage` until OK
   cs-wave run --stage F/phases/<dir>        (level 2, whole list above; audits per the rule above)
   done_when (script)   the phase's own command in the repo with the project's test_env; red -> BLOCKED.md, back to the lead
   phase done
the `NN-finish` phase ends with the master-merge pause of level 2
```
Dry run: `cs-wave feature --feature F --dry-run` prints the phases only; planning and the run are not started.

## 4. One task (inside a wave)
Model ladder and limits: `orch-pipeline.md` "Rounds and fix routes".
```
dev (haiku)  --DONE-->  [prereview (script, Strata, config `prereview`): FAIL goes back to dev] --> reviewer (haiku)
   |                                                                                                 |
   |-- ESCALATE (dev found real logic) ---------------------------------------------------------------+-- PASS: task done
   |        dev (sonnet)                                                                              |-- ESCALATE: reviewer (sonnet) once
   |-- other verdict: the lead is asked                                                               `-- FAIL: dev (sonnet) with the findings, reviewer (sonnet);
                                                                                                          a second FAIL asks the lead
```
The plan never picks models (a `sonnet` word in an old TASKS.md is ignored). Agents talk through report files under `.work/` with `VERDICT:` as the first line.

## Where Strata is optional
Everything marked "Strata, optional" is skipped with a visible note when the local model is down; no step needs it to finish. It is used in: planner context (levels 1 and 3), audit-context "Related, unchanged", prereview.
