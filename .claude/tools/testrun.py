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
import wt  # noqa: E402  (sys.path set above)
from localmodel.prereview import job_diff  # noqa: E402  (sys.path set above)
from pipeline import cite_check, gate_paths, gate_record, gate_secrets, gitx, linkguard, maincheck, worktree  # noqa: E402  (sys.path set above)
from pipeline.configuration import (  # noqa: E402  (sys.path set above)
    ADD_REPO_CMD,
    BLOCKED,
    CITE_CHECK_DEFAULT,
    CITE_CHECK_KEY,
    CITE_DIFF_MAX_CHARS,
    DONE,
    FAIL,
    GATE_TIMEOUT_S,
    GIT_TIMEOUT_S,
    WORK_DIR,
    project,
    repo_dir,
    tool_cmd,
    work_branch,
)
from pipeline.plan_check import LEAD_MARK, is_lead_verified  # noqa: E402  (sys.path set above)
from pipeline.plan_files import task_type  # noqa: E402  (sys.path set above)
from pipeline.util import read_text, work_path  # noqa: E402  (sys.path set above)

TIMEOUT_S = GATE_TIMEOUT_S
TAIL_LINES = 40
BLOCKED_EXIT = (126, 127)  # not executable / command not found
SCAN_NOTE_PREFIXES = ("secret scan error:", "gitleaks exit ")  # gate_secrets.scan_diff reports its own failures as lines of these forms: notes, never findings


def _test_cmds(text, lead):
    """The commands of the `Test:` lines of a task brief, backticks stripped: the lead-verified ones when `lead` is True, else the ones the dev runs."""
    return [c for c in (m[1].strip().strip("`").strip() for m in re.finditer(r"(?m)^Test:\s*(.+)$", text) if is_lead_verified(m[0]) == lead) if c]


def test_lines(text):
    """The commands the dev runs: the `Test:` lines not marked lead-verified."""
    return _test_cmds(text, lead=False)


def lead_lines(text):
    """The commands of the lead-verified `Test:` lines, marker and its `#` comment removed: the driver runs them after the dev reports DONE."""
    return [re.sub(rf"(\s+#)?\s+{LEAD_MARK}\s*$", "", c).strip() for c in _test_cmds(text, lead=True)]


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


def outside_verdict(note):
    """-> (BLOCKED, note) when NOTE is main-checkout dirt (maincheck: the lead clears it, the dev is not at fault), else (FAIL, note) for a dev's own write (linkguard). Callers pass the outside_writes note."""
    return (BLOCKED if maincheck.dirt(note) else FAIL), note


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


def _run_lead(cwd, cmds, runner):
    """Run the lead-verified Test lines in cwd, every time (no green-tree skip). -> FAIL note lines for the red ones; the dev verdict is not changed."""
    notes = []
    for c in cmds:
        try:
            p = runner(shlex.split(c), cwd=cwd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S)
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            notes.append(f"FAIL lead-verified `{c}` could not run: {e}")
            continue
        if p.returncode != 0:
            full = (p.stdout + p.stderr).strip()
            out = "\n".join(full.splitlines()[-TAIL_LINES:])
            notes.append(f"FAIL lead-verified `{c}` exited {p.returncode}:\n{out}")
    return notes


def gate_findings(cwd, d, stage):
    """Secret scan and path-dependent asserts of the task diff in cwd, against the job base (merge-base of HEAD and the stage work branch, else HEAD~1) -> finding lines and scanner note lines."""
    base = (gitx.try_out(cwd, "merge-base", "HEAD", work_branch(project(d), stage)) or "").strip() or "HEAD~1"
    return gate_secrets.scan_diff(cwd, base, project(d)) + gate_paths.path_dependent_tests(cwd, base)


def _gate(cwd, d, stage, scan):
    """-> (finding lines, note lines) of the secret and path gates. A scanner that cannot run is a note: it never blocks the run."""
    try:
        lines = scan(cwd, d, stage)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return [], [f"gate scan could not run: {e}"]
    notes = [x for x in lines if x.startswith(SCAN_NOTE_PREFIXES)]
    return [x for x in lines if not x.startswith(SCAN_NOTE_PREFIXES)], notes


def _red_verdict(cwd, c, p):
    """-> (BLOCKED or FAIL, note) of a Test line C that exited non-zero in process P: a missing module or command is BLOCKED, the dev's own bad import or a red test is FAIL."""
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


def _run_line(cwd, c, runner):
    """-> (BLOCKED or FAIL, note) when the Test line C could not run or exited non-zero, else None."""
    try:
        p = runner(shlex.split(c), cwd=cwd, capture_output=True, text=True, errors="replace", timeout=TIMEOUT_S)
    except (OSError, ValueError, subprocess.TimeoutExpired) as e:
        return BLOCKED, f"`{c}` could not run: {e}"
    return _red_verdict(cwd, c, p) if p.returncode != 0 else None


def _cite_findings(cwd, d, stage, task, report, diff_fn):
    """-> (FAIL note, skip note). The FAIL note names each `path:line` cite the task adds (committed diff) or the dev report makes that does not hold, else "". The skip note says the check did not run because the job diff was unavailable, else "". Off when the `cite_check` key of pipeline.json is false; no job diff -> nothing to check."""
    if not project(d).get(CITE_CHECK_KEY, CITE_CHECK_DEFAULT):
        return "", ""
    diff = diff_fn(stage, task, d, CITE_DIFF_MAX_CHARS)
    if diff is None:  # job_diff: no manifest entry or the diff failed; never silently off
        return "", "cite check skipped: the job diff is unavailable"
    if not diff:
        return "", ""
    added = cite_check.added_lines(diff)
    msgs = cite_check.check(cwd, added, read_text(report) if report and os.path.isfile(report) else "", split=cite_check.split_files(diff))
    if not msgs:
        return "", ""
    bare = ["cite the full repo-relative path, not a bare file name"] if cite_check.bare_names(msgs) else []
    return "\n".join(["FAIL cite check: a path:line cite does not hold:", *msgs, *bare]), ""


def run(stage, task, d, runner=subprocess.run, scan=gate_findings, diff_fn=job_diff, report=""):
    """-> (verdict word, note). `runner`, `scan` and `diff_fn` are injectable for tests; `report` is the dev report file. A Test line already green on the identical tree is skipped, noted with its run id. A finding of the secret or path gate is FAIL before any Test line runs. Green Test lines then run the cite check (`_cite_findings`): a wrong cite is FAIL."""
    try:
        text = Path(d, WORK_DIR, stage, f"{task}.md").read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return BLOCKED, f"task brief unreadable, Test lines not run: {e}"
    cmds, leads = test_lines(text), lead_lines(text)
    outside = outside_writes(d, stage, task)
    if outside:
        return outside_verdict(outside)
    if not cmds and not leads:
        return DONE, "test run skipped: the task has no Test line"
    cwd = workdir(d, stage, task)
    if cwd is None:
        return BLOCKED, "no job worktree for this task: the Test lines would run on the main checkout, so they were not run"
    findings, gate_notes = _gate(cwd, d, stage, scan)
    if findings:
        return FAIL, "\n".join(["secret or path gate: findings in the task diff", *findings])
    tree = _tree(cwd)  # taken once, before any line runs: each line is keyed on the tree the dev left
    skipped = []
    for c in cmds:
        hit = _passed(cwd, c, tree) if tree else None
        if hit:
            skipped.append(f"skipped {c}: {gate_record.skip_note(cwd, hit)}")
            continue
        red = _run_line(cwd, c, runner)
        if red:
            return red
    lead_notes = _run_lead(cwd, leads, runner)  # a red lead-verified line is a note, never a BLOCKED or FAIL of the dev
    cite, cite_note = _cite_findings(cwd, d, stage, task, report, diff_fn)
    if cite:
        return FAIL, cite
    return DONE, "\n".join([f"{len(cmds)} Test line(s) green in {cwd}", *gate_notes, *skipped, *lead_notes, *([cite_note] if cite_note else [])])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--dir", default=".")
    ap.add_argument("--report", default="", help="the dev report file: its path:line cites go through the cite check")
    ap.add_argument("--repair-env", action="store_true", help="rerun the worktree_setup commands in the job worktree instead of the Test lines")
    a = ap.parse_args(argv)
    if a.repair_env:  # an environment repair: the step that asked for it runs its test again next
        try:
            worktree.repair_env(os.path.abspath(a.dir), a.stage, a.task)
        except RuntimeError as ex:
            print(f"repair failed: {ex}")
            return 0  # the rerun of the red step shows the state; a second env red goes to the lead
        print("repair done: worktree_setup reran")
        return 0
    v, note = run(a.stage, a.task, os.path.abspath(a.dir), report=a.report)
    print(f"VERDICT: {v}\n{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
