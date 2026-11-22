# Pipeline hardening decisions

One section per problem of the stage.

## Problem 1

Reason: Gate lists derive from the CI workflow and `ci-drift` is an error, so the stage gates run what CI runs (ccn, bandit, format check, cov, markers). Tests needing a binary skip with a reason; CI records the binary.

## Problem 2

Reason: A guard in ptest records the main checkout branch before and after a run. The driver restores the branch, logs once and does not block the merge.

## Problem 3

Reason: The brief names the worktree root and says relative paths only. The Edit/Write hook denies a main-checkout path while a job worktree is active.

## Problem 4

Reason: `flags: docs` with no docs root is reported at plan time. A retry conflict is recorded as a blocked step with the conflict text.

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

Reason: `scripts/sync-claude-config.sh` syncs `~/.claude` in one command. It refuses while a driver is mid-step and never runs `host_agents.py --apply`.

