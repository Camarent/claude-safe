"""linkguard: a dev's writes through a link to a nested repo its task does not list.

A job worktree holds such a repo only as a symlink to the main copy (worktree.link_private), so an edit through it reaches the shared repo. `snapshot` records the main copies when the job's first dev starts (once: a later attempt keeps it, so an earlier attempt's writes stay visible); `check` (after the dev) compares: tracked files changed since are saved as a patch (checked with `git apply --reverse --check`) and reverted; new untracked files and a commit are only reported (a deleted file could be in use by a test running in another task) and then taken into the baseline, so each is reported once. Changes that were already there at the start (a lead's edit) are not the dev's. Tasks that run at the same time share the main copy: a write by another task after the snapshot is blamed on this one, its patch is kept. A git that cannot answer gives no verdict, never a crash. A job without a snapshot (made by hand, or before this check) gets one at the first check and no verdict.
"""

import contextlib
import json
import os
import re
import subprocess
import time

from . import gitx, util
from .configuration import (
    DOC_CITE_RE,
    DOC_PATHS,
    GIT_LONG_TIMEOUT_S,
    LINK_BASE_FILE,
    LINK_CITE_NOTE,
    LINK_COMMIT_NOTE,
    LINK_GIT_MERGE_FILES,
    LINK_NEW_FILES_NOTE,
    LINK_UNSAVED_NOTE,
    LINK_WRITE_NOTE,
    LINK_WRITE_PATCH,
    LINK_WRITE_SHOWN,
    WORK_DIR,
)
from .worktree import git, layout, nested_repos

ERRORS = (RuntimeError, OSError, subprocess.SubprocessError)  # a git that cannot answer: timeout, missing, failing
PATHSPEC_LITERAL = "--literal-pathspecs"  # a file named `a[1].txt` or `*` must match only itself
KEYS = ("head", "tracked", "untracked")  # the fields of a saved state


def _names(text):
    """File names of NUL-separated git output (`-z`: no quoting of odd names)."""
    return [x for x in text.split("\0") if x]


def merging(main):
    """True while a merge or squash-merge is unfinished in the repo MAIN: its staged changes are not a dev's write."""
    for f in LINK_GIT_MERGE_FILES:
        path = git(main, "rev-parse", "--git-path", f, check=False).strip()
        if path and os.path.exists(os.path.join(main, path)):
            return True
    return False


def _linked(a_dir, stage, task):
    """-> (job worktree root, [(relative path, main copy)]) of the nested repos the job only links."""
    _cfg, repo, root, _branch = layout(a_dir, stage, task)
    return root, [(rel, os.path.join(repo, rel)) for rel in nested_repos(repo) if os.path.islink(os.path.join(root, rel))]


def _digest(main, name):
    return git(main, "hash-object", "--", name).strip() if os.path.isfile(os.path.join(main, name)) else ""  # "" = the file is gone


def _state(main):
    """-> {head, tracked: {changed tracked file: content hash}, untracked: [file]} of a main copy. A git error raises RuntimeError (no state is better than a wrong one)."""
    names = _names(git(main, "diff", "--name-only", "--no-renames", "-z", "HEAD"))
    return {
        "head": git(main, "rev-parse", "HEAD").strip(),
        "tracked": {n: _digest(main, n) for n in names},
        "untracked": _names(git(main, "ls-files", "--others", "--exclude-standard", "-z")),
    }


def _base_path(root):
    return os.path.join(os.path.dirname(root), LINK_BASE_FILE)


def _save(root, base):
    util.write_text(_base_path(root), json.dumps(base))


def _base(root):
    """-> the saved {repo: state}; entries that are not a full state are dropped (a missing or odd file is no baseline)."""
    try:
        with open(_base_path(root), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        rel: st
        for rel, st in data.items()
        if isinstance(st, dict)
        and all(k in st for k in KEYS)
        and isinstance(st["tracked"], dict)
        and isinstance(st["untracked"], list)
        and isinstance(st["head"], str)
    }


def snapshot(a_dir, stage, task):
    """Record the state of the task's linked main repos when a dev starts. The first call keeps the tracked and untracked state for the job's life (a later attempt must not absorb an earlier attempt's writes); a later call only moves `head` up to the repo's current HEAD (another task's merge is not the dev's commit). Each repo on its own: one that git cannot answer for gets no baseline and is not guarded until the next call. A disk error leaves the old file."""
    with contextlib.suppress(*ERRORS):
        root, links = _linked(a_dir, stage, task)
        base = _base(root)
        for rel, main in links:
            try:
                cur = _state(main)
            except ERRORS:
                continue
            base[rel] = {**base[rel], "head": cur["head"]} if rel in base else cur
        if base:
            _save(root, base)


def _compare(cur, b):
    return {
        "tracked": [f for f, h in cur["tracked"].items() if b["tracked"].get(f) != h],
        "untracked": [f for f in cur["untracked"] if f not in b["untracked"]],
        "moved": cur["head"] != b["head"],
    }


def _raw_diff(main, files):
    """The diff of FILES against HEAD as bytes (CRLF and non-UTF-8 text survive; git's text decoding would not)."""
    return gitx.raw_out(main, PATHSPEC_LITERAL, "diff", "--binary", "--no-renames", "HEAD", "--", *files, timeout=GIT_LONG_TIMEOUT_S)


def _saved(main, files, path):
    """True when the diff of FILES was written to PATH and applies in reverse to the files as they are now (so it restores them)."""
    try:
        diff = _raw_diff(main, files)
        if not diff.strip():
            return False
        with open(path, "wb") as f:
            f.write(diff)
        return gitx.run(main, "apply", "--reverse", "--check", path).returncode == 0
    except ERRORS:
        return False


def _revert(main, files, path):
    """Restore FILES in the repo MAIN from HEAD (a file HEAD lacks is removed) after their patch is saved at PATH. -> the files reverted; none when no good patch exists."""
    if not _saved(main, files, path):
        return []
    done = []
    for f in files:
        try:
            if gitx.try_out(main, PATHSPEC_LITERAL, "cat-file", "-e", f"HEAD:{f}") is not None:
                git(main, PATHSPEC_LITERAL, "checkout", "HEAD", "--", f)
            else:
                git(main, PATHSPEC_LITERAL, "rm", "-f", "-q", "--", f)
            done.append(f)
        except ERRORS:
            continue
    return done


def _shown(rel, files):
    return ", ".join(f"{rel}/{f}" for f in files[:LINK_WRITE_SHOWN]), max(0, len(files) - LINK_WRITE_SHOWN)


def _notes(a_dir, stage, task, rel, c, add_cmd, stamp):
    """-> the notes for one linked repo REL with changes C (reverts the tracked ones)."""
    notes = []
    if c["tracked"]:
        patch = util.work_path(os.path.join(a_dir, WORK_DIR, stage), LINK_WRITE_PATCH.format(task=task, stamp=stamp, repo=rel.replace("/", "_")), create=True)
        done = _revert(c["main"], c["tracked"], patch)
        files, more = _shown(rel, done or c["tracked"])
        notes.append((LINK_WRITE_NOTE if done else LINK_UNSAVED_NOTE).format(files=files, more=more, patch=patch, add_repo=add_cmd))
    if c["untracked"]:
        files, more = _shown(rel, c["untracked"])
        notes.append(LINK_NEW_FILES_NOTE.format(files=files, more=more, add_repo=add_cmd))
    if c["moved"]:
        notes.append(LINK_COMMIT_NOTE.format(repos=rel, add_repo=add_cmd))
    return notes


def _target_line(root, path, n):
    """-> text of line N (1-based) of the file PATH in ROOT, or None when the file or the line does not exist."""
    try:
        with open(os.path.join(root, path), encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return lines[n - 1] if 0 < n <= len(lines) else None


def cite_notes(root):
    """-> one note per `path:line` cite in the modified or new docs files of the job worktree ROOT whose quoted text is not on that line now (a stale line number after an edit). A git that cannot answer gives no note."""
    notes: list[str] = []
    try:
        docs = _names(gitx.try_out(root, "ls-files", "-m", "-o", "--exclude-standard", "-z", "--", *DOC_PATHS) or "")
    except ERRORS:
        return notes
    for doc in docs:
        try:
            with open(os.path.join(root, doc), encoding="utf-8") as f:
                lines = f.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            for m in re.finditer(DOC_CITE_RE, line):
                n = int(m["line"])
                target = _target_line(root, m["path"], n)
                if target is None or m["text"] not in target:
                    notes.append(LINK_CITE_NOTE.format(doc=doc, path=m["path"], line=n, text=m["text"]))
    return notes


def check(a_dir, stage, task, add_repo_cmd):
    """-> "" when the dev wrote nothing through a link, else the FAIL note for the dev. ADD_REPO_CMD is the command that adds a nested repo's worktree; the repo is appended."""
    try:
        root, links = _linked(a_dir, stage, task)
    except ERRORS:
        return ""
    base, notes, stamp = _base(root), [], time.time_ns()
    for rel, main in links:
        try:
            if merging(main):
                continue
            cur = _state(main)
            if rel not in base:  # no snapshot of this repo: nothing can be told apart, so nothing is blamed
                base[rel] = cur
                continue
            c = {"main": main, **_compare(cur, base[rel])}
            if c["tracked"] or c["untracked"] or c["moved"]:
                notes += _notes(a_dir, stage, task, rel, c, f"{add_repo_cmd} {rel}", stamp)
                base[rel] = {**base[rel], "head": cur["head"], "untracked": cur["untracked"]}  # reported once; tracked files were reverted
        except ERRORS:  # git cannot answer for this repo: no verdict for it, the others still count
            continue
    with contextlib.suppress(*ERRORS):
        _save(root, base)
    return "\n".join([*notes, *cite_notes(root)])
