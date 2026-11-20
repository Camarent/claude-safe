#!/usr/bin/env python3
"""Commit helper: validates the subject, appends the trailer, runs git commit.

Usage: commit.py --subject "type(area): what" [--body TEXT] [--dir D] [--workspace W]
Never stages files, never passes --no-verify.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
from pipeline import commit_subject
from pipeline.configuration import DEFAULT_WORKSPACE, ConfigError, project

TYPES = ("feat", "fix", "refactor", "test", "docs", "chore", "perf", "build", "style")  # same list as dojo-kit/.githooks/commit-msg
SUBJECT_RE = commit_subject.policy("|".join(TYPES), r"[A-Za-z0-9][A-Za-z0-9._-]*", area_required=True, min_tail=0)


def subject_ok(s):
    """True for a one-line `type(area): what`, or `Revert "..."` / `Reapply "..."` around one (nested reverts allowed)."""
    return commit_subject.subject_ok(s, SUBJECT_RE)


DEFAULT_TRAILER = "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"


def trailer(workspace=None):
    """-> `commit_trailer` of the project config: pipeline.json in WORKSPACE (default: DEFAULT_WORKSPACE), else the default."""
    try:
        val = project(workspace or DEFAULT_WORKSPACE).get("commit_trailer")
    except (OSError, ConfigError):
        val = None
    return val if isinstance(val, str) and val.strip() else DEFAULT_TRAILER


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--workspace", default=None, help="folder holding pipeline.json (default: the workspace mount)")
    a = ap.parse_args(argv)
    if "\n" in a.subject or not subject_ok(a.subject):
        print('bad subject: need one line `type(area): what` (or `Revert "..."` / `Reapply "..."`), type in ' + "|".join(TYPES), file=sys.stderr)
        return 2
    msg = a.subject + "\n\n"
    if a.body.strip():
        msg += a.body.strip() + "\n\n"
    msg += trailer(a.workspace)
    r = subprocess.run(["git", "-C", a.dir, "commit", "-m", msg], check=False)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
