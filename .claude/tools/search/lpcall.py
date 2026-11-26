"""lpcall: the one way tools call `lp` (local.py) or another command, fail open. Shared by plan_context, audit_context, audit_enrich, prereview, find_eval."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx
from pipeline.util import run_capture

LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local.py")


def run_rc(args, cwd, t=60):
    """-> (returncode, stdout); (None, "") when the command cannot run or times out."""
    r = run_capture(args, cwd, t, swallow=True)
    return (None, "") if r is None else (r.returncode, r.stdout)


def sh(args, cwd, t=40):
    """-> stdout of a command that exited 0, else ""."""
    rc, out = run_rc(args, cwd, t)
    return out if rc == 0 else ""


def git_text(args, cwd, t=40):
    """-> stdout of `git args` that exited 0, else "" (also on a timeout or when git cannot run)."""
    return gitx.try_text(cwd, *args, timeout=t)


def lp_rc(repo, *a, t=60):
    return run_rc(["python3", "-I", LOCAL, *a], repo, t)


def lp(repo, *a, t=60):
    return sh(["python3", "-I", LOCAL, *a], repo, t)


def changed_files(repo, base, head):
    """Files that differ between two git refs, [] when git cannot say."""
    return [x for x in git_text(["diff", "-z", "--name-only", f"{base}..{head}"], repo, 60).split("\0") if x]


def added_ranges(repo, base, head=None):
    """-> {file: [(first, last) added line ranges]} for every changed file, any language. head=None: working tree against `base`."""
    spec = [f"{base}..{head}"] if head else [base]
    text = git_text(["-c", "core.quotepath=false", "diff", "-U0", "--no-color", *spec], repo, 120)
    out: dict = {}
    for f, lines in gitx.added_lines(text).items():
        ranges = out[f] = []
        for n, _ in lines:  # consecutive added lines form one range
            if ranges and ranges[-1][1] == n - 1:
                ranges[-1] = (ranges[-1][0], n)
            else:
                ranges.append((n, n))
    return out
