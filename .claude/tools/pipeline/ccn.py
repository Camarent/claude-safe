"""Complexity gate for the reviewer: the driver (not Haiku) measures the ccn of the functions a task changed."""

import csv
import io
import re
import subprocess

from . import gitx
from .configuration import LIZARD_TIMEOUT_S, work_branch
from .util import swallowed

DEFAULT_REVIEW_CCN = 10
# dispatch reason of a Haiku reviewer started by a ccn hit; a FAIL of it moves the next reviewer to Sonnet
CCN_REVIEW_WHY = "reviewer ccn hit (Haiku, ccn brief)"
CCN_REVIEW_BRIEF = " Complexity hit: check the changed functions named in your reason for the logic a long branch chain can hide (missed cases, wrong fallthrough)."  # appended to the Haiku reviewer brief of a ccn hit
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def _git(w, *args):
    return gitx.try_out(w, *args) or ""


def changed_lines(w, base, branch):
    """-> {file: set(new line numbers)} of `base...branch` in the checkout `w`."""
    out: dict = {}
    cur = None
    for ln in _git(w, "diff", "-U0", "--no-color", f"{base}...{branch}").splitlines():
        if ln.startswith("+++ "):
            cur = ln[6:] if ln.startswith("+++ b/") else None
            continue
        m = HUNK.match(ln)
        if m and cur:
            start, n = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
            out.setdefault(cur, set()).update(range(start, start + max(n, 1)))
    return out


def worst_changed(w, base, branch, threshold):
    """-> (ccn, function, file) of the most complex changed function above `threshold`, else None. Any error -> None."""
    try:
        lines = changed_lines(w, base, branch)
        if not lines:
            return None
        r = subprocess.run(["lizard", "--csv", *sorted(lines)], cwd=w, capture_output=True, text=True, timeout=LIZARD_TIMEOUT_S, check=False)
        worst = None
        for row in csv.reader(io.StringIO(r.stdout)):
            if len(row) < 11 or not row[1].isdigit():
                continue
            ccn, name, f = int(row[1]), row[7], row[6].removeprefix("./")
            lo, hi = int(row[9]), int(row[10])
            if ccn > threshold and f in lines and any(lo <= n <= hi for n in lines[f]) and (worst is None or ccn > worst[0]):
                worst = (ccn, name, f)
        return worst
    except Exception as ex:
        swallowed("ccn.worst_changed", ex)
        return None


def review_ccn_hit(a, task_id):
    """-> reason text when a changed function of the task's job branch exceeds pipeline.json `review_ccn`, else None. Never raises."""
    try:
        from . import worktrees
        from .configuration import project
        from .worktrees import job_info

        if not job_info(a, task_id, create=False):
            return None
        d = getattr(a, "dir", ".")
        cfg = project(d)
        th = int(cfg.get("review_ccn", DEFAULT_REVIEW_CCN))
        wt = worktrees.wt
        _cfg, branch, parts = wt._parts(d, a.stage, task_id)
        want = work_branch(cfg, getattr(a, "feature", None) or a.stage)
        for _rel, r, w in parts:
            hit = worst_changed(w, wt._base_branch(r, want), branch, th)
            if hit:
                return f"{CCN_REVIEW_WHY}: ccn {hit[0]} in {hit[1]} ({hit[2]}), above review_ccn {th}"
    except Exception as ex:
        swallowed("ccn.review_ccn_hit", ex)
        return None
    return None
