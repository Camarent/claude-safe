# task-14 reopen after cleanup (stage retro-m25-followup2, a306-a308)

## (a) What made a307 and a308 appear
- Evidence: `.work/retro-m25-followup2/orch.jsonl`. a278 is the test run of task-14 (DONE). a279 is merge-w4 (task-14 merged). a306 is cleanup (script, "deleted 314 files"). a307 is the quality run and a308 is the reviewer, both dispatched for task-14 after a306.
- task-14 is `Type: research`. `steps.py:800` `_next_research` returns `None` (task passed) when the last event is a test run with verdict DONE. That path needs `is_research` (`steps.py:760`) to be true.
- `is_research` reads `.work/<S>/task-14.md` (`steps.py:763`). `util.py:189` `read_text` returns "" for a missing file, so `task_type` (`plan_files.py:413`) gives `code`.
- Cleanup (`phases.py:343` `_cleanup_step`, prompt line 354) deletes `task-*.md`. After a306 the file is gone (checked: no `task-14.md` in the stage folder; `TASKS.md` is kept).
- So `is_research` is false, `next_step` (`steps.py:818`) takes the code path: `_after_script` -> `_after_test` -> `_quality_or_review` (`steps.py:245`) -> quality run a307 -> `_to_review` -> reviewer a308. The path from `_after_test` to the quality run is inferred from the dispatch sequence; I did not trace every branch.
- `decide` (`graph.py:707`) re-decides from `TASKS.md` (kept) and `orch.jsonl`. It does not need the task files for the task list. It does need them for the type check above.

## (b) Why the reviewer found no job worktree
- Reviewer a308 result: "review not started: task task-14 has no job worktree to review". Source: `worktree_merge.py:563` (`review_begin`) raises; `driver.py:217` turns it into `VERDICT: BLOCKED`; the driver logs it as FAIL ("verdict BLOCKED not valid for reviewer").
- Cause (partly unverified): merge-w4 (a279) merged task-14, and the job worktree goes with the merge. `graph.py:541` comment says "the merge script removes it". I did not read the merge removal code. a304 (branch-cleanup) also kept only `job/s-task-1*` branches, which is consistent.
- A research task has no job worktree by design (scout writes the report; `research_apply.py` commits to the stage branch). The reviewer should not run for it at all.

## (c) Does the driver re-decide from deleted task files?
- Yes, partly. `decide` re-runs after cleanup. Task list comes from `TASKS.md`. Per-task type comes from the task file via `is_research`, `is_webtest`, `is_report` (`steps.py:123`). With the file gone these fall back to the default `code`.

## Fix
- `steps.py:763` `is_research` (and the same read in `is_webtest`/`is_report`): when the task file is missing, the type is lost. Proposed concrete change: record `Type:` in `TASKS.md` (kept by cleanup) or keep `task-*.md` in `phases.py:354` for tasks still needed. Owner decision needed on which; I did not verify either.
- Expected-behaviour alternative (if the owner prefers): none; this is a real defect, not expected.

## Settled-task wait (driver-gates task-7)
- `decide` now calls `_unsettled_action` before `_closing_phases`: the final steps (final-test, audits, pr-ready, master-merged, branch-cleanup) wait while a task that has events still has an open step, for example a retry after its wave merged. That dispatch or ask is returned first. This does not fix the lost `Type:` after cleanup in the section above.

## Steps needing stage branch
- merge-w4-late (a312): BLOCKED. "pathspec 'stage/retro-m25-followup2' did not match". Cause: branch-cleanup (a304) deleted `stage/retro-m25-followup2` after master-merged (a303) squash-merged the PR. Any merge-w*-late after a304 needs the stage branch and will fail the same way.
- Other steps after final (a293-a305, a302 pr-ready, a303 master-merged, a304 branch-cleanup, a305 index-refresh, a306 cleanup): no record in the log shows them needing the stage branch. Not verified beyond the log.

Unverified: merge removes job worktree (code path not read); `is_finish_phase` at `graph.py:725` not traced.
