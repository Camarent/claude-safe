"""Job worktree base: git helpers, nested repo discovery, job layout and manifest. Split from worktree.py; pipeline.worktree re-exports every name."""

import contextlib
import fcntl
import json
import os
import pathlib
import subprocess

from . import gitx, util
from .configuration import (
    GIT_LONG_TIMEOUT_S,
    JOB_BRANCH_PREFIX,
    WORK_DIR,
    project,
    repo_dir,
)

SKIP = ("node_modules", "target", WORK_DIR, ".cache", "dist", ".wt", ".venv", "__pycache__", ".code-index", "vendor")


LINK = ("node_modules",)


def git(repo, *args, check=True):
    return gitx.out(repo, *args, timeout=GIT_LONG_TIMEOUT_S, check=check)


def _exclude_link(root, name):
    """Make git ignore the pipeline-made symlink NAME in ROOT: a `name/` rule of the project's .gitignore matches a directory only, so `git add -A` would commit the link (an absolute, machine-local target). Appends NAME (no trailing slash) to the exclude file of the checkout (`git rev-parse --git-path info/exclude`); idempotent."""
    path = git(root, "rev-parse", "--git-path", "info/exclude")
    path = path if os.path.isabs(path) else os.path.join(root, path)
    old = pathlib.Path(path).read_text(encoding="utf-8") if os.path.isfile(path) else ""
    if name in old.splitlines():
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pathlib.Path(path).write_text(old + ("" if not old or old.endswith("\n") else "\n") + name + "\n", encoding="utf-8")


def _looks_like_repo(root):
    """True when ROOT/.git is a repo folder (has HEAD) or a gitfile (`gitdir: ...`); no git call."""
    dot = os.path.join(root, ".git")
    if os.path.isdir(dot):
        return os.path.isfile(os.path.join(dot, "HEAD"))
    try:
        return pathlib.Path(dot).read_text(encoding="utf-8", errors="replace").startswith("gitdir:")
    except OSError:
        return False


def _stray_ignored(repo, root):
    """True when ROOT is git-ignored by REPO and its `.git` is a stray empty folder or file, not a valid repository. A real repo stays nested even when an exclude lists it, and costs no git call. Any git error (REPO not a repo): False, the old behaviour."""
    if _looks_like_repo(root):
        return False
    try:
        return gitx.run(repo, "check-ignore", "-q", "--", os.path.relpath(root, repo)).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def nested_repos(repo):
    """Relative paths of git repos nested in `repo` (depth <= 3), not the repo itself. A git-ignored dir whose `.git` is a stray empty folder or file (no valid repo) is neither returned nor walked."""
    found = []
    base = repo.rstrip(os.sep).count(os.sep)
    for root, dirs, _ in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP]
        if root != repo and (".git" in dirs or os.path.isfile(os.path.join(root, ".git"))):
            if not _stray_ignored(repo, root):
                found.append(os.path.relpath(root, repo))
            dirs[:] = []
        dirs[:] = [d for d in dirs if d != ".git"]  # seen for the check above; never walk into the repo's own object store
        if root.count(os.sep) - base >= 3:  # a repo at depth 3 is found above; nothing is searched below it
            dirs[:] = []
    return found


def nested_repo_of(path, repos):
    """-> the relative dir in REPOS (nested_repos of a repo) that is the longest proper ancestor dir of the repo-relative PATH, else ""."""
    parts = path.rstrip("/").split("/")[:-1]
    for n in range(len(parts), 0, -1):
        sub = "/".join(parts[:n])
        if sub in repos:
            return sub
    return ""


def _base_branch(repo, want):
    return want if git(repo, "rev-parse", "--verify", "--quiet", want, check=False) else "HEAD"


def _catch_up(repo, path, want):
    """A reused worktree whose branch is a clean ancestor of `want` (no commits of its own, base moved on, e.g. after a wave merge): fast-forward it, so a retry sees the merged tree. Rework branches with own commits or uncommitted changes stay as they are."""
    base = _base_branch(repo, want)
    if git(path, "status", "--porcelain", check=False):
        return
    anc = gitx.run(path, "merge-base", "--is-ancestor", "HEAD", base, timeout=GIT_LONG_TIMEOUT_S)
    if anc.returncode:  # the branch holds commits `want` lacks: keep it
        return
    head, tip = git(path, "rev-parse", "HEAD", check=False), git(repo, "rev-parse", base, check=False)
    if head and tip and head != tip:
        git(path, "merge", "--ff-only", base, check=False)


def _add(repo, path, branch, want):
    if os.path.isdir(path) and not os.path.islink(path):
        _catch_up(repo, path, want)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if git(repo, "rev-parse", "--verify", "--quiet", branch, check=False):
        git(repo, "worktree", "add", path, branch)  # branch left by an earlier run: reuse it
    else:
        git(repo, "worktree", "add", "-b", branch, path, _base_branch(repo, want))


def _job_worktrees(info):
    """-> {rel: path} of the manifest entry's repos without symlinks: a link is a link_private view of the main checkout, never a job worktree."""
    return {rel: path for rel, path in info["repos"].items() if not os.path.islink(path)}


def layout(a_dir, stage, task):
    cfg = project(a_dir)
    repo = os.path.abspath(repo_dir(a_dir, cfg))
    root = os.path.join(os.path.abspath(a_dir), ".wt", stage, task, os.path.basename(repo))
    state = os.path.join(os.path.dirname(root), ".branch")
    text = pathlib.Path(state).read_text().strip() if os.path.isfile(state) else ""
    branch = text or f"{JOB_BRANCH_PREFIX}{stage}-{task}"  # an empty file (torn write) is no branch name
    return cfg, repo, root, branch


def _unique_branch(repo, stage, task, nested):
    """`job/<stage>-<task>`, with -2, -3 ... added while a branch of that name exists in the repo or a nested repo (a leftover or a reused stage name)."""
    repos = [repo, *[os.path.join(repo, r) for r in nested]]
    n, name = 1, f"{JOB_BRANCH_PREFIX}{stage}-{task}"
    while any(git(r, "rev-parse", "--verify", "--quiet", name, check=False) for r in repos):
        n += 1
        name = f"{JOB_BRANCH_PREFIX}{stage}-{task}-{n}"
    return name


def _manifest(a_dir, stage):
    return os.path.join(os.path.abspath(a_dir), ".wt", stage, "manifest.json")


def read_manifest(a_dir, stage):
    """The persisted structure of a stage's job worktrees: {task: {branch, base, root, repos: {relpath: path}}}. No file -> {}; a file that does not parse is an error (an empty dict would forget every job)."""
    p = _manifest(a_dir, stage)
    if not os.path.isfile(p):
        return {}
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"manifest {p} unreadable ({e}): restore it or remove the stage's .wt folder") from e


@contextlib.contextmanager
def _manifest_lock(a_dir, stage):
    """Exclusive lock around a manifest read-modify-write (create and remove may run in two processes)."""
    p = _manifest(a_dir, stage) + ".lock"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def live_job(a_dir, stage, task):
    """The manifest entry of a task whose job worktree still exists on disk, else None."""
    info = read_manifest(a_dir, stage).get(task)
    return info if info and os.path.isdir(info["root"]) else None


def _write_manifest(a_dir, stage, data):
    p = _manifest(a_dir, stage)
    if data:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        util.write_text(p, json.dumps(data, indent=1))
    elif os.path.isfile(p):
        os.remove(p)
