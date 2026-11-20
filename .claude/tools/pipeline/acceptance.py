"""Acceptance step: run the `- cmd:` lines of the Plan.md `## Acceptance checklist` from a fresh worktree of the stage branch (plus a worktree of each nested repo that has that branch), no model. Prebuilt artifacts named by pipeline.json `seed_build` are copied in, like for a job worktree (a fresh checkout has no target/). pipeline.json `acceptance_prep` (list of commands, e.g. a build) runs first in the worktree, listed like any command; a failing prep fails the step.

The worktree is removed in a finally; the stage branch and the main checkout stay untouched. `- eyes:` lines are not run: they are copied
into acceptance.md as MANUAL and listed in the output for the lead."""

import os
import shutil
import subprocess
import sys
import tempfile

from . import gitx
from .configuration import (
    ACCEPTANCE_CMD_TAG,
    ACCEPTANCE_CMD_TIMEOUT_S,
    ACCEPTANCE_EYES_TAG,
    ACCEPTANCE_FILE,
    ACCEPTANCE_MANUAL,
    ACCEPTANCE_TAIL_LINES,
    DONE,
    ENV_SCRIPT_TIMEOUT,
    FAIL,
    GIT_LONG_TIMEOUT_S,
    PLAN_FILE,
    TOOLS_DIR,
    WORK_DIR,
    project,
    repo_dir,
    work_branch,
)
from .plan_files import acceptance_items
from .util import read_text, write_text
from .worktree import _seed_build, link_private, nested_repos

QRUN = os.path.join(TOOLS_DIR, "qrun.py")
EXTRA_S = 60  # backstop over the per-command limit that qrun enforces itself


def qrun_runner(cmd, cwd, timeout):
    """-> (exit code, output) of one shell command run through qrun in CWD."""
    env = {**os.environ, ENV_SCRIPT_TIMEOUT: str(timeout)}
    try:
        r = subprocess.run(
            [sys.executable, "-I", QRUN, "--", "sh", "-c", cmd], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout + EXTRA_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, f"cannot run: {e}"
    return r.returncode, (r.stdout + r.stderr).strip()


def _line(status, rc, cmd, text):
    tail = " | ".join(x.strip() for x in text.splitlines()[-ACCEPTANCE_TAIL_LINES:] if x.strip())
    return f"- {status} exit {rc}: `{cmd}`" + (f" -> {tail}" if tail else "")


def run_items(items, cwd, runner, timeout):
    """-> (lines, failed count) for the checklist ITEMS [(tag, text)], run in CWD with RUNNER(cmd, cwd, timeout) -> (rc, text)."""
    lines, bad = [], 0
    for tag, text in items:
        if tag == ACCEPTANCE_EYES_TAG:
            lines.append(f"- {ACCEPTANCE_MANUAL}: {text}")
        elif tag == ACCEPTANCE_CMD_TAG:
            rc, out = runner(text, cwd, timeout)
            bad += rc != 0
            lines.append(_line(FAIL if rc else "PASS", rc, text, out))
    return lines, bad


def _add_worktree(repo, branch):
    path = tempfile.mkdtemp(prefix="acceptance-")
    try:
        gitx.out(repo, "worktree", "add", "--detach", path, branch, timeout=GIT_LONG_TIMEOUT_S)
    except RuntimeError:
        shutil.rmtree(path, ignore_errors=True)
        raise
    return path


def _add_nested(repo, branch, path, nested):
    """Worktree of each nested repo (a private course, ignored by the outer repo) that has `branch`, at the same relative path.
    NESTED = nested_repos(repo). -> list of (nested repo, worktree path). A nested repo without the branch is left absent, as in a clean checkout."""
    added = []
    for rel in nested:
        sub = os.path.join(repo, rel)
        if not gitx.try_out(sub, "rev-parse", "--verify", "--quiet", branch):
            continue
        dest = os.path.join(path, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        gitx.out(sub, "worktree", "add", "--detach", dest, branch, timeout=GIT_LONG_TIMEOUT_S)
        added.append((sub, dest))
    return added


def _drop_worktree(repo, path, nested=()):
    for sub, dest in nested:
        gitx.try_out(sub, "worktree", "remove", "--force", dest, timeout=GIT_LONG_TIMEOUT_S)
        gitx.try_out(sub, "worktree", "prune")
    gitx.try_out(repo, "worktree", "remove", "--force", path, timeout=GIT_LONG_TIMEOUT_S)
    shutil.rmtree(path, ignore_errors=True)
    gitx.try_out(repo, "worktree", "prune")


def run(workspace, stage, name, runner=qrun_runner):
    """-> (verdict, text). Writes .work/<stage>/acceptance.md; DONE when every cmd passed (no cmd = DONE), else FAIL."""
    cfg = project(workspace)
    repo, branch = repo_dir(workspace, cfg), work_branch(cfg, name)
    plan = os.path.join(workspace, WORK_DIR, str(stage).split("/phases/")[0], PLAN_FILE)
    items = [i for i in acceptance_items(read_text(plan)) if i[0]]
    try:
        wt = _add_worktree(repo, branch)
    except RuntimeError as e:
        return FAIL, f"cannot create a worktree of `{branch}`: {e}"
    nested = []
    try:
        _seed_build(repo, wt, cfg.get("seed_build") or [])
        names = nested_repos(repo)
        nested = _add_nested(repo, branch, wt, names)
        link_private(repo, wt, cfg.get("nested_repo_dir_prefix") or "", {os.path.relpath(dest, wt) for _sub, dest in nested}, names)
        prep = [(ACCEPTANCE_CMD_TAG, c) for c in cfg.get("acceptance_prep") or []]
        lines, bad = run_items(prep + items, wt, runner, ACCEPTANCE_CMD_TIMEOUT_S)
    finally:
        _drop_worktree(repo, wt, nested)
    verdict = FAIL if bad else DONE
    body = "\n".join(lines)
    write_text(os.path.join(workspace, WORK_DIR, stage, ACCEPTANCE_FILE), f"# Acceptance of `{branch}`: {verdict}\n\n{body}\n")
    manual = [ln for ln in lines if ln.startswith(f"- {ACCEPTANCE_MANUAL}")]
    head = f"{bad} of {len(items) - len(manual)} commands failed" if bad else "all commands passed"
    return verdict, f"{head}; {len(manual)} {ACCEPTANCE_MANUAL} item(s) for the lead\n{body}"
