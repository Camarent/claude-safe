"""The Needs rule (an error): a task file's `Needs: <repo path> :: <literal>` line names a fact the repo must hold before the task runs. The plan fails at plan time when the file is missing or lacks the literal, unless a task the task depends on lists that path in its Predicted paths (it creates the file first)."""

import os

from .configuration import NEEDS_PREFIX, NEEDS_SEP
from .plan_files import paths_in, read_entries
from .plan_rules import _transitive_deps
from .task_text import lines
from .util import read_text


def _split(value):
    """-> (repo path, literal) of one Needs value; the literal is "" when the line has no separator (only the file is checked)."""
    path, sep, literal = value.partition(NEEDS_SEP)
    return path.strip(), literal.strip() if sep else ""


def check_needs(d, tid, workspace) -> list[str]:
    """`plan-gap:` problems for the Needs lines of task TID: WORKSPACE is the repo root the paths are relative to. A line is satisfied when its file exists and holds the literal; a dependency that lists the path in Predicted paths exempts it."""
    text = read_text(os.path.join(d, tid + ".md"))
    ents = read_entries(d)
    created = set()
    for dep in _transitive_deps(ents, tid):
        created |= set(paths_in(read_text(os.path.join(d, dep + ".md"))))
    probs = []
    for _, value in lines(text, "Needs"):
        path, literal = _split(value)
        if path in created:
            continue
        full = os.path.join(workspace, path)
        if os.path.isfile(full) and literal in read_text(full):
            continue
        probs.append(
            f"{NEEDS_PREFIX} {tid}: Needs line `{value}` names {path}, which is missing or lacks the literal, and no task it depends on creates it: fix the Needs line or the task's depends"
        )
    return probs
