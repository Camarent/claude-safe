"""Bridge from the wave runner to wt.py: the job worktrees of a task, only when pipeline.json has `"worktrees": true`."""

import hashlib
import os
import sys

from . import gitx
from .configuration import DIFF_SHA_LEN, JOB_DIFF_MAX_CHARS, NO_REVIEW_ROOT, PREREVIEW_FILE_EXTS, PREREVIEW_GIT_TIMEOUT_S, project, work_branch
from .util import TOOLS, swallowed

sys.path.insert(0, str(TOOLS))
import wt


def job_info(a, task_id, create):
    """-> {branch, base, root, repos} or None when worktrees are off (or the task has none yet and create is False)."""
    d = getattr(a, "dir", ".")
    cfg = project(d)
    if not cfg.get("worktrees"):
        return None
    if not create:
        info = wt.read_manifest(d, a.stage).get(task_id)  # persisted structure, no rediscovery
        return info if info and os.path.isdir(info["root"]) else None
    info = wt.create(d, a.stage, task_id)
    info["base"] = work_branch(cfg, getattr(a, "feature", None) or a.stage)
    return info


def job_diff(a, task_id, limit=JOB_DIFF_MAX_CHARS):
    """The task's committed change as text (stat + diff per repo, cut at `limit` characters per repo) for the reviewer brief, or "" when there is none."""
    memo = getattr(a, "wt_memo", None)  # decide() sets it: one git call per task and limit while the event log stands
    key = ("diff", task_id, limit)
    if memo is not None and key in memo:
        return memo[key]
    out = ""
    if job_info(a, task_id, create=False):
        try:
            out = wt.diff(getattr(a, "dir", "."), a.stage, task_id, limit)
        except (RuntimeError, OSError):
            out = ""
    if memo is not None:
        memo[key] = out
    return out


def git_text(repo, *args, timeout=PREREVIEW_GIT_TIMEOUT_S):
    """stdout of a git call, "" when it fails."""
    return gitx.try_out(repo, *args, timeout=timeout) or ""


def uncommitted_diff(d, paths, git=git_text):
    """`git diff HEAD` of the task's Predicted `paths` in `d`: the work of a task that has no job worktree. Pre-review and the driver both hash it.
    `git(repo, *args, timeout=)` -> stdout is injectable for tests."""
    return git(d, "diff", "HEAD", "--", *paths, timeout=PREREVIEW_GIT_TIMEOUT_S)


def deliverable_texts(paths, d):
    """-> [(path, text)] of the Predicted `paths` (absolute, or relative to the workspace `d`) that are regular text files (PREREVIEW_FILE_EXTS) and decode as UTF-8."""
    out = []
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(d, p)
        if not (os.path.isfile(full) and full.lower().endswith(PREREVIEW_FILE_EXTS)):
            continue
        try:
            with open(full, encoding="utf-8") as fh:
                out.append((p, fh.read()))
        except (OSError, UnicodeDecodeError):
            continue
    return out


def files_sha(texts):
    """-> the short hash of the judged deliverables ([(path, text)]): pre-review stamps a FAIL with it, the driver compares it."""
    h = hashlib.sha256()
    for p, t in texts:
        h.update(p.encode("utf-8") + b"\0" + t.encode("utf-8") + b"\0")
    return h.hexdigest()[:DIFF_SHA_LEN]


def tasks_merged(a, task_ids):
    """True when wt.py says every task is merged already (marker or no unmerged content). False on any doubt or error."""
    if not project(getattr(a, "dir", ".")).get("worktrees"):
        return False
    memo = getattr(a, "wt_memo", None)
    key = ("merged", tuple(task_ids))
    if memo is not None and key in memo:
        return memo[key]
    try:
        res = bool(task_ids) and all(wt.is_merged(a.dir, a.stage, t) for t in task_ids)
    except Exception as ex:
        swallowed("worktrees.tasks_merged", ex)
        return False
    if memo is not None:
        memo[key] = res
    return res


def job_dirty(a, task_id):
    """Uncommitted changes in the task's job worktrees as a list of `git status` lines; [] when worktrees are off, none exists or all is clean."""
    if not job_info(a, task_id, create=False):
        return []
    _cfg, _branch, parts = wt._parts(getattr(a, "dir", "."), a.stage, task_id)
    return [f"{rel + ': ' if rel else ''}{ln.strip()}" for rel, r, w in parts for ln in wt._dirty(w, r)]


REVIEW_NOTE = (
    " Mutation checks (C3): you may edit files in this worktree to break the code and confirm the named test goes red; never commit; "
    "the driver resets the worktree after you (do not rely on leftovers)."
)


def review_note(a, task_id):
    """Reviewer brief sentence on mutation checks; '' when there is no job worktree."""
    return REVIEW_NOTE if job_info(a, task_id, create=False) else ""


def review_root(a, task_id):
    """The one path the reviewer may edit: the job worktree root, or NO_REVIEW_ROOT (every edit denied) when there is none."""
    info = job_info(a, task_id, create=False)
    return info["root"] if info else NO_REVIEW_ROOT


def review_begin(a, task_id):
    """-> '' when the HEAD is recorded (worktrees off: nothing to do), else why the reviewer must not start."""
    if not project(getattr(a, "dir", ".")).get("worktrees"):
        return ""
    try:
        wt.review_begin(getattr(a, "dir", "."), a.stage, task_id)
    except (RuntimeError, OSError) as e:
        return str(e)
    return ""


def review_reset(a, task_id):
    """-> '' when the job worktree is back at the recorded HEAD (or no review was started), else why not."""
    try:
        wt.review_reset(getattr(a, "dir", "."), a.stage, task_id)
    except (RuntimeError, OSError) as e:
        return str(e)
    return ""


def review_recover(a):
    """Crash recovery before a stage starts: reset every job worktree whose review never finished. -> list of 'task: why' for the resets that failed."""
    d = getattr(a, "dir", ".")
    out = []
    for t in wt.review_pending(d, a.stage):
        why = review_reset(a, t)
        if why:
            out.append(f"{t}: {why}")
    return out


def job_heads(a, task_id):
    """The job branch HEAD of every repo of the task as one string ("rel=sha;..."), "" when worktrees are off, none exists or git fails."""
    if not job_info(a, task_id, create=False):
        return ""
    try:
        _cfg, _branch, parts = wt._parts(getattr(a, "dir", "."), a.stage, task_id)
        return ";".join(f"{rel}={git_text(w, 'rev-parse', 'HEAD').strip()}" for rel, _r, w in parts)
    except (RuntimeError, OSError):
        return ""
