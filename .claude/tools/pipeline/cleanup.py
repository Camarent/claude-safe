"""Cleanup step: job worktrees whose branch is merged or gone, and the acceptance temp dirs runs leave behind.

Live work is never touched: a worktree is removed only when its job branch is merged into the work branch, or its branch is gone and
its worktree has no uncommitted changes and every commit in it is already on the work branch. An acceptance temp dir touched
recently is kept (a live run may use it). `dry_run` lists the same decisions and changes nothing."""

import argparse
import contextlib
import os
import shutil
import sys
import tempfile
import time

from . import gitx, worktree
from .configuration import ACCEPTANCE_TMP_PREFIX, CLEANUP_DRY_RUN_FLAG, CLEANUP_TMP_MIN_AGE_S, CLEANUP_WT_DIR


def _stages(a_dir):
    base = os.path.join(os.path.abspath(a_dir), CLEANUP_WT_DIR)
    return sorted(s for s in os.listdir(base) if os.path.isdir(os.path.join(base, s))) if os.path.isdir(base) else []


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


def cleanup(a_dir, tmp_root=None, dry_run=False, now=None):
    """-> {"worktrees": [(stage, task, why)], "kept": [(stage, task)], "tmp": [paths], "dry_run": bool}. Removes unless dry_run."""
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
    tmp = _acceptance_dirs(tmp_root, now)
    if not dry_run:
        for p in tmp:
            shutil.rmtree(p, ignore_errors=True)
    return {"worktrees": done, "kept": kept, "tmp": tmp, "dry_run": dry_run}


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
