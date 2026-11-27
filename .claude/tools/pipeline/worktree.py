"""Job worktrees: create, commit, merge, remove, review markers. The command line is wt.py."""

import contextlib
import fnmatch
import os
import pathlib
import re
import shutil
import subprocess
import time

from . import gitx, util
from .configuration import (
    DOC_PATHS,
    DOCS_WAVE_DIR,
    EV_SETUP,
    GIT_LONG_TIMEOUT_S,
    METRICS_LOG,
    PICK_BACKUP_REF,
    PROJECT_DEFAULTS,
    WORK_DIR,
    work_branch,
)
from .plan_files import paths_in
from .worktree_base import (
    LINK as LINK,
)
from .worktree_base import (
    SKIP as SKIP,
)
from .worktree_base import (
    _add as _add,
)
from .worktree_base import (
    _base_branch as _base_branch,
)
from .worktree_base import (
    _catch_up as _catch_up,
)
from .worktree_base import (
    _exclude_link as _exclude_link,
)
from .worktree_base import (
    _job_worktrees as _job_worktrees,
)
from .worktree_base import (
    _looks_like_repo as _looks_like_repo,
)
from .worktree_base import (
    _manifest as _manifest,
)
from .worktree_base import (
    _manifest_lock as _manifest_lock,
)
from .worktree_base import (
    _stray_ignored as _stray_ignored,
)
from .worktree_base import (
    _unique_branch as _unique_branch,
)
from .worktree_base import (
    _write_manifest as _write_manifest,
)
from .worktree_base import (
    git as git,
)
from .worktree_base import (
    layout as layout,
)
from .worktree_base import (
    live_job as live_job,
)
from .worktree_base import (
    nested_repo_of as nested_repo_of,
)
from .worktree_base import (
    nested_repos as nested_repos,
)
from .worktree_base import (
    read_manifest as read_manifest,
)
from .worktree_merge import (
    _GIT_VERSION as _GIT_VERSION,
)
from .worktree_merge import (
    CONFLICT_HUNK_CHARS as CONFLICT_HUNK_CHARS,
)
from .worktree_merge import (
    MERGED_LEGACY as MERGED_LEGACY,
)
from .worktree_merge import (
    MERGED_MARKER as MERGED_MARKER,
)
from .worktree_merge import (
    MERGED_MIN_SHA as MERGED_MIN_SHA,
)
from .worktree_merge import (
    PATCH_CAP as PATCH_CAP,
)
from .worktree_merge import (
    PIPELINE_DIRS as PIPELINE_DIRS,
)
from .worktree_merge import (
    SUBJECT as SUBJECT,
)
from .worktree_merge import (
    Blocked as Blocked,
)
from .worktree_merge import (
    Conflict as Conflict,
)
from .worktree_merge import (
    _abs_tails as _abs_tails,
)
from .worktree_merge import (
    _apply_modes as _apply_modes,
)
from .worktree_merge import (
    _check_merge_preconditions as _check_merge_preconditions,
)
from .worktree_merge import (
    _commit_squash as _commit_squash,
)
from .worktree_merge import (
    _conflict_hunks as _conflict_hunks,
)
from .worktree_merge import (
    _dirty as _dirty,
)
from .worktree_merge import (
    _driver_holder as _driver_holder,
)
from .worktree_merge import (
    _git_version as _git_version,
)
from .worktree_merge import (
    _job_heads as _job_heads,
)
from .worktree_merge import (
    _log_forced as _log_forced,
)
from .worktree_merge import (
    _need_merge_tree as _need_merge_tree,
)
from .worktree_merge import (
    _parallel_wave as _parallel_wave,
)
from .worktree_merge import (
    _parts as _parts,
)
from .worktree_merge import (
    _predicted as _predicted,
)
from .worktree_merge import (
    _record_scope as _record_scope,
)
from .worktree_merge import (
    _refuse_unmerged_removal as _refuse_unmerged_removal,
)
from .worktree_merge import (
    _regen_docs_index as _regen_docs_index,
)
from .worktree_merge import (
    _remove_state_files as _remove_state_files,
)
from .worktree_merge import (
    _remove_worktrees as _remove_worktrees,
)
from .worktree_merge import (
    _same_head as _same_head,
)
from .worktree_merge import (
    _save_patch as _save_patch,
)
from .worktree_merge import (
    _unmerged as _unmerged,
)
from .worktree_merge import (
    clean as clean,
)
from .worktree_merge import (
    commit as commit,
)
from .worktree_merge import (
    diff as diff,
)
from .worktree_merge import (
    dirty_report as dirty_report,
)
from .worktree_merge import (
    empty_job as empty_job,
)
from .worktree_merge import (
    is_merged as is_merged,
)
from .worktree_merge import (
    list_wt as list_wt,
)
from .worktree_merge import (
    mark_merged as mark_merged,
)
from .worktree_merge import (
    marked_merged as marked_merged,
)
from .worktree_merge import (
    marker_state as marker_state,
)
from .worktree_merge import (
    merge as merge,
)
from .worktree_merge import (
    remove as remove,
)
from .worktree_merge import (
    review_begin as review_begin,
)
from .worktree_merge import (
    review_marker as review_marker,
)
from .worktree_merge import (
    review_pending as review_pending,
)
from .worktree_merge import (
    review_reset as review_reset,
)
from .worktree_merge import (
    settled_job as settled_job,
)
from .worktree_merge import (
    skip_blocker as skip_blocker,
)
from .worktree_merge import (
    subject_ok as subject_ok,
)
from .worktree_merge import (
    touched_nested as touched_nested,
)


class RefreshConflict(RuntimeError):
    """A rebase that failed and was aborted. `files`: the unmerged paths (may be empty on a timeout), `path`: the job worktree, `base`: the branch the rebase targeted, `commits`: the job's own commits not on base ({sha, subject, doc_only}, oldest first), `rebasing`: the abort failed and the job worktree is still mid-rebase (no commits listed then)."""

    def __init__(self, msg, files, path, base, commits=(), rebasing=False, commits_unknown=False):
        super().__init__(msg)
        self.files = files
        self.path = path
        self.base = base
        self.commits = list(commits)
        self.rebasing = rebasing
        self.commits_unknown = commits_unknown  # the commit list timed out: [] then means unknown, not none


def refresh(a_dir, stage, task, discard=False) -> dict[str, list]:
    """Before a retry: rebase the job branch of every repo in the manifest onto its recorded base (the stage branch), so the retry sees merges that landed after the first dispatch. `discard`: first drop uncommitted files (a retry after BLOCKED starts clean). -> {"rebased": [...], "discarded": [...]}; RefreshConflict (a RuntimeError; rebase aborted, nothing resolved) on a conflict."""
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
        if _skip_dirty(path, rel, discard, res):
            continue
        base = _base_branch(src, info["base"])
        if base == "HEAD" or git(path, "rev-list", "--count", f"HEAD..{base}", check=False) in ("", "0"):
            continue
        _rebase_job(path, rel, base, info["branch"])
        res["rebased"].append(rel)
    return res


def pick_commits(a_dir, stage, task, shas) -> dict[str, list]:
    """retry --pick: rebuild the main repo's job branch from its stage tip: the old tip is kept as a backup ref, the branch is reset to the tip and `shas` are cherry-picked in the given order. Every sha must be an own commit of the job (base..HEAD); one that is not is refused before any write. A failed cherry-pick is aborted (no pick state left): the picks made so far stay, and the RuntimeError names the old tip and its backup ref. -> {"discarded": [...], "picked": [...]}"""
    info = live_job(a_dir, stage, task)
    if not info:
        raise RuntimeError(f"no live job for {task}: nothing to pick from")
    _cfg, repo, _root, _b = layout(a_dir, stage, task)
    rel = os.path.basename(repo)
    path = _job_worktrees(info).get(rel)
    if not path:
        raise RuntimeError(f"{task} has no job worktree for {rel}: nothing to pick from")
    base = _base_branch(repo, info["base"])
    own = _own_commits(path, base)
    if own is None:
        raise RuntimeError(f"the job commits of {info['branch']} could not be listed (git timed out): nothing picked")
    own_shas = {c["sha"] for c in own}
    full = []
    for s in shas:
        sha = git(path, "rev-parse", "--verify", "--quiet", f"{s}^{{commit}}", check=False)
        if sha not in own_shas:
            raise RuntimeError(f"--pick refused: {s} is not a commit of {info['branch']} on top of {base} (nothing reset)")
        full.append(sha)
    res: dict[str, list] = {"discarded": [], "picked": []}
    old = git(path, "rev-parse", "HEAD")
    ref = f"{PICK_BACKUP_REF}/{old}"
    git(path, "update-ref", ref, old)  # the old tip survives the reset below
    _skip_dirty(path, rel, True, res)  # uncommitted leftovers of the dead dev go (logged)
    git(path, "reset", "--hard", "-q", base)
    for sha in full:
        r = gitx.run(path, "cherry-pick", sha, timeout=GIT_LONG_TIMEOUT_S)
        if r.returncode:
            why = (r.stdout + r.stderr).strip()[-300:]
            with contextlib.suppress(subprocess.SubprocessError):
                gitx.run(path, "cherry-pick", "--abort", timeout=GIT_LONG_TIMEOUT_S)
            raise RuntimeError(
                f"cherry-pick of {sha} onto {base} in {rel} failed (aborted, not resolved): {why}. The old tip {old} is kept as {ref}; "
                f"git -C {path} reset --hard {old} restores it"
            )
        res["picked"].append(sha)
    return res


def _skip_dirty(path, rel, discard, res) -> bool:
    """True when the job worktree has uncommitted work to keep (a live dev: a rebase would fail on it). `discard`: drop that work first and record it in res["discarded"]."""
    dirty = git(path, "status", "--porcelain", check=False)
    if not dirty:
        return False
    if not discard:
        return True
    git(path, "reset", "--hard", "-q")
    git(path, "clean", "-fdq", *[f"--exclude=/{n}" for n in LINK])  # the symlinks create() made stay
    res["discarded"] += [f"{rel}: {ln.strip()}" for ln in dirty.splitlines()]
    return False


def _rebase_job(path, rel, base, branch) -> None:
    """Rebase the job worktree `path` onto `base`; on a conflict or a timeout abort the rebase and raise RefreshConflict."""
    try:
        r = gitx.run(path, "rebase", base, timeout=GIT_LONG_TIMEOUT_S)
        failed, why = r.returncode, (r.stdout + r.stderr).strip()[-300:]
    except subprocess.TimeoutExpired:  # git may still hold the rebase state: abort it below, then report
        failed, why = True, f"timed out after {GIT_LONG_TIMEOUT_S} s"
    if not failed:
        return
    files = git(path, "diff", "--name-only", "--diff-filter=U", check=False).split()  # the unmerged paths, read before the abort clears them
    if not _abort_rebase(path):  # still mid-rebase: base..HEAD would list a partial pick, so list nothing
        raise RefreshConflict(
            f"rebase of {branch} onto {base} in {rel} conflicts or hangs, and the abort left the rebase in place: {why}", files, path, base, rebasing=True
        )
    commits = _own_commits(path, base)  # the abort leaves HEAD on the job branch (_abort_rebase checks it), so base..HEAD is the job's own work
    raise RefreshConflict(
        f"rebase of {branch} onto {base} in {rel} conflicts or hangs (aborted, not resolved): {why}",
        files,
        path,
        base,
        commits or (),
        commits_unknown=commits is None,
    )


def _rebase_state(path) -> bool:
    """True while git keeps a rebase in progress in the job worktree `path`."""
    for name in ("rebase-merge", "rebase-apply"):
        try:
            p = gitx.run(path, "rev-parse", "--path-format=absolute", "--git-path", name).stdout.strip()
        except subprocess.TimeoutExpired:  # state unknown: treat as still in progress
            return True
        if p and os.path.exists(p):
            return True
    return False


def _abort_rebase(path) -> bool:
    """Leave no rebase state in `path`: `--abort` restores the branch; when that fails or times out, `--quit` drops the state alone. -> True when no rebase state is left and HEAD is on a branch (`--quit` keeps a detached HEAD at the partial pick, so base..HEAD would omit the job's commits)."""
    for action in ("--abort", "--quit"):
        with contextlib.suppress(subprocess.SubprocessError):
            gitx.run(path, "rebase", action, timeout=GIT_LONG_TIMEOUT_S)
        if not _rebase_state(path):
            try:
                return gitx.run(path, "symbolic-ref", "-q", "HEAD").returncode == 0
            except subprocess.TimeoutExpired:
                return False
    return False


def _is_doc_path(path) -> bool:
    """A docs path of the project (pipeline.json DOC_PATHS globs, or under DOCS_WAVE_DIR)."""
    return any(fnmatch.fnmatch(path, g) for g in (*DOC_PATHS, DOCS_WAVE_DIR + "*"))


def _own_commits(path, base) -> list[dict] | None:
    """The job's own commits not on `base`, oldest first: {sha, subject, doc_only}; doc_only: every file the commit touches is a docs path. A git timeout gives None (unknown)."""
    try:
        shas = gitx.run(path, "rev-list", "--reverse", f"{base}..HEAD").stdout.split()
        out = []
        for sha in shas:
            subject = gitx.run(path, "log", "-1", "--format=%s", sha).stdout.strip()
            files = gitx.run(path, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).stdout.split()
            out.append({"sha": sha, "subject": subject, "doc_only": bool(files) and all(_is_doc_path(f) for f in files)})
        return out
    except subprocess.TimeoutExpired:
        return None


def refresh_stale(a_dir, stage, tasks) -> tuple[list, dict]:
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


def _run_setup(a_dir, stage, task, root, cfg, clock=time.monotonic, keep=False):
    """Run the project's `worktree_setup` commands (pipeline.json; sh -c, cwd = the new worktree) in order. The first failure removes the fresh worktree and raises with its output tail, so no dev starts in a half-ready one (KEEP: a repair of an existing worktree, never removed). Each command logs an `EV_SETUP` metrics line with its exit code and seconds (CLOCK = the time source)."""
    commands = cfg.get("worktree_setup") or []
    limit = float(cfg.get("worktree_setup_timeout") or PROJECT_DEFAULTS["worktree_setup_timeout"])  # a cfg without the key (older callers) gets the default
    metrics = os.path.join(os.path.abspath(a_dir), WORK_DIR, stage, METRICS_LOG)
    for cmd in commands:
        t0, rc = clock(), None
        try:
            r = subprocess.run(["sh", "-c", cmd], cwd=root, capture_output=True, text=True, timeout=limit, check=False)
            rc = r.returncode
            why = f"exit {r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}" if r.returncode else ""
        except subprocess.TimeoutExpired:
            why = f"timed out after {limit:.0f}s"
        try:  # a timing line is best effort: an unwritable stage dir must not fail the setup
            util.append_jsonl(metrics, {"ev": EV_SETUP, "ts": util.now(), "stage": stage, "for": task, "cmd": cmd, "rc": rc, "seconds": round(clock() - t0, 1)})
        except OSError as ex:
            util.swallowed("worktree._run_setup", ex)
        if why:
            if keep:
                raise RuntimeError(f"worktree_setup `{cmd}` failed in the worktree of {task} ({why}); the worktree was kept")
            remove(a_dir, stage, task, force=True)
            raise RuntimeError(f"worktree_setup `{cmd}` failed in the new worktree of {task} ({why}); the worktree was removed")


def repair_env(a_dir, stage, task) -> None:
    """Rerun the `worktree_setup` commands in the existing job worktree of TASK (one environment repair; the worktree is kept on failure)."""
    cfg, _repo, root, _branch = layout(a_dir, stage, task)
    _run_setup(a_dir, stage, task, root, cfg, keep=True)


def link_private(repo, root, prefix, skip=(), nested=None) -> list:
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


def add_repo(a_dir, stage, task, rel) -> dict:
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


def create(a_dir, stage, task) -> dict:
    cfg, repo, root, branch = layout(a_dir, stage, task)
    want = work_branch(cfg, stage)
    nested = nested_repos(repo)
    state = os.path.join(os.path.dirname(root), ".branch")
    if not os.path.isfile(state):  # first call for this task: pick a free name and keep it for every later call (rework, review, merge, remove)
        branch = _claim_branch(repo, stage, task, nested, state)
    fresh = not os.path.isdir(root)
    _add_with_entry(a_dir, stage, task, (repo, root, branch, want))
    if fresh:
        _seed_fresh(a_dir, stage, task, repo, root, cfg)
    _link_shared(repo, root)
    text = ""
    tf = os.path.join(a_dir, WORK_DIR, stage, f"{task}.md")
    if os.path.isfile(tf):
        text = pathlib.Path(tf).read_text(encoding="utf-8")
    repos = {os.path.basename(repo): root}
    for rel in nested:
        if _wants_nested(a_dir, stage, task, root, rel, text):
            repos[rel] = _add_nested(repo, root, rel, branch, want)
    prefix = cfg.get("nested_repo_dir_prefix") or ""
    _check_named_dirs(a_dir, stage, task, repo, root, text, fresh, prefix)
    link_private(repo, root, prefix, repos, nested)
    info = {"branch": branch, "root": root, "base": want, "repos": repos}
    _put_entry(a_dir, stage, task, info)
    return info


def _claim_branch(repo, stage, task, nested, state) -> str:
    """Pick a free branch name for the task and write it to the `state` file (kept for every later call)."""
    branch = _unique_branch(repo, stage, task, nested)
    os.makedirs(os.path.dirname(state), exist_ok=True)
    util.write_text(state, branch + "\n")
    return branch


def _seed_fresh(a_dir, stage, task, repo, root, cfg) -> None:
    """First-time seeding of a new job worktree: index, build files, mtimes, setup command."""
    _seed_index(repo, root)
    _seed_build(repo, root, cfg.get("seed_build") or [])
    _copy_mtimes(repo, root)
    _run_setup(a_dir, stage, task, root, cfg)


def _link_shared(repo, root) -> None:
    """Symlink the LINK entries of the repo into the job worktree and exclude the links from git."""
    for name in LINK:
        src, dst = os.path.join(repo, name), os.path.join(root, name)
        if os.path.isdir(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
        if os.path.islink(dst):
            _exclude_link(root, name)
