"""Acceptance step: run the `- cmd:` lines of the Plan.md `## Acceptance checklist` from a fresh worktree of the stage branch (plus a worktree of each nested repo that has that branch), no model. Prebuilt artifacts named by pipeline.json `seed_build` are copied in, like for a job worktree (a fresh checkout has no target/). pipeline.json `acceptance_prep` (list of commands, e.g. a build) runs first in the worktree, listed like any command; a failing prep fails the step.

The worktree is removed in a finally; the stage branch and the main checkout stay untouched. `- eyes:` lines are not run: they are copied
into acceptance.md as MANUAL and listed in the output for the lead."""

import os
import re
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
    ACCEPTANCE_REVIEW_AGENT,
    ACCEPTANCE_REVIEW_FILE,
    ACCEPTANCE_TAIL_LINES,
    ACCEPTANCE_TMP_PREFIX,
    DONE,
    ENV_SCRIPT_TIMEOUT,
    FAIL,
    GIT_LONG_TIMEOUT_S,
    PLAN_FILE,
    REPORTS_DIR,
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


PASS, UNKNOWN = "PASS", "UNKNOWN"  # verdicts of a MANUAL item (FAIL is imported from configuration)
_MANUAL_VERDICT_RE = re.compile(r"^\s*(?:[-*]\s*)?MANUAL (\d+):\s*(PASS|FAIL|UNKNOWN)\b", re.MULTILINE)
_QRUN_LOG_RE = re.compile(r"^(?:FAIL|OK|SKIPPED)[^\n(]*\(log: ([^)\n]+)\)", re.MULTILINE)  # qrun summary line naming its full log


def manual_review_brief(manual, branch, base, docs):
    """-> reviewer brief for the `eyes:` texts MANUAL: check each against the diff `base...branch` and the DOCS, one `MANUAL n: PASS|FAIL|UNKNOWN` line per item."""
    refs = ", ".join(f"`{d}`" for d in docs) or "none listed"
    items = "\n".join(f"{n}. {text}" for n, text in enumerate(manual, 1))
    return (
        f"Check each manual item below against the code. Read the diff: `git diff {base}...{branch}`. Read these docs: {refs}. "
        "You cannot run the app: judge from the code, with file:line evidence.\n"
        "Answer with exactly one line per item, in this form and no other: `MANUAL n: PASS|FAIL|UNKNOWN - <evidence>`. "
        "PASS = the code shows the item holds; FAIL = it does not; UNKNOWN = you cannot tell. A missing line counts as UNKNOWN and blocks.\n"
        f"Items:\n{items}\n"
    )


def manual_verdicts(reply, n):
    """-> the n verdicts of the reviewer REPLY (`MANUAL i: PASS|FAIL|UNKNOWN`, first line per item wins); an item with no line is UNKNOWN."""
    found: dict[int, str] = {}
    for m in _MANUAL_VERDICT_RE.finditer(reply):
        found.setdefault(int(m.group(1)), m.group(2))
    return [found.get(i, UNKNOWN) for i in range(1, n + 1)]


def claude_reviewer(workspace, stage):
    """-> reviewer(brief) -> reply text: runs the reviewer agent in WORKSPACE; it writes its verdict lines to .work/<stage>/manual-review.md, which is the reply."""
    from . import claude

    path = os.path.join(workspace, WORK_DIR, stage, ACCEPTANCE_REVIEW_FILE)

    def reviewer(brief):
        write_text(path, "")
        prompt = f"{brief}\nWrite the answer lines to `{os.path.relpath(path, workspace)}`."
        claude.session(claude.claude_args(ACCEPTANCE_REVIEW_AGENT, None, None, prompt, workspace), workspace, None)
        return read_text(path)

    return reviewer


def _line(status, rc, cmd, text):
    tail = " | ".join(x.strip() for x in text.splitlines()[-ACCEPTANCE_TAIL_LINES:] if x.strip())
    return f"- {status} exit {rc}: `{cmd}`" + (f" -> {tail}" if tail else "")


def _keep_failed_log(out, dest, n):
    """Copy the qrun log that the failed command's output OUT names into DEST (the stage reports folder) as failure N, before the temp checkout is dropped. No log named: DEST gets OUT itself."""
    os.makedirs(dest, exist_ok=True)
    m = _QRUN_LOG_RE.search(out)
    if m and os.path.isfile(m.group(1)):
        shutil.copyfile(m.group(1), os.path.join(dest, f"acceptance-{n}-{os.path.basename(m.group(1))}"))
    else:
        write_text(os.path.join(dest, f"acceptance-{n}.log"), out + "\n")


def run_items(items, cwd, runner, timeout, keep=None):
    """-> (lines, failed count) for the checklist ITEMS [(tag, text)], run in CWD with RUNNER(cmd, cwd, timeout) -> (rc, text).
    KEEP(n, text), when given, gets the output of each failed command (n = 1, 2, ...) before the caller drops CWD."""
    lines, bad = [], 0
    for tag, text in items:
        if tag == ACCEPTANCE_EYES_TAG:
            lines.append(f"- {ACCEPTANCE_MANUAL}: {text}")
        elif tag == ACCEPTANCE_CMD_TAG:
            rc, out = runner(text, cwd, timeout)
            bad += rc != 0
            if rc and keep:
                keep(bad, out)
            lines.append(_line(FAIL if rc else "PASS", rc, text, out))
    return lines, bad


def _add_worktree(repo, branch):
    path = tempfile.mkdtemp(prefix=ACCEPTANCE_TMP_PREFIX)
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


def _populate(repo, branch, wt, cfg):
    """-> nested worktrees added under WT: the seed build, the nested repos and the private links of the checkout. Same steps as before the split."""
    _seed_build(repo, wt, cfg.get("seed_build") or [])
    names = nested_repos(repo)
    nested = _add_nested(repo, branch, wt, names)
    link_private(repo, wt, cfg.get("nested_repo_dir_prefix") or "", {os.path.relpath(dest, wt) for _sub, dest in nested}, names)
    return nested


def _apply_review(lines, items, branch, cfg, reviewer):
    """-> (lines, not_pass): with a REVIEWER and MANUAL items, the reviewer verdicts replace the MANUAL lines; not_pass counts the items not PASS."""
    manual_items = [text for tag, text in items if tag == ACCEPTANCE_EYES_TAG]
    if not (reviewer and manual_items):
        return lines, 0
    reply = reviewer(manual_review_brief(manual_items, branch, cfg["branch_release"], cfg.get("manual_review_docs") or []))
    verdicts = manual_verdicts(reply, len(manual_items))
    review = iter(f"- {ACCEPTANCE_MANUAL} {i}: {v}: {text}" for i, (v, text) in enumerate(zip(verdicts, manual_items, strict=True), 1))
    lines = [next(review) if ln.startswith(f"- {ACCEPTANCE_MANUAL}:") else ln for ln in lines]
    return lines, sum(v != PASS for v in verdicts)


def run(workspace, stage, name, runner=qrun_runner, reviewer=None):
    """-> (verdict, text). Writes .work/<stage>/acceptance.md; DONE when every cmd passed (no cmd = DONE), else FAIL.
    REVIEWER(brief) -> reply text dispatches the reviewer for the MANUAL items: any item not PASS (UNKNOWN when its line is missing) makes the step FAIL. No reviewer: MANUAL items stay for the lead."""
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
        nested = _populate(repo, branch, wt, cfg)
        prep = [(ACCEPTANCE_CMD_TAG, c) for c in cfg.get("acceptance_prep") or []]
        reports = os.path.join(workspace, WORK_DIR, stage, REPORTS_DIR)
        lines, bad = run_items(prep + items, wt, runner, ACCEPTANCE_CMD_TIMEOUT_S, keep=lambda n, out: _keep_failed_log(out, reports, n))
    finally:
        _drop_worktree(repo, wt, nested)
    lines, not_pass = _apply_review(lines, items, branch, cfg, reviewer)
    verdict = FAIL if bad or not_pass else DONE
    body = "\n".join(lines)
    write_text(os.path.join(workspace, WORK_DIR, stage, ACCEPTANCE_FILE), f"# Acceptance of `{branch}`: {verdict}\n\n{body}\n")
    manual = [ln for ln in lines if ln.startswith(f"- {ACCEPTANCE_MANUAL}")]
    if bad:
        head = f"{bad} of {len(items) - len(manual)} commands failed"
    elif not_pass:
        head = f"{not_pass} of {len(manual)} {ACCEPTANCE_MANUAL} item(s) not PASS"
    else:
        head = "all commands passed"
    return verdict, f"{head}; {len(manual)} {ACCEPTANCE_MANUAL} item(s) for the lead\n{body}"
