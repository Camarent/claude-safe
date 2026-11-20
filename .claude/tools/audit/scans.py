#!/usr/bin/env python3
"""scans: run the `scans` commands of <workspace>/pipeline.json by script, no model, before the audits.

    python3 -I /root/.claude/tools/audit/scans.py --dir WORKSPACE --stage S

`"scans": ["gitleaks detect --no-banner", "cargo audit"]`: one command per entry, split with shlex, no shell, 600 s each, run in the repo folder.
Writes the combined, tail-limited output to `.work/<S>/audits/scans.md`. Findings (non-zero exit) still give `VERDICT: DONE`;
only a command that cannot start (or times out) gives `VERDICT: BLOCKED`."""

import argparse
import os
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the script dir
from pipeline.configuration import WORK_DIR, project, repo_dir
from pipeline.util import add_common_args, work_path
from retro import stale_findings

TIMEOUT = 600
TAIL_LINES = 80


def run_scans(cmds, repo, runner=subprocess.run):
    """-> (markdown, blocked reason or ""). Every command runs even after findings."""
    parts, blocked = [], ""
    for c in cmds:
        try:
            r = runner(shlex.split(c), cwd=repo, capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            blocked = blocked or f"`{c}` could not run: {e}"
            parts.append(f"## `{c}`\nnot run: {e}")
            continue
        lines = (r.stdout + r.stderr).strip().splitlines()
        cut = f"(first {len(lines) - TAIL_LINES} lines cut)\n" if len(lines) > TAIL_LINES else ""
        parts.append(f"## `{c}`\nexit {r.returncode}\n```\n{cut}" + "\n".join(lines[-TAIL_LINES:]) + "\n```")
    return "# Scans\n\n" + "\n\n".join(parts) + "\n", blocked


def stale_section(report, repo):
    """-> markdown listing the cites of an earlier audit REPORT by status (`maybe fixed`: a later commit touched the cited line; `open`); "" without a report."""
    if not report:
        return ""
    found = stale_findings.stale_findings(report, repo)
    return "\n## Earlier audit findings (script, git)\n" + "\n".join(f"- {s}: {p}:{n}" for p, n, s in found) + "\n" if found else ""


def main(argv=None):
    ap = argparse.ArgumentParser()
    add_common_args(ap, stage=True)
    ap.add_argument("--audit-report", default="", help="an earlier audit report: its cited lines are marked maybe fixed when a later commit touched them")
    a = ap.parse_args(argv)
    cfg = project(a.dir)
    repo = repo_dir(a.dir, cfg)
    text, blocked = run_scans([str(c) for c in cfg.get("scans") or []], repo)
    text += stale_section(a.audit_report, repo)
    out = work_path(os.path.join(a.dir, WORK_DIR, a.stage), "scans.md", create=True)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        f.write(text)
    print(f"VERDICT: BLOCKED\n{blocked}" if blocked else f"VERDICT: DONE\nscans written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
