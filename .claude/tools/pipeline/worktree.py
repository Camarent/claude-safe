"""Job worktrees: create, commit, merge, remove, review markers. The command line is wt.py."""

import contextlib
import fcntl
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

from . import commit_subject, gitx, util
from .configuration import (
    EV_FORCED_REMOVE,
    FORCED_WHY_MAX,
    GIT_LONG_TIMEOUT_S,
    JOB_BRANCH_PREFIX,
    JOB_DIFF_MAX_CHARS,
    LINK_BASE_FILE,
    MERGE_TREE_MIN_GIT,
    METRICS_LOG,
    ORCH_LOG,
    PROJECT_DEFAULTS,
    WORK_DIR,
    project,
    repo_dir,
    work_branch,
)
from .plan_files import paths_in

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


def nested_repos(repo):
    """Relative paths of git repos nested in `repo` (depth <= 3), not the repo itself."""
    found = []
    base = repo.rstrip(os.sep).count(os.sep)
    for root, dirs, _ in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP]
        if root != repo and (".git" in dirs or os.path.isfile(os.path.join(root, ".git"))):
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


def refresh(a_dir, stage, task, discard=False):
    """Before a retry: rebase the job branch of every repo in the manifest onto its recorded base (the stage branch), so the retry sees merges that landed after the first dispatch. `discard`: first drop uncommitted files (a retry after BLOCKED starts clean). -> {"rebased": [...], "discarded": [...]}; RuntimeError (rebase aborted, nothing resolved) on a conflict."""
    info = live_job(a_dir, stage, task)
    res: dict[str, list] = {"rebased": [], "discarded": []}
    if not info:
        return res
    if is_merged(a_dir, stage, task) and not empty_job(
        a_dir, stage, task
    ):  # own commits already on the stage branch (squash): replaying them would only conflict or re-apply; an empty job still rebases
        mark_merged(a_dir, stage, task)
        return res
    _cfg, repo, _root, _b = layout(a_dir, stage, task)
    for rel, path in _job_worktrees(info).items():
        src = repo if rel == os.path.basename(repo) else os.path.join(repo, rel)
        dirty = git(path, "status", "--porcelain", check=False)
        if dirty and discard:
            git(path, "reset", "--hard", "-q")
            git(path, "clean", "-fdq", *[f"--exclude=/{n}" for n in LINK])  # the symlinks create() made stay
            res["discarded"] += [f"{rel}: {ln.strip()}" for ln in dirty.splitlines()]
        elif dirty:
            continue  # uncommitted work of a live dev: a rebase would fail on it
        base = _base_branch(src, info["base"])
        if base == "HEAD" or git(path, "rev-list", "--count", f"HEAD..{base}", check=False) in ("", "0"):
            continue
        try:
            r = gitx.run(path, "rebase", base, timeout=GIT_LONG_TIMEOUT_S)
            failed, why = r.returncode, (r.stdout + r.stderr).strip()[-300:]
        except subprocess.TimeoutExpired:  # git may still hold the rebase state: abort it below, then report
            failed, why = True, f"timed out after {GIT_LONG_TIMEOUT_S} s"
        if failed:
            with contextlib.suppress(subprocess.SubprocessError):
                gitx.run(path, "rebase", "--abort", timeout=GIT_LONG_TIMEOUT_S)
            raise RuntimeError(f"rebase of {info['branch']} onto {base} in {rel} conflicts or hangs (aborted, not resolved): {why}")
        res["rebased"].append(rel)
    return res


def refresh_stale(a_dir, stage, tasks):
    """Before merge-w<N>: rebase every task's job branch whose base is not an ancestor of the stage tip (a lead fix or an earlier wave landed after it was cut) and whose work is not on the stage branch yet. -> (refreshed task ids, {task: reason} for rebases that conflicted, left as they were: the merge then reports the conflict)."""
    done: list[str] = []
    failed: dict[str, str] = {}
    for t in tasks:
        if is_merged(a_dir, stage, t):
            continue
        try:
            if refresh(a_dir, stage, t)["rebased"]:
                done.append(t)
        except RuntimeError as e:
            failed[t] = str(e)
    return done, failed


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


def _seed_index(src, dst):
    """Copy the main checkout's code index (index.db, ctx-cache.json) into a new worktree: the refresh is by content hash, so only edited files are re-parsed and re-embedded."""
    s, d = os.path.join(src, ".code-index"), os.path.join(dst, ".code-index")
    if os.path.isdir(s) and not os.path.exists(d):
        os.makedirs(d)
        for f in ("index.db", "ctx-cache.json"):
            if os.path.isfile(os.path.join(s, f)):
                shutil.copy2(os.path.join(s, f), os.path.join(d, f))
        pathlib.Path(d, ".gitignore").write_text("*\n")


def _seed_build(src, dst, paths):
    """Each worktree builds into its OWN `target/` (a shared one bakes the first checkout's path into test binaries via env!(CARGO_MANIFEST_DIR): later tests fail with NotFound once that worktree is gone, or main's do). A cold build costs ~20 s. Only the project's `seed_build` paths (pipeline.json) are copied (mtime kept), so tests that need a prebuilt output work before the dev builds."""
    for rel in paths:
        f = os.path.join(src, rel)
        if os.path.isfile(f):
            os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
            shutil.copy2(f, os.path.join(dst, rel))


def _copy_mtimes(src, dst):
    """Give each tracked file of a new worktree the mtime of the same file in the main checkout. A build output linked in from the main checkout (target/) would else look stale next to the fresher sources (web/tests/loader.js checks that)."""
    for rel in git(dst, "ls-files", "-z").split("\0"):
        try:
            st = os.stat(os.path.join(src, rel))
            os.utime(os.path.join(dst, rel), ns=(st.st_atime_ns, st.st_mtime_ns), follow_symlinks=False)
        except OSError:
            pass


def _run_setup(a_dir, stage, task, root, cfg):
    """Run the project's `worktree_setup` commands (pipeline.json; sh -c, cwd = the new worktree) in order. The first failure removes the fresh worktree and raises with its output tail, so no dev starts in a half-ready one."""
    commands = cfg.get("worktree_setup") or []
    limit = float(cfg.get("worktree_setup_timeout") or PROJECT_DEFAULTS["worktree_setup_timeout"])  # a cfg without the key (older callers) gets the default
    for cmd in commands:
        try:
            r = subprocess.run(["sh", "-c", cmd], cwd=root, capture_output=True, text=True, timeout=limit, check=False)
            why = f"exit {r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}" if r.returncode else ""
        except subprocess.TimeoutExpired:
            why = f"timed out after {limit:.0f}s"
        if why:
            remove(a_dir, stage, task, force=True)
            raise RuntimeError(f"worktree_setup `{cmd}` failed in the new worktree of {task} ({why}); the worktree was removed")


def link_private(repo, root, prefix, skip=(), nested=None):
    """Symlink every nested repo below PREFIX (pipeline.json `nested_repo_dir_prefix`; "" = off) that ROOT has no worktree of (SKIP = their relative paths) to the main checkout's copy, so a dev or an acceptance command can read a gitignored private repo. NESTED = the nested repos of REPO when the caller has them already. -> the links made. A link stays only when git ignores it in ROOT (`check-ignore`): otherwise it would show as an untracked path, make the worktree dirty and could be committed, so it is removed again (any project). A write through a link reaches the main checkout: tasks that edit a nested repo must name its files (they get a worktree of their own)."""
    made = []
    for rel in (nested_repos(repo) if nested is None else nested) if prefix else []:
        dst = os.path.join(root, rel)
        if rel.startswith(prefix) and rel not in skip and not os.path.lexists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(os.path.join(repo, rel), dst)
            if gitx.run(root, "check-ignore", "-q", rel).returncode == 0:
                made.append(dst)
            else:
                os.remove(dst)
    return made


def _check_named_dirs(a_dir, stage, task, repo, root, text, fresh, prefix):
    """Stop when the task names a PREFIX<dir> nested repo (pipeline.json `nested_repo_dir_prefix`; "" = off) that no worktree covers: the dev would edit the main checkout. Named only in Done/Test text does not count (data, an export)."""
    if not prefix:
        return
    for m in re.finditer(r"(?<![\w.-])" + re.escape(prefix) + r"([\w.-]+)", text):
        rel = prefix + m.group(1)
        if not any(p == rel or p.startswith(rel + "/") for p in paths_in(text)):
            continue  # named only in Done/Test text: not a repo the dev must work in, nothing to hold
        if os.path.exists(os.path.join(repo, rel)) and not os.path.exists(os.path.join(root, rel)):
            if fresh:  # leave nothing half-made behind; a reused worktree may hold the dev's commits, so keep it
                remove(a_dir, stage, task, force=True)
            raise RuntimeError(f"task {task} names {rel}, but it has no git repo, so no job worktree can hold it: `git init` it (local dev branch) and retry")


def _put_entry(a_dir, stage, task, info):
    with _manifest_lock(a_dir, stage):
        m = read_manifest(a_dir, stage)
        m[task] = info
        _write_manifest(a_dir, stage, m)


def _early_entry(a_dir, stage, task, info):
    """Write the task's first manifest entry before the worktree is added: a crash later in `create` still leaves a worktree `live_job` sees (create rewrites the entry with every repo at its end). A task already in the manifest keeps its entry. -> True when this call made the entry."""
    with _manifest_lock(a_dir, stage):
        m = read_manifest(a_dir, stage)
        if task in m:
            return False
        m[task] = info
        _write_manifest(a_dir, stage, m)
        return True


def _drop_entry(a_dir, stage, task):
    """Remove the task's manifest entry (the worktree could not be added, so no stale entry stays for readers of the manifest)."""
    with _manifest_lock(a_dir, stage):
        m = read_manifest(a_dir, stage)
        if m.pop(task, None) is not None:
            _write_manifest(a_dir, stage, m)


def _add_with_entry(a_dir, stage, task, spec):
    """Write the early manifest entry, then add the main worktree (SPEC = repo, root, branch, want). When the add fails and this call made the entry, the entry goes again: no stale entry stays for readers of the manifest."""
    repo, root, branch, want = spec
    early = _early_entry(a_dir, stage, task, {"branch": branch, "root": root, "base": want, "repos": {os.path.basename(repo): root}})
    try:
        _add(repo, root, branch, want)
    except BaseException:
        if early and not os.path.isdir(root):
            _drop_entry(a_dir, stage, task)
        raise


def _add_nested(repo, root, rel, branch, want):
    """Give the job worktree ROOT a real worktree of the nested repo REL (a link_private view of it is replaced; the link comes back when the worktree cannot be added). -> its path."""
    sub = os.path.join(root, rel)
    target = (
        os.readlink(sub) if os.path.islink(sub) else ""
    )  # a link_private view from an earlier create: the task now names this repo, so it gets a real worktree
    if target:
        os.remove(sub)
    fresh_sub = not os.path.isdir(sub)
    try:
        _add(os.path.join(repo, rel), sub, branch, want)
    except BaseException:
        if target and not os.path.lexists(sub):
            os.symlink(target, sub)
        raise
    if fresh_sub:
        _seed_index(os.path.join(repo, rel), sub)
        _copy_mtimes(os.path.join(repo, rel), sub)
    return sub


def add_repo(a_dir, stage, task, rel):
    """A dev needs a nested repo its task does not list: give the task's job worktree a real worktree of REL (on the job branch) and record it, so commit, review and merge handle it like a listed repo. -> the manifest entry."""
    cfg, repo, root, branch = layout(a_dir, stage, task)
    if rel not in nested_repos(repo):
        raise Blocked(f"{rel} is not a nested git repo of {repo} (known: {', '.join(nested_repos(repo)) or 'none'})")
    with _manifest_lock(a_dir, stage):
        m = read_manifest(a_dir, stage)
        if task not in m or not os.path.isdir(m[task]["root"]):
            raise Blocked(f"task {task} has no job worktree in stage {stage}")
        m[task]["repos"][rel] = _add_nested(repo, root, rel, branch, work_branch(cfg, stage))
        _write_manifest(a_dir, stage, m)
    return m[task]


def _wants_nested(a_dir, stage, task, root, rel, text):
    """True when the task's job worktree ROOT gets a real worktree of the nested repo REL: its brief TEXT names it, or `wt.py add-repo` added it earlier (the manifest and the folder say so)."""
    sub = os.path.join(root, rel)
    added = os.path.isdir(sub) and not os.path.islink(sub) and rel in (read_manifest(a_dir, stage).get(task) or {}).get("repos", {})
    return added or bool(re.search(r"(?<![\w.-])" + re.escape(rel) + r"(?![\w-])", text))  # named by the task, or added later


def create(a_dir, stage, task):
    cfg, repo, root, branch = layout(a_dir, stage, task)
    want = work_branch(cfg, stage)
    nested = nested_repos(repo)
    state = os.path.join(os.path.dirname(root), ".branch")
    if not os.path.isfile(state):  # first call for this task: pick a free name and keep it for every later call (rework, review, merge, remove)
        branch = _unique_branch(repo, stage, task, nested)
        os.makedirs(os.path.dirname(root), exist_ok=True)
        util.write_text(state, branch + "\n")
    fresh = not os.path.isdir(root)
    _add_with_entry(a_dir, stage, task, (repo, root, branch, want))
    if fresh:
        _seed_index(repo, root)
        _seed_build(repo, root, cfg.get("seed_build") or [])
        _copy_mtimes(repo, root)
        _run_setup(a_dir, stage, task, root, cfg)
    for name in LINK:
        src, dst = os.path.join(repo, name), os.path.join(root, name)
        if os.path.isdir(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
        if os.path.islink(dst):
            _exclude_link(root, name)
    text = ""
    tf = os.path.join(a_dir, WORK_DIR, stage, f"{task}.md")
    if os.path.isfile(tf):
        text = pathlib.Path(tf).read_text(encoding="utf-8")
    repos = {os.path.basename(repo): root}
    for rel in nested:
        if _wants_nested(a_dir, stage, task, root, rel, text):
            repos[rel] = _add_nested(repo, root, rel, branch, want)
    _check_named_dirs(a_dir, stage, task, repo, root, text, fresh, cfg.get("nested_repo_dir_prefix") or "")
    link_private(repo, root, cfg.get("nested_repo_dir_prefix") or "", repos, nested)
    info = {"branch": branch, "root": root, "base": want, "repos": repos}
    _put_entry(a_dir, stage, task, info)
    return info


_GIT_VERSION: list = []  # one entry once `git --version` was read in this process


def _git_version():
    """-> (major, minor) of the installed git, read once per process; (0, 0) when the text does not parse."""
    if not _GIT_VERSION:
        text = gitx.try_out(os.curdir, "--version") or ""
        m = re.search(r"(\d+)\.(\d+)", text)
        _GIT_VERSION.append((int(m.group(1)), int(m.group(2))) if m else (0, 0))
    return _GIT_VERSION[0]


def _need_merge_tree():
    """RuntimeError when git is older than MERGE_TREE_MIN_GIT: `merge-tree --write-tree` would fail and every branch would read as not merged."""
    have = _git_version()
    if have != (0, 0) and have < MERGE_TREE_MIN_GIT:
        need = ".".join(map(str, MERGE_TREE_MIN_GIT))
        raise RuntimeError(f"git >= {need} needed for `git merge-tree --write-tree` (merge checks); found {'.'.join(map(str, have))}: upgrade git")


def _unmerged(repo, branch, want, wt=""):
    """Why removing `branch` (and its worktree `wt`) would lose work: uncommitted changes, or a file the branch changed that `want` does not hold with the branch's content (squash merges leave no ancestry, so compare blobs). "" when safe."""
    if wt and os.path.isdir(wt) and _dirty(wt, repo, skip_link=True):
        return f"uncommitted changes in {wt}"
    if not git(repo, "rev-parse", "--verify", "--quiet", branch, check=False):
        return ""
    base = _base_branch(repo, want)
    mb = git(repo, "merge-base", base, branch, check=False)
    if not mb:  # no common ancestor: nothing proves the branch's work is in `base`
        return f"{branch} in {repo} shares no history with {base}"
    # merging the branch into base adds nothing when base already holds its changes (squash merges leave no ancestry);
    # a later commit on base that touches the same file does not matter. A conflict counts as not merged.
    _need_merge_tree()
    try:
        r = gitx.run(repo, "merge-tree", "--write-tree", base, branch, timeout=GIT_LONG_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:  # same wording as git(): say what hung and what to check
        raise RuntimeError(
            f"git merge-tree {base} {branch} in {repo} timed out after {e.timeout:.0f} s; check the repo for a half-done merge before retrying"
        ) from e
    if r.returncode == 0 and r.stdout.split()[:1] == [git(repo, "rev-parse", f"{base}^{{tree}}", check=False)]:
        return ""
    return f"{branch} in {repo} is not merged into {base}"


def skip_blocker(a_dir, stage, task):
    """Why skipping `task` would strand its work: uncommitted changes in its job worktree, or job commits the work branch lacks (`_unmerged`). "" when safe or when the task has no live job worktree. `wt.py merge` lands the work first."""
    if not live_job(a_dir, stage, task):
        return ""
    _cfg, branch, parts = _parts(a_dir, stage, task)
    want = work_branch(_cfg, stage)
    for _rel, r, w in parts:
        why = _unmerged(r, branch, want, w)
        if why:
            return why
    return ""


def empty_job(a_dir, stage, task):
    """True when a task's job worktrees hold nothing to merge: no uncommitted changes and no commits ahead of the stage branch, in every repo. False when a branch is missing (no proof) or there is no live worktree."""
    cfg, branch, parts = _parts(a_dir, stage, task)
    if not parts:
        return False
    want = work_branch(cfg, stage)
    for _rel, r, w in parts:
        if _dirty(w, r) or not git(r, "rev-parse", "--verify", "--quiet", branch, check=False):
            return False
        if git(r, "rev-list", "--count", f"{_base_branch(r, want)}..{branch}") != "0":
            return False
    return True


def settled_job(a_dir, stage, task):
    """True when nothing of the task's job worktrees is left to merge: empty (`empty_job`), its content already in the stage branch (`is_merged`, also after a red merge gate left the branch behind) or hand-merged (`marked_merged`). A done task like this needs no merge: its worktree may go."""
    return empty_job(a_dir, stage, task) or is_merged(a_dir, stage, task) or marked_merged(a_dir, stage, task)


def remove(a_dir, stage, task, force=False):
    """Remove a task's worktrees and branches. Refuses (RuntimeError, nothing removed) when that would lose uncommitted or unmerged work, unless `force`."""
    cfg, repo, root, branch = layout(a_dir, stage, task)
    if not marked_merged(a_dir, stage, task):
        try:
            _refuse_unmerged_removal(a_dir, cfg, stage, task, repo, root, branch)
        except Exception as e:
            if not force:
                raise
            if isinstance(e, RuntimeError):  # a refusal: the discard is real. Any other probe error: --force goes on as it always did
                _log_forced(a_dir, stage, task, str(e))
    done = _remove_worktrees(repo, root, branch)
    with _manifest_lock(a_dir, stage):
        m = read_manifest(a_dir, stage)
        if m.pop(task, None) is not None:
            _write_manifest(a_dir, stage, m)
    _remove_state_files(root)
    return {"removed": done}


def _log_forced(a_dir, stage, task, why):
    """`wt.py remove --force` threw work away: leave an `orch.jsonl` event (what was refused) so a later look can tell a discard from a merge."""
    if os.path.isdir(os.path.join(a_dir, WORK_DIR, stage)):
        with contextlib.suppress(OSError):  # a full or read-only disk must not stop the removal
            util.append_event(os.path.join(a_dir, WORK_DIR, stage, ORCH_LOG), {"ev": EV_FORCED_REMOVE, "job": task, "why": why[:FORCED_WHY_MAX]})


def _refuse_unmerged_removal(a_dir, cfg, stage, task, repo, root, branch):
    for rel in [*nested_repos(repo), ""]:
        r = os.path.join(repo, rel) if rel else repo
        why = _unmerged(r, branch, work_branch(cfg, stage), os.path.join(root, rel) if rel else root)
        if why:
            state = f"base={_base_branch(r, work_branch(cfg, stage))} job={git(r, 'rev-parse', '--short', branch, check=False) or 'gone'} merged-marker={marker_state(a_dir, stage, task)}"
            raise RuntimeError(f"refusing to remove {task}: {why} [{state}]. Merge it first (or --force to discard).")


def _remove_worktrees(repo, root, branch):
    done = []
    for rel in [*nested_repos(repo), ""]:  # nested first: they live inside the root worktree
        r = os.path.join(repo, rel) if rel else repo
        w = os.path.join(root, rel) if rel else root
        if os.path.isdir(w) and not os.path.islink(w):
            git(r, "worktree", "remove", "--force", w, check=False)
        if git(r, "rev-parse", "--verify", "--quiet", branch, check=False):
            git(r, "branch", "-D", branch, check=False)
            done.append(rel or ".")
    return done


def _remove_state_files(root):
    state = os.path.join(os.path.dirname(root), ".branch")
    if os.path.isfile(state):
        os.remove(state)
        marker = os.path.join(os.path.dirname(root), MERGED_MARKER)
        if os.path.isfile(marker):
            os.remove(marker)
        with contextlib.suppress(OSError):
            os.remove(os.path.join(os.path.dirname(root), LINK_BASE_FILE))  # the snapshot belongs to this job: a later job of the same id starts fresh
        with contextlib.suppress(OSError):
            os.rmdir(os.path.dirname(root))


class Conflict(RuntimeError):
    """A squash merge conflicted; the main checkout was reset, nothing is merged for that repo."""


class Blocked(RuntimeError):
    """The merge cannot start (dirty or wrong-branch main checkout, uncommitted job work): not a conflict, a person must look."""


SUBJECT = commit_subject.policy("[a-z]+", r"[\w./-]+", area_required=False, min_tail=3)


def subject_ok(s):
    """True for `<type>(<area>): <what>`, or Revert/Reapply around one (the quoted text must itself be a valid subject). No newline."""
    return commit_subject.subject_ok(s, SUBJECT)


def _parts(a_dir, stage, task):
    """-> (cfg, branch, [(rel, main repo, job worktree)]) for the task's existing worktrees, nested first."""
    cfg, repo, root, branch = layout(a_dir, stage, task)
    out = []
    for rel in [*nested_repos(repo), ""]:
        w = os.path.join(root, rel) if rel else root
        if os.path.isdir(w) and not os.path.islink(w):  # a symlink is a link_private read-only view of the main checkout, never a job worktree
            out.append((rel, os.path.join(repo, rel) if rel else repo, w))
    return cfg, branch, out


def _dirty(path, repo, nested=None, skip_link=False):
    """Changed paths in a checkout; nested worktrees/repos that sit inside it do not count. NESTED = nested_repos(repo) when the caller has it. SKIP_LINK: PATH is a job worktree part, where a symlink is wt.py's read-only view of a private repo's main copy: no job work, and `git status` there would report the main copy's own edits as the job's. A main checkout that is itself a link is still checked."""
    if skip_link and os.path.islink(path):
        return []
    inner = [n + "/" for n in (nested_repos(repo) if nested is None else nested)]
    links = {n for n in LINK if os.path.islink(os.path.join(path, n))}  # wt.py's own symlinks: a `node_modules/` ignore entry does not match a symlink
    return [
        ln
        for ln in git(path, "status", "--porcelain", check=False).split("\n")
        if ln and not (ln.startswith("?? ") and (any(i.startswith(ln[3:]) for i in inner) or ln[3:].rstrip("/") in links))
    ]


PIPELINE_DIRS = (".work/", ".wt/", ".audits/")  # the pipeline's own folders: never a stray edit


def touched_nested(a_dir, stage, nested):
    """-> the nested repos of NESTED that STAGE touches (a Predicted path of its task files or a job manifest repo lies inside), or NESTED whole when the stage has no plan yet."""
    plan = os.path.join(os.path.abspath(a_dir), WORK_DIR, stage)
    try:
        files = [os.path.join(plan, f) for f in os.listdir(plan) if f.endswith(".md")]
    except OSError:
        return nested
    named = {p for f in files for p in paths_in(pathlib.Path(f).read_text(encoding="utf-8", errors="replace"))}
    named |= {rel for job in read_manifest(a_dir, stage).values() for rel in job.get("repos", {})}
    if not named:
        return nested
    return [n for n in nested if any(p == n or p.startswith(n + "/") for p in named)]


def dirty_report(a_dir, stage=None):
    """ "" when the main checkout and every nested repo hold no uncommitted work, else one line naming the files. Run before a pipeline starts: a hung or hand-run agent may have left edits. With STAGE, a nested repo the stage's plan does not touch is not checked."""
    out = []
    nested = nested_repos(a_dir)
    for rel in ["", *(touched_nested(a_dir, stage, nested) if stage else nested)]:
        r = os.path.join(a_dir, rel) if rel else a_dir
        out += [
            f"{rel + ': ' if rel else ''}{ln.strip()}" for ln in _dirty(r, r, None if rel else nested) if not any(ln[3:].startswith(p) for p in PIPELINE_DIRS)
        ]
    if not out:
        return ""
    shown = ", ".join(out[:8]) + (f", +{len(out) - 8} more" if len(out) > 8 else "")
    return f"blocked: uncommitted changes in {a_dir}: {shown}. Commit or drop them (a hung agent may have left them), or rerun with --allow-dirty"


def _regen_docs_index(r, w):
    """Regenerate the docs index of job worktree W (repo R's `docs_dir`) before the commit, so a stale INDEX.md ships with the task. Only when the repo keeps an INDEX.md; a failure is logged and the commit goes on."""
    docs = str(project(r).get("docs_dir") or "").strip("/")
    if not docs or not os.path.isfile(os.path.join(w, docs, "INDEX.md")):
        return
    try:
        res = util.run_py("docs_index.py", "--root", w, "--docs-dir", docs, timeout=util.script_timeout())
        if res.returncode != 0:
            raise RuntimeError(f"docs_index.py exit {res.returncode}: {res.stderr.strip()[:200]}")
    except (OSError, subprocess.SubprocessError, RuntimeError) as ex:
        util.swallowed("worktree._regen_docs_index", ex)


def commit(a_dir, stage, task, subject):
    """Commit every change of the task's worktrees (one commit per repo, nested first) with `<type>(<area>): <what>`. -> {rel: short sha | None (nothing to commit)}."""
    if not subject_ok(subject):
        raise RuntimeError(f"commit subject must look like `<type>(<area>): <what>` (lower-case type, at least 4 chars of text): {subject!r}")
    _cfg, _branch, parts = _parts(a_dir, stage, task)
    out: dict = {}
    for rel, r, w in parts:
        _regen_docs_index(r, w)
        inner = nested_repos(r)
        if not _dirty(w, r, inner):
            out[rel or "."] = None
            continue
        # a nested repo is excluded so it is not added as a gitlink; a gitignored one is already skipped (and git errors on an exclude of it)
        skip = [f":(exclude){n}" for n in inner if gitx.run(w, "check-ignore", "-q", n).returncode != 0]
        git(w, "add", "-A", "--", ".", *skip)
        for name in LINK:
            if os.path.islink(os.path.join(w, name)):
                git(w, "reset", "-q", "--", name)  # a pipeline-made link is never part of a job commit, whatever the .gitignore says
        git(w, "commit", "-q", "-m", subject)
        out[rel or "."] = git(w, "rev-parse", "--short", "HEAD")
    return out


PATCH_CAP = 1_000_000


def _save_patch(a_dir, stage, task, rel, repo, branch, want):
    """Keep the task's own change (merge-base..branch) in `.work/<stage>/patches/` before the squash and the worktree removal, so a retro can still
    rebuild a reviewed diff later. Never fails the merge."""
    try:
        base = _base_branch(repo, want)
        mb = git(repo, "merge-base", base, branch, check=False)
        text = git(repo, "diff", mb, branch, check=False)
        if not mb or not text:
            return
        d = os.path.join(a_dir, WORK_DIR, stage, "patches")
        os.makedirs(d, exist_ok=True)
        name = task + ("__" + rel.replace("/", "_") if rel else "") + ".patch"
        pathlib.Path(d, name).write_text(text[:PATCH_CAP] + "\n", encoding="utf-8")
        _record_scope(a_dir, stage, task, rel, git(repo, "diff", "--name-only", mb, branch, check=False).split("\n"))
    except Exception as ex:  # evidence only, never block a merge
        util.swallowed("worktree._save_patch", ex)


def _abs_tails(p):
    """Suffixes of an absolute path P with two or more parts, longest first: the root of the brief's checkout is unknown, so try each."""
    parts = p.strip("/").split("/")
    return ["/".join(parts[i:]) for i in range(len(parts) - 1)]


def _predicted(f, pre, rel):
    """True when edited file F (relative to its repo REL, "" = the workspace repo) matches a predicted entry; entries are workspace-relative, so a sub-repo file also counts under `REL/F`.
    An absolute entry (not already made relative by `_record_scope`) matches through its tails."""
    names = (f, f"{rel}/{f}") if rel else (f,)
    pre = [t for p in pre for t in (_abs_tails(p) if p.startswith("/") else [p])]
    return any(n == p or ("/" in p and n.endswith("/" + p)) or n.startswith(p + "/") or ("/" + p + "/") in ("/" + n) for n in names for p in pre)


def _record_scope(a_dir, stage, task, rel, edited):
    """Append one `scope` line to `.work/<stage>/metrics.jsonl`: the task's predicted paths, the files the dev edited (merge-base..branch) and the extra ones
    (edited, not predicted; a predicted entry matches itself or any file under it). The retro reads it; nothing gates on it."""
    brief = pathlib.Path(a_dir, WORK_DIR, stage, f"{task}.md")
    predicted = paths_in(brief.read_text(encoding="utf-8", errors="replace")) if brief.is_file() else []
    edited = [f for f in edited if f]
    root = os.path.abspath(a_dir).rstrip("/") + "/"
    pre = [p.rstrip("/") for p in predicted]
    pre = [p[len(root) :] if p.startswith(root) else p for p in pre]
    extra = [f for f in edited if not _predicted(f, pre, rel)]
    rec = {"ev": "scope", "stage": stage, "task": task, "repo": rel or ".", "predicted": predicted, "edited": edited, "extra": extra}
    with open(pathlib.Path(a_dir, WORK_DIR, stage, METRICS_LOG), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def merge(a_dir, stage, task):
    """Squash-merge the task's job branch into `branch_work` of every repo it has a worktree in (nested first), one commit per repo with the branch's own subject.
    -> {rel: "merged" | "nothing"}. Conflict: the main checkout is reset, raises Conflict. Cannot start: raises Blocked. A `.merged` marker (mark_merged) skips the task."""
    cfg, branch, parts = _parts(a_dir, stage, task)
    want = work_branch(cfg, stage)
    if marked_merged(a_dir, stage, task):
        return {"skipped": "marked merged"}
    _check_merge_preconditions(parts, want)  # everything first so a refusal leaves every repo as it was
    out = {}
    for rel, r, _w in parts:
        if not _unmerged(r, branch, want):
            out[rel or "."] = "nothing"
            continue
        _save_patch(a_dir, stage, task, rel, r, branch, want)
        sq = gitx.run(r, "merge", "--squash", branch, timeout=GIT_LONG_TIMEOUT_S)
        if sq.returncode:
            git(r, "reset", "--hard", "HEAD", check=False)
            raise Conflict(f"conflict merging {branch} into {want} in {r}: {(sq.stdout + sq.stderr).strip()[-300:]}")
        out[rel or "."] = _commit_squash(r, branch)
    return out


def _check_merge_preconditions(parts, want):
    for _rel, r, w in parts:
        if _dirty(w, r):
            raise Blocked(f"uncommitted changes in job worktree {w}: the dev must run wt.py commit")
        if git(r, "branch", "--show-current", check=False) != want:
            raise Blocked(f"{r} is not on `{want}`")
        dirty = _dirty(r, r)
        if dirty:
            shown = ", ".join(ln.strip() for ln in dirty[:8]) + (f", +{len(dirty) - 8} more" if len(dirty) > 8 else "")
            raise Blocked(
                f"main checkout {r} has uncommitted changes: {shown}. Commit them, or park them with `git -C {r} stash -u` and `git -C {r} stash pop` after the merge"
            )


def _commit_squash(r, branch):
    if not git(r, "diff", "--cached", "--name-only"):
        return "nothing"
    c = gitx.run(r, "commit", "-q", "-m", git(r, "log", "-1", "--format=%s", branch), timeout=GIT_LONG_TIMEOUT_S)
    if c.returncode:
        git(r, "reset", "--hard", "HEAD", check=False)
        raise Blocked(f"commit in {r} refused: {(c.stdout + c.stderr).strip()[-300:]}")
    return "merged"


MERGED_MARKER = ".merged"
MERGED_MIN_SHA = 7  # shortest abbreviated sha a marker head may have and still be compared by prefix
MERGED_LEGACY = "1"  # marker text of an older mark_merged: no job branch heads recorded, so it cannot be checked


def _job_heads(a_dir, stage, task):
    """-> {repo rel ("." for the main repo): full head sha of the job branch} for the task's worktrees (a short sha can grow as the repo gains objects, so the same commit would compare unequal)."""
    _cfg, branch, parts = _parts(a_dir, stage, task)
    return {rel or ".": git(r, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", check=False) for rel, r, _w in parts}


def _same_head(recorded, now):
    """True when two shas name the same commit although one may be abbreviated (markers written before the full sha was stored)."""
    return isinstance(recorded, str) and len(recorded) >= MERGED_MIN_SHA and bool(now) and (now.startswith(recorded) or recorded.startswith(now))


def marker_state(a_dir, stage, task):
    """-> "none" (no marker), "valid" (the marker's job branch heads are unchanged, or it is an older marker without heads) or "stale" (a head moved, the marker is unreadable or no worktree is left to compare)."""
    path = os.path.join(os.path.dirname(layout(a_dir, stage, task)[2]), MERGED_MARKER)
    if not os.path.isfile(path):
        return "none"
    text = util.read_text(path).strip()
    if text == MERGED_LEGACY:
        return "valid"
    try:
        recorded = json.loads(text)
    except ValueError:  # a torn or hand-edited marker proves nothing
        return "stale"
    heads = _job_heads(a_dir, stage, task)
    same = isinstance(recorded, dict) and set(recorded) == set(heads) and all(_same_head(recorded[k], heads[k]) for k in heads)
    return "valid" if heads and same else "stale"


def marked_merged(a_dir, stage, task):
    """True while the task's `.merged` marker is valid (`marker_state`): it records the job branch head of every repo and counts only while each head is unchanged, so a rework commit made after a hand merge is merged and guarded again. Mark a hand merge last, right before the branch and worktree are removed."""
    return marker_state(a_dir, stage, task) == "valid"


def mark_merged(a_dir, stage, task):
    """The task's work was merged by hand (a conflict fix): later `merge` skips it and `remove` stops guarding it, while the job branches keep the heads they have now (`marked_merged`)."""
    base = os.path.dirname(layout(a_dir, stage, task)[2])
    os.makedirs(base, exist_ok=True)
    util.write_text(os.path.join(base, MERGED_MARKER), json.dumps(_job_heads(a_dir, stage, task), sort_keys=True) + "\n")
    return {"marked": task}


def is_merged(a_dir, stage, task):
    """True when every job branch exists and the work branch already holds its tree (merge-tree check, uncommitted changes count as not merged). A `.merged` marker alone proves nothing (it may be stale), and a missing branch is not merged. False when it cannot tell (no worktree)."""
    cfg, branch, parts = _parts(a_dir, stage, task)
    want = work_branch(cfg, stage)
    return bool(parts) and all(git(r, "rev-parse", "--verify", "--quiet", branch, check=False) and not _unmerged(r, branch, want, w) for _rel, r, w in parts)


def diff(a_dir, stage, task, limit=JOB_DIFF_MAX_CHARS):
    """The task's work for review: per repo, the stat and the diff `branch_work...branch`, cut at `limit` characters per repo."""
    cfg, branch, parts = _parts(a_dir, stage, task)
    want = work_branch(cfg, stage)
    out = []
    for rel, r, w in parts:
        b = _base_branch(r, want)
        stat = git(w, "diff", "--stat", f"{b}...{branch}", check=False)
        if not stat:
            continue
        body = git(w, "diff", f"{b}...{branch}", check=False)
        cut = f"\n[... cut at {limit} characters; full diff: git -C {w} diff {b}...{branch}]" if len(body) > limit else ""
        out.append(f"=== repo {rel or '.'} ({w}, branch {branch}) ===\n{stat}\n\n{body[:limit]}{cut}")
    return "\n\n".join(out) or "(no committed changes on the job branch)"


def clean(a_dir, stage):
    """Remove every job worktree and branch of a stage, then `.wt/<stage>` and stale worktree records. Only after the stage is merged: unmerged branches are deleted too."""
    _cfg, repo, _root, _b = layout(a_dir, stage, "x")
    out = {t: remove(a_dir, stage, t, force=True)["removed"] for t in list_wt(a_dir, stage)}
    base = os.path.join(os.path.abspath(a_dir), ".wt", stage)
    if os.path.isdir(base):
        shutil.rmtree(base, ignore_errors=True)
    with contextlib.suppress(OSError):
        os.rmdir(os.path.dirname(base))  # .wt itself, when empty
    for r in [repo, *[os.path.join(repo, n) for n in nested_repos(repo)]]:
        git(r, "worktree", "prune", check=False)
    return {"cleaned": out}


def review_marker(a_dir, stage, task):
    """The review-base marker lives next to the job folder, outside every repo root, so it never shows as dirty."""
    return os.path.join(os.path.abspath(a_dir), ".wt", stage, f"{task}.review-base")


def review_begin(a_dir, stage, task):
    """Before the reviewer edits for mutation checks: record the HEAD of each job worktree. Blocked (RuntimeError naming the files) when one is dirty or there is none."""
    _cfg, _branch, parts = _parts(a_dir, stage, task)
    if not parts:
        raise RuntimeError(f"task {task} has no job worktree to review")
    marker = review_marker(a_dir, stage, task)
    if os.path.isfile(marker):  # an earlier run died before its reset: put that state back first, then check
        review_reset(a_dir, stage, task)
    dirty = [f"{rel + ': ' if rel else ''}{ln.strip()}" for rel, r, w in parts for ln in _dirty(w, r)]
    if dirty:
        raise Blocked(f"job worktree of {task} has uncommitted changes, the reviewer would not be able to reset it: {', '.join(dirty[:8])}")
    shas = {rel: git(w, "rev-parse", "HEAD") for rel, _r, w in parts}
    pathlib.Path(marker).write_text(json.dumps({"shas": shas, "time": time.time()}), encoding="utf-8")
    return {"shas": shas}


def review_reset(a_dir, stage, task):
    """Undo whatever the reviewer did in the job worktrees: reset --hard to the recorded HEAD, clean -fd (no -x: ignored build/seed files stay), verify.
    RuntimeError when HEAD or the status is still wrong (the marker stays). No marker = nothing to do. Idempotent."""
    marker = review_marker(a_dir, stage, task)
    if not os.path.isfile(marker):
        return {"reset": False}
    try:
        shas = json.loads(pathlib.Path(marker).read_text(encoding="utf-8"))["shas"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"review marker {marker} is unreadable ({e}); reset the job worktree by hand") from e
    _cfg, _branch, parts = _parts(a_dir, stage, task)
    for rel, r, w in parts:
        sha = shas.get(rel)
        if not sha:
            continue
        git(w, "reset", "--hard", "-q", sha)
        git(w, "clean", "-fdq", *[f"--exclude=/{n}" for n in LINK])  # the symlinks create() made stay
        head, left = git(w, "rev-parse", "HEAD", check=False), _dirty(w, r)
        if head != sha or left:
            raise RuntimeError(
                f"review reset of {w} failed: HEAD {head[:9] or '?'} (want {sha[:9]}), left: {', '.join(x.strip() for x in left[:8]) or 'nothing'}"
            )
    os.remove(marker)
    return {"reset": True}


def review_pending(a_dir, stage):
    """Tasks of a stage whose review marker is still there (the reviewer or the driver died before the reset)."""
    base = os.path.join(os.path.abspath(a_dir), ".wt", stage)
    return sorted(f[: -len(".review-base")] for f in os.listdir(base) if f.endswith(".review-base")) if os.path.isdir(base) else []


def list_wt(a_dir, stage):
    base = os.path.join(os.path.abspath(a_dir), ".wt", stage)
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
