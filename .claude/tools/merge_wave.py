#!/usr/bin/env python3
"""merge_wave: merge a wave's job branches into the work branch by script, no model.

    python3 -I /root/.claude/tools/merge_wave.py --dir WORKSPACE --stage S --tasks task-1,task-2

Per task: `wt.merge` (squash, one commit per repo, nested repos too). Then the project's `gates` and `each` checks on the main checkout.
Only when all are green are the tasks' worktrees and branches removed. Safe to re-run: merged tasks are skipped.
First output line: `VERDICT: DONE` (merged, green, removed), `VERDICT: FAIL` (a conflict, or red tests: the lead hands it to a dev) or
`VERDICT: BLOCKED` (the merge cannot start: dirty or wrong-branch main checkout, uncommitted job work)."""

import argparse
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I drops the script dir
import wt
from audit import final_check
from pipeline import gate_record
from pipeline.configuration import (
    BLOCKED,
    DONE,
    FAIL,
    GATE_KIND_MERGE,
    MERGE_CONFLICT_MARK,
    MERGE_RED_MARK,
    METRICS_LOG,
    WORK_DIR,
    all_gates_of,
    project,
    repo_dir,
    test_env,
)
from pipeline.util import add_common_args


def _drop_merged(a_dir, stage, tasks):
    """After a red gate: remove the worktree and branch of every task whose content is already in the stage branch. -> report lines. A refused removal is only noted."""
    out = []
    for t in tasks:
        if not wt.is_merged(a_dir, stage, t):
            continue
        try:
            wt.remove(a_dir, stage, t)
            out.append(f"{t}: removed (its content is in the stage branch)")
        except RuntimeError as e:
            out.append(f"{t}: not removed: {e}")
    return out


def _passed_gates(repo, cmds):
    """CMDS whose green run (a qrun record) is on the merged tree of REPO -> {cmd: record}. The base (stage-branch HEAD) is not compared: a squash merge makes a new HEAD, the tree is what was tested. No tree (not a repo): no skip. A command that does not parse has no record."""
    tree = gate_record.tree_hash(repo)
    hits: dict[str, dict] = {}
    if not tree:
        return hits
    for c in cmds:
        try:
            argv = shlex.split(c)
        except ValueError:
            continue
        rec = gate_record.find_passed(repo, argv, tree=tree)
        if rec:
            hits[c] = rec
    return hits


def _log_skipped(a_dir, stage, tasks, skipped, gates):
    """A skipped gate command still gets its gate_cmd metrics record (rc 0, seconds 0)."""
    final_check.log_gate_cmds(os.path.join(a_dir, WORK_DIR, stage, METRICS_LOG), GATE_KIND_MERGE, [(c, 0, 0, "") for c in skipped], gates, ",".join(tasks))


def _gate_results(a_dir, stage, tasks, cfg, repo, cmds, gates, runner, lines):
    """Run the merge gate commands CMDS (`gates`, `gates_merge`, `each`) in REPO. -> the results, with a command accepted by `gate_warn_ok` turned green; LINES gets the notes."""
    kw = {"runner": runner} if runner else {}
    results = final_check.run_all(cmds, repo, {**os.environ, **test_env(cfg, a_dir)}, groups=final_check.gate_groups(cfg, repo, merge=True), **kw)
    final_check.log_gate_cmds(os.path.join(a_dir, WORK_DIR, stage, METRICS_LOG), GATE_KIND_MERGE, results, gates, ",".join(tasks))
    rules, notes = final_check.warn_ok_rules(cfg, stage)
    results, accepted = final_check.accept_warnings(results, [g["name"] for g in all_gates_of(cfg)], rules)
    lines += notes
    for g, tail in accepted:
        lines.append(f"gate {g}: red, accepted by pipeline.json `gate_warn_ok` for this stage\n" + "\n".join("  " + x for x in tail.splitlines()))
    return results


def run(a_dir, stage, tasks, runner=None):
    """-> (verdict, report text)."""
    lines: list[str] = []
    refreshed, stale = wt.refresh_stale(a_dir, stage, tasks)
    lines += [f"{t}: job branch rebased onto the stage branch (its base was behind)" for t in refreshed]
    lines += [f"{t}: job branch is behind the stage branch and the rebase conflicts: {why}" for t, why in stale.items()]
    for t in tasks:
        try:
            res = wt.merge(a_dir, stage, t)
        except wt.Conflict as e:
            return FAIL, f"{t}: {MERGE_CONFLICT_MARK}, main checkout reset to its last commit. {e}\n" + "\n".join(lines)
        except wt.Blocked as e:
            return BLOCKED, f"{t}: {e}\n" + "\n".join(lines)
        lines.append(f"{t}: {res}")
    cfg = project(a_dir)
    repo = repo_dir(a_dir, cfg)
    cmds = final_check.commands(cfg, repo, merge=True)  # the merge gate also runs `gates_merge`
    if cmds:
        gates = final_check.gate_names(cfg, merge=True)
        passed = _passed_gates(repo, cmds)  # the merged tree was green on this base: no second run
        for c, rec in passed.items():
            lines.append(f"gate {gates.get(c, c)}: skipped, identical passed tree {rec['tree']}, run {rec['run']}")
        _log_skipped(a_dir, stage, tasks, passed, gates)
        results = _gate_results(a_dir, stage, tasks, cfg, repo, [c for c in cmds if c not in passed], gates, runner, lines)
        if any(r[1] for r in results):
            lines += _drop_merged(
                a_dir, stage, tasks
            )  # the work is in the stage branch: a fix goes there, a leftover worktree would only block the driver as "unmerged"
            return FAIL, f"{MERGE_RED_MARK} (merged job branches removed)\n" + "\n".join(lines) + "\n" + final_check.report(results, gates)
    else:
        lines.append("no `gates` in pipeline.json: nothing was run")
    for t in tasks:
        try:
            wt.remove(a_dir, stage, t)
        except RuntimeError as e:
            return BLOCKED, f"{t}: {e}\n" + "\n".join(lines)
        lines.append(f"{t}: removed")
    return DONE, "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    add_common_args(ap, stage=True)
    ap.add_argument("--tasks", required=True)
    a = ap.parse_args(argv)
    v, text = run(a.dir, a.stage, [t for t in a.tasks.split(",") if t])
    print(f"VERDICT: {v}\n{text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
