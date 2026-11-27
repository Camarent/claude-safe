"""Draft PR, merge, release, cleanup and local-merge of a stage branch by script. The command line is pr_steps.py."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from . import ci_clean, cleanup, gitx, pr_base, pr_mode, webskip, worktree  # noqa: F401 -- tests patch pr_ops.gitx.run
from .ci_drift import ci_commands, ignore_hint
from .configuration import (
    BLOCKED,
    CHECKS_TIMEOUT_S,
    CI_DENIED_MSG,
    CI_EMPTY_POLLS,
    CI_POLL_S,
    CI_RUNS_LIMIT,
    DONE,
    GH_CHECKS_PENDING_RC,
    GIT_LONG_TIMEOUT_S,
    GIT_NETWORK_TIMEOUT_S,
    GIT_SAFE_ENV,  # noqa: F401 -- re-exported: test_pr_ops_names checks the name is importable from pr_ops
    PUSH_ERR_HEAD,
    PUSH_ERR_TAIL,
    TASKS_FILE,
    WORK_DIR,
    all_gates_of,
    project,
    release_branch,
    repo_path,
)
from .configuration.pr_gates import ENV_OUTPUT_RE, PR_GATE_TIMEOUT_S, PR_GATES_STEP
from .pr_base import (  # noqa: F401 -- re-exported: pr_ops keeps the shared git/gh helpers under their old names
    TITLE_RE,
    Blocked,
    _git,
    _landed,
    _merge_title,
    _run,
    _view,
)
from .pr_release import (  # noqa: F401 -- re-exported: pr_ops keeps every release name
    _PIN,
    _SEMVER,
    LEVELS,
    _already_released,
    _bump,
    _ensure_tag,
    _github_release,
    _goal_version,
    _hook,
    _merged_sha,
    _plan_bump,
    _release,
    _release_level,
    _release_ref,
    _rewrite_pins,
    _uses_workspace_version,
    _ver,
    _version_bump,
)
from .run import Run
from .slow_suite import REASON
from .stagedir import stage_root
from .util import TOOLS, paths, read_jsonl


def checks_timeout(cfg) -> float:
    """Seconds a PR step waits for pending checks: pipeline.json `pr_checks_timeout`, else CHECKS_TIMEOUT_S."""
    return float(cfg.get("pr_checks_timeout") or CHECKS_TIMEOUT_S)


def _has_origin(repo):
    """A repo without `origin` has no PR: never ask gh."""
    return pr_base._git(repo, "remote", "get-url", "origin").returncode == 0


def _ends(text):
    """TEXT whole, or its first PUSH_ERR_HEAD and last PUSH_ERR_TAIL chars: the cause is often at the start (hook, auth), the verdict at the end."""
    text = re.sub(r"://[^/@\s]+@", "://***@", text)  # no credentials in a report
    if len(text) <= PUSH_ERR_HEAD + PUSH_ERR_TAIL:
        return text
    return f"{text[:PUSH_ERR_HEAD]} [...] {text[-PUSH_ERR_TAIL:]}"


def _pushed(repo, br):
    r = pr_base._git(repo, "-c", pr_mode.GH_CRED, "push", "-u", "origin", br, timeout=GIT_NETWORK_TIMEOUT_S)
    if r.returncode:
        raise Blocked(f"push of {br} failed: {_ends((r.stderr or r.stdout).strip())}")


def _pr_title(a_dir, stage, br):
    """-> PR title: the feature name for a phase stage, else the first line of TASKS.md, else the branch."""
    if "/phases/" in stage:  # one PR per feature: name it after the feature, not the phase that opened it
        return f"Stage: {stage.split('/phases/')[0]}"
    tl = os.path.join(a_dir, WORK_DIR, stage, TASKS_FILE)
    if not os.path.isfile(tl):
        return br
    with open(tl, encoding="utf-8") as f:
        first = next((x.strip() for x in f if x.strip()), "")
    return first.lstrip("# ").strip()[:120] or br


def _open_pr(a_dir, repo, cfg, stage, br):
    """-> note. Reuses an open PR of the branch; creates none while the branch has no commit beyond the release branch."""
    r = pr_base._run(["gh", "pr", "list", "--head", br, "--state", "open", "--json", "number,url"], repo)
    if r.returncode:
        raise Blocked(f"gh pr list failed: {r.stderr.strip()[-300:]}")
    try:
        found = json.loads(r.stdout or "[]")
    except ValueError:
        found = []
    if found:
        return f"PR #{found[0]['number']} reused"
    rel = release_branch(repo, cfg)
    base = (
        f"origin/{rel}" if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode == 0 else rel
    )  # the remote head, not a stale local one
    ahead = pr_base._git(repo, "rev-list", "--count", f"{base}..{br}")
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        return "no commit beyond the release branch yet: the PR opens at the next push"
    title = _pr_title(a_dir, stage, br)
    body = f"Stage {stage}. Task list: `.work/{stage}/TASKS.md`. Opened by the pipeline as a draft; it is marked ready after the last audit."
    r = pr_base._run(["gh", "pr", "create", "--draft", "--base", rel, "--head", br, "--title", title, "--body", body], repo)
    if r.returncode:
        raise Blocked(f"gh pr create failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"PR opened: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else br}"


class CiReadError(Blocked):
    """The CI runs of a PR head could not be read: never read as "no checks" (a merge must not pass on it)."""


def _ci_runs(repo, sha):
    """-> [{name, status, conclusion}] (upper case, like a status rollup) of the workflow runs of one commit; [] when the commit has no runs.
    Raises CiReadError when the read fails (no sha, gh error, bad JSON). Reads Actions runs only (no checks:read needed: fine-grained tokens lack it)."""
    if not sha:
        raise CiReadError("the head commit of the PR is unknown: CI cannot be read")
    r = pr_base._run(["gh", "run", "list", "--commit", sha, "--limit", str(CI_RUNS_LIMIT), "--json", "name,status,conclusion"], repo)
    if r.returncode:
        raise CiReadError(f"gh run list failed for {sha}: {(r.stderr or r.stdout).strip()[-200:]}")
    try:
        rows = json.loads(r.stdout or "[]")
    except ValueError:
        raise CiReadError(f"gh run list gave no JSON for {sha}") from None
    if not isinstance(rows, list):
        raise CiReadError(f"gh run list gave no run list for {sha}")
    return [
        {"name": x.get("name", ""), "status": (x.get("status") or "").upper(), "conclusion": (x.get("conclusion") or "").upper()}
        for x in rows
        if isinstance(x, dict)
    ]


def _ci_wait(repo, br, limit):
    """-> the finished workflow runs of the PR head, polling every CI_POLL_S; Blocked after `limit` s. An empty list is re-read CI_EMPTY_POLLS times when the repo has workflows (the runs may not exist yet)."""
    sha = pr_base._view(repo, br, "headRefOid")
    has_workflows = (Path(repo) / ".github" / "workflows").is_dir()
    deadline, empty = time.monotonic() + limit, 0
    while True:
        runs = _ci_runs(repo, sha)
        empty = empty + 1 if not runs else 0
        if runs and all(x["status"] == "COMPLETED" for x in runs):
            return runs
        if not runs and (not has_workflows or empty > CI_EMPTY_POLLS):
            return runs
        if time.monotonic() >= deadline:
            raise Blocked(f"checks of {br} still running after {limit}s")
        time.sleep(CI_POLL_S)


def _ci_failed(runs):
    return [x["name"] for x in runs if x["conclusion"] in ("FAILURE", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED")]


def _pr_checks(repo, br, limit=None):
    """Checks of BR -> CompletedProcess: rc 0 green, 1 red or "no checks reported", GH_CHECKS_PENDING_RC pending. Reads the workflow
    runs of the PR head (`gh run list --commit <head sha>`, inside _ci_runs), never `gh pr checks`: a token without checks:read cannot read that.
    A failed read raises CiReadError, never "no checks reported". With `limit`, waits up to `limit` s."""
    runs = _ci_wait(repo, br, limit) if limit else _ci_runs(repo, pr_base._view(repo, br, "headRefOid"))
    pending = any(x["status"] != "COMPLETED" for x in runs)
    bad = _ci_failed(runs)
    rc, out = (GH_CHECKS_PENDING_RC, "pending") if pending else (1, ", ".join(bad)) if bad else (0, "") if runs else (1, "no checks reported")
    # args label the answer only: the command that ran is `gh run list --commit <head sha>` (see _ci_runs)
    return subprocess.CompletedProcess(["gh", "run", "list", "--commit", "<head sha>"], rc, out, "")


def _failing(repo, br):
    """-> the names of the failed workflow runs of the PR head (`gh run list`, as _pr_checks reads them)."""
    return _ci_failed(_ci_runs(repo, pr_base._view(repo, br, "headRefOid")))


def web_note(a_dir, stage) -> str:
    """-> the UNVERIFIED-WEB line(s) of the stage's skipped browser check, joined by newlines; '' when none."""
    return "\n".join(webskip.skips(a_dir, stage))


def skipped_notes(a_dir, stage) -> str:
    """-> one line per timed-out step the owner skipped (cli_orch skip --note), with that note; '' when none."""
    root = stage_root(a_dir, stage)
    if root is None:
        return ""
    _w, log = paths(Run(dir=root, stage=stage))
    return "\n".join(f"SKIPPED {e['task']} ({REASON}): {e['note']}" for e in read_jsonl(log) if e.get("ev") == "skip" and e.get("timed_out") and e.get("note"))


def append_body(repo, br, note) -> None:
    """Append NOTE to the body of the PR of branch BR (best effort: a failed edit changes nothing)."""
    cur = pr_base._run(["gh", "pr", "view", br, "--json", "body", "--jq", ".body"], repo)
    if cur.returncode:
        return
    pr_base._run(["gh", "pr", "edit", br, "--body", f"{cur.stdout.rstrip()}\n\n{note}"], repo)


def _gates_gate(repo, cfg, br, runner=None):
    """Runs every project gate (`all_gates_of`) and each CI run line no gate covers, in REPO, before the push; a red one raises Blocked naming it. `runner(args, cwd, timeout)` -> returncode holder, default `_run`."""
    run_cmd = runner or _run
    gates = [g["cmd"] for g in all_gates_of(cfg)]
    ci = ci_commands(repo, cfg)
    before = ci_clean.snapshot(repo)
    red = []
    try:
        for c in gates:
            if run_cmd(["sh", "-c", c], repo, PR_GATE_TIMEOUT_S).returncode:
                red.append(f"`{c}`")
        for c in ci:
            r = run_cmd(["sh", "-c", c], repo, PR_GATE_TIMEOUT_S)
            if not r.returncode:
                continue
            if ENV_OUTPUT_RE.search((r.stdout or "") + (r.stderr or "")):
                red.append(f"`{c}` -> environment, not code: set the variable or add the ignore entry")
            else:
                red.append(f'`{c}` -> add `ci_drift_ignore` entry "{ignore_hint(c)}"')
    finally:
        ci_clean.remove_new(repo, before)
    if red:
        raise Blocked(f"{PR_GATES_STEP} red on {br}: {'; '.join(red)}; fix them, then run pr-ready again")
    return f"{PR_GATES_STEP}: {len(gates) + len(ci)} command(s) green"


CI_OFF_NOTE = "CI unavailable by setting: merged on green local gates"


def ci_off(cfg) -> bool:
    """True when the project set `ci_available` false: no CI runs, so the pipeline waits for none and merges on green local gates."""
    return not cfg.get("ci_available", True)


def _ready(repo, cfg, br):
    r = pr_base._run(["gh", "pr", "ready", br], repo)
    note = "marked ready"
    if r.returncode:
        if pr_base._view(repo, br, "isDraft") is not False:
            raise Blocked(f"gh pr ready failed and the PR of {br} is still a draft: {(r.stderr or r.stdout).strip()[-200:]}")
        note = "already ready"
    if ci_off(cfg):  # the local gates of _gates_gate are the only gates here: no CI wait
        return f"{note}; {CI_OFF_NOTE}"
    limit = int(checks_timeout(cfg))
    try:
        c = _pr_checks(repo, br, limit)
    except CiReadError:
        raise
    except Blocked:
        raise Blocked(f"checks of {br} not finished after {limit}s; still running: {_failing(repo, br) or 'see the PR'}") from None
    if c.returncode:
        if "no checks reported" in (c.stdout + c.stderr):
            return f"{note}; no checks configured"
        names = _failing(repo, br)
        raise Blocked(f"CI red on {br}: {', '.join(names) or (c.stdout + c.stderr).strip()[-300:]}")
    return f"{note}; checks green"


def _idle(repo, cfg, br):
    """True when the main repo has no PR work: nested repos exist, the stage branch is missing or has no commit beyond the release branch, and no PR is open."""
    if not worktree.nested_repos(repo):
        return False
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
        return True
    ahead = pr_base._git(repo, "rev-list", "--count", f"{release_branch(repo, cfg)}..{br}")
    return ahead.returncode == 0 and ahead.stdout.strip() == "0" and pr_base._view(repo, br, "state") not in ("OPEN", "MERGED")


def _empty(repo, cfg, br):
    """True when the stage branch is missing or has no commit beyond the release branch and no PR of it is open or merged."""
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode == 0:
        ahead = pr_base._git(repo, "rev-list", "--count", f"{release_branch(repo, cfg)}..{br}")
        if ahead.returncode or ahead.stdout.strip() != "0":
            return False
    return pr_base._view(repo, br, "state") not in ("OPEN", "MERGED")


def _nested_unlanded(repo, cfg, br, sep=""):
    """-> '' when every nested repo holding the stage branch has it in its release branch, else raises Blocked naming the repos; returns `sep` + note when all landed."""
    bad = []
    for n in worktree.nested_repos(repo):
        sub = os.path.join(repo, n)
        if pr_base._git(sub, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
            continue
        why = pr_base._landed(sub, release_branch(sub, cfg), br, False)
        if why:
            bad.append(f"{n}: {why}")
    if bad:
        raise Blocked("nested repo with stage work not in its release branch (run local-merge or merge by hand, then skip master-merged): " + "; ".join(bad))
    return ""


def _squash_forbidden(repo):
    """True only when `gh repo view` says squash merges are off; unknown (gh error, odd output) is False."""
    r = pr_base._run(["gh", "repo", "view", "--json", "squashMergeAllowed"], repo)
    try:
        return r.returncode == 0 and json.loads(r.stdout or "{}").get("squashMergeAllowed") is False
    except (ValueError, AttributeError):
        return False


class SystemClock:
    """The real clock of the merge wait; a test passes a fake with the same two methods, monotonic() and sleep(seconds)."""

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds) -> None:
        time.sleep(seconds)


def _say(line):
    sys.stderr.write(line + "\n")
    sys.stderr.flush()


def _poll(read, limit, clock=SystemClock, what="poll"):
    """-> the first value of read() that is not None, reading every CI_POLL_S; None once `limit` seconds have passed. Each read logs one line: attempt, state, elapsed."""
    start = clock.monotonic()
    deadline = start + limit
    attempt = 0
    while True:
        attempt += 1
        got = read()
        _say(f"{what}: attempt {attempt}, state {'pending' if got is None else 'ready'}, elapsed {clock.monotonic() - start:.0f}s")
        if got is not None:
            return got
        if clock.monotonic() >= deadline:
            return None
        clock.sleep(CI_POLL_S)


def _checks_read(repo, br):
    """-> the checks answer (_pr_checks) once no check is pending, else None (a pending check is polled again)."""
    c = _pr_checks(repo, br)
    return None if c.returncode == GH_CHECKS_PENDING_RC else c


def _mergeable_read(repo, br):
    """-> (mergeable,) once GitHub has computed it, else None: UNKNOWN is polled like a pending check."""
    m = pr_base._view(repo, br, "mergeable")
    return None if m == "UNKNOWN" else (m,)


def _checks_wait(repo, cfg, br, clock=SystemClock):
    """Polls the checks of `br` until none is pending (pr_checks_timeout), then raises Blocked unless they are green."""
    c = _poll(lambda: _checks_read(repo, br), checks_timeout(cfg), clock, what=f"checks of {br}")
    if c is None:
        raise Blocked(f"checks of {br} still pending after the wait: merge by hand when green, then skip master-merged")
    if c.returncode and "no checks reported" not in (c.stdout + c.stderr):
        names = _failing(repo, br)
        raise Blocked(f"checks of {br} are not green ({', '.join(names) or 'pending'}): merge by hand when they are, then skip master-merged")


def _checks_gate(repo, cfg, br, clock=SystemClock):
    """Waits for the checks of `br` (_checks_wait) unless `ci_available` is false, then raises Blocked unless squash merges are allowed."""
    if not ci_off(cfg):
        _checks_wait(repo, cfg, br, clock)
    if _squash_forbidden(repo):
        raise Blocked(f"the repo allows no squash merge (rebase-only or merge-commit-only): merge the PR of {br} by hand, then skip master-merged")


def _merge(a_dir, repo, cfg, stage, br, clock=SystemClock):
    """-> note. Polls an UNKNOWN mergeability and pending checks (pr_checks_timeout), then squash-merges the stage PR once it is open, ready, mergeable and its checks are green; anything else is BLOCKED (the lead merges by hand)."""
    state = pr_base._view(repo, br, "state")
    if state == "MERGED":
        return "PR already merged"
    if state != "OPEN" and pr_base._landed(repo, release_branch(repo, cfg), br, False) == "":
        return f"{br} adds nothing to {release_branch(repo, cfg)}: nothing to merge"
    if state != "OPEN":
        raise Blocked(f"no open PR for {br} (state: {state}): merge by hand, then skip master-merged")
    _mergeable_gate(repo, cfg, br, clock)
    _checks_gate(repo, cfg, br, clock)
    title = pr_base._merge_title(a_dir, stage, br)
    r = pr_base._run(["gh", "pr", "merge", br, "--squash", "--delete-branch", "--subject", title, "--body", _merge_body(cfg, stage)], repo, 300)
    if r.returncode:
        if pr_base._view(repo, br, "state") == "MERGED":  # merged remotely; only the local branch cleanup failed
            return f"PR squash-merged: {title} (local cleanup failed: {(r.stderr or r.stdout).strip()[-200:]})"
        raise Blocked(f"gh pr merge failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return _merged_text(cfg, title)


def _mergeable_gate(repo, cfg, br, clock):
    """Raise Blocked unless the open PR BR is not a draft and reports MERGEABLE (polls an UNKNOWN mergeability first)."""
    if pr_base._view(repo, br, "isDraft") is not False:
        raise Blocked(f"the PR of {br} is still a draft")
    got = _poll(lambda: _mergeable_read(repo, br), checks_timeout(cfg), clock, what=f"mergeability of {br}")
    mergeable = got[0] if got else None
    if mergeable != "MERGEABLE":
        raise Blocked(f"the PR of {br} is {mergeable or 'of unknown mergeability'}: resolve it by hand, then skip master-merged")


def _merge_body(cfg, stage) -> str:
    """The squash-commit body of the stage PR, naming the gates that ran in place of CI when CI is off."""
    if ci_off(cfg):
        return f"Stage {stage}. Squash-merged by the pipeline on green local gates (CI unavailable by setting)."
    return f"Stage {stage}. Squash-merged by the pipeline after green CI."


def _merged_text(cfg, title) -> str:
    """-> the note after a good squash-merge; with CI off it says the pr-ready gates were relied on."""
    if ci_off(cfg):  # the gates ran at the pr-ready step; the master-merged step does not run them again
        return f"PR squash-merged: {title}; local gates of the pr-ready step relied on ({CI_OFF_NOTE})"
    return f"PR squash-merged: {title}"


def _wt_clean(a) -> None:
    """Run wt.py clean for the stage's job worktrees (before the branch cleanup: it force-deletes the job branches)."""
    w = pr_base._run([sys.executable, "-I", os.path.join(TOOLS, "wt.py"), "clean", "--stage", a.stage, "--dir", a.dir], a.dir, 300)
    if w.returncode:
        raise Blocked(f"wt.py clean failed: {(w.stderr or w.stdout).strip()[-300:]}")


def _nested_clean(a, repo, cfg, br) -> str:
    """Clean BR in the local-only nested repos of REPO that hold it (no PR, no remote steps) -> note text, "" when none."""
    res = ""
    for n in worktree.nested_repos(repo):
        sub = os.path.join(repo, n)
        if pr_base._git(sub, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
            continue
        try:
            res += f"; [{n}] " + _clean_repo(a, sub, cfg, br, False)
        except Blocked as e:
            raise Blocked(f"nested repo {n}: {e}") from None
    return res


def _prune_note(a_dir) -> str:
    """Delete the merged orphan job/* branches of earlier stages; name the unmerged ones -> note text, "" when none."""
    pruned, unmerged = cleanup.prune_branches(a_dir)
    res = ""
    if pruned:
        res += "; pruned job branches: " + ", ".join(b for b, _into in pruned)
    if unmerged:
        res += "; kept unmerged: " + ", ".join(unmerged)
    return res


def _cleanup(a, repo, cfg, br):
    if pr_base._git(repo, "rev-parse", "--git-dir").returncode:
        return "no git repo: nothing to clean"
    exists = pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode == 0
    if (
        exists and br != (rel := release_branch(repo, cfg)) and (why := pr_base._landed(repo, rel, br, _has_origin(repo)))
    ):  # before wt.py clean: it force-deletes the job branches
        raise Blocked(why)
    if cfg.get("worktrees"):
        _wt_clean(a)
    res = _clean_repo(a, repo, cfg, br, True)
    res += _nested_clean(a, repo, cfg, br)
    return res + _prune_note(a.dir)


def _to_release_branch(repo, rel, cur):
    """Switch to the release branch (or create it from origin) -> note or None."""
    if cur == rel:
        return None
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{rel}").returncode == 0:
        r = pr_base._git(repo, "switch", rel)
        if r.returncode:
            raise Blocked(f"cannot switch to {rel}: {r.stderr.strip()[-200:]}")
        return f"on {rel}"
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode == 0:  # no local release branch yet: create it from origin
        r = pr_base._git(repo, "switch", "-c", rel, f"origin/{rel}")
        if r.returncode:
            raise Blocked(f"cannot create {rel} from origin/{rel}: {r.stderr.strip()[-200:]}")
        return f"created {rel} from origin/{rel}"
    return None


def _prune_origin(repo, br, rel):
    """Delete origin/br, fetch --prune, fast-forward the release branch -> notes."""
    out = []
    if pr_base._git(repo, "ls-remote", "--exit-code", "--heads", "origin", br).returncode == 0:
        r = pr_base._git(repo, "-c", pr_mode.GH_CRED, "push", "origin", "--delete", br, timeout=GIT_LONG_TIMEOUT_S)
        if r.returncode:
            raise Blocked(f"origin/{br} not deleted: {r.stderr.strip()[-200:]}")
        out.append(f"deleted origin/{br}")
    r = pr_base._git(repo, "-c", pr_mode.GH_CRED, "fetch", "--prune", "origin", timeout=GIT_NETWORK_TIMEOUT_S)
    out.append("fetched" if r.returncode == 0 else f"fetch failed: {r.stderr.strip()[-120:]}")
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode == 0:
        r = pr_base._git(repo, "merge", "--ff-only", f"origin/{rel}")
        out.append(f"{rel} fast-forwarded" if r.returncode == 0 else f"{rel} not fast-forwarded: {r.stderr.strip()[-120:]}")
    return out


def _clean_repo(a, repo, cfg, br, main):
    rel, out = release_branch(repo, cfg), []
    exists = pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode == 0
    if exists and br != rel and (why := pr_base._landed(repo, rel, br, main and _has_origin(repo))):
        raise Blocked(why)
    cur = pr_base._git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if note := _to_release_branch(repo, rel, cur):
        out.append(note)
    if exists and br != rel:
        r = pr_base._git(repo, "branch", "-D", br)
        if r.returncode:
            raise Blocked(f"could not delete {br}: {r.stderr.strip()[-200:]}")
        out.append(f"deleted {br}")
    if main and pr_mode.has_origin(Run(dir=a.dir)):
        out += _prune_origin(repo, br, rel)
    pr_base._git(repo, "worktree", "prune")
    return "; ".join(out) or "already clean"


def _squash_commit(repo, name, br, a_dir):
    """Commit the staged squash through commit.py -> (completed process, subject)."""
    subject = f"chore({name.split('/phases/')[0].split('/')[-1]}): squash stage branch {br}"  # area has no slash
    sf = os.path.join(a_dir, WORK_DIR, name, "SUBJECT")
    if os.path.isfile(sf):
        with open(sf, encoding="utf-8") as f:
            subject = next((x.strip() for x in f if x.strip()), "") or subject
    c = pr_base._run(
        [
            sys.executable,
            "-I",
            os.path.join(TOOLS, "commit.py"),
            "--subject",
            subject,
            "--dir",
            repo,
            "--workspace",
            a_dir,
        ],
        repo,
    )
    return c, subject


def _back(repo, was, rel):
    """Return to the branch the user was on after a failed local merge."""
    if was and was != rel:
        pr_base._git(repo, "switch", was)


def _reset_back(repo, was, rel):
    """Undo a failed squash (reset --merge) and return to the branch the user was on."""
    pr_base._git(repo, "reset", "--merge")
    _back(repo, was, rel)


def _skip_note(repo, rel, br) -> str:
    """-> why BR needs no local merge into REL (no such branch, or it adds nothing), else ""."""
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
        return f"no branch {br}: skipped"
    if br == rel or pr_base._landed(repo, rel, br, False) == "":
        return f"{br} adds nothing to {rel}: already merged"
    return ""


def _local_merge_repo(repo, cfg, br, name, a_dir):
    rel = release_branch(repo, cfg)
    if note := _skip_note(repo, rel, br):
        return note
    # untracked files count too (a squash can overwrite them), except the pipeline's own folders
    if pr_base._git(repo, "status", "--porcelain", "--", ".", ":(exclude).work", ":(exclude).wt", ":(exclude).audits").stdout.strip():
        raise Blocked("working tree is dirty: commit or stash first")
    was = pr_base._git(repo, "branch", "--show-current").stdout.strip()
    r = pr_base._git(repo, "switch", rel)
    if r.returncode:
        raise Blocked(f"cannot switch to {rel}: {r.stderr.strip()[-200:]}")
    # the repo stays on `rel` after a good merge (loud note below); a failed one goes back to where the user was
    r = pr_base._git(repo, "merge", "--squash", br)
    if r.returncode:
        files = pr_base._git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        _reset_back(repo, was, rel)
        raise Blocked(f"conflict squashing {br} into {rel}; files: {', '.join(files) or r.stdout.strip()[-200:]}")
    if pr_base._git(repo, "diff", "--cached", "--quiet").returncode == 0:
        return f"{br} brings no change to {rel}: already merged"
    c, subject = _squash_commit(repo, name, br, a_dir)
    if c.returncode:
        _reset_back(repo, was, rel)
        raise Blocked(f"commit failed: {(c.stderr or c.stdout).strip()[-300:]}")
    note = f" NOTE: the checkout is now on `{rel}`, you were on `{was}`" if was and was != rel else ""
    return f"squashed {br} into {rel}: {subject}.{note}"


def _local_merge(a, cfg, br, name):
    repo = repo_path(a.dir)
    out = []
    if pr_mode.direct_flow(a):  # no origin, or `"pr": false`; a repo with an origin waits for the PR flow, however gh auth looks right now
        try:
            out.append(_local_merge_repo(repo, cfg, br, name, a.dir))
        except Blocked as e:
            raise Blocked(f"main repo: {e}") from None
    for n in worktree.nested_repos(repo):
        try:
            out.append(f"[{n}] " + _local_merge_repo(os.path.join(repo, n), cfg, br, name, a.dir))
        except Blocked as e:
            raise Blocked(f"repo {n}: {e}") from None
    return "; ".join(out) or "no local-only repo"


def _green(rollup):
    """All checks finished and passing; an empty rollup is not green."""
    if not rollup:
        return False
    return all((c.get("status") in (None, "COMPLETED")) and (c.get("conclusion") or c.get("state")) in ("SUCCESS", "NEUTRAL", "SKIPPED") for c in rollup)


def _red(rollup):
    return any((c.get("conclusion") or c.get("state")) in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED") for c in rollup)


def _merge_green(repo, p):
    """Squash-merge one green Dependabot PR -> (merged, failed, line)."""
    n, title = p.get("number"), p.get("title", "")
    m = pr_base._run(["gh", "pr", "merge", str(n), "--squash", "--delete-branch"], repo, 300)
    if m.returncode == 0:
        return 1, 0, f"#{n} merged: {title}"
    return 0, 1, f"#{n} skipped (merge failed: {(m.stderr or m.stdout).strip()[-120:]}): {title}"


def _sweep_ff(repo, rel):
    """Fetch and fast-forward the release branch -> note ("" when nothing to say)."""
    f = pr_base._git(repo, "-c", pr_mode.GH_CRED, "fetch", "--prune", "origin", timeout=GIT_NETWORK_TIMEOUT_S)
    if f.returncode:
        return "fetch failed: " + f.stderr.strip()[-120:]
    if pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode:
        return ""
    cur = pr_base._git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if cur == rel:
        u = pr_base._git(repo, "merge", "--ff-only", f"origin/{rel}")
    elif pr_base._git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{rel}").returncode == 0:
        u = pr_base._git(repo, "fetch", ".", f"origin/{rel}:{rel}")
    else:
        return ""
    return f"{rel} fast-forwarded" if u.returncode == 0 else f"{rel} not fast-forwarded: {u.stderr.strip()[-120:]}"


def _dependabot_prs(repo, r, token_gap) -> list:
    """Parse the gh pr list reply R; with TOKEN_GAP the checks of each head come from its workflow runs."""
    if r.returncode:
        raise Blocked(f"gh pr list failed: {r.stderr.strip()[-300:]}")
    try:
        prs = json.loads(r.stdout or "[]")
    except ValueError:
        prs = []
    if token_gap:
        for p in prs:
            p["statusCheckRollup"] = _ci_runs(repo, p.get("headRefOid"))
    return prs


def _sweep_lines(repo, prs):
    """Merge each green Dependabot PR, name the red and pending ones -> (lines, merged, red, pending, failed)."""
    lines, merged, red, pending, failed = [], 0, 0, 0, 0
    for p in prs:
        n, title, roll = p.get("number"), p.get("title", ""), p.get("statusCheckRollup") or []
        if _green(roll):
            ok, bad, line = _merge_green(repo, p)
            merged, failed = merged + ok, failed + bad
            lines.append(line)
        elif _red(roll):
            red += 1
            lines.append(f"#{n} skipped red: {title}")
        else:
            pending += 1
            lines.append(f"#{n} skipped pending: {title}")
    return lines, merged, red, pending, failed


def _sweep(repo, cfg):
    rel = release_branch(repo, cfg)
    listing = ["gh", "pr", "list", "--author", "app/dependabot", "--state", "open", "--json"]
    r = pr_base._run([*listing, "number,title,statusCheckRollup"], repo)
    token_gap = bool(r.returncode) and CI_DENIED_MSG in r.stdout + r.stderr
    if token_gap:  # the token cannot read check runs: list without them and read the workflow runs of each head
        r = pr_base._run([*listing, "number,title,headRefOid"], repo)
    prs = _dependabot_prs(repo, r, token_gap)
    lines, merged, red, pending, failed = _sweep_lines(repo, prs)
    ff = _sweep_ff(repo, rel)
    counts = f"{len(prs)} Dependabot PR(s), {merged} merged, {red} skipped red, {pending} skipped pending" + (f", {failed} merge failed" if failed else "")
    lines.append(f"summary: {counts}" + (f"; {ff}" if ff else ""))
    return "\n".join(lines)


def _no_push_note(what, repo, cfg, br):
    """-> why the step has nothing to push or open (no origin, empty stage branch, work only in nested repos), else ""."""
    if (
        what in ("open", "push", "ready", "merge") and not _has_origin(repo) and (what != "merge" or pr_base._view(repo, br, "state") not in ("OPEN", "MERGED"))
    ):  # no origin = no PR: the local-merge flow owns the branch
        return "no origin: PR step skipped, the stage branch is merged by local-merge" + _nested_unlanded(repo, cfg, br, "; ")
    if what != "merge" and _empty(repo, cfg, br):  # an empty stage branch is never pushed and gets no PR
        return f"{br} has no commit beyond the release branch: not pushed, no PR"
    if what == "merge" and _empty(repo, cfg, br):
        return f"{br} has no commit beyond the release branch: nothing to merge, nothing pushed" + _nested_unlanded(repo, cfg, br, "; ")
    if what != "merge" and _idle(repo, cfg, br):
        return f"{br} has no commit beyond the release branch in the main repo (work is in nested repos): no PR"
    return ""


def _merge_step(a_dir, repo, cfg, stage, br) -> str:
    """The merge step text: the squash-merge plus the nested repos the stage left unlanded."""
    return _merge(a_dir, repo, cfg, stage, br) + _nested_unlanded(repo, cfg, br, "; ")


def _open_and_note(a_dir, repo, cfg, stage, br) -> str:
    """Open the stage PR, then add the skipped checks and timed-out skips to its body -> the open note."""
    opened = _open_pr(a_dir, repo, cfg, stage, br)
    if note := "\n".join(x for x in (web_note(a_dir, stage), skipped_notes(a_dir, stage), CI_OFF_NOTE if ci_off(cfg) else "") if x):
        append_body(repo, br, note)  # the skipped browser check and timed-out skips must show in the PR, never silent
    return opened


def _pr_step(what, a_dir, repo, cfg, stage, br):
    """open/push/ready/merge -> the report text (raises Blocked)."""
    if note := _no_push_note(what, repo, cfg, br):
        return note
    if what == "merge" and pr_base._view(repo, br, "state") == "MERGED":  # a re-run: --delete-branch removed the branch, nothing to push
        return _merge_step(a_dir, repo, cfg, stage, br)
    if what == "merge" and _idle(repo, cfg, br):  # stage work only in nested repos: no PR to merge
        return f"{br} adds nothing to the main repo release branch" + _nested_unlanded(repo, cfg, br, "; ")
    gates = _gates_gate(repo, cfg, br) if what == "ready" else ""  # red gates block before anything is pushed
    _pushed(repo, br)
    if what == "merge":
        return _merge_step(a_dir, repo, cfg, stage, br)
    if what == "ready":
        opened = _open_and_note(a_dir, repo, cfg, stage, br)
        return gates + "; " + opened + "; " + _ready(repo, cfg, br)
    return _open_pr(a_dir, repo, cfg, stage, br)


def run(what, a_dir, stage, name) -> tuple[str, str]:
    """-> (verdict, text)."""
    a = Run(dir=a_dir, stage=stage)
    cfg = project(a_dir)
    repo = repo_path(a.dir)
    br = pr_mode.branch(a, name)
    try:
        if what == "sweep":
            return DONE, _sweep(repo, cfg)
        if what == "local-merge":
            return DONE, _local_merge(a, cfg, br, name)
        if what in ("cleanup", "merge") and (old := pr_mode.git_too_old(repo)):
            raise Blocked(f"{old}: `git merge-tree --write-tree` is missing, so the landed-check cannot run; upgrade git, then retry")
        if what == "cleanup":
            return DONE, _cleanup(a, repo, cfg, br)
        if what == "version-bump":
            return DONE, _version_bump(a_dir, repo, cfg, stage, br)
        if what == "release":
            pr = pr_mode.pr_active(a)
            if not pr and pr_mode.has_origin(a):
                raise Blocked(
                    "release needs the PR flow: this repo has an `origin` but the PR flow is off (run `gh auth login`, check `gh auth status`, and make sure pipeline.json does not set pr to false), then retry release"
                )
            return DONE, _release(a_dir, repo, cfg, stage, br, pr)
        return DONE, _pr_step(what, a_dir, repo, cfg, stage, br)
    except Blocked as e:
        return BLOCKED, str(e)


def sweep_before_branch(a) -> str:
    """Run the sweep on the release branch before the work branch is created. -> '' (always; red or pending PRs are no failure, a failed sweep only warns)."""
    if not pr_mode.sweep_active(a):
        return ""
    v, text = run("sweep", a.dir, getattr(a, "stage", None) or pr_mode._sweep_name(a), pr_mode._sweep_name(a))
    print(f"== deps-sweep {v}\n{text}")
    return ""
