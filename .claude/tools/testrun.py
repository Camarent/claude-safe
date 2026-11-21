#!/usr/bin/env python3
"""testrun: script step after a dev reports DONE. Runs the task's `Test:` lines in the task's job worktree.

    python3 -I /root/.claude/tools/testrun.py --stage S --task task-N [--dir D]

Prints `VERDICT: DONE` (all green, or no Test line), `VERDICT: FAIL` plus the last lines of the failing command's output (the dev reworks it), or `VERDICT: BLOCKED` when the Test lines cannot run at all (no job worktree, runner error, exit 126/127, missing command or module: the lead decides).
Commands run without a shell, one per `Test:` line, in the first job worktree root (else in --dir); a line that needs a shell is brieflint's problem."""

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path[:0] = [os.path.dirname(os.path.abspath(__file__))]  # -I drops the script dir
import wt  # noqa: E402
from pipeline import gate_record, gitx, linkguard, maincheck  # noqa: E402
from pipeline.configuration import ADD_REPO_CMD, BLOCKED, DONE, FAIL, GIT_TIMEOUT_S, WORK_DIR, project, repo_dir, tool_cmd  # noqa: E402
from pipeline.plan_files import task_type  # noqa: E402
from pipeline.util import read_text, work_path  # noqa: E402

TIMEOUT_S = 900
TAIL_LINES = 40
BLOCKED_EXIT = (126, 127)  # not executable / command not found


def test_lines(text):
    """The commands of the `Test:` lines of a task brief, backticks stripped."""
    return [m[1].strip().strip("`").strip() for m in re.finditer(r"(?m)^Test:\s*(.+)$", text) if m[1].strip().strip("`").strip()]


def workdir(d, stage, task):
    """Root of the task's job worktree; the repo folder (D when `repo` is unset) only when worktrees are off. None when they are on and the task has no worktree (never test the main checkout, except for a `Type: webtest` task)."""
    info = wt.live_job(d, stage, task)
    if info:
        return info["root"]
    cfg = project(d)
    if cfg.get("worktrees") and not _webtest(d, stage, task):
        return None
    return repo_dir(d, cfg)


def _webtest(d, stage, task):
    """True for a `Type: webtest` task: the web-tester made no change, so its Test lines (a check of the report file) run on the stage branch checkout."""
    return task_type(read_text(work_path(os.path.join(d, WORK_DIR, stage), task + ".md"))) == "webtest"


def _imported_by_changes(cwd, top):
    """True when a changed or new .py file under cwd has `import top` / `from top`. Git status lists the changes; no git -> False."""
    try:
        st = gitx.run(cwd, "status", "--porcelain", "-z", "-uall", timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False
    pat = re.compile(rf"^\s*(import|from)\s+{re.escape(top)}\b", re.M)
    for ln in st.stdout.split("\0"):  # -z: raw names, NUL-separated (a rename lists its old name as its own entry, harmless here)
        path = os.path.join(cwd, ln[3:])
        if path.endswith(".py") and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    if pat.search(f.read()):
                        return True
            except OSError:
                continue
    return False


def outside_writes(d, stage, task):
    """-> "" or the FAIL note for a dev that wrote to the main checkout (maincheck.check: named, never deleted) or through a link to a nested repo its task does not list (linkguard.check: tracked changes are reverted, a patch is kept)."""
    if not wt.live_job(d, stage, task):
        return ""
    notes = [maincheck.check(d, stage, task), linkguard.check(d, stage, task, ADD_REPO_CMD.format(tool=tool_cmd("wt.py"), stage=stage, task=task, dir=d))]
    return "\n".join(n for n in notes if n)


def _tree(cwd):
    """Tree hash of cwd for the gate-run record, or None: a record error never blocks the run."""
    try:
        return gate_record.tree_hash(cwd)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


def _passed(cwd, c, tree):
    """The green record of Test line c on this tree (skip it), or None: a record error runs the line as before."""
    try:
        return gate_record.find_passed(cwd, shlex.split(c), tree)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


def run(stage, task, d, runner=subprocess.run):
    """-> (verdict word, note). `runner` is injectable for tests. A Test line already green on the identical tree is skipped, noted with its run id."""
    try:
        cmds = test_lines(Path(d, WORK_DIR, stage, f"{task}.md").read_text(encoding="utf-8", errors="replace"))
    except OSError as e:
        return BLOCKED, f"task brief unreadable, Test lines not run: {e}"
    outside = outside_writes(d, stage, task)
    if outside:
        return FAIL, outside
    if not cmds:
        return DONE, "test run skipped: the task has no Test line"
    cwd = workdir(d, stage, task)
    if cwd is None:
        return BLOCKED, "no job worktree for this task: the Test lines would run on the main checkout, so they were not run"
    tree = _tree(cwd)  # taken once, before any line runs: each line is keyed on the tree the dev left
    skipped = []
    for c in cmds:
        hit = _passed(cwd, c, tree) if tree else None
        if hit:
            skipped.append(f"skipped {c}: identical passed tree, run {hit['run']}")
            continue
        try:
            p = runner(shlex.split(c), cwd=cwd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            return BLOCKED, f"`{c}` could not run: {e}"
        if p.returncode != 0:
            full = (p.stdout + p.stderr).strip()
            out = "\n".join(full.splitlines()[-TAIL_LINES:])
            missing = re.search(r"No module named '?([\w.]+)", full)
            if missing and not _imported_by_changes(cwd, missing.group(1).split(".")[0]):
                return BLOCKED, f"`{c}` cannot run (module {missing.group(1)} not installed):\n{out}"
            if missing:  # the dev's own bad import: a red test
                return FAIL, f"`{c}` exited {p.returncode}: the changed code imports {missing.group(1)}:\n{out}"
            if p.returncode in BLOCKED_EXIT or "command not found" in full:
                return BLOCKED, f"`{c}` cannot run (exit {p.returncode}; missing command):\n{out}"
            return FAIL, f"`{c}` exited {p.returncode}:\n{out}"
    return DONE, "\n".join([f"{len(cmds)} Test line(s) green in {cwd}", *skipped])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    a = ap.parse_args(argv)
    v, note = run(a.stage, a.task, os.path.abspath(a.dir))
    print(f"VERDICT: {v}\n{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
