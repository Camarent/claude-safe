"""plan.py dryrun: run each task's `Dry run: <cmd>  # expect > N changes` line in a temp worktree of the repo and fail when the tool is a no-op. Opt-in per task; the checkout and `.work` stay untouched."""

import os
import re
import subprocess
import tempfile

from . import gitx
from .configuration import DRYRUN_COUNT_RE, DRYRUN_FIELD, DRYRUN_SHELL, DRYRUN_TIMEOUT_S
from .plan_files import read_entries
from .plan_rules import _field, parse_dryrun
from .util import read_text


def effect_count(output):
    """The first `<n> changes|updated|records|...` count in OUTPUT, 0 when none."""
    m = re.search(DRYRUN_COUNT_RE, output)
    return int(m[1]) if m else 0


def run_in(wt, cmd):
    """-> (exit code, stdout+stderr) of CMD run by the shell in WT."""
    try:
        r = subprocess.run([*DRYRUN_SHELL, cmd], cwd=wt, capture_output=True, text=True, errors="replace", timeout=DRYRUN_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {DRYRUN_TIMEOUT_S} s"
    return r.returncode, r.stdout + r.stderr


def judge(tid, cmd, want, code, output):
    """-> problem string or '' for one dry run."""
    if code:
        return f"{tid}: dry run `{cmd}` exited {code}: {output.strip()[-200:]}"
    got = effect_count(output)
    if got <= want:
        return f"{tid}: dry run `{cmd}` reports {got} changes, expected > {want}: the tool is a no-op on the current data"
    return ""


def dryrun_stage(d, repo):
    """-> (count of runs, problems) over the tasks of stage D with a `Dry run:` line, run in a throwaway detached worktree of REPO HEAD (removed afterwards)."""
    jobs = []
    for e in read_entries(d):
        spec = parse_dryrun(_field(read_text(os.path.join(d, e["id"] + ".md")), DRYRUN_FIELD))
        if spec:
            jobs.append((e["id"], *spec))
    if not jobs:
        return 0, []
    probs = []
    with tempfile.TemporaryDirectory(prefix="plan-dryrun-") as tmp:
        wt = os.path.join(tmp, "wt")
        added = gitx.run(repo, "worktree", "add", "--detach", wt, "HEAD")
        if added.returncode:
            return 0, [f"dryrun: git worktree add failed: {added.stderr.strip()}"]
        try:
            for tid, cmd, want in jobs:
                code, out = run_in(wt, cmd)
                if msg := judge(tid, cmd, want, code, out):
                    probs.append(msg)
        finally:
            gitx.run(repo, "worktree", "remove", "--force", wt)
            gitx.run(repo, "worktree", "prune")
    return len(jobs), probs
