#!/usr/bin/env python3
"""qualityrun: advisory script step after a dev's tests are green. Runs the repo's audit tools on what the task touched.

    python3 -I /root/.claude/tools/audit/qualityrun.py --stage S --task task-N [--dir D]

For every repo the task has a worktree in (else --dir): the files changed against the work branch pick the tools (.rs -> clippy; .js/.mjs ->
eslint, knip, jscpd, madge; other languages (ruff, clang-tidy ...) only when pipeline.json `quality_tools` lists them; only tools the repo's AUDIT.md lists and that are installed), `audit_run.py <repo> --only ...` runs them, and only findings on changed lines
(every line of a new file) are kept.

Prints `VERDICT: FAIL` plus the findings (the dev gets them once, as a recommendation) or `VERDICT: DONE` (clean, nothing to check, or the tools
could not run: never BLOCKED, a missing tool must not stop a task)."""

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir
import wt  # noqa: E402
from pipeline import gitx  # noqa: E402
from pipeline.configuration import project, quality_tools, work_branch  # noqa: E402
from search import lpcall  # noqa: E402

TIMEOUT_S = 1500  # audit_run of one repo
MAX_LISTED = 60
HEADER = (
    "ADVISORY quality findings on the lines you changed. Not required: fix the cheap, safe ones inside your predicted paths, "
    "leave the rest, never widen scope, and say in your report which you skipped and why."
)


def git(repo, *args):
    return gitx.try_out(repo, *args) or ""


def changed(repo, base):
    """{path relative to the repo: set of changed line numbers, or None for a new file (every line)}."""
    ranges = lpcall.added_ranges(repo, base)
    new = set(git(repo, "ls-files", "-o", "--exclude-standard").split()) | set(git(repo, "diff", "--name-only", "--diff-filter=A", base).split())
    out = dict.fromkeys(new)
    for f, rs in ranges.items():
        if f not in out:
            out[f] = {n for a, b in rs for n in range(a, b + 1)}
    for f in git(repo, "diff", "--name-only", base).split():
        out.setdefault(f, set())  # touched without added lines (pure deletion)
    return dict(sorted(out.items()))


def tools_for(files, table):
    """Tool names for the changed files; `table` is {extension: tools} (pipeline.configuration.quality_tools)."""
    names = []
    for f in files:
        for t in table.get(os.path.splitext(f)[1], ()):
            if t not in names:
                names.append(t)
    return names


def repo_tools(repo):
    """Tool names the repo's AUDIT.md lists and whose program is installed, [] when none (a missing tool is skipped, never a failure)."""
    try:
        from audit import audit_run

        return [t[0] for t in audit_run.load_tools(repo) if _installed(t[2])]
    except (OSError, ValueError, KeyError, ImportError):
        return []


def _installed(cmd):
    words = cmd.split()
    return bool(words) and shutil.which(words[0]) is not None


def matches(finding_file, touched):
    f = (finding_file or "").removeprefix("./")
    return f == touched or f.endswith("/" + touched) or touched.endswith("/" + f)


def keep(findings, ch):
    """The findings on changed lines of changed files."""
    out = []
    for f in findings:
        for path, lines in ch.items():
            if matches(f.get("file"), path) and (lines is None or not f.get("line") or f["line"] in lines):
                out.append({**f, "file": path})
                break
    return out


def run_audit(repo, names):
    """-> findings list; raises RuntimeError with a reason when the tools could not run."""
    cmd = [sys.executable, "-I", os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit_run.py"), repo, "--only", ",".join(names)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(str(e)) from e
    m = re.search(r"^findings: (.+)$", p.stdout, re.M)
    if not m:
        raise RuntimeError("audit_run gave no findings file: " + (p.stdout + p.stderr).strip()[-200:])
    try:
        return json.loads(pathlib.Path(m[1].strip()).read_text(encoding="utf-8")).get("findings", [])
    except (OSError, ValueError) as e:
        raise RuntimeError(str(e)) from e


def repos_of(d, stage, task):
    """-> [(repo path, base branch)]; the task's worktrees, else the project dir itself when worktrees are off."""
    cfg = project(d)
    info = wt.live_job(d, stage, task)
    if info:
        return [(p, info.get("base") or work_branch(cfg, stage)) for p in info["repos"].values() if os.path.isdir(p)]
    return [] if cfg.get("worktrees") else [(d, "HEAD")]


def run(stage, task, d, auditor=run_audit):
    """-> (verdict word, text). `auditor` is injectable for tests."""
    blocks, notes = [], []
    table = quality_tools(project(d))
    for repo, base in repos_of(d, stage, task):
        ch = changed(repo, base)
        names = [t for t in tools_for(ch, table) if t in repo_tools(repo)]
        if not names:
            notes.append(f"{repo}: no audit tool applies to the changed files")
            continue
        try:
            found = keep(auditor(repo, names), ch)
        except RuntimeError as e:
            notes.append(f"{repo}: quality run skipped ({e})")
            continue
        if found:
            lines = [f"  {f['file']}:{f.get('line', '?')} [{f['tool']}] {f.get('rule', '')} {f.get('message', '')}".rstrip() for f in found]
            more = len(lines) - MAX_LISTED
            blocks.append(
                f"{repo} ({', '.join(names)}): {len(found)} finding(s)\n" + "\n".join(lines[:MAX_LISTED]) + (f"\n  ... +{more} more" if more > 0 else "")
            )
        else:
            notes.append(f"{repo}: clean ({', '.join(names)})")
    if blocks:
        return "FAIL", HEADER + "\n\n" + "\n\n".join(blocks)
    return "DONE", "quality run: " + ("; ".join(notes) if notes else "nothing to check")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    a = ap.parse_args(argv)
    v, text = run(a.stage, a.task, os.path.abspath(a.dir))
    print(f"VERDICT: {v}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
