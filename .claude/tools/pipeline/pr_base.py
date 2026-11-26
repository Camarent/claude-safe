"""Git and gh helpers shared by pr_ops.py and pr_release.py: the Blocked error, the command runners, the PR read and the squash title. A leaf: pr_ops and pr_release both import it, neither imports the other."""

import json
import os
import re
import subprocess
from pathlib import Path

from . import gitx, pr_mode
from .configuration import GIT_LONG_TIMEOUT_S, GIT_SAFE_ENV, TASKS_FILE, WORK_DIR


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


TITLE_RE = re.compile(r"^[a-z]+(\([\w./-]+\))?!?: \S.{0,100}$")


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
