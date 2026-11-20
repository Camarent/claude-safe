"""Stale audit findings: which `file:line` cites of an audit report were touched by commits made after the report.

    python3 -I /root/.claude/tools/retro/stale_findings.py <audit-report.md> --repo <repo>

A script, no model. A finding whose cited line sits in a hunk that a later commit changed is marked `maybe fixed`; it is a hint to
re-check, not proof (the hunk may touch the line without fixing the finding). Others stay `open`. Scouts and retro scans that list
a report's findings use this so a fixed finding is not reported as open.
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx

CITE_RE = re.compile(r"(?<![\w/.-])([\w./-]+\.[A-Za-z0-9]+):(\d+)")  # path.ext:LINE
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? ")
GIT_DATE_FMT = "%Y-%m-%dT%H:%M:%S+0000"
MAYBE_FIXED, OPEN = "maybe fixed", "open"


def cites(text):
    """-> the distinct (path, line) cites of a report text, in order of first appearance."""
    return list(dict.fromkeys((m.group(1), int(m.group(2))) for m in CITE_RE.finditer(text)))


def base_commit(repo, mtime):
    """-> the last commit of HEAD made at or before epoch MTIME ("" when there is none)."""
    when = time.strftime(GIT_DATE_FMT, time.gmtime(mtime))
    return gitx.run(repo, "rev-list", "-1", f"--before={when}", "HEAD").stdout.strip()


def changed_ranges(repo, base, path):
    """-> [(first, last)] old-side line ranges of the hunks that commits after BASE changed in PATH (an insertion counts as its anchor line)."""
    diff = gitx.run(repo, "diff", "-U0", base, "HEAD", "--", path).stdout
    out = []
    for ln in diff.splitlines():
        m = HUNK_RE.match(ln)
        if m:
            first = int(m.group(1))
            out.append((first, first + max(int(m.group(2) or 1), 1) - 1))
    return out


def stale_findings(report, repo):
    """-> [(path, line, status)] for every cite of the audit REPORT file: `maybe fixed` when a commit after the report's mtime changed that line, else `open`. [] when the report is unreadable or the repo has no commit before it."""
    try:
        with open(report, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        mtime = os.path.getmtime(report)
    except OSError:
        return []
    base = base_commit(repo, mtime)
    if not base:
        return []
    cache: dict = {}
    out = []
    for path, line in cites(text):
        if path not in cache:
            cache[path] = changed_ranges(repo, base, path)
        out.append((path, line, MAYBE_FIXED if any(a <= line <= b for a, b in cache[path]) else OPEN))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--repo", default=".")
    a = ap.parse_args(argv)
    for path, line, status in stale_findings(a.report, a.repo):
        print(f"{status}: {path}:{line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
