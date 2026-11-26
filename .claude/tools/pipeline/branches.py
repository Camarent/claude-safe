"""The main checkout's work branch: the dirty guard and the switch to the stage or feature branch."""

import json
import os
import subprocess
import sys

from . import gitx, worktree
from .configuration import (
    MISSING_BINARIES_KEY,
    MISSING_BINARY_MARK,
    ORCH_LOG,
    PREFLIGHT_DETAIL_LINES,
    PREFLIGHT_KEY,
    PREFLIGHT_MARKER,
    PREFLIGHT_MSG_MAX,
    RED_BASE_LAST_LINE,
    RED_BASE_MISSING_LINE,
    WORK_DIR,
    project,
    repo_dir,
    test_env,
)


def dirty_block(a) -> str:
    """-> the 'blocked: ...' line when the main checkout holds uncommitted work and --allow-dirty is not given, else ''. Dry runs never block."""
    if getattr(a, "dry_run", False) or getattr(a, "allow_dirty", False):
        return ""
    return worktree.dirty_report(a.dir, getattr(a, "stage", None))


def _read_marker(path):
    """-> the sha recorded in the preflight marker at PATH, '' when missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return str(json.load(f).get("head", ""))
    except (OSError, ValueError, AttributeError):
        return ""


def red_base_block(a) -> str:
    """-> the 'blocked: base is red: ...' text when a fresh start's base fails its gates, else ''. No override: the base is fixed first.
    Skipped: dry runs, the setting off, no gates, a resume (the work branch has work), and a HEAD whose gates already ran green (marker)."""
    if getattr(a, "dry_run", False):
        return ""
    from .graph import _name

    cfg = project(a.dir)
    if cfg.get(PREFLIGHT_KEY) is False:
        return ""
    repo = repo_dir(a.dir, cfg)
    root = _name(a).split("/phases/")[0]  # a phase stage (feature/phases/NN-x) keeps its work under the feature folder
    folder = os.path.join(a.dir, WORK_DIR, root)
    if any(ORCH_LOG in files for _, _, files in os.walk(folder)):
        return ""
    head = gitx.out(repo, "rev-parse", "HEAD", check=False)
    return _run_preflight(a, cfg, repo, folder, head)


def _run_preflight(a, cfg, repo, folder, head):
    """Run the gates (and `gates_merge`) on the base unless HEAD already ran green. -> the blocked text, '' when green."""
    from audit import final_check  # tools folder is on sys.path (pipeline is imported from there)

    cmds = final_check.commands(cfg, repo, merge=True)  # the base must pass what the merge gate will run (`gates_final` runs in the final test only)
    marker = os.path.join(folder, PREFLIGHT_MARKER)
    if not cmds or (head and _read_marker(marker) == head):
        return ""
    names = final_check.gate_names(cfg, merge=True)
    print(f"preflight: running {len(cmds)} gates on the base before planning (same commands as final-test)", file=sys.stderr, flush=True)
    results = final_check.run_all(cmds, repo, {**os.environ, **test_env(cfg, a.dir)}, groups=final_check.gate_groups(cfg, repo, merge=True))
    bad = [r for r in results if r[1] != 0]
    if not bad:
        if head:
            os.makedirs(folder, exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"head": head}, f)
        return ""
    return _red_base_message(bad, names, cfg)


def _red_base_message(bad, names, cfg):
    """-> the blocked text for the failed gates BAD: last lines per gate, then the missing-binaries line and the fix hint (kept within PREFLIGHT_MSG_MAX)."""
    detail = []
    for c, _rc, secs, text in bad:
        detail.append(f"gate {names.get(c, c)}: FAILED ({secs}s)")
        detail.extend("  " + line for line in text.splitlines()[-PREFLIGHT_DETAIL_LINES:])
    tail = []
    missing = _missing_binaries("\n".join(r[3] for r in bad), cfg.get(MISSING_BINARIES_KEY, []))
    if missing:
        tail.append(RED_BASE_MISSING_LINE.format(names=", ".join(missing)))
    tail.append(RED_BASE_LAST_LINE.format(gate=names.get(bad[0][0], bad[0][0])))
    tail_text = "\n".join(tail)
    head = "\n".join(detail)[: max(PREFLIGHT_MSG_MAX - len(tail_text) - 1, 0)]
    return f"{head}\n{tail_text}" if head else tail_text


def _missing_binaries(text, binaries):
    """-> the configured BINARIES named on a line of TEXT that carries MISSING_BINARY_MARK, in config order; [] when none."""
    named: set[str] = set()
    for line in text.splitlines():
        if MISSING_BINARY_MARK in line:
            named.update(w.strip(".,:;'\"()") for w in line.split())
    return [b for b in binaries if b in named]


def released(a, name) -> bool:
    """True when the stage's orch.jsonl holds a DONE `branch-cleanup` report: the stage branch is gone on purpose and must not be recreated."""
    from .util import read_jsonl

    log = os.path.join(a.dir, WORK_DIR, name, ORCH_LOG)
    return any(e.get("ev") == "report" and e.get("task") == "branch-cleanup" and e.get("verdict") == "DONE" for e in read_jsonl(log))


def ensure_work_branch(a, name) -> str:
    """Put the main checkout on the work branch of feature or stage `name`: create it from `branch_release` when missing (HEAD when that is missing too), switch to it, reuse it on resume. Nested git repos (worktree.nested_repos) get the same branch.
    -> '' when done or not applicable (dry run, no git repo), else a 'blocked: ...' line."""
    if getattr(a, "dry_run", False) or released(a, name):
        return ""
    from .configuration import project, release_branch, repo_dir, work_branch

    cfg = project(a.dir)
    repo = repo_dir(a.dir, cfg)

    want = work_branch(cfg, name)
    if not os.path.isdir(repo) or not _is_git(repo):
        return ""
    for r in [repo, *[os.path.join(repo, n) for n in worktree.nested_repos(repo)]]:
        if msg := _switch_work(r, release_branch(r, cfg), want):
            return msg if r == repo else msg.replace("blocked: ", f"blocked: nested repo {os.path.relpath(r, repo)}: ", 1)
    return ""


def _is_git(repo):
    return gitx.run(repo, "rev-parse", "--git-dir").returncode == 0


def _switch_work(repo, release, want):
    """Create `want` in one repo from `release` (HEAD when missing), switch to it. -> '' or a 'blocked: ...' line."""

    def git(*args) -> subprocess.CompletedProcess:
        return gitx.run(repo, *args)

    cur = git("symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if cur == want:
        return ""
    if git("rev-parse", "--verify", "-q", f"refs/heads/{want}").returncode:
        start = release if git("rev-parse", "--verify", "-q", f"refs/heads/{release}").returncode == 0 else "HEAD"
        r = git("branch", want, start)
        if r.returncode:
            return f"blocked: cannot create work branch {want} from {start}: {r.stderr.strip()[:200]}"
    r = git("switch", want)
    if r.returncode:
        return f"blocked: cannot switch to work branch {want}: {r.stderr.strip()[:200]}"
    return ""
