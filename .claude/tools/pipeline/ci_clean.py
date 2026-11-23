"""CI clean: remove only the untracked files a CI command created. snapshot() lists the untracked files before the command, remove_new() deletes the untracked files that are not in that list. Tracked and modified files are never touched and `git clean` is never run."""

import os

from . import gitx

UNTRACKED_ARGS = ("ls-files", "--others", "--exclude-standard", "-z")  # untracked, not ignored, NUL-separated (exact names)


def snapshot(repo):
    """-> set of untracked paths (repo-relative, forward slashes) in REPO right now."""
    r = gitx.run(repo, *UNTRACKED_ARGS)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(UNTRACKED_ARGS)} in {repo}: {(r.stderr or r.stdout).strip()[-500:]}")
    return {p for p in r.stdout.split("\0") if p}


def _inside(root, path):
    """True when PATH (after resolving links and "..") stays under ROOT and is not a symlink itself."""
    if os.path.islink(path):
        return False
    real = os.path.realpath(path)
    return real.startswith(root + os.sep)


def remove_new(repo, before):
    """Delete the untracked files in REPO that are absent from BEFORE (a snapshot()). -> sorted list of removed paths. Paths that escape REPO are skipped; directories left empty by a removal are pruned."""
    root = os.path.realpath(repo)
    now = snapshot(repo)
    removed = []
    for rel in sorted(now - set(before)):
        full = os.path.join(root, *rel.split("/"))
        if not _inside(root, full) or not os.path.isfile(full):
            continue
        os.remove(full)
        removed.append(rel)
        _prune_empty_parents(root, os.path.dirname(full))
    return removed


def _prune_empty_parents(root, folder):
    """Remove FOLDER and its parents while they are empty, stopping at ROOT."""
    while folder != root and folder.startswith(root + os.sep) and os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
        folder = os.path.dirname(folder)
