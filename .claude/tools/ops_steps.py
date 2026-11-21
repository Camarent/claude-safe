#!/usr/bin/env python3
"""ops_steps: the mechanical `ops` sessions as script steps (pipeline.json `ops_scripts` lists which ones the driver runs by script).

    python3 -I /root/.claude/tools/ops_steps.py cleanup|push|digest --dir D --stage S

Prints `VERDICT: DONE|FAIL` first, then facts. No model."""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import gitx, webskip
from pipeline.configuration import BLOCKED, DONE, ESCALATE, EV_SESSION, FAIL, GIT_LONG_TIMEOUT_S, METRICS_LOG, VERDICT_PREFIX, project, release_branch, repo_dir
from pipeline.stagedir import resolve_stage
from pipeline.util import add_common_args, read_jsonl, read_text, work_path
from pipeline.worktree import nested_repos

KEEP_REPORT_RE = re.compile(r"^\s*(?:VERDICT\s*:\s*)?(BLOCKED|FAIL|ESCALATE)\b")  # the retro reads these reports
CRED_ARGS = ("-c", "credential.helper=!gh auth git-credential")
TOP_SESSIONS = 5
REMOTE = "origin"


def stage_dir(d, stage):
    return resolve_stage(d, stage)


def first_line(path):
    text = read_text(path).lstrip()
    return text.splitlines()[0] if text else ""


def cleanup(d, stage):
    """-> (ok, lines). Job worktrees (when `worktrees` is on), finished task/plan files, passing reports and leads/ of the stage; TASKS.md, orch.jsonl and the rest stay."""
    lines, root = [], stage_dir(d, stage)
    if project(d).get("worktrees"):
        import wt

        wt.clean(d, stage)
    victims = (
        glob.glob(os.path.join(root, "task-*.md")) + glob.glob(os.path.join(root, "reports", "report-a*.md")) + glob.glob(os.path.join(root, "report-a*.md"))
    )
    gone, failed = 0, []
    for f in sorted(set(victims)):
        if os.path.basename(f).startswith("report-") and KEEP_REPORT_RE.match(first_line(f)):
            continue
        try:
            os.remove(f)
            gone += 1
        except OSError as e:
            failed.append(f"{f}: {e}")
    leads = os.path.join(root, "leads")
    if os.path.isdir(leads):
        gone += sum(len(fs) for _r, _d, fs in os.walk(leads))
        shutil.rmtree(leads, onerror=lambda _f, path, exc: failed.append(f"{path}: {exc[1]}"))
    lines.append(f"deleted {gone} files")
    kept = sorted(n for n in os.listdir(root)) if os.path.isdir(root) else []
    lines.append("kept: " + ", ".join(kept))
    return not failed, lines + failed


def repos(d):
    """The project repo, then every repo nested in it (as far as the stage merge looks: worktree.nested_repos)."""
    cfg = project(d)
    top = repo_dir(os.path.abspath(d), cfg)
    out = [(top, release_branch(top, cfg))]
    for n in sorted(nested_repos(top)):
        p = os.path.join(top, n)
        out.append((p, release_branch(p, {})))
    return out


def push(d):
    """-> (ok, lines): push the release branch of each repo that has an `origin`; a repo without one is local-only, never a failure."""
    ok, lines = True, []
    for repo, rel in repos(d):
        if gitx.run(repo, "remote", "get-url", REMOTE).returncode:
            lines.append(f"{repo}: local-only (no {REMOTE})")
            continue
        try:
            r = gitx.run(repo, *CRED_ARGS, "push", REMOTE, rel, timeout=GIT_LONG_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            ok = False
            lines.append(f"{repo}: error: push timed out after {GIT_LONG_TIMEOUT_S}s")
            continue
        if r.returncode:
            ok = False
            lines.append(f"{repo}: error: {(r.stderr or r.stdout).strip()[-200:]}")
        else:
            lines.append(f"{repo}: pushed {rel}")
    return ok, lines


def _cost(row):
    return row.get("cost_usd") or 0  # a hand-edited row may hold null


def digest(d, stage):
    """-> (ok, lines): the cost digest of the stage from metrics.jsonl, also written to retro/retro-digest.md."""
    rows = [r for r in read_jsonl(os.path.join(stage_dir(d, stage), METRICS_LOG)) if r.get("ev") == EV_SESSION]
    per: defaultdict = defaultdict(lambda: [0, 0.0])
    for r in rows:
        g = per[(r.get("agent"), r.get("model"))]
        g[0] += 1
        g[1] += _cost(r)
    out = [f"cost per agent and model ({len(rows)} sessions, ${sum(g[1] for g in per.values()):.2f}):"]
    out += [f"- {a} {m}: {g[0]} sessions, ${g[1]:.2f}" for (a, m), g in sorted(per.items(), key=lambda x: -x[1][1])]
    out.append(f"costliest {TOP_SESSIONS} sessions:")
    out += [
        f"- {r.get('task') or r.get('id')} {r.get('agent')} {r.get('verdict') or '-'} ${_cost(r):.2f}"
        for r in sorted(rows, key=lambda r: -_cost(r))[:TOP_SESSIONS]
    ]
    bad = [r for r in rows if r.get("verdict") in (BLOCKED, FAIL, ESCALATE)]
    out.append(f"not passed: {len(bad)} sessions, ${sum(_cost(r) for r in bad):.2f}")
    out += [f"- {r.get('task') or r.get('id')} {r.get('verdict')}: {r.get('reason', '')}" for r in bad[:TOP_SESSIONS]]
    out += webskip.skips(d, stage)  # a skipped browser check stays visible in the retro
    f = work_path(stage_dir(d, stage), "retro-digest.md", create=True)
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return True, out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("cleanup", "push", "digest", "webskip"))
    add_common_args(ap, stage=True, dir_required=True)
    ap.add_argument("--ids", default="", help="webskip: the task ids the skipped browser check covered")
    a = ap.parse_args(argv)
    try:
        ok, lines = _run(a)
    except ValueError as e:
        ok, lines = False, [str(e)]
    print(VERDICT_PREFIX + (DONE if ok else FAIL))
    print("\n".join(lines))
    return 0 if ok else 1


def _run(a):
    return {
        "cleanup": lambda: cleanup(a.dir, a.stage),
        "push": lambda: push(a.dir),
        "digest": lambda: digest(a.dir, a.stage),
        "webskip": lambda: (True, [webskip.line(project(a.dir), a.ids)]),
    }[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())
