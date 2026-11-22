"""Post-exit summary for the lead: stage branch tip, PR state and the last events of the stage's orch.jsonl.

Read-only: it runs `git rev-parse` and `gh pr list` and never writes. `gh` and `git` calls go through runners passed in, so tests need no network.

    python3 .claude/tools/run_summary.py --stage S [--dir .]
"""

import argparse
import functools
import json
import os
import subprocess
import sys

from pipeline import gitx
from pipeline.configuration import GIT_TIMEOUT_S, ORCH_LOG, WORK_DIR, project, work_branch
from pipeline.util import read_jsonl

GH_BIN = "gh"
GH_PR_FIELDS = "number,state,url,isDraft"
SUMMARY_EVENTS = 3  # the last N events of orch.jsonl
EVENT_CHARS = 200  # one event line is cut to this many characters


def gh_run(args, cwd):
    """-> (rc, stdout) of `gh ARGS` run in CWD; rc None when gh is missing or times out."""
    try:
        r = subprocess.run([GH_BIN, *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
    return r.returncode, r.stdout


def tip_line(repo, branch, git):
    """-> the stage branch tip line; GIT is a runner (repo, *args) -> stdout or None."""
    sha = git(repo, "rev-parse", "--short", branch)
    return f"stage branch {branch}: {sha.strip() if sha else 'not found'}"


def pr_line(branch, gh):
    """-> the PR state line for BRANCH; GH is a runner (args) -> (rc, stdout)."""
    rc, out = gh(["pr", "list", "--head", branch, "--state", "all", "--json", GH_PR_FIELDS])
    if rc is None:
        return "PR: gh not available"
    if rc != 0:
        return f"PR: gh failed (rc {rc})"
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return "PR: gh gave unreadable output"
    if not prs:
        return f"PR: none for {branch}"
    p = prs[0]
    draft = " draft" if p.get("isDraft") else ""
    return f"PR: #{p.get('number')} {p.get('state')}{draft} {p.get('url')}"


def event_lines(log):
    """-> one line per event among the last SUMMARY_EVENTS of LOG, or one line saying there is none."""
    if not os.path.isfile(log):
        return [f"events: no {ORCH_LOG} for this stage yet"]
    evs = read_jsonl(log, quiet=True)[-SUMMARY_EVENTS:]
    if not evs:
        return [f"events: {ORCH_LOG} has no events"]
    return [f"event: {json.dumps(e, ensure_ascii=False, sort_keys=True)[:EVENT_CHARS]}" for e in evs]


def summary(project_dir, stage, gh=None, git=gitx.try_out):
    """-> the summary lines for STAGE in PROJECT_DIR. GH and GIT are runners; None means the real gh and git."""
    if gh is None:
        gh = functools.partial(gh_run, cwd=project_dir)
    branch = work_branch(project(project_dir), stage)
    lines = [tip_line(project_dir, branch, git), pr_line(branch, gh)]
    lines += event_lines(os.path.join(project_dir, WORK_DIR, stage, ORCH_LOG))
    return lines


def main(argv=None, gh=None, git=gitx.try_out):
    p = argparse.ArgumentParser(description="Print the stage branch tip, PR state and last events of a stage run.")
    p.add_argument("--stage", required=True, help="stage name (the work branch and .work/<stage> folder)")
    p.add_argument("--dir", default=None, help="project dir (default: the current one)")
    a = p.parse_args(argv)
    for line in summary(a.dir or os.getcwd(), a.stage, gh, git):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
