"""Names of the cleanup step: the temp-dir prefix of acceptance runs, the job-worktree folder and the list-only flag."""

ACCEPTANCE_TMP_PREFIX = "acceptance-"  # acceptance.py: prefix of the temp dir one acceptance run works in (cleanup removes the leftovers)
CLEANUP_DRY_RUN_FLAG = "--dry-run"  # cleanup.py CLI: list what would be removed, change nothing
CLEANUP_WT_DIR = ".wt"  # job worktrees of the project: <dir>/.wt/<stage>/<task>
CLEANUP_TMP_MIN_AGE_S = 3600  # cleanup.py: an acceptance temp dir touched within this many seconds may belong to a live run: keep it
