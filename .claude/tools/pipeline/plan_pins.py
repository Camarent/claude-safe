"""The pins rule: a task that edits a data or setting file must list the test that pins it (`pins:` error)."""

import glob
import os
import re

from . import gitx
from .configuration import (
    AFFECTED_CODE_EXTS,
    AFFECTED_MAX_TESTS,
    AFFECTED_TEST_DIRS,
    PIN_DATA_DIR,
    PIN_DATA_EXTS,
    PIN_PY_WORDS,
    PINS_PREFIX,
    TEST_NAME_RE,
)
from .plan_files import paths_in, task_type
from .util import read_text
from .worktree import nested_repos


def _is_test_path(path):
    """True for a test file: a TEST_NAME_RE file name, or any file under a `test`/`tests` directory."""
    return bool(re.search(TEST_NAME_RE, os.path.basename(path)) or AFFECTED_TEST_DIRS & set(path.split("/")[:-1]))


def _referencing_tests(repo, rels, stem):
    """-> sorted repo-relative test files (TEST_NAME_RE name, or under a `test`/`tests` dir) that mention STEM as a whole word, in REPO and its nested repos."""
    found = set()
    for rel in rels:
        r = os.path.join(repo, rel) if rel else repo
        for f in gitx.try_text(r, "grep", "-l", "-w", "-F", "-e", stem).splitlines():
            full = os.path.join(rel, f)
            if full.endswith(AFFECTED_CODE_EXTS) and _is_test_path(full):
                found.add(full)
    return sorted(found)


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
