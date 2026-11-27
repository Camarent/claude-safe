# Research task trial

Trial of one research task, end to end, through `cs-plan`. It shows how a `Type: research` task
is planned, what the checker accepts, and which gaps the trial found. The fixes for the gaps are
in the stage tasks that follow this trial.

## Plan shape

- Task A (research): `Type: research`, one tracked output `Outputs: docs/research-findings.md`.
  It collects facts from the repo; it writes no code.
- Task B (consumer): depends on task A and names the output in its inputs:
  `Inputs: after:task-A:docs/research-findings.md`.

## cs-plan commands used

```
cs-plan task --type research --outputs docs/research-findings.md ...   # task A
cs-plan task --inputs after:task-A:docs/research-findings.md ...       # task B, depends on A
cs-plan check                                                          # validate the stage
```

`cs-plan task` takes `--type`, `--outputs` and `--inputs` since task-1 of this stage. Before that
the flags did not exist.

## Gaps found

1. `cs-plan task` could not write `Type:`, `Outputs:` or `Inputs:` headers, so `cs-plan check`
   said OK without validating a research task. Fixed by task-1 (`--type`, `--outputs`, `--inputs`).
2. The research trigger table lived only in `.claude/agents/architect.md`. Moved to
   `.claude/practices/planning.md` by task-3 and named by the architect by task-4.
3. No `cs-plan` command writes the `Carried` section of a stage's `TASKS.md`; it is written by hand.
   Left open: not in this stage's scope.

## Checker

`check_research` in `.claude/tools/pipeline/plan_research.py` needed no change: the trial
found no gap there. A research task with one tracked output and a consumer that names it passes.
