"""The pins rule: a task that edits a data or setting file must list the test that pins it (`pins:` error)."""

import glob
import os
import re

from .configuration import AFFECTED_MAX_TESTS, PIN_DATA_DIR, PIN_DATA_EXTS, PIN_PY_WORDS, PINS_PREFIX
from .plan_files import paths_in, task_type
from .plan_rules import _is_test_path, _referencing_tests
from .util import read_text
from .worktree import nested_repos


def _goal_lines(text):
    """The Goal and Done lines of a task file: the only text that may give a .py file its data role."""
    return "\n".join(ln for ln in text.splitlines() if ln.startswith(("Goal:", "Done:")))


def _is_pinnable(p, goal):
    """True for a Predicted path P that is a data or setting file: a data extension, a file under a `configuration` folder, or a .py file that the Goal/Done text names with a whole word of PIN_PY_WORDS."""
    if p.endswith(PIN_DATA_EXTS) or PIN_DATA_DIR in p.split("/")[:-1]:
        return True
    if not p.endswith(".py") or os.path.basename(p) not in goal:
        return False
    low = goal.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in PIN_PY_WORDS)


def check_pinned_tests(d, tid, repo) -> list[str]:
    """`pins:` error: a Predicted path that is a data or setting file is mentioned by a test file that the task does not list in its Predicted paths. Skipped for `Type: report`; a file name more than AFFECTED_MAX_TESTS tests mention is too common and gives nothing."""
    text = read_text(os.path.join(d, tid + ".md"))
    if task_type(text) == "report":
        return []
    paths = [p for p in paths_in(text) if not p.endswith("/") and not glob.has_magic(p)]
    goal = _goal_lines(text)
    pins = [p for p in paths if not _is_test_path(p) and _is_pinnable(p, goal)]
    rels = ["", *nested_repos(repo)]
    out = []
    for p in pins:
        pinners = _referencing_tests(repo, rels, os.path.basename(p))
        if len(pinners) > AFFECTED_MAX_TESTS:
            continue
        for t in pinners:
            if t not in paths:
                out.append(
                    f"{PINS_PREFIX} {tid}: {t} pins {p} (a data or setting file) but the task does not list {t} under Predicted paths: add it, so the dev runs the test that pins the file"
                )
    return out
