# Retro lessons: stuck causes and useless escalations

**Validator.** Run `python3 -I .claude/tools/retro_lessons_check.py --no-file-check docs/retro-lessons.md`. The source column is prose, so no report file is checked (`--no-file-check`). It exits 1 on a missing section or a totals footer that does not add up.

Source: the stage reports and orchestrator logs of the stages `pipeline-fixes` and `planner-quality` (read only; not tracked in git). A row is a report with verdict BLOCKED or ESCALATE. A stuck cause is any such row. A useless escalation is an ESCALATE whose cause is a review verdict (`wrong N, risky N, nit N`) instead of a PLAN GAP or a blocker: the lead should have retried, not escalated.

## Stuck causes

| stage | task | source | cause |
|---|---|---|---|
| pipeline-fixes | task-5 | report a70: job branch on an older base; dev doc over size cap | job branch based on 7871819, before stage commit 8c1dfa8; dev.md over the size cap, so the test failed |
| pipeline-fixes | task-5 | report a80: review verdict filed as escalation | review verdict `wrong 1` filed as ESCALATE |
| pipeline-fixes | task-12 | report a148: main checkout changed mid-task | MAIN-DIRT: files in the main checkout changed while the task ran |
| planner-quality | task-3 | report a45: review verdict filed as escalation | review verdict `risky 3` filed as ESCALATE |
| planner-quality | task-7 | report a66: backlog file gitignored, outside the worktree | PLAN GAP: backlog file is gitignored, outside the worktree |
| planner-quality | task-7 | report a67: backlog file gitignored, absent from the worktree | PLAN GAP: backlog file is gitignored, absent from the worktree |

## Useless escalations

| stage | task | source | cause |
|---|---|---|---|
| pipeline-fixes | task-5 | report a80: review verdict filed as escalation | review verdict `wrong 1, risky 0, nit 1` filed as ESCALATE |
| planner-quality | task-3 | report a45: review verdict filed as escalation | review verdict `wrong 0, risky 3, nit 1` filed as ESCALATE |

Totals: stuck causes 6, useless escalations 2.
