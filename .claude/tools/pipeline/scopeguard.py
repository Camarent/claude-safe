"""scopeguard: files a job changed outside its task's `Predicted paths:`, as a note for the reviewer brief.

The check is by script: it compares the job branch's changed paths with the predicted ones. A file named in the task text, and the kinds listed in the `scope_allow` setting (a test tied to a predicted file, docs, notes), are no finding. `scope_guard` sets how hard the note is: off, warn (mention) or strict (the reviewer fails the task unless its text justifies each file)."""

import os
import re

from .configuration import (
    SCOPE_ALLOW_ALL,
    SCOPE_MODES,
    SCOPE_NOTES_DIR,
    SCOPE_SHOWN,
    SCOPE_STEM_MIN,
    SCOPE_STRICT_NOTE,
    SCOPE_TEST_RE,
    SCOPE_WARN_NOTE,
    WORK_DIR,
    project,
    work_branch,
)
from .generated import generated_among, marker
from .plan_files import paths_in
from .util import read_text
from .worktree import _base_branch, _parts, _predicted, git


def _stems(predicted):
    stems = (os.path.splitext(os.path.basename(p.rstrip("/")))[0].lower() for p in predicted)
    return [s for s in stems if len(s) >= SCOPE_STEM_MIN]


def _allowed(path, kinds, predicted, docs_dir):
    low = path.lower()
    if "tests" in kinds and re.search(SCOPE_TEST_RE, path) and any(s in low for s in _stems(predicted)):
        return True
    if "docs" in kinds and (path.startswith(docs_dir.rstrip("/") + "/") or low.endswith(".md")):
        return True
    return "notes" in kinds and path.startswith(SCOPE_NOTES_DIR + "/")


def unplanned(changed, predicted, text, kinds, docs_dir, generated=()) -> list[str]:
    """-> the CHANGED paths that are outside PREDICTED, not named in the task TEXT and not of an allowed kind (KINDS, see `scope_allow`). A path in GENERATED (a generated file, see generated.py) is no finding while a predicted source file changed: it is the generator's output."""
    source_changed = any(_predicted(p, predicted, "") for p in changed if p not in generated)
    return [
        p
        for p in changed
        if not _predicted(p, predicted, "") and p not in text and not _allowed(p, kinds, predicted, docs_dir) and not (p in generated and source_changed)
    ]


def _changed(a, task_id, rx=None):
    """-> (workspace-relative paths changed on the job branch (every repo of the job), those of them that are generated files per regex RX), ([], []) when there is no job."""
    d = getattr(a, "dir", ".")
    cfg, branch, parts = _parts(d, a.stage, task_id)
    want = work_branch(cfg, a.stage)
    out, gen = [], []
    for rel, r, w in parts:
        names = [n for n in git(w, "diff", "--name-only", f"{_base_branch(r, want)}...{branch}", check=False).split("\n") if n]
        out += [os.path.join(rel, n) if rel else n for n in names]
        gen += [os.path.join(rel, n) if rel else n for n in generated_among(w, names, rx)] if rx else []
    return out, gen


def scope_note(a, task_id) -> str:
    """Reviewer brief sentence naming the files the job changed outside the task's predicted paths; '' when the guard is off, the job has no worktree or stayed in scope."""
    d = getattr(a, "dir", ".")
    cfg = project(d)
    mode = str(cfg.get("scope_guard") or "off")
    if mode not in SCOPE_MODES or mode == "off" or not cfg.get("worktrees"):
        return ""
    text = read_text(os.path.join(os.path.abspath(d), WORK_DIR, a.stage, f"{task_id}.md"))
    predicted = paths_in(text)
    if not predicted:
        return ""  # no prediction, nothing to compare with
    kinds = [k for k in (cfg.get("scope_allow") or ()) if k in SCOPE_ALLOW_ALL]
    try:
        changed, gen = _changed(a, task_id, marker(d))
        extra = unplanned(changed, predicted, text, kinds, str(cfg.get("docs_dir") or "docs"), gen)
    except (RuntimeError, OSError):
        return ""
    if not extra:
        return ""
    files = ", ".join(extra[:SCOPE_SHOWN]) + (f" (+{len(extra) - SCOPE_SHOWN} more)" if len(extra) > SCOPE_SHOWN else "")
    return (SCOPE_STRICT_NOTE if mode == "strict" else SCOPE_WARN_NOTE).format(files=files)
