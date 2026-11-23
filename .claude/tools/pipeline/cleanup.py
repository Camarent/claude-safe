"""Cleanup step: job worktrees whose branch is merged or gone, and the acceptance temp dirs runs leave behind.

Live work is never touched: a worktree is removed only when its job branch is merged into the work branch, or its branch is gone and
its worktree has no uncommitted changes and every commit in it is already on the work branch. An acceptance temp dir touched
recently is kept (a live run may use it). Local `job/*` branches merged into the release or a work branch and checked out nowhere go
too. Temp leftovers: acceptance dirs, qrun TMPDIRs of a dead pid, and the ptest scratch dirs of `.work/<stage>/tmp/`, each past its age.
`dry_run` lists the same decisions and changes nothing."""

import argparse
import contextlib
import os
import shutil
import sys
import tempfile
import time

from . import gitx, scratch, worktree
from .configuration import (
    ACCEPTANCE_TMP_PREFIX,
    CLEANUP_DRY_RUN_FLAG,
    CLEANUP_HEADS_REF,
    CLEANUP_PTEST_MIN_AGE_S,
    CLEANUP_PTEST_PREFIX,
    CLEANUP_QRUN_TMP_MIN_AGE_S,
    CLEANUP_SCRATCH_SUB,
    CLEANUP_TMP_MIN_AGE_S,
    CLEANUP_WORK_DIR,
    CLEANUP_WT_DIR,
    JOB_BRANCH_PREFIX,
    QRUN_TMP_PREFIX,
    project,
    release_branch,
    repo_dir,
    work_branch,
)


def _subdirs(base):
    return sorted(s for s in os.listdir(base) if os.path.isdir(os.path.join(base, s))) if os.path.isdir(base) else []


def _stages(a_dir):
    return _subdirs(os.path.join(os.path.abspath(a_dir), CLEANUP_WT_DIR))


def _verdict(a_dir, stage, task):
    """-> "merged", "gone" (branch deleted, worktree clean) or None (live: keep)."""
    if worktree.is_merged(a_dir, stage, task):
        return "merged"
    _cfg, repo, _root, branch = worktree.layout(a_dir, stage, task)
    if gitx.try_out(repo, "rev-parse", "--verify", "--quiet", branch):
        return None
    _cfg, _branch, parts = worktree._parts(a_dir, stage, task)
    want = worktree.work_branch(_cfg, stage)
    for _rel, r, w in parts:
        if worktree._dirty(w, r):
            return None  # branch gone but uncommitted work sits in the folder: keep it for a person to look at
        if gitx.try_out(w, "merge-base", "--is-ancestor", "HEAD", want) is None:
            return None  # commits only this folder holds (detached): removing it would make them unreachable
    return "gone"


def _newest_mtime(path):
    newest = os.path.getmtime(path)
    for d, dirs, files in os.walk(path):
        for n in [*dirs, *files]:
            with contextlib.suppress(OSError):
                newest = max(newest, os.lstat(os.path.join(d, n)).st_mtime)
    return newest


def _acceptance_dirs(tmp_root, now):
    out = []
    for name in sorted(os.listdir(tmp_root)):
        p = os.path.join(tmp_root, name)
        if name.startswith(ACCEPTANCE_TMP_PREFIX) and os.path.isdir(p) and not os.path.islink(p) and now - _newest_mtime(p) >= CLEANUP_TMP_MIN_AGE_S:
            out.append(p)
    return out


def _qrun_tmp_dirs(tmp_root, now):
    """qrun TMPDIRs whose pid is dead and that were last touched CLEANUP_QRUN_TMP_MIN_AGE_S ago or more."""
    return [p for p in scratch.dead_owner_dirs(tmp_root, QRUN_TMP_PREFIX) if now - _newest_mtime(p) >= CLEANUP_QRUN_TMP_MIN_AGE_S]


def _ptest_dirs(a_dir, now):
    """ptest scratch dirs under <dir>/.work/<stage>/tmp/ untouched CLEANUP_PTEST_MIN_AGE_S or longer (real dirs only, no symlinks)."""
    work = os.path.join(os.path.abspath(a_dir), CLEANUP_WORK_DIR)
    out = []
    for stage in _subdirs(work):
        base = os.path.join(work, stage, CLEANUP_SCRATCH_SUB)
        for name in _subdirs(base):
            p = os.path.join(base, name)
            if name.startswith(CLEANUP_PTEST_PREFIX) and not os.path.islink(p) and now - _newest_mtime(p) >= CLEANUP_PTEST_MIN_AGE_S:
                out.append(p)
    return out


def _checked_out(repo):
    """Local branches checked out in some worktree of REPO (the main one included)."""
    lines = (gitx.try_out(repo, "worktree", "list", "--porcelain") or "").splitlines()
    return {ln[len("branch ") :].removeprefix(CLEANUP_HEADS_REF) for ln in lines if ln.startswith("branch ")}


def _folder_branches(a_dir):
    """Branches named by a .wt/<stage>/<task> folder, with or without a worktree: such a branch is live, not an orphan."""
    return {worktree.layout(a_dir, s, t)[3] for s in _stages(a_dir) for t in worktree.list_wt(a_dir, s)}


def prune_branches(a_dir, dry_run=False):
    """Orphan local job branches: merged into the release branch or a work branch, with no .wt folder and checked out nowhere.
    -> ([(branch, into)] deleted, [branch] kept because not merged anywhere). Deletes unless dry_run.
    A worktree removed by this run takes its branch with it (worktree.remove), so its branch is already held here."""
    cfg = project(a_dir)
    repo = os.path.abspath(repo_dir(a_dir, cfg))
    # a constant branch_work is the work branch of every stage; with {feature} each stage names its own
    bases = [release_branch(repo, cfg), work_branch(cfg, ""), *(work_branch(cfg, s) for s in _stages(a_dir))]
    targets = [b for b in dict.fromkeys(bases) if gitx.try_out(repo, "rev-parse", "--verify", "--quiet", b)]
    held = _checked_out(repo) | _folder_branches(a_dir)
    refs = gitx.try_out(repo, "for-each-ref", "--format=%(refname:short)", CLEANUP_HEADS_REF + JOB_BRANCH_PREFIX) or ""
    pruned, unmerged = [], []
    for branch in refs.split():
        if branch in held:
            continue
        into = next((b for b in targets if gitx.try_out(repo, "merge-base", "--is-ancestor", branch, b) is not None), None)
        if into is None:
            unmerged.append(branch)  # not merged anywhere: its commits may be the only copy
            continue
        if not dry_run and gitx.try_out(repo, "branch", "-D", branch) is None:
            continue  # git refused the delete: keep it and report nothing
        pruned.append((branch, into))
    return pruned, unmerged


def cleanup(a_dir, tmp_root=None, dry_run=False, now=None):
    """-> {"worktrees": [(stage, task, why)], "kept": [(stage, task)], "tmp": [paths], "branches": [(branch, into)],
    "unmerged": [branch] (orphan job branches merged nowhere, kept), "dry_run": bool}. Removes unless dry_run."""
    tmp_root = tmp_root or tempfile.gettempdir()
    now = time.time() if now is None else now
    done, kept = [], []
    for stage in _stages(a_dir):
        for task in worktree.list_wt(a_dir, stage):
            why = _verdict(a_dir, stage, task)
            if why is None:
                kept.append((stage, task))
                continue
            if not dry_run:
                worktree.remove(a_dir, stage, task, force=(why == "gone"))
            done.append((stage, task, why))
    tmp = [*_acceptance_dirs(tmp_root, now), *_qrun_tmp_dirs(tmp_root, now), *_ptest_dirs(a_dir, now)]
    if not dry_run:
        for p in tmp:
            shutil.rmtree(p, ignore_errors=True)
    branches, unmerged = prune_branches(a_dir, dry_run)
    return {"worktrees": done, "kept": kept, "tmp": tmp, "branches": branches, "unmerged": unmerged, "dry_run": dry_run}


def main(argv=None):
    p = argparse.ArgumentParser(description="Remove merged or gone job worktrees and acceptance temp dirs.")
    p.add_argument("--dir", default=None, help="project dir (default: the current one)")
    p.add_argument("--tmp-root", default=None, help="where acceptance temp dirs live (default: the system temp dir)")
    p.add_argument(CLEANUP_DRY_RUN_FLAG, action="store_true", dest="dry_run", help="list what would be removed, change nothing")
    a = p.parse_args(argv)
    res = cleanup(a.dir or os.getcwd(), a.tmp_root, a.dry_run)
    verb = "would remove" if res["dry_run"] else "removed"
    for stage, task, why in res["worktrees"]:
        print(f"{verb} worktree {stage}/{task} ({why})")
    for stage, task in res["kept"]:
        print(f"kept {stage}/{task} (live)")
    for path in res["tmp"]:
        print(f"{verb} temp dir {path}")
    for branch, into in res["branches"]:
        print(f"{verb} branch {branch} (merged into {into})")
    for branch in res["unmerged"]:
        print(f"kept unmerged branch {branch}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
