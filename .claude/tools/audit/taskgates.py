#!/usr/bin/env python3
"""taskgates: script step after a dev DONE and a green test run (always, when the project has gates or each and job worktrees). Runs the project's full gate set
(`gates` + `each`, the same commands and `parallel` groups as the merge gate) once in the task's job worktree.

    python3 -I /root/.claude/tools/audit/taskgates.py --stage S --task task-N [--dir D]

Prints `VERDICT: DONE` (all green, or the worktree content is unchanged since the last green run of this task), `VERDICT: FAIL` plus the
failing gates' output (the dev reworks it), or `VERDICT: BLOCKED` when the task has no job worktree (never run on the main checkout) or no gates exist."""

import argparse
import hashlib
import os
import sys

sys.path[:0] = [os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]  # -I drops the script dir
import testrun  # noqa: E402
import wt  # noqa: E402
from pipeline import gitx  # noqa: E402
from pipeline.configuration import (  # noqa: E402
    BASE_FLAKE_PREFIX,
    BASE_WHY_BRANCH,
    BASE_WHY_DIRTY,
    BLOCKED,
    DIFF_SHA_LEN,
    DONE,
    FAIL,
    GATE_BASE_CHECK_KEY,
    GATE_FLAKE_RERUN_KEY,
    GATE_KIND_TASK,
    METRICS_LOG,
    WORK_DIR,
    project,
    repo_dir,
    test_env,
    work_branch,
)
from pipeline.util import read_text, write_text  # noqa: E402

from audit import final_check, gatewatch  # noqa: E402


def content_sha(d, stage, task, git=gitx.try_out):
    """Hash of the task's worktree content: per repo HEAD, `git diff HEAD` and the untracked files (names and bytes). `git(repo, *args)` -> text is injectable."""
    h = hashlib.sha256()
    _cfg, _branch, parts = wt._parts(d, stage, task)
    for rel, _repo, w in parts:
        h.update(f"{rel}\0{git(w, 'rev-parse', 'HEAD')}\0{git(w, 'diff', 'HEAD')}\0".encode())
        for name in sorted((git(w, "ls-files", "--others", "--exclude-standard") or "").splitlines()):
            try:
                with open(os.path.join(w, name), "rb") as f:
                    h.update(name.encode() + b"\0" + f.read())
            except OSError:
                h.update(name.encode() + b"\0")
    return h.hexdigest()[:DIFF_SHA_LEN]


def no_change(d, stage, task, git=gitx.try_out):
    """True when every repo of the task's job worktree is still at its base commit with nothing changed: the gates would only repeat the base's result (a read-only task, its output is outside the repo). A git that cannot answer (timeout, missing, locked) is "changed": the gates run."""
    try:
        info = wt.live_job(d, stage, task)
        _cfg, _branch, parts = wt._parts(d, stage, task)
        if not info or not parts:
            return False
        for _rel, repo, w in parts:
            head, base = (git(w, "rev-parse", "HEAD") or "").strip(), (git(repo, "rev-parse", "--verify", info["base"]) or "").strip()
            if not head or head != base or git(w, "status", "--porcelain") is None or wt._dirty(w, repo):
                return False
    except (RuntimeError, OSError):
        return False
    return True


def sha_file(d, stage, task):
    return os.path.join(d, WORK_DIR, stage, f"taskgates-{task}.sha")


def _extra(warn, green, env, cwd, git, names=()):
    """The text after the gate output: the gate_watch warning, and on a red run the process environment."""
    if green:
        return warn
    hooks = (git(cwd, "config", "core.hooksPath") or "").strip()
    return "\n".join(x for x in (warn, gatewatch.environment(env, cwd, hooks, names)) if x)


def _run_gates(cfg, cmds, cwd, root, env, runner, stage):
    """-> (results, commands cleared, {red command: why it was not cleared}, {flaky command: test ids}). The gates run in the job worktree CWD; with `gate_base_check` (default on) a red command that is red on the stage base ROOT too is not this task's failure; with `gate_flake_rerun` (default on) a red command left over runs once more, unchanged, and one that is green then is a flake."""
    kw = {"runner": runner} if runner else {}
    res = final_check.run_all(cmds, cwd, env, groups=final_check.gate_groups(cfg, cwd), **kw)
    cleared, why = [], {}
    if cfg.get(GATE_BASE_CHECK_KEY, True):
        problem = _base_problem(cfg, root, stage)
        base_cmds = set(final_check.commands(cfg, root))
        if problem:
            why = final_check.uncleared_reasons(res, [], base_cmds, problem)
        else:
            res, cleared = final_check.clear_base_red(res, root, env, groups=final_check.gate_groups(cfg, root), base_cmds=base_cmds, **kw)
            why = final_check.uncleared_reasons(res, cleared, base_cmds)
    flaky = {}
    if cfg.get(GATE_FLAKE_RERUN_KEY, True):
        res, flaky = final_check.rerun_red(res, cwd, env, groups=final_check.gate_groups(cfg, cwd), **kw)
        why = {c: w for c, w in why.items() if c not in flaky}
    return res, cleared, why, flaky


def _base_problem(cfg, root, stage, git=gitx.try_out):
    """-> "" when the main checkout ROOT is a fair stage base (on the stage's work branch and clean), else what is wrong. A dirty or other-branch checkout (a merge in progress, a lead's edit) proves nothing, so no red is cleared."""
    want = work_branch(cfg, stage)
    have = (git(root, "branch", "--show-current") or "").strip()
    if have != want:
        return BASE_WHY_BRANCH.format(have=have or "no branch", want=want)
    if (git(root, "status", "--porcelain") or "").strip():
        return BASE_WHY_DIRTY
    return ""


def _skip_reason(cfg, d, stage, task, git):
    """-> (why the gates need not run, "") or ("", content hash of the worktree): no change against the base, or the same content as the last green run."""
    if not cfg.get("worktrees"):
        return "", ""
    if no_change(d, stage, task, git):
        return "the job worktree has no change against its base: gates not run (they would repeat the base's result; the merge gate runs the real code)", ""
    sha = content_sha(d, stage, task, git)
    if sha == read_text(sha_file(d, stage, task)).strip():
        return f"worktree unchanged since the last green gate run ({sha}): not run again", sha
    return "", sha


def _flake_line(c, res, names, why):
    """`base-flake: <failing test ids>` (the command when its output names none) plus WHY it is not the task's."""
    text = next((t for cmd, _rc, _s, t in res if cmd == c), "")
    ids = ", ".join(final_check.failing_ids(text)) or c
    return f"\n{BASE_FLAKE_PREFIX} {ids} (gate {names.get(c, c)}: {why})"


def _gate_text(res, names, cleared, why, root, flaky=()):
    """The gate report plus one line per command cleared against the stage base, one per flaky command, and one per red command that was not cleared (and why)."""
    return (
        final_check.report(res, names)
        + "".join(f"\ngate {names.get(c, c)}: red on the stage base too ({root}), not counted against this task: {c}" + _flake_line(c, res, names, "red on the stage base too") for c in cleared)
        + "".join(_flake_line(c, res, names, "red once, green on an unchanged rerun in the job worktree; not counted against this task") for c in flaky)
        + "".join(f"\ngate {names.get(c, c)}: not cleared against the stage base: {w}" for c, w in why.items())
    )


def run(stage, task, d, runner=None, git=gitx.try_out):
    """-> (verdict word, text). `runner` (final_check.run_group signature) and `git` are injectable for tests."""
    cfg = project(d)
    cwd = testrun.workdir(d, stage, task)
    if cwd is None:
        return BLOCKED, "no job worktree for this task: the gates would run on the main checkout, so they were not run"
    cmds = final_check.commands(cfg, cwd)
    if not cmds:
        return BLOCKED, "no `gates` in pipeline.json: nothing to run"
    outside = testrun.outside_writes(d, stage, task)  # a project without the test-run step is still guarded
    if outside:
        return FAIL, outside
    skip, sha = _skip_reason(cfg, d, stage, task, git)
    if skip:
        return DONE, skip
    env = {**os.environ, **test_env(cfg, d)}
    root, watch = repo_dir(d, cfg), cfg.get("gate_watch") or []
    before = gatewatch.snapshot(root, watch)
    res, cleared, why, flaky = _run_gates(cfg, cmds, cwd, root, env, runner, stage)
    names = final_check.gate_names(cfg)
    final_check.log_gate_cmds(os.path.join(d, WORK_DIR, stage, METRICS_LOG), GATE_KIND_TASK, res, names, task, [*cleared, *flaky])
    text = _gate_text(res, names, cleared, why, root, flaky)
    green = text.startswith("VERDICT: DONE")
    if green and sha:
        write_text(sha_file(d, stage, task), sha + "\n")
    note = text.split("\n", 1)[1]
    extra = _extra(gatewatch.warning(cwd, before, gatewatch.snapshot(root, watch), git) if watch else "", green, env, cwd, git, test_env(cfg, d))
    return (DONE if green else FAIL), "\n".join(x for x in (note, extra) if x)


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
