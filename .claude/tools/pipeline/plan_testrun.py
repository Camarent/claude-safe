"""plan.py testrun: run each task's `Test:` line once in a throwaway worktree of the repo HEAD before dispatch, and reject a line that cannot validate its task (no test matches, bad command). A failing test is accepted: the change it checks is the missing work. Tasks of Type webtest or report are skipped; the checkout and `.work` stay untouched."""

import os
import sys
import tempfile
import time

from . import gitx
from .plan_dryrun import run_in
from .plan_files import read_entries, task_type
from .plan_rules import _field
from .util import read_text

TEST_FIELD = "Test"
SKIP_TYPES = ("webtest", "report")  # no dev worktree to test: the web-tester or the report reader checks them
NO_MATCH_EXIT = 3  # the runner's "skipped or empty" exit: nothing ran
BAD_COMMAND_EXITS = (126, 127)  # shell: found but not executable, not found
BAD_COMMAND_MARKERS = ("command not found", "No such file")


def judge(tid, cmd, code, output) -> str:
    """-> problem string or '' for one Test line run once in a fresh worktree."""
    if code == NO_MATCH_EXIT or "no test files match" in output:
        return f"{tid}: Test `{cmd}` matched no test (exit {code}): fix the path or the test name"
    first = cmd.split()[0] if cmd.split() else ""
    if code in BAD_COMMAND_EXITS or "command not found" in output or ("No such file" in output and first and first in output):
        return f"{tid}: Test `{cmd}` cannot run (exit {code}): {output.strip()[-200:]}"
    return ""


def testrun_stage(d, repo, runner=run_in) -> tuple[int, list[str]]:
    """-> (count of Test lines run, problems) over the tasks of stage D; each line runs once by RUNNER(wt, cmd) in a throwaway detached worktree of REPO HEAD (removed afterwards)."""
    jobs = []
    for e in read_entries(d):
        text = read_text(os.path.join(d, e["id"] + ".md"))
        cmd = _field(text, TEST_FIELD)
        if cmd and task_type(text) not in SKIP_TYPES:
            jobs.append((e["id"], cmd))
    if not jobs:
        return 0, []
    probs = []
    with tempfile.TemporaryDirectory(prefix="plan-testrun-") as tmp:
        wt = os.path.join(tmp, "wt")
        added = gitx.run(repo, "worktree", "add", "--detach", wt, "HEAD")
        if added.returncode:
            return 0, [f"testrun: git worktree add failed: {added.stderr.strip()}"]
        try:
            t0 = time.monotonic()
            for i, (tid, cmd) in enumerate(jobs, 1):
                code, out = runner(wt, cmd)
                if msg := judge(tid, cmd, code, out):
                    probs.append(msg)
                print(f"testrun: {i}/{len(jobs)} {tid} {time.monotonic() - t0:.1f} s", file=sys.stderr, flush=True)
        finally:
            gitx.run(repo, "worktree", "remove", "--force", wt)
            gitx.run(repo, "worktree", "prune")
    return len(jobs), probs
