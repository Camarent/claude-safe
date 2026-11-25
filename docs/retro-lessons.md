# Retro lessons: stuck causes and useless escalations

**Validator.** Run `python3 -I .claude/tools/retro_lessons_check.py docs/retro-lessons.md`. It exits 1 on a missing section, a row whose report file is missing, or a totals footer that does not add up.

Source: the reports and `orch.jsonl` of the stages `pipeline-fixes` and `planner-quality` under `/home/cam/claude-safe/.work` (read only). A row is a report with verdict BLOCKED or ESCALATE. A stuck cause is any such row. A useless escalation is an ESCALATE whose cause is a review verdict (`wrong N, risky N, nit N`) instead of a PLAN GAP or a blocker: the lead should have retried, not escalated.

## Stuck causes

| stage | task | report | cause |
|---|---|---|---|
| pipeline-fixes | task-5 | /home/cam/claude-safe/.work/pipeline-fixes/reports/report-a70.md | job branch based on 7871819, before stage commit 8c1dfa8; dev.md over the size cap, so the test failed |
| pipeline-fixes | task-5 | /home/cam/claude-safe/.work/pipeline-fixes/reports/report-a80.md | review verdict `wrong 1` filed as ESCALATE |
| pipeline-fixes | task-12 | /home/cam/claude-safe/.work/pipeline-fixes/reports/report-a148.md | MAIN-DIRT: files in the main checkout changed while the task ran |
| planner-quality | task-3 | /home/cam/claude-safe/.work/planner-quality/reports/report-a45.md | review verdict `risky 3` filed as ESCALATE |
| planner-quality | task-7 | /home/cam/claude-safe/.work/planner-quality/reports/report-a66.md | PLAN GAP: `.notes/pipeline-backlog.md` is gitignored, outside the worktree |
| planner-quality | task-7 | /home/cam/claude-safe/.work/planner-quality/reports/report-a67.md | PLAN GAP: `.notes/pipeline-backlog.md` is gitignored, absent from the worktree |

## Useless escalations

| stage | task | report | cause |
|---|---|---|---|
| pipeline-fixes | task-5 | /home/cam/claude-safe/.work/pipeline-fixes/reports/report-a80.md | review verdict `wrong 1, risky 0, nit 1` filed as ESCALATE |
| planner-quality | task-3 | /home/cam/claude-safe/.work/planner-quality/reports/report-a45.md | review verdict `wrong 0, risky 3, nit 1` filed as ESCALATE |

Totals: stuck causes 6, useless escalations 2.
