"""maincheck: a dev's writes to the main checkout instead of its job worktree.

`snapshot` records the dirty state of the main checkout (changed and untracked files with content hashes) when the job's first dev starts and keeps it for the job's life. `check` (after the dev) compares: a file that is new or changed since is named in a MAIN-DIRT note. The dev's own tracked writes (a file clean at the snapshot, present in HEAD) are saved as a patch in the stage folder and reverted to HEAD; the note names the patch. Untracked new files, files dirty at the snapshot (the lead's own work) and a write whose patch cannot be saved are never touched, only named. The pipeline's own folders (job worktrees, stage work files) are ignored, and so is a repo in the middle of a merge. Tasks that run at the same time share the main checkout: another task's write is blamed on this one. A git that cannot answer gives no verdict, never a crash.
"""

import contextlib
import datetime
import fnmatch
import json
import os
import time

from . import gitx, util
from .configuration import (
    GUARD_LOG_FILE,
    LINK_WRITE_SHOWN,
    MAIN_BASE_FILE,
    MAIN_DIRT_PREFIX,
    MAIN_GUARD_IGNORE,
    MAIN_GUARD_IGNORE_GLOBS,
    MAIN_KEPT_STATE,
    MAIN_REVERT_DEFAULT,
    MAIN_REVERT_KEY,
    MAIN_REVERTED_STATE,
    MAIN_UNSAVED_STATE,
    MAIN_WRITE_NOTE,
    MAIN_WRITE_PATCH,
    WORK_DIR,
)
from .linkguard import ERRORS, PATHSPEC_LITERAL, _revert, merging
from .worktree import git, layout

RENAME_CODES = ("R", "C")  # a porcelain rename/copy entry is followed by the source path as its own entry
GUARD_GLOBS_KEY = "main_guard_ignore_globs"  # pipeline.json key that replaces MAIN_GUARD_IGNORE_GLOBS (read like cfg "gate_watch" in audit/taskgates.py)
GONE = "gone"  # the mtime shown for a path the main checkout no longer has
# appended to a note entry when the guard log names the job that wrote the path (the writer, not the task that found it)
WRITER_NOTE = ", written by job {job}"


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


def _tracked(repo, p):
    """True when HEAD has path P: a file the dev changed, not one it created. A git that cannot answer: False (never reverted)."""
    try:
        return gitx.try_out(repo, PATHSPEC_LITERAL, "cat-file", "-e", f"HEAD:{p}") is not None
    except ERRORS:
        return False


def _revert_state(a_dir, stage, task, repo, cfg, files):
    """-> the last sentence of the note. Saves the diff of FILES (the dev's tracked writes) to a patch in the stage folder, then reverts them to HEAD. The switch (MAIN_REVERT_KEY) and the patch name are settings; no patch saved means no revert."""
    if not files or not cfg.get(MAIN_REVERT_KEY, MAIN_REVERT_DEFAULT):
        return MAIN_KEPT_STATE
    patch = ""
    try:
        patch = util.work_path(os.path.join(a_dir, WORK_DIR, stage), MAIN_WRITE_PATCH.format(task=task, stamp=int(time.time())), create=True)
        done = _revert(repo, files, patch)
    except ERRORS:
        done = []
    if done:
        return MAIN_REVERTED_STATE.format(patch=patch)
    return MAIN_UNSAVED_STATE.format(patch=patch or WORK_DIR)


def read_writers(a_dir, stage):
    """-> {path: job} of the guard log of STAGE (the first job denied a write to that path). A missing or unreadable log, or a bad line, gives no entry ({} for none)."""
    try:
        with open(os.path.join(os.path.abspath(a_dir), WORK_DIR, stage, GUARD_LOG_FILE), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("path"), str) and isinstance(entry.get("job"), str) and entry["job"]:
            out.setdefault(entry["path"], entry["job"])
    return out


def _owners(a_dir, stage, task, repo, changed):
    """-> {path: writer job or None} of the CHANGED paths this task owns: a path the guard log gives to another job is left out."""
    writers = read_writers(a_dir, stage)
    owner = {p: writers.get(p) or writers.get(os.path.join(repo, p)) for p in changed}  # a log path may be absolute
    return {p: job for p, job in owner.items() if job in (None, task)}


def _listing(repo, changed, owner, started):
    """-> the note's file list: the first LINK_WRITE_SHOWN paths with their mtime, the job start and the writer job when known."""
    return ", ".join(
        f"{p} (mtime {_stamp(repo, p)}, job start {started}{WRITER_NOTE.format(job=owner[p]) if owner[p] else ''})" for p in changed[:LINK_WRITE_SHOWN]
    )


def check(a_dir, stage, task):
    """-> "" or the MAIN-DIRT note naming the files that are new or changed in the main checkout since the snapshot. The dev's own tracked writes are saved to a patch and reverted; the rest is only named. A path the guard log attributes to another job is left to that job's check. No snapshot: no verdict."""
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
    owner = _owners(a_dir, stage, task, repo, changed) if changed else {}
    changed = [p for p in changed if p in owner]
    if not changed:
        return ""
    note = MAIN_WRITE_NOTE.format(
        files=_listing(repo, changed, owner, started), more=max(0, len(changed) - LINK_WRITE_SHOWN)
    )  # the stamps are read before any revert
    mine = [p for p in changed if p not in base and _tracked(repo, p)]  # clean at the snapshot, in HEAD: the dev's own edit
    return note + " " + _revert_state(a_dir, stage, task, repo, cfg, mine)


def dirt(note):
    """True when NOTE is a check() note about the main checkout (not a dev's own failure)."""
    return note.startswith(MAIN_DIRT_PREFIX)
