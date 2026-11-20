# Plan: base integrity (owner discussion 2026-11-19)

Problem: the orchestrator used `--allow-red-base` (and `skip`, config edits) to get past a red base instead of fixing it, and a red base that appears mid-stage is found late and blamed on whoever runs next.

Rule: **the orchestrator can never change what counts as green, and a red base has an owner (a task or the stage), never a tolerance.** A blocked stage always continues by fixing the cause; the only other way is an owner-approved waiver of one named gate.

Dispatch: dev tasks by hand or `cs-plan`; Sonnet for logic (tasks 1 to 4), Haiku for task 5 docs; one reviewer per group, then tester (`ptest.py`), then `scripts/release.sh`. Order matters: 0, 1, 2, 3, 4, 5.

## Task 0 (done): remove the bypass
Landed before the 2026-11-20 history squash: `--allow-red-base`, `preflight-red.json`, `recorded_base_red`, `clear_recorded`, `BASE_RED_NOTE` are gone; the preflight message ends `fix the base first`. Kept, not rewritten: tasks 1 to 4 build on it. Review it with the rest.

## Task 1. Green base before every dispatch
- After each `merge-w<N>` (and a late merge), the driver runs the merge gates on the stage branch once (`merge_wave.py` already does this at merge; reuse its result, do not run twice). Red: the driver dispatches no new task, opens one fix task on the stage branch (existing fix-plan path), and blocks the next wave until `merge_wave` or a gates run is green.
- Resume (`orch.jsonl` exists) runs the same check first, so a restart cannot skip it. Predicted paths: `pipeline/steps.py`, `pipeline/graph.py`, `merge_wave.py`, `pipeline/phases.py`.
- Remove the per-task "red on the base too" clearing (`gate_base_check`, `clear_base_red`, `taskgates` base run, `CLEAR_WHY_*`, `uncleared_reasons`): with a green base, every task red counts. Remove the key from `PROJECT_DEFAULTS` and docs. Keep the exit 124/127 handling (timeout, cannot run) as a retry, not a clear.
- Done: a test with two tasks green alone and red merged (merge gate red, next wave not dispatched, fix task created); a resume test (red base, restart, still no dispatch); `taskgates` tests lose the base-run cases.

## Task 2. Automatic culprit
- On a red merge gate caused by a wave of 2 or more tasks, a script step `merge-culprit-w<N>` re-merges the wave's tasks one at a time on a scratch branch off the pre-merge stage HEAD, runs the failing gate commands after each, and names the first task that turns one red (or `interaction: <a>+<b>` when no single task does but the pair does).
- The fix task is created with `Culprit: task-<id>` and the failing command and output in its brief. Report line goes into the merge report and `metrics.jsonl` (`culprit` event).
- Scratch branch and worktree are always removed (tests must clean up; see the tmp-leak lesson). A bisect of N tasks costs up to N gate runs: cap with `culprit_max_tasks` (default 8); above the cap name the stage, not a task.
- Predicted paths: new `pipeline/culprit.py`, `merge_wave.py`, `pipeline/steps.py`, `configuration/limits.py`. Done: tests with a scratch repo (single culprit, pair interaction, no culprit found, cap exceeded, cleanup).

## Task 3. Frozen gate definition
- At stage start the driver stores `sha256` of the gate definition in `.work/<feature>/gates.lock`: `gates`, `gates_merge`, `gates_final`, `each`, `preflight_gates`, `gate_warn_ok`, `missing_binaries`, and the pipeline.json file path. The file is written by the driver only; agents run with it read-only (add to the `cmd_guard` write deny list and the dev/reviewer scope rules).
- `merge_wave.py`, `final_check.py` and the preflight compare the current definition with the lock; a mismatch is `VERDICT: FAIL gate definition changed (<keys>)`, exit non-zero, not a gate result.
- Re-stamp only through `orch.py gates-restamp --reason "<why>"`, which needs the owner (same confirmation path as the other `ask_main` steps, never auto-answered) and writes an `override` event (task 4).
- Done: tests for each key changed, unchanged = green, lock missing on an old run (treated as "stamp now, warn once"), agent write to the lock denied.

## Task 4. Override ledger and waivers
- Every `orch.py skip` or `retry` of a gate step (preflight, task gates, merge gate, final test), every re-stamp and every waiver appends an `override` event to `orch.jsonl` with the actor, step, reason and HEAD. The final report prints them under "gates weakened in this stage".
- Waiver: `orch.py waive --gate <name> --reason "<why>"`, owner-confirmed. It records gate, reason and HEAD, applies to this stage only (expires with the stage branch), clears that gate's red at the merge gate and final test, and nothing else. A `NO FIX` owner decision can offer it.
- A stage with any override or waiver ends `CLEAN-WITH-WAIVERS`, never `CLEAN`; the retro and the score job count them. No flag on `cs-plan run`, `cs-wave run` or `cs-wave feature` can pass a red gate (tests assert the parsers reject any `--allow-*` red/base flag).
- Done: tests for the ledger, waiver scope (one gate, one stage), `CLEAN-WITH-WAIVERS`, and a skip of the merge gate that is logged and listed.

## Task 5. Docs and backlog
- `practices/orch-pipeline.md`: replace the red-base paragraphs (preflight, merge gate, `gate_base_check`) with the green-base, culprit, frozen-gate and waiver rules; list what resume does first.
- `ORCHESTRATOR.md`, `pipeline-dev.md` if they name the removed flag or key; `AUDIT.md` untouched.
- Backlog: add the bypass routes found 2026-11-19 (skip of the merge gate, `"preflight_gates": false`, the stale `preflight-green.json` marker when the base turns red without a new HEAD) as closed by tasks 3 and 4; a stale marker: also store the gate hash in the marker.
- Memory: one `project` note for the rule above.

## Out of scope
- Tags and releases: unchanged, owner's word. PR squash-merges on green CI need no confirmation.
- Changing what the gates check; this plan only protects their verdict.

## Risks
- Task 1 stops the stage on any red merge, including a flaky gate. Mitigation: one automatic re-run of a red gate on the same HEAD before blocking, logged.
- Task 2 doubles gate time on a red wave. Acceptable: it only runs when red.
- Task 3 breaks an existing run with no lock: handled as stamp-now.
