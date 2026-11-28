# task-14 reopen after cleanup (stage retro-m25-followup2, a306-a308)

Evidence: `.work/retro-m25-followup2/orch.jsonl` (read-only). Code cites are symbol-first; line numbers are from this tree.

## (a) What made a307 and a308 appear
- task-14 timeline (orch.jsonl): a275 scout (`research task (Type: research)`) BLOCKED, then a retry; a276 scout DONE; a277 research apply committed `docs/cost-lp-nav.md`; a278 Test run DONE; a279 merge-w4 merged task-14; a306 cleanup (script, "deleted 314 files"); a307 quality run for task-14; a308 reviewer for task-14.
- Research routing: `steps.py` `_next_step` (line ~818) sends a task to `_next_research` when `is_research` is true. `_next_research` (`steps.py:800`) returns `None` (task passed) when the last event is a Test run with verdict DONE.
- `is_research` (`steps.py:760`) calls `task_type(read_text(<stage>/task-14.md))`. `read_text` (`util.py:189`) returns "" for a missing file. `task_type` (`plan_files.py:413`) returns `code` when no `Type:` line is found.
- Cleanup `_cleanup_step` (`phases.py:343`, prompt line 354) deletes `task-*.md`. After a306 `task-14.md` is gone. Checked: the file no longer exists in `.work/retro-m25-followup2/`; `TASKS.md` is kept.
- So `is_research` is false after a306. `next_step` (`steps.py:602`) takes the code path: script DONE -> quality run a307 -> reviewer a308. The branch from the Test-run event to the quality run is inferred from the dispatch sequence; not every branch was traced (unverified).

## (b) Why the reviewer found no job worktree
- Reviewer a308 result: "review not started: task task-14 has no job worktree to review". The reviewer raises it in `worktree_merge.py` `review_begin` (line ~563, read from an earlier note, not re-checked here). The driver logs it as FAIL ("verdict BLOCKED not valid for reviewer").
- Why no worktree: task-14 was already merged at a279 (merge-w4). The job worktree goes with the merge (`graph.py` comment near line 541 says "the merge script removes it"; unverified, the removal code was not read).
- Root cause: a research task has no job worktree by design (scout writes the report; `research_apply.py` commits to the stage branch). The code path sends a merged research task to a reviewer that needs a worktree.

## (c) Does the driver re-decide from deleted task files?
- Yes. `decide` (`graph.py:707`) re-runs after cleanup. The task list comes from `TASKS.md` (kept). Per-task type comes from the task file through `is_research`, `is_webtest` and `is_report` (`steps.py`, `is_webtest`/`is_report` near line 123). With the file gone these fall back to `code`.

## Steps needing stage branch
- merge-w4-late (a312): BLOCKED. "pathspec 'stage/retro-m25-followup2' did not match any file(s) known to git". Cause: branch-cleanup (a304) deleted `stage/retro-m25-followup2` after master-merged (a303) squash-merged the PR. Verified from the log line only.
- Late-merge step names (from `graph.py` `_late_merge`, line ~676, and `cli_wave.py:598`): `merge-w<N>-late`, `merge-w<N>-late<k>`, `merge-w<N>-p<k>`, `merge-fix-w<N>`. Any of these dispatched after branch-cleanup needs the stage branch and fails the same way. Which of them run after the final steps is not verified beyond a312.
- Other final steps (a293-a305, a302 pr-ready, a303 master-merged, a304 branch-cleanup, a305 index-refresh, a306 cleanup): no record in the log shows them needing the stage branch. Not verified beyond the log.

## Settled-task wait (driver-gates task-7)
- `decide` now calls `_unsettled_action` before `_closing_phases`: the final steps wait while a task has an open step, for example a retry after its wave merged. This does not fix the lost `Type:` after cleanup.

Unverified: merge removes the job worktree (removal code not read); `is_finish_phase` (`graph.py`, near line 725) not traced; the exact branch from a278 to the quality run not traced.

## Fix
- Proposed, not applied (this task is docs only): `steps.py` `is_research` (line 760), and the same read in `is_webtest` and `is_report`: when the task file is missing, read the `Type:` from that task's row in `TASKS.md` (kept by cleanup, `phases.py:354` keeps `TASKS.md`). Needs the planner to write `Type:` into the TASKS.md row (format change, unverified: the planner's TASKS.md writer was not read).
- Rejected: "expected behaviour". A reviewer must not run for a merged research task; this is a defect.
