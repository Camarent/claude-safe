"""plan_check rule: a `Test:` line whose ptest selector matches no test file (the run is empty and the dev stops BLOCKED)."""

import fnmatch
import os
import re
import shlex
from pathlib import Path

from .configuration import TASKS_FILE
from .plan_files import paths_in
from .shellwords import split_commands
from .tasks import parse_tasks
from .util import read_text

TEST_LINE_RE = re.compile(r"(?m)^Test:\s*(.*)$")
PTEST_DIR = ".claude/tools/tests"  # ptest.py default --dir (its tests folder, relative to the repo)
PTEST_VALUE_OPTS = ("--jobs", "--dir")  # ptest options that take a value (the next token is not a pattern)
DEV_TEST_CMD = ("scripts/dev.sh", "test")  # the wrapper's test subcommand, which passes its arguments to ptest.py


def _selector_args(part):
    """-> the ptest arguments of one shell command, [] when it is not a ptest call (`scripts/dev.sh test ...` or `ptest.py ...`)."""
    try:
        toks = shlex.split(part)
    except ValueError:
        return []
    for i, tok in enumerate(toks):
        if tok.endswith("ptest.py"):
            return toks[i + 1 :]
        if tuple(toks[i : i + 2]) == DEV_TEST_CMD:
            return toks[i + 2 :]
    return []


def _patterns_and_dir(args):
    """-> (patterns, tests dir or None): the positional arguments of ptest, skipping options and the values of PTEST_VALUE_OPTS."""
    pats, tests_dir, skip = [], None, False
    for i, tok in enumerate(args):
        if skip:
            skip = False
            continue
        if tok in PTEST_VALUE_OPTS:
            skip = True
            if tok == "--dir" and i + 1 < len(args):
                tests_dir = args[i + 1]
        elif not tok.startswith("-"):
            pats.append(tok)
    return pats, tests_dir


def ptest_selector_problems(d, tid, repo) -> list[str]:
    """Problems for one task file: a ptest selector pattern in its `Test:` line that matches no test file under the tests dir and that no Predicted path (or one of the tasks it depends on) creates."""
    text = read_text(os.path.join(d, tid + ".md"))
    m = TEST_LINE_RE.search(text)
    if not m:
        return []
    pats, tests_dir = [], None
    for part in split_commands(m[1].replace("`", " ")):
        args = _selector_args(part)
        if args:
            found, given = _patterns_and_dir(args)
            pats += found
            tests_dir = given or tests_dir
    if not pats:
        return []
    root = os.path.join(repo, tests_dir or PTEST_DIR)
    rel_root = os.path.relpath(root, repo)
    made = _made_paths(d, tid)
    probs = []
    for pat in pats:
        try:
            if next(Path(root).glob(pat), None) is not None:
                continue
        except (NotImplementedError, ValueError) as e:  # an absolute or empty pattern: report it, never crash the check
            probs.append(f"{tid}: ptest selector {pat} is not a valid pattern under {rel_root} ({e}); use a name relative to the tests dir")
            continue
        if any(fnmatch.fnmatch(x, os.path.join(rel_root, pat)) for x in made):
            continue
        probs.append(
            f"{tid}: ptest selector {pat} matches no test file under {rel_root} and no Predicted path creates one "
            "(end a ptest selector with .py or *: a name without .py matches nothing)"
        )
    return probs


def _made_paths(d, tid):
    """The Predicted paths of task TID plus those of every task it depends on (transitive), as check_test_globs counts them."""
    text = read_text(os.path.join(d, tid + ".md"))
    made = paths_in(text)
    deps = {t["id"]: t.get("depends", []) for t in parse_tasks(os.path.join(d, TASKS_FILE))}
    todo, seen = list(deps.get(tid, [])), set()
    while todo:
        dep = todo.pop()
        if dep in seen:
            continue
        seen.add(dep)
        made = made + paths_in(read_text(os.path.join(d, dep + ".md")))
        todo += deps.get(dep, [])
    return made
