"""maincheck: a dev's writes to the main checkout instead of its job worktree.

`snapshot` records the dirty state of the main checkout (changed and untracked files with content hashes) when the job's first dev starts and keeps it for the job's life. `check` (after the dev) compares: a file that is new or changed since is named in a FAIL note. It never deletes or reverts: the files may be the dev's only copy of its work. The pipeline's own folders (job worktrees, stage work files) are ignored, and so is a repo in the middle of a merge. Tasks that run at the same time share the main checkout: another task's write is blamed on this one. A git that cannot answer gives no verdict, never a crash.
"""

import contextlib
import datetime
import fnmatch
import json
import os

from . import util
from .configuration import (
    LINK_WRITE_SHOWN,
    MAIN_BASE_FILE,
    MAIN_DIRT_PREFIX,
    MAIN_GUARD_IGNORE,
    MAIN_GUARD_IGNORE_GLOBS,
    MAIN_WRITE_NOTE,
)
from .linkguard import ERRORS, merging
from .worktree import git, layout

RENAME_CODES = ("R", "C")  # a porcelain rename/copy entry is followed by the source path as its own entry
GUARD_GLOBS_KEY = "main_guard_ignore_globs"  # pipeline.json key that replaces MAIN_GUARD_IGNORE_GLOBS (read like cfg "gate_watch" in audit/taskgates.py)
GONE = "gone"  # the mtime shown for a path the main checkout no longer has


def _entries(text):
    """Paths of `git status --porcelain -z` output (a rename's source entry is skipped)."""
    parts, out, i = text.split("\0"), [], 0
    while i < len(parts):
        e = parts[i]
        i += 1
        if len(e) > 3:
            out.append(e[3:])
            if e[0] in RENAME_CODES or e[1] in RENAME_CODES:
                i += 1
    return out


def _globs(cfg):
    """The path globs the guard never reports: the pipeline.json key when set (an empty list ignores nothing), else the default."""
    return cfg.get(GUARD_GLOBS_KEY, MAIN_GUARD_IGNORE_GLOBS)


def _state(main, globs):
    """-> {path: content hash ("" = gone)} of every dirty path of the repo MAIN outside the pipeline's own folders and the GLOBS. A git error raises."""
    out = git(main, "status", "--porcelain", "-z", "-uall")
    paths = [p for p in _entries(out) if p.split("/", 1)[0] not in MAIN_GUARD_IGNORE and not any(fnmatch.fnmatch(p, g) for g in globs)]
    return {p: git(main, "hash-object", "--", p).strip() if os.path.isfile(os.path.join(main, p)) else "" for p in paths}


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).isoformat(timespec="seconds")


def _stamp(main, p):
    """The UTC mtime of path P of the main checkout, GONE when it is no longer there."""
    try:
        return _iso(os.path.getmtime(os.path.join(main, p)))
    except OSError:
        return GONE


def _base_path(a_dir, stage, task):
    _cfg, _repo, root, _branch = layout(a_dir, stage, task)
    return os.path.join(os.path.dirname(root), MAIN_BASE_FILE)


def snapshot(a_dir, stage, task):
    """Record the main checkout's dirty state when a dev starts; the first call wins (a later attempt must not absorb an earlier attempt's writes). Errors leave no baseline."""
    with contextlib.suppress(*ERRORS):
        path = _base_path(a_dir, stage, task)
        if not os.path.exists(path):
            cfg, repo, _root, _branch = layout(a_dir, stage, task)
            util.write_text(path, json.dumps(_state(repo, _globs(cfg))))


def _base(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def check(a_dir, stage, task):
    """-> "" or the FAIL note naming the files that are new or changed in the main checkout since the snapshot. Nothing is deleted. No snapshot: no verdict."""
    try:
        path = _base_path(a_dir, stage, task)
        cfg, repo, _root, _branch = layout(a_dir, stage, task)
        base = _base(path)
        if base is None or merging(repo):
            return ""
        cur = _state(repo, _globs(cfg))
        started = _iso(os.path.getmtime(path))  # the snapshot is written when the job's first dev starts
    except ERRORS:
        return ""
    changed = sorted(p for p, h in cur.items() if base.get(p) != h)
    if not changed:
        return ""
    shown = ", ".join(f"{p} (mtime {_stamp(repo, p)}, job start {started})" for p in changed[:LINK_WRITE_SHOWN])
    return MAIN_WRITE_NOTE.format(files=shown, more=max(0, len(changed) - LINK_WRITE_SHOWN))


def dirt(note):
    """True when NOTE is a check() note about the main checkout (not a dev's own failure)."""
    return note.startswith(MAIN_DIRT_PREFIX)
