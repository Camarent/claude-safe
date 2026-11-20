"""Release and work branch detection from git, with the per-process caches."""

import fnmatch
import os
import subprocess

from .defaults import PROJECT_DEFAULTS
from .env import GIT_SAFE_ENV
from .limits import GIT_QUICK_TIMEOUT_S

DEFAULT_BRANCH_CANDIDATES = (
    "main",
    "master",
)  # release branch detection order after origin/HEAD and init.defaultBranch; the last one is also the answer outside a git repo


def _git_out(repo, *args):
    try:
        r = subprocess.run(
            ["git", "-C", repo, *args], capture_output=True, text=True, env={**os.environ, **GIT_SAFE_ENV}, timeout=GIT_QUICK_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _has_branch(repo, name):
    return bool(name) and bool(
        _git_out(repo, "rev-parse", "--verify", "-q", f"refs/heads/{name}") or _git_out(repo, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{name}")
    )


JOB_BRANCH_PREFIX = "job/"
MERGE_TREE_MIN_GIT = (2, 38)  # `git merge-tree --write-tree` (worktree._unmerged) needs this git version


def release_guess(repo, cfg):
    """-> (release branch, True when it is only the last-resort default and may not exist) for REPO: cfg `branch_release` when set by the user; else origin/HEAD, `init.defaultBranch` (if it exists), the first existing of DEFAULT_BRANCH_CANDIDATES, the current branch; the last candidate outside a git repo."""
    if cfg.get("branch_release") and not cfg.get("_release_detected"):
        return cfg["branch_release"], False
    head = _git_out(repo, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if head.startswith("refs/remotes/origin/"):
        return head[len("refs/remotes/origin/") :], False
    if _has_branch(repo, d := _git_out(repo, "config", "init.defaultBranch")):
        return d, False
    for n in DEFAULT_BRANCH_CANDIDATES:
        if _has_branch(repo, n):
            return n, False
    cur = _git_out(repo, "symbolic-ref", "--short", "-q", "HEAD")
    if cur and not _is_pipeline_branch(cfg, cur):
        return cur, False
    if cur:  # HEAD is a stage/job branch: the release branch is the one other local branch, else the user must say
        others = [b for b in _git_out(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split() if not _is_pipeline_branch(cfg, b)]
        if len(others) == 1:
            return others[0], False
    return DEFAULT_BRANCH_CANDIDATES[-1], True


def release_branch(repo, cfg):
    """-> the release branch of REPO (see release_guess)."""
    return release_guess(repo, cfg)[0]


GIT_STATE_NAMES = (
    "packed-refs",
    "refs/heads",
    "refs/remotes/origin/HEAD",
    "config",
)  # files under the common git dir that release_guess reads (HEAD sits in the git dir, see _git_state_sig)

GIT_DIRS_CACHE: dict[str, tuple[str, str]] = {}  # abs repo path -> (git dir, common git dir) from `rev-parse`, ("", "") outside a repo; see clear_cache()
GIT_DETECTED_CACHE: dict[str, tuple[tuple, str]] = {}  # abs repo path -> (signature, release branch); see detected_release_branch()


def _git_dirs(repo):
    """-> (git dir, common git dir) of REPO, resolved by one `git rev-parse` per path (worktrees and .git files included)."""
    key = os.path.abspath(repo)
    if key not in GIT_DIRS_CACHE:
        lines = _git_out(key, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir").splitlines()
        GIT_DIRS_CACHE[key] = (lines[0], lines[1]) if len(lines) == 2 else ("", "")
    return GIT_DIRS_CACHE[key]


def _stat_sig(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _git_state_sig(repo):
    """-> the (inode, mtime_ns, size) of every file git reads for the release branch: HEAD (git dir), packed-refs, refs/heads, refs/remotes/origin/HEAD and config (common dir). None when REPO is no repo."""
    gd, cd = _git_dirs(repo)
    if not gd:
        return None
    return tuple(_stat_sig(p) for p in (os.path.join(gd, "HEAD"), *(os.path.join(cd, n) for n in GIT_STATE_NAMES)))


def detected_release_branch(repo, cfg):
    """-> release_branch(REPO, cfg), memoized per process on the git state signature (see _git_state_sig) and cfg `branch_work`: a repo HEAD or branch change shows at once, git is not spawned again while nothing changed."""
    key = os.path.abspath(repo)
    sig = (cfg.get("branch_work"), _git_state_sig(key))
    hit = GIT_DETECTED_CACHE.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    branch = release_branch(key, cfg)
    GIT_DETECTED_CACHE[key] = (sig, branch)
    return branch


def _is_pipeline_branch(cfg, name):
    """-> True when NAME looks like a stage work branch (cfg `branch_work` with `*` for the feature) or a `job/` branch."""
    tpl = cfg.get("branch_work") or PROJECT_DEFAULTS["branch_work"]
    return name.startswith(JOB_BRANCH_PREFIX) or fnmatch.fnmatchcase(name, tpl.replace("{feature}", "*"))


def work_branch(cfg, name):
    """-> the work branch for feature or stage `name`: cfg `branch_work` with `{feature}` filled in (a stage `<feature>/phases/<dir>` counts as its feature). A value without `{feature}` is returned unchanged."""
    tpl = cfg.get("branch_work") or PROJECT_DEFAULTS["branch_work"]
    return tpl.replace("{feature}", str(name).split("/phases/")[0])
