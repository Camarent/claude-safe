#!/usr/bin/env python3
"""Job worktrees for dev tasks, created by script so the dev only has to work in the paths it is given.

    wt.py create --stage S --task task-3 [--dir .]   -> JSON {branch, root, repos: {relpath: worktree path}}
    wt.py add-repo --stage S --task T --repo workspace/x  -> a nested repo the task does not list gets a worktree in the task's job worktree (the dev needs to change it); commit, review and merge then cover it
    wt.py list   --stage S [--dir .]                  -> JSON list of the stage's job worktrees
    wt.py show   --stage S [--dir .]                  -> the persisted structure: {task: {branch, base, root, repos}}
    wt.py remove --stage S --task task-3 [--dir .]    -> removes the task's worktrees and branches (after the merge; refuses when work is uncommitted or unmerged, --force discards)
    wt.py clean  --stage S [--dir .]                  -> removes all of the stage's job worktrees and branches, prunes records (stage end)
    wt.py commit --stage S --task T --subject "<type>(<area>): <what>"  -> commits the task's changes in every worktree (the dev's last step)
    wt.py merge  --stage S --task T [--dir .]         -> squash-merges the job branch into the work branch (`branch_work`, default stage/<feature>) in every repo; exit 2 on a conflict, 3 when blocked
    wt.py mark-merged --stage S --task T              -> after a hand merge: merge skips the task, remove stops guarding it
    wt.py diff   --stage S --task T [--dir .]         -> the task's stat + diff per repo (the reviewer's context)
    wt.py review-begin --stage S --task T [--dir .]   -> before the reviewer: refuses (exit 3) when the job worktree is dirty, else records HEAD in .wt/<stage>/<task>.review-base
    wt.py review-reset --stage S --task T [--dir .]   -> after the reviewer: back to the recorded HEAD (reset --hard, clean -fd; ignored files stay), verified; exit 4 when not clean

Layout: <dir>/.wt/<stage>/<task>/<repo>/ is a worktree of the main repo (pipeline.json `repo`) on branch `job/<stage>-<task>`,
cut from `branch_work` (else HEAD). A git repo nested in the main repo (e.g. a private repo in <dir>/<name>, ignored by the
main repo) gets its own worktree at the same relative path inside it when the task file names that path. node_modules is linked in; `target/` is not shared (_seed_build).
create is idempotent.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subprocess  # noqa: F401  (tests patch wt.subprocess)

from pipeline.util import stage_root
from pipeline.worktree import (  # noqa: F401  (the library lives in pipeline/worktree.py; these names stay reachable as wt.<name>)
    Blocked,
    Conflict,
    _base_branch,
    _dirty,
    _parts,
    _record_scope,
    _seed_build,
    _unmerged,
    add_repo,
    clean,
    commit,
    create,
    diff,
    dirty_report,
    git,
    is_merged,
    list_wt,
    live_job,
    mark_merged,
    merge,
    nested_repos,
    read_manifest,
    refresh_stale,
    remove,
    review_begin,
    review_marker,
    review_pending,
    review_reset,
    settled_job,
    skip_blocker,
)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "cmd", choices=["create", "add-repo", "list", "show", "remove", "clean", "commit", "merge", "mark-merged", "diff", "review-begin", "review-reset"]
    )
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", default="")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--subject", default="")
    ap.add_argument("--repo", default="", help="add-repo: the nested repo, relative to the main repo")
    a = ap.parse_args(argv)
    _check_args(ap, a)
    a.dir = stage_root(a.dir, a.stage) or a.dir  # run from inside a job worktree, `.` holds no manifest: use the stage root
    try:
        out = _run(a)
    except Conflict as e:
        sys.exit(_fail(e, 2))
    except Blocked as e:
        sys.exit(_fail(e, 3))
    except RuntimeError as e:
        if not a.cmd.startswith("review-"):
            raise
        sys.exit(_fail(e, 4))
    if a.cmd == "commit" and not out:
        _nothing_committed(a, out)
    print(out if isinstance(out, str) else json.dumps(out))


def _check_args(ap, a):
    if a.cmd in ("create", "add-repo", "remove", "commit", "merge", "mark-merged", "diff", "review-begin", "review-reset") and not a.task:
        ap.error("--task is required")
    if a.cmd == "add-repo" and not a.repo:
        ap.error("--repo is required")


def _fail(e, code):
    """Print the error as JSON and return the exit code for the caller to pass to sys.exit."""
    print(json.dumps({"error": str(e)}))
    return code


def _nothing_committed(a, out):
    print(
        f"wt.py commit: task {a.task} has no job worktree in stage {a.stage}, nothing was committed (worked in place? commit with commit.py)",
        file=sys.stderr,
    )
    print(json.dumps(out))
    sys.exit(3)


def _run(a):
    return {
        "create": lambda: create(a.dir, a.stage, a.task),
        "add-repo": lambda: add_repo(a.dir, a.stage, a.task, a.repo.rstrip("/")),
        "remove": lambda: remove(a.dir, a.stage, a.task, a.force),
        "list": lambda: list_wt(a.dir, a.stage),
        "show": lambda: read_manifest(a.dir, a.stage),
        "clean": lambda: clean(a.dir, a.stage),
        "commit": lambda: commit(a.dir, a.stage, a.task, a.subject),
        "merge": lambda: merge(a.dir, a.stage, a.task),
        "mark-merged": lambda: mark_merged(a.dir, a.stage, a.task),
        "diff": lambda: diff(a.dir, a.stage, a.task),
        "review-begin": lambda: review_begin(a.dir, a.stage, a.task),
        "review-reset": lambda: review_reset(a.dir, a.stage, a.task),
    }[a.cmd]()


if __name__ == "__main__":
    main()
