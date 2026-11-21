"""Depends check: the `Depends:` line of each task file must name the same ids as its TASKS.md entry."""

import os
import re

from .util import read_text

DEPENDS_LINE_RE = re.compile(r"^\s*depends\s*:\s*(.*)", re.I)
TASK_ID_RE = re.compile(r"task-\d+|ops-\d+", re.I)


def _file_depends(text):
    """-> set of task ids on the `Depends:` lines of a task file (line start, case-insensitive)."""
    ids = set()
    for line in text.splitlines():
        m = DEPENDS_LINE_RE.match(line)
        if m:
            ids |= {t.lower() for t in TASK_ID_RE.findall(m.group(1))}
    return ids


def _fmt(ids):
    return ", ".join(sorted(ids)) if ids else "nothing"


def depends_line_problems(d, ents):
    """-> messages for entries whose task file `Depends:` ids differ from their TASKS.md depends (both sides compared as sets)."""
    probs = []
    for e in ents:
        f = os.path.join(d, e["id"] + ".md")
        if not os.path.isfile(f):
            continue  # a missing task file is reported by the task-file check
        md = set(e.get("depends", []))
        tf = _file_depends(read_text(f))
        if md == tf:
            continue
        if md and not tf:
            probs.append(f"{e['id']}: TASKS.md depends on {_fmt(md)} but {e['id']}.md has no Depends line")
        elif tf and not md:
            probs.append(f"{e['id']}: {e['id']}.md depends on {_fmt(tf)} but TASKS.md has no depends")
        else:
            probs.append(f"{e['id']}: TASKS.md depends on {_fmt(md)} but {e['id']}.md depends on {_fmt(tf)}")
    return probs
