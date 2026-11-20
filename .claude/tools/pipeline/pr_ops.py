"""Draft PR, merge, release, cleanup and local-merge of a stage branch by script. The command line is pr_steps.py."""

import json
import os
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path

from . import gitx, pr_mode, worktree
from .configuration import (
    BLOCKED,
    CHECKS_TIMEOUT_S,
    CI_DENIED_MSG,
    CI_EMPTY_POLLS,
    CI_POLL_S,
    CI_RUNS_LIMIT,
    DONE,
    GIT_LONG_TIMEOUT_S,
    GIT_NETWORK_TIMEOUT_S,
    GIT_SAFE_ENV,
    PLAN_FILE,
    PROJECT_DEFAULTS,
    PUSH_ERR_HEAD,
    PUSH_ERR_TAIL,
    TASKS_FILE,
    WORK_DIR,
    project,
    release_branch,
    repo_path,
)
from .plan_files import section_body
from .run import Run
from .util import TOOLS


def checks_timeout(cfg):
    """Seconds a PR step waits for pending checks: pipeline.json `pr_checks_timeout`, else CHECKS_TIMEOUT_S."""
    return float(cfg.get("pr_checks_timeout") or CHECKS_TIMEOUT_S)


class Blocked(Exception):
    pass


def _run(args, cwd, timeout=GIT_LONG_TIMEOUT_S):
    if args[:2] == ["gh", "pr"] and (slug := pr_mode.origin_parts(cwd)[1]):  # a fork's origin: name the repo, never let gh pick the upstream
        args = [*args[:3], "--repo", slug, *args[3:]]
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_SAFE_ENV}, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise Blocked(f"{' '.join(args[:4])} timed out after {timeout}s") from e
    except OSError as e:
        raise Blocked(f"{args[0]}: {e}") from e


def _git(repo, *args, timeout=GIT_LONG_TIMEOUT_S):
    try:
        return gitx.run(repo, *args, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise Blocked(f"{' '.join(['git', *args][:4])} timed out after {timeout}s") from e
    except OSError as e:
        raise Blocked(f"git: {e}") from e


def _has_origin(repo):
    """A repo without `origin` has no PR: never ask gh."""
    return _git(repo, "remote", "get-url", "origin").returncode == 0


def _ends(text):
    """TEXT whole, or its first PUSH_ERR_HEAD and last PUSH_ERR_TAIL chars: the cause is often at the start (hook, auth), the verdict at the end."""
    text = re.sub(r"://[^/@\s]+@", "://***@", text)  # no credentials in a report
    if len(text) <= PUSH_ERR_HEAD + PUSH_ERR_TAIL:
        return text
    return f"{text[:PUSH_ERR_HEAD]} [...] {text[-PUSH_ERR_TAIL:]}"


def _pushed(repo, br):
    r = _git(repo, "-c", pr_mode.GH_CRED, "push", "-u", "origin", br, timeout=GIT_NETWORK_TIMEOUT_S)
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
    r = _run(["gh", "pr", "list", "--head", br, "--state", "open", "--json", "number,url"], repo)
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
        f"origin/{rel}" if _git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode == 0 else rel
    )  # the remote head, not a stale local one
    ahead = _git(repo, "rev-list", "--count", f"{base}..{br}")
    if ahead.returncode == 0 and ahead.stdout.strip() == "0":
        return "no commit beyond the release branch yet: the PR opens at the next push"
    title = _pr_title(a_dir, stage, br)
    body = f"Stage {stage}. Task list: `.work/{stage}/TASKS.md`. Opened by the pipeline as a draft; it is marked ready after the last audit."
    r = _run(["gh", "pr", "create", "--draft", "--base", rel, "--head", br, "--title", title, "--body", body], repo)
    if r.returncode:
        raise Blocked(f"gh pr create failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"PR opened: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else br}"


def _view(repo, br, field):
    """-> the value of `gh pr view --json FIELD`, None when unknown."""
    try:
        r = _run(["gh", "pr", "view", br, "--json", field], repo)
    except Blocked:  # no gh binary or it hangs: no PR
        return None
    try:
        return json.loads(r.stdout or "{}").get(field) if r.returncode == 0 else None
    except (ValueError, AttributeError):
        return None


def _landed(repo, rel, br, pr=True):
    """-> '' when the stage branch is safe to delete (PR merged, or it adds nothing to the release branch), else the reason it is not."""
    if pr and _view(repo, br, "state") == "MERGED":
        return ""
    for ref in (rel, f"origin/{rel}"):
        if _git(repo, "rev-parse", "--verify", "-q", ref).returncode:
            continue
        if _git(repo, "diff", "--quiet", ref, br).returncode == 0:
            return ""
        cherry = _git(repo, "cherry", ref, br)
        if cherry.returncode == 0 and not any(x.startswith("+") for x in cherry.stdout.splitlines()):
            return ""
        # squash of several commits, release moved on: a clean merge that leaves the release tree unchanged adds nothing
        merged = _git(repo, "merge-tree", "--write-tree", ref, br)
        if merged.returncode == 0 and merged.stdout.split()[:1] == [_git(repo, "rev-parse", f"{ref}^{{tree}}").stdout.strip()]:
            return ""
    return f"{br} has work that is not in {rel} and its PR is not MERGED; merge it first, then skip master-merged again"


def _ci_runs(repo, sha):
    """-> [{name, status, conclusion}] (upper case, like a status rollup) of the workflow runs of one commit; [] when unreadable.
    Fallback for a token without checks:read (fine-grained tokens have none): it reads Actions runs only."""
    if not sha:
        return []
    r = _run(["gh", "run", "list", "--commit", sha, "--limit", str(CI_RUNS_LIMIT), "--json", "name,status,conclusion"], repo)
    try:
        rows = json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except ValueError:
        return []
    return [
        {"name": x.get("name", ""), "status": (x.get("status") or "").upper(), "conclusion": (x.get("conclusion") or "").upper()}
        for x in rows
        if isinstance(x, dict)
    ]


def _ci_wait(repo, br, limit):
    """-> the finished workflow runs of the PR head, polling every CI_POLL_S; Blocked after `limit` s. An empty list is re-read CI_EMPTY_POLLS times when the repo has workflows (the runs may not exist yet)."""
    sha = _view(repo, br, "headRefOid")
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
            raise Blocked(f"gh pr checks {br} --watch timed out after {limit}s")
        time.sleep(CI_POLL_S)


def _ci_failed(runs):
    return [x["name"] for x in runs if x["conclusion"] in ("FAILURE", "CANCELLED", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED")]


def _pr_checks(repo, br, limit=None):
    """`gh pr checks BR` (with --watch and a time limit when `limit` is set) -> CompletedProcess. A token that cannot read check runs
    falls back to the workflow runs of the PR head, answered in the same shape: rc 0 green, 1 red or none, 8 pending."""
    c = _run(["gh", "pr", "checks", br, *(["--watch"] if limit else [])], repo, *([limit] if limit else []))
    if not c.returncode or CI_DENIED_MSG not in c.stdout + c.stderr:
        return c
    runs = _ci_wait(repo, br, limit) if limit else _ci_runs(repo, _view(repo, br, "headRefOid"))
    pending = any(x["status"] != "COMPLETED" for x in runs)
    bad = _ci_failed(runs)
    rc, out = (8, "pending") if pending else (1, ", ".join(bad)) if bad else (0, "") if runs else (1, "no checks reported")
    return subprocess.CompletedProcess(c.args, rc, out, "")


def _failing(repo, br):
    r = _run(["gh", "pr", "checks", br, "--json", "name,bucket"], repo)
    if r.returncode and CI_DENIED_MSG in r.stdout + r.stderr:
        return _ci_failed(_ci_runs(repo, _view(repo, br, "headRefOid")))
    try:
        return [c["name"] for c in json.loads(r.stdout or "[]") if isinstance(c, dict) and c.get("bucket") in ("fail", "cancel")]
    except (ValueError, TypeError, KeyError):
        return []


def _ready(repo, cfg, br):
    r = _run(["gh", "pr", "ready", br], repo)
    note = "marked ready"
    if r.returncode:
        if _view(repo, br, "isDraft") is not False:
            raise Blocked(f"gh pr ready failed and the PR of {br} is still a draft: {(r.stderr or r.stdout).strip()[-200:]}")
        note = "already ready"
    limit = int(checks_timeout(cfg))
    try:
        c = _pr_checks(repo, br, limit)
    except Blocked:
        raise Blocked(f"checks of {br} not finished after {limit}s; still running: {_failing(repo, br) or 'see the PR'}") from None
    if c.returncode:
        if "no checks reported" in (c.stdout + c.stderr):
            return f"{note}; no checks configured"
        names = _failing(repo, br)
        raise Blocked(f"CI red on {br}: {', '.join(names) or (c.stdout + c.stderr).strip()[-300:]}")
    return f"{note}; checks green"


TITLE_RE = re.compile(r"^[a-z]+(\([\w./-]+\))?!?: \S.{0,100}$")


def _idle(repo, cfg, br):
    """True when the main repo has no PR work: nested repos exist, the stage branch is missing or has no commit beyond the release branch, and no PR is open."""
    if not worktree.nested_repos(repo):
        return False
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
        return True
    ahead = _git(repo, "rev-list", "--count", f"{release_branch(repo, cfg)}..{br}")
    return ahead.returncode == 0 and ahead.stdout.strip() == "0" and _view(repo, br, "state") not in ("OPEN", "MERGED")


def _empty(repo, cfg, br):
    """True when the stage branch is missing or has no commit beyond the release branch and no PR of it is open or merged."""
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode == 0:
        ahead = _git(repo, "rev-list", "--count", f"{release_branch(repo, cfg)}..{br}")
        if ahead.returncode or ahead.stdout.strip() != "0":
            return False
    return _view(repo, br, "state") not in ("OPEN", "MERGED")


def _nested_unlanded(repo, cfg, br, sep=""):
    """-> '' when every nested repo holding the stage branch has it in its release branch, else raises Blocked naming the repos; returns `sep` + note when all landed."""
    bad = []
    for n in worktree.nested_repos(repo):
        sub = os.path.join(repo, n)
        if _git(sub, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
            continue
        why = _landed(sub, release_branch(sub, cfg), br, False)
        if why:
            bad.append(f"{n}: {why}")
    if bad:
        raise Blocked("nested repo with stage work not in its release branch (run local-merge or merge by hand, then skip master-merged): " + "; ".join(bad))
    return ""


def _merge_title(a_dir, stage, br):
    """-> the squash commit title: the `pr-title:` line of the stage TASKS.md when it is `<type>(<area>): <what>`, else `chore(<stage>): <goal>`."""
    tl = os.path.join(a_dir, WORK_DIR, stage, TASKS_FILE)
    text = Path(tl).read_text(encoding="utf-8") if os.path.isfile(tl) else ""
    m = re.search(r"(?im)^pr-title:\s*(.+)$", text)
    if m and TITLE_RE.match(m[1].strip()):
        return m[1].strip()
    g = re.search(r"(?im)^goal:\s*(.+)$", text)
    goal = re.sub(r"\s+", " ", g[1]).strip(" .")[:80] if g else stage
    return f"chore({stage.split('/phases/')[0].split('/')[-1]}): {goal[:1].lower() + goal[1:]}"


def _squash_forbidden(repo):
    """True only when `gh repo view` says squash merges are off; unknown (gh error, odd output) is False."""
    r = _run(["gh", "repo", "view", "--json", "squashMergeAllowed"], repo)
    try:
        return r.returncode == 0 and json.loads(r.stdout or "{}").get("squashMergeAllowed") is False
    except (ValueError, AttributeError):
        return False


def _checks_gate(repo, cfg, br):
    """Waits for pending checks, then raises Blocked unless they are green and squash merges are allowed."""
    try:
        _pr_checks(repo, br, checks_timeout(cfg))
    except Blocked:
        raise Blocked(f"checks of {br} still pending after the wait: merge by hand when green, then skip master-merged") from None
    c = _pr_checks(repo, br)
    if c.returncode and "no checks reported" not in (c.stdout + c.stderr):
        names = _failing(repo, br)
        raise Blocked(f"checks of {br} are not green ({', '.join(names) or 'pending'}): merge by hand when they are, then skip master-merged")
    if _squash_forbidden(repo):
        raise Blocked(f"the repo allows no squash merge (rebase-only or merge-commit-only): merge the PR of {br} by hand, then skip master-merged")


def _merge(a_dir, repo, cfg, stage, br):
    """-> note. Waits for pending checks (pr_checks_timeout), then squash-merges the stage PR once it is open, ready, mergeable and its checks are green; anything else is BLOCKED (the lead merges by hand)."""
    state = _view(repo, br, "state")
    if state == "MERGED":
        return "PR already merged"
    if state != "OPEN" and _landed(repo, release_branch(repo, cfg), br, False) == "":
        return f"{br} adds nothing to {release_branch(repo, cfg)}: nothing to merge"
    if state != "OPEN":
        raise Blocked(f"no open PR for {br} (state: {state}): merge by hand, then skip master-merged")
    if _view(repo, br, "isDraft") is not False:
        raise Blocked(f"the PR of {br} is still a draft")
    mergeable = _view(repo, br, "mergeable")
    if mergeable != "MERGEABLE":
        raise Blocked(f"the PR of {br} is {mergeable or 'of unknown mergeability'}: resolve it by hand, then skip master-merged")
    _checks_gate(repo, cfg, br)
    title = _merge_title(a_dir, stage, br)
    r = _run(
        ["gh", "pr", "merge", br, "--squash", "--delete-branch", "--subject", title, "--body", f"Stage {stage}. Squash-merged by the pipeline after green CI."],
        repo,
        300,
    )
    if r.returncode:
        if _view(repo, br, "state") == "MERGED":  # merged remotely; only the local branch cleanup failed
            return f"PR squash-merged: {title} (local cleanup failed: {(r.stderr or r.stdout).strip()[-200:]})"
        raise Blocked(f"gh pr merge failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"PR squash-merged: {title}"


LEVELS = ("patch", "minor", "major")


def _release_level(a_dir, stage):
    """-> highest `release: patch|minor|major` level over the stage TASKS.md and, for a phase stage, every phase TASKS.md of its feature (owner's word at planning); default patch."""
    work = os.path.join(a_dir, WORK_DIR)
    feature = stage.split("/phases/")[0]
    lists = [os.path.join(work, stage, TASKS_FILE)]
    pdir = os.path.join(work, feature, "phases")
    if os.path.isdir(pdir):
        lists += [os.path.join(pdir, n, TASKS_FILE) for n in sorted(os.listdir(pdir))]
    best = 0
    for tl in lists:
        text = Path(tl).read_text(encoding="utf-8") if os.path.isfile(tl) else ""
        for m in re.finditer(r"(?im)^\s*release\s*:\s*(patch|minor|major)\s*$", text):
            best = max(best, LEVELS.index(m[1].lower()))
    return LEVELS[best]


def _goal_version(a_dir, stage):
    """-> X.Y.Z named as `vX.Y.Z` in the first lines of the feature Plan.md (the owner's release goal), or ''."""
    plan = os.path.join(a_dir, WORK_DIR, stage.split("/phases/")[0], PLAN_FILE)
    if not os.path.isfile(plan):
        return ""
    head = "\n".join(Path(plan).read_text(encoding="utf-8").splitlines()[:12])
    m = re.search(r"\bv(\d+\.\d+\.\d+)\b", head)
    return m[1] if m else ""


def _ver(v):
    return tuple(int(n) for n in v.split("."))


_PIN = re.compile(
    r'(\{[^}\n]*?\bpath\s*=\s*"([^"\n]+)"[^}\n]*?\bversion\s*=\s*)"[^"\n]*"|(\{[^}\n]*?\bversion\s*=\s*)"[^"\n]*"([^}\n]*?\bpath\s*=\s*"([^"\n]+)")'
)


def _uses_workspace_version(manifest):
    if not os.path.isfile(manifest):
        return False
    try:
        data = tomllib.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):  # unreadable or invalid TOML: not a workspace-version crate
        return False
    version = data.get("package", {}).get("version")
    return isinstance(version, dict) and version.get("workspace") is True


def _rewrite_pins(repo, cfg, new):
    """-> repo-relative manifests changed: every `version = "X"` next to a `path =` dependency whose target crate uses the workspace version is set to NEW."""
    name = cfg.get("pin_manifest", PROJECT_DEFAULTS["pin_manifest"])
    if not name:
        return []
    out = []
    for rel in _git(repo, "ls-files", "--", name, f"*/{name}").stdout.split():
        path = os.path.join(repo, rel)
        base = os.path.dirname(path)

        def sub(m, base=base):
            tgt = m[2] or m[5]
            if not _uses_workspace_version(os.path.join(base, tgt, name)):
                return m[0]
            return f'{m[1]}"{new}"' if m[1] else f'{m[3]}"{new}"{m[4]}'

        text = Path(path).read_text(encoding="utf-8")
        fixed = _PIN.sub(sub, text)
        if fixed != text:
            Path(path).write_text(fixed, encoding="utf-8")
            out.append(rel)
    return out


def _bump(v, level):
    if not re.fullmatch(r"\d+\.\d+\.\d+", v):
        raise Blocked(f"version {v!r} is not X.Y.Z: bump it by hand (or set flag no-release)")
    x, y, z = (int(n) for n in v.split("."))
    return f"{x + 1}.0.0" if level == "major" else f"{x}.{y + 1}.0" if level == "minor" else f"{x}.{y}.{z + 1}"


def _hook(repo, cfg, *args):
    """-> stdout of the project's `release_script` (pipeline.json, relative to the repo) run with ARGS; BLOCKED when it is missing or fails."""
    rs = cfg.get("release_script")
    path = os.path.join(repo, rs) if rs else ""
    if not rs or not os.path.isfile(path):
        raise Blocked(f"release_script {rs!r} is not a file in {repo}: fix pipeline.json or set flag no-release")
    r = _run(["bash", path, *args], repo, 120)
    if r.returncode:
        raise Blocked(f"release_script {' '.join(args[:1])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return r.stdout.strip()


def _plan_bump(a_dir, stage, base, cur, level):
    """-> (old, new, how) for a fresh bump; how is '' when the files already sit at the Plan.md goal (new = goal)."""
    goal = _goal_version(a_dir, stage)
    if goal and _ver(goal) > _ver(base) and cur == goal:
        return cur, goal, ""
    if goal and _ver(goal) > _ver(cur):
        return cur, goal, "goal bump"
    return cur, _bump(cur, level), f"{level} bump"


def _release_ref(repo, cfg):
    """-> the ref to compare against: origin/<release branch> after a fresh fetch, else the local branch."""
    rname = release_branch(repo, cfg)
    if _git(repo, "remote", "get-url", "origin").returncode == 0:  # read the release head as origin has it now
        f = _git(repo, "-c", pr_mode.GH_CRED, "fetch", "-q", "origin", rname, timeout=GIT_NETWORK_TIMEOUT_S)
        if f.returncode:
            raise Blocked(f"fetch of origin/{rname} failed: {(f.stderr or f.stdout).strip()[-200:]}")
    rel = f"origin/{rname}"
    if _git(repo, "rev-parse", "--verify", "-q", f"{rel}^{{commit}}").returncode:  # local-only repo: no remote ref, use the local release branch
        rel = rname
    return rel


def _version_bump(a_dir, repo, cfg, stage, br):
    """-> note. Bumps the version on the stage branch (last phase only, before the PR is marked ready) through the project's `release_script`: `current [REF]` and `set OLD NEW ENTRY`. Re-running completes a partial bump in a follow-up commit."""
    if _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != br:
        raise Blocked(f"{repo} is not on {br}: check out the stage branch, then retry version-bump")
    if _landed(repo, release_branch(repo, cfg), br, False) == "":
        return f"{br} adds nothing to {release_branch(repo, cfg)}: no version bump"
    if _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise Blocked(f"{repo} has uncommitted changes: commit or stash them, then retry version-bump")
    rel = _release_ref(repo, cfg)
    base = _hook(repo, cfg, "current", rel)
    cur = _hook(repo, cfg, "current")
    have = bool(_git(repo, "log", "--grep", r"^chore(release): v[0-9]", "--format=%s", "-1", f"{rel}..{br}").stdout.strip())
    level = _release_level(a_dir, stage)
    if have and cur != base:
        old, new, how = base, cur, "completed"  # a bump commit exists: the script reads the new version already
    else:
        old, new, how = _plan_bump(a_dir, stage, base, cur, level)
        if not how:
            return f"version files already at the goal v{new}: no second bump"
    files = [f for f in _hook(repo, cfg, "set", old, new, _merge_title(a_dir, stage, br)).splitlines() if f]
    files += [f for f in _rewrite_pins(repo, cfg, new) if f not in files]
    if not files:
        return f"version files already at v{new}"
    _git(repo, "add", "--", *files)
    r = _git(repo, "commit", "-m", f"chore(release): v{new}" + (" (complete version files)" if have else ""))
    if r.returncode:
        raise Blocked(f"bump commit failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"{how} v{old} -> v{new} ({', '.join(files)})"


_SEMVER = r"\d+\.\d+\.\d+"


def _already_released(repo, cfg, rel, br):
    """-> the no-release note when nothing of the stage landed and the release head is already tagged, else None."""
    if _landed(repo, rel, br, False) != "":  # nothing of this stage landed: release only if the release branch head has no tag yet
        return None
    cur = _hook(repo, cfg, "current", rel)
    if re.fullmatch(_SEMVER, cur) and _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/v{cur}").returncode == 0:
        return f"{br} adds nothing to {rel} and v{cur} is already tagged: no release"
    return None


def _merged_sha(repo, br, rel):
    """-> the squash SHA of the merged PR of br, fetched when it is not local."""
    sha = (_view(repo, br, "mergeCommit") or {}).get("oid")
    if _view(repo, br, "state") != "MERGED" or not sha:
        raise Blocked(f"the PR of {br} is not MERGED: finish master-merged first")
    if _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode:  # the squash was made on GitHub: fetch it before tagging
        f = _git(repo, "-c", pr_mode.GH_CRED, "fetch", "-q", "origin", rel, timeout=GIT_NETWORK_TIMEOUT_S)
        if f.returncode:
            raise Blocked(f"fetch of origin/{rel} failed, the merge commit {sha[:8]} is not local: {(f.stderr or f.stdout).strip()[-200:]}")
    return sha


def _ensure_tag(repo, tag, sha):
    """Create the annotated tag on sha; an existing tag on another commit blocks."""
    if _git(repo, "rev-parse", "-q", "--verify", f"refs/tags/{tag}").returncode == 0:
        if _git(repo, "rev-parse", f"{tag}^{{commit}}").stdout.strip() != sha:
            raise Blocked(f"tag {tag} already exists on another commit: resolve by hand, then skip release")
        return
    t = _git(repo, "tag", "-a", tag, sha, "-m", tag)
    if t.returncode:
        raise Blocked(f"git tag failed: {(t.stderr or t.stdout).strip()[-300:]}")


def _github_release(repo, tag, ver, sha):
    """Push the tag and create the GitHub release (notes from the CHANGELOG section) -> note."""
    p = _git(repo, "-c", pr_mode.GH_CRED, "push", "origin", tag, timeout=GIT_NETWORK_TIMEOUT_S)
    if p.returncode:
        raise Blocked(f"push of {tag} failed: {(p.stderr or p.stdout).strip()[-300:]}")
    cl = Path(repo, "CHANGELOG.md")
    notes = "--generate-notes"
    body = ""
    if cl.is_file():
        body = (section_body(cl.read_text(encoding="utf-8"), lambda ln: ln.startswith(f"## [{ver}]"), "## [") or "").strip()
    args = ["gh", "release", "create", tag, "--verify-tag", "--title", tag] + (["--notes", body] if body else [notes])
    if _run(["gh", "release", "view", tag], repo).returncode == 0:
        return f"tagged {tag} on {sha[:8]}; release already exists"
    r = _run(args, repo, 300)
    if r.returncode:
        raise Blocked(f"gh release create failed: {(r.stderr or r.stdout).strip()[-300:]}")
    return f"released {tag} on {sha[:8]}"


def _release(a_dir, repo, cfg, stage, br, pr):
    """-> note. Tags the landed commit vX.Y.Z (the squash SHA of the merged PR; the release branch head for a local repo), pushes the tag and creates the GitHub release."""
    rel = release_branch(repo, cfg)
    if note := _already_released(repo, cfg, rel, br):
        return note
    sha = _merged_sha(repo, br, rel) if pr else _git(repo, "rev-parse", rel).stdout.strip()
    ver = _hook(repo, cfg, "current", sha)  # the version the landed commit holds
    if not re.fullmatch(_SEMVER, ver):
        raise Blocked(f"release_script current {sha[:8]} printed {ver!r}, not X.Y.Z")
    tag = f"v{ver}"
    _ensure_tag(repo, tag, sha)
    if not pr:
        return f"tagged {tag} on {sha[:8]} (local-only repo, no release)"
    return _github_release(repo, tag, ver, sha)


def _cleanup(a, repo, cfg, br):
    if _git(repo, "rev-parse", "--git-dir").returncode:
        return "no git repo: nothing to clean"
    exists = _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode == 0
    if (
        exists and br != (rel := release_branch(repo, cfg)) and (why := _landed(repo, rel, br, _has_origin(repo)))
    ):  # before wt.py clean: it force-deletes the job branches
        raise Blocked(why)
    if cfg.get("worktrees"):
        w = _run([sys.executable, "-I", os.path.join(TOOLS, "wt.py"), "clean", "--stage", a.stage, "--dir", a.dir], a.dir, 300)
        if w.returncode:
            raise Blocked(f"wt.py clean failed: {(w.stderr or w.stdout).strip()[-300:]}")
    res = _clean_repo(a, repo, cfg, br, True)
    for n in worktree.nested_repos(repo):  # local-only nested repos: no PR, no remote steps
        sub = os.path.join(repo, n)
        if _git(sub, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
            continue
        try:
            res += f"; [{n}] " + _clean_repo(a, sub, cfg, br, False)
        except Blocked as e:
            raise Blocked(f"nested repo {n}: {e}") from None
    return res


def _to_release_branch(repo, rel, cur):
    """Switch to the release branch (or create it from origin) -> note or None."""
    if cur == rel:
        return None
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{rel}").returncode == 0:
        r = _git(repo, "switch", rel)
        if r.returncode:
            raise Blocked(f"cannot switch to {rel}: {r.stderr.strip()[-200:]}")
        return f"on {rel}"
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode == 0:  # no local release branch yet: create it from origin
        r = _git(repo, "switch", "-c", rel, f"origin/{rel}")
        if r.returncode:
            raise Blocked(f"cannot create {rel} from origin/{rel}: {r.stderr.strip()[-200:]}")
        return f"created {rel} from origin/{rel}"
    return None


def _prune_origin(repo, br, rel):
    """Delete origin/br, fetch --prune, fast-forward the release branch -> notes."""
    out = []
    if _git(repo, "ls-remote", "--exit-code", "--heads", "origin", br).returncode == 0:
        r = _git(repo, "-c", pr_mode.GH_CRED, "push", "origin", "--delete", br, timeout=GIT_LONG_TIMEOUT_S)
        if r.returncode:
            raise Blocked(f"origin/{br} not deleted: {r.stderr.strip()[-200:]}")
        out.append(f"deleted origin/{br}")
    r = _git(repo, "-c", pr_mode.GH_CRED, "fetch", "--prune", "origin", timeout=GIT_NETWORK_TIMEOUT_S)
    out.append("fetched" if r.returncode == 0 else f"fetch failed: {r.stderr.strip()[-120:]}")
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode == 0:
        r = _git(repo, "merge", "--ff-only", f"origin/{rel}")
        out.append(f"{rel} fast-forwarded" if r.returncode == 0 else f"{rel} not fast-forwarded: {r.stderr.strip()[-120:]}")
    return out


def _clean_repo(a, repo, cfg, br, main):
    rel, out = release_branch(repo, cfg), []
    exists = _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode == 0
    if exists and br != rel and (why := _landed(repo, rel, br, main and _has_origin(repo))):
        raise Blocked(why)
    cur = _git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if note := _to_release_branch(repo, rel, cur):
        out.append(note)
    if exists and br != rel:
        r = _git(repo, "branch", "-D", br)
        if r.returncode:
            raise Blocked(f"could not delete {br}: {r.stderr.strip()[-200:]}")
        out.append(f"deleted {br}")
    if main and pr_mode.has_origin(Run(dir=a.dir)):
        out += _prune_origin(repo, br, rel)
    _git(repo, "worktree", "prune")
    return "; ".join(out) or "already clean"


def _squash_commit(repo, name, br, a_dir):
    """Commit the staged squash through commit.py -> (completed process, subject)."""
    subject = f"chore({name.split('/phases/')[0].split('/')[-1]}): squash stage branch {br}"  # area has no slash
    sf = os.path.join(a_dir, WORK_DIR, name, "SUBJECT")
    if os.path.isfile(sf):
        with open(sf, encoding="utf-8") as f:
            subject = next((x.strip() for x in f if x.strip()), "") or subject
    c = _run(
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
        _git(repo, "switch", was)


def _local_merge_repo(repo, cfg, br, name, a_dir):
    rel = release_branch(repo, cfg)
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{br}").returncode:
        return f"no branch {br}: skipped"
    if br == rel or _landed(repo, rel, br, False) == "":
        return f"{br} adds nothing to {rel}: already merged"
    # untracked files count too (a squash can overwrite them), except the pipeline's own folders
    if _git(repo, "status", "--porcelain", "--", ".", ":(exclude).work", ":(exclude).wt", ":(exclude).audits").stdout.strip():
        raise Blocked("working tree is dirty: commit or stash first")
    was = _git(repo, "branch", "--show-current").stdout.strip()
    r = _git(repo, "switch", rel)
    if r.returncode:
        raise Blocked(f"cannot switch to {rel}: {r.stderr.strip()[-200:]}")
    # the repo stays on `rel` after a good merge (loud note below); a failed one goes back to where the user was
    r = _git(repo, "merge", "--squash", br)
    if r.returncode:
        files = _git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        _git(repo, "reset", "--merge")
        _back(repo, was, rel)
        raise Blocked(f"conflict squashing {br} into {rel}; files: {', '.join(files) or r.stdout.strip()[-200:]}")
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
        return f"{br} brings no change to {rel}: already merged"
    c, subject = _squash_commit(repo, name, br, a_dir)
    if c.returncode:
        _git(repo, "reset", "--merge")
        _back(repo, was, rel)
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
    m = _run(["gh", "pr", "merge", str(n), "--squash", "--delete-branch"], repo, 300)
    if m.returncode == 0:
        return 1, 0, f"#{n} merged: {title}"
    return 0, 1, f"#{n} skipped (merge failed: {(m.stderr or m.stdout).strip()[-120:]}): {title}"


def _sweep_ff(repo, rel):
    """Fetch and fast-forward the release branch -> note ("" when nothing to say)."""
    f = _git(repo, "-c", pr_mode.GH_CRED, "fetch", "--prune", "origin", timeout=GIT_NETWORK_TIMEOUT_S)
    if f.returncode:
        return "fetch failed: " + f.stderr.strip()[-120:]
    if _git(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{rel}").returncode:
        return ""
    cur = _git(repo, "symbolic-ref", "--short", "-q", "HEAD").stdout.strip()
    if cur == rel:
        u = _git(repo, "merge", "--ff-only", f"origin/{rel}")
    elif _git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{rel}").returncode == 0:
        u = _git(repo, "fetch", ".", f"origin/{rel}:{rel}")
    else:
        return ""
    return f"{rel} fast-forwarded" if u.returncode == 0 else f"{rel} not fast-forwarded: {u.stderr.strip()[-120:]}"


def _sweep(repo, cfg):
    rel = release_branch(repo, cfg)
    listing = ["gh", "pr", "list", "--author", "app/dependabot", "--state", "open", "--json"]
    r = _run([*listing, "number,title,statusCheckRollup"], repo)
    token_gap = bool(r.returncode) and CI_DENIED_MSG in r.stdout + r.stderr
    if token_gap:  # the token cannot read check runs: list without them and read the workflow runs of each head
        r = _run([*listing, "number,title,headRefOid"], repo)
    if r.returncode:
        raise Blocked(f"gh pr list failed: {r.stderr.strip()[-300:]}")
    try:
        prs = json.loads(r.stdout or "[]")
    except ValueError:
        prs = []
    if token_gap:
        for p in prs:
            p["statusCheckRollup"] = _ci_runs(repo, p.get("headRefOid"))
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
    ff = _sweep_ff(repo, rel)
    counts = f"{len(prs)} Dependabot PR(s), {merged} merged, {red} skipped red, {pending} skipped pending" + (f", {failed} merge failed" if failed else "")
    lines.append(f"summary: {counts}" + (f"; {ff}" if ff else ""))
    return "\n".join(lines)


def _no_push_note(what, repo, cfg, br):
    """-> why the step has nothing to push or open (no origin, empty stage branch, work only in nested repos), else ""."""
    if (
        what in ("open", "push", "ready", "merge") and not _has_origin(repo) and (what != "merge" or _view(repo, br, "state") not in ("OPEN", "MERGED"))
    ):  # no origin = no PR: the local-merge flow owns the branch
        return "no origin: PR step skipped, the stage branch is merged by local-merge" + _nested_unlanded(repo, cfg, br, "; ")
    if what != "merge" and _empty(repo, cfg, br):  # an empty stage branch is never pushed and gets no PR
        return f"{br} has no commit beyond the release branch: not pushed, no PR"
    if what == "merge" and _empty(repo, cfg, br):
        return f"{br} has no commit beyond the release branch: nothing to merge, nothing pushed" + _nested_unlanded(repo, cfg, br, "; ")
    if what != "merge" and _idle(repo, cfg, br):
        return f"{br} has no commit beyond the release branch in the main repo (work is in nested repos): no PR"
    return ""


def _pr_step(what, a_dir, repo, cfg, stage, br):
    """open/push/ready/merge -> the report text (raises Blocked)."""
    if note := _no_push_note(what, repo, cfg, br):
        return note
    if what == "merge" and _view(repo, br, "state") == "MERGED":  # a re-run: --delete-branch removed the branch, nothing to push
        return _merge(a_dir, repo, cfg, stage, br) + _nested_unlanded(repo, cfg, br, "; ")
    if what == "merge" and _idle(repo, cfg, br):  # stage work only in nested repos: no PR to merge
        return f"{br} adds nothing to the main repo release branch" + _nested_unlanded(repo, cfg, br, "; ")
    _pushed(repo, br)
    if what == "merge":
        return _merge(a_dir, repo, cfg, stage, br) + _nested_unlanded(repo, cfg, br, "; ")
    if what == "ready":
        return _open_pr(a_dir, repo, cfg, stage, br) + "; " + _ready(repo, cfg, br)
    return _open_pr(a_dir, repo, cfg, stage, br)


def run(what, a_dir, stage, name):
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


def sweep_before_branch(a):
    """Run the sweep on the release branch before the work branch is created. -> '' (always; red or pending PRs are no failure, a failed sweep only warns)."""
    if not pr_mode.sweep_active(a):
        return ""
    v, text = run("sweep", a.dir, getattr(a, "stage", None) or pr_mode._sweep_name(a), pr_mode._sweep_name(a))
    print(f"== deps-sweep {v}\n{text}")
    return ""
