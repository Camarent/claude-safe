"""Names of the cleanup step: the temp-dir prefix of acceptance runs, the job-worktree folder and the list-only flag."""

ACCEPTANCE_TMP_PREFIX = "acceptance-"  # acceptance.py: prefix of the temp dir one acceptance run works in (cleanup removes the leftovers)
CLEANUP_DRY_RUN_FLAG = "--dry-run"  # cleanup.py CLI: list what would be removed, change nothing
CLEANUP_WT_DIR = ".wt"  # job worktrees of the project: <dir>/.wt/<stage>/<task>
CLEANUP_TMP_MIN_AGE_S = 3600  # cleanup.py: an acceptance temp dir touched within this many seconds may belong to a live run: keep it
CLEANUP_QRUN_TMP_MIN_AGE_S = 3600  # cleanup.py: a qrun TMPDIR of a dead pid is removed only when untouched this long (a fresh one may still be read)
CLEANUP_PTEST_PREFIX = "ptest-"  # ptest.py: prefix of the scratch dir of a test run; cleanup removes the old ones under <dir>/.work/<stage>/tmp/
CLEANUP_PTEST_MIN_AGE_S = 3600  # cleanup.py: a ptest scratch dir touched within this many seconds may belong to a live run: keep it
CLEANUP_WORK_DIR = ".work"  # the project's work folder: <dir>/.work/<stage>/ holds the stage's files and its test scratch dirs
CLEANUP_SCRATCH_SUB = "tmp"  # <dir>/.work/<stage>/tmp/: where the ptest scratch dirs of a stage are kept
CLEANUP_HEADS_REF = "refs/heads/"  # git ref namespace of local branches: the job branches and the checked-out ones of `git worktree list`
