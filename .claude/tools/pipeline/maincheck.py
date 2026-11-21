"""maincheck: a dev's writes to the main checkout instead of its job worktree.

`snapshot` records the dirty state of the main checkout (changed and untracked files with content hashes) when the job's first dev starts and keeps it for the job's life. `check` (after the dev) compares: a file that is new or changed since is named in a FAIL note. It never deletes or reverts: the files may be the dev's only copy of its work. The pipeline's own folders (job worktrees, stage work files) are ignored, and so is a repo in the middle of a merge. Tasks that run at the same time share the main checkout: another task's write is blamed on this one. A git that cannot answer gives no verdict, never a crash.
"""

import contextlib
import json
import os

from . import util
from .configuration import LINK_WRITE_SHOWN, MAIN_BASE_FILE, MAIN_GUARD_IGNORE, MAIN_WRITE_NOTE
from .linkguard import ERRORS, merging
from .worktree import git, layout

RENAME_CODES = ("R", "C")  # a porcelain rename/copy entry is followed by the source path as its own entry


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


def _state(main):
    """-> {path: content hash ("" = gone)} of every dirty path of the repo MAIN outside the pipeline's own folders. A git error raises."""
    out = git(main, "status", "--porcelain", "-z", "-uall")
    paths = [p for p in _entries(out) if p.split("/", 1)[0] not in MAIN_GUARD_IGNORE]
    return {p: git(main, "hash-object", "--", p).strip() if os.path.isfile(os.path.join(main, p)) else "" for p in paths}


def _base_path(a_dir, stage, task):
    _cfg, _repo, root, _branch = layout(a_dir, stage, task)
    return os.path.join(os.path.dirname(root), MAIN_BASE_FILE)


def snapshot(a_dir, stage, task):
    """Record the main checkout's dirty state when a dev starts; the first call wins (a later attempt must not absorb an earlier attempt's writes). Errors leave no baseline."""
    with contextlib.suppress(*ERRORS):
        path = _base_path(a_dir, stage, task)
        if not os.path.exists(path):
            _cfg, repo, _root, _branch = layout(a_dir, stage, task)
            util.write_text(path, json.dumps(_state(repo)))


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
        _cfg, repo, _root, _branch = layout(a_dir, stage, task)
        base = _base(path)
        if base is None or merging(repo):
            return ""
        cur = _state(repo)
    except ERRORS:
        return ""
    changed = sorted(p for p, h in cur.items() if base.get(p) != h)
    if not changed:
        return ""
    shown = ", ".join(changed[:LINK_WRITE_SHOWN])
    return MAIN_WRITE_NOTE.format(files=shown, more=max(0, len(changed) - LINK_WRITE_SHOWN))
