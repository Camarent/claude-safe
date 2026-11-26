"""Job worktree merge side: commit, merge, remove, review and merged markers, the cleanup of a finished job. Split from worktree.py; pipeline.worktree re-exports every name."""

import contextlib
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
    EV_MAIN_RESTORED,
    FORCED_WHY_MAX,
    GIT_LONG_TIMEOUT_S,
    JOB_DIFF_MAX_CHARS,
    LINK_BASE_FILE,
    MERGE_TREE_MIN_GIT,
    METRICS_LOG,
    ORCH_LOG,
    STAGE_LOCK,
    WORK_DIR,
    project,
    work_branch,
)
from .plan_files import paths_in
from .preflight import mode_lines
from .worktree_base import LINK, _base_branch, _manifest_lock, _write_manifest, git, layout, live_job, nested_repos, read_manifest

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


def skip_blocker(a_dir, stage, task) -> str:
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


def empty_job(a_dir, stage, task) -> bool:
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


def settled_job(a_dir, stage, task) -> bool:
    """True when nothing of the task's job worktrees is left to merge: empty (`empty_job`), its content already in the stage branch (`is_merged`, also after a red merge gate left the branch behind) or hand-merged (`marked_merged`). A done task like this needs no merge: its worktree may go."""
    return empty_job(a_dir, stage, task) or is_merged(a_dir, stage, task) or marked_merged(a_dir, stage, task)


def remove(a_dir, stage, task, force=False) -> dict:
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


def subject_ok(s) -> bool:
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


def touched_nested(a_dir, stage, nested) -> list[str]:
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


def dirty_report(a_dir, stage=None) -> str:
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


def _apply_modes(a_dir, stage, task, rel, w):
    """Set the index mode of each `Mode: <path> <mode>` line of the task brief that is staged in job worktree W (git update-index --chmod +x for a mode with an exec bit, -x otherwise), so the commit carries the brief's mode, not the disk's. A path not staged here is skipped. No brief: nothing."""
    brief = pathlib.Path(a_dir, WORK_DIR, stage, f"{task}.md")
    if not brief.is_file():
        return
    staged = set(git(w, "diff", "--cached", "--name-only", "--diff-filter=ACMR").split("\n"))
    for mpath, mode in mode_lines(brief.read_text(encoding="utf-8", errors="replace")):
        local = mpath[len(rel) + 1 :] if rel else mpath
        if rel and not mpath.startswith(rel + "/"):
            continue
        if local not in staged:
            continue
        flag = "+x" if int(mode, 8) & 0o111 else "-x"
        git(w, "update-index", f"--chmod={flag}", "--", local)


def _parallel_wave(a_dir, stage, task):
    """True when the brief of TASK names a `Wave: <w>` that another task brief of STAGE shares (the task runs beside others). No wave line, or a wave of its own: False."""
    plan = os.path.join(os.path.abspath(a_dir), WORK_DIR, stage)

    def wave_of(name: str) -> str | None:
        m = re.search(r"(?m)^Wave: (\S+)$", util.read_text(os.path.join(plan, name)))
        return m[1] if m else None

    mine = wave_of(f"{task}.md")
    if mine is None:
        return False
    try:
        names = os.listdir(plan)
    except OSError:
        return False
    return any(n.endswith(".md") and n != f"{task}.md" and wave_of(n) == mine for n in names)


def commit(a_dir, stage, task, subject, regen=None) -> dict:
    """Commit every change of the task's worktrees (one commit per repo, nested first) with `<type>(<area>): <what>`. -> {rel: short sha | None (nothing to commit)}.
    `regen` False skips the docs index regeneration (the commit then holds only what the caller staged); None: regenerate unless the task shares its parallel wave."""
    if not subject_ok(subject):
        raise RuntimeError(f"commit subject must look like `<type>(<area>): <what>` (lower-case type, at least 4 chars of text): {subject!r}")
    _cfg, _branch, parts = _parts(a_dir, stage, task)
    if regen is None:
        regen = not _parallel_wave(a_dir, stage, task)  # tasks of one parallel wave would each rewrite the shared index: the lead regenerates it
    out: dict = {}
    for rel, r, w in parts:
        if regen:
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
        _apply_modes(a_dir, stage, task, rel, w)
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


CONFLICT_HUNK_CHARS = 2000  # the conflicted hunks kept in a Conflict message: the merger reads them from the merge report


def _conflict_hunks(r):
    """Conflicted files and their hunks (`git diff --diff-filter=U`) of the failed squash merge in REPO -> text, the last CONFLICT_HUNK_CHARS characters."""
    out = gitx.run(r, "diff", "--diff-filter=U")
    hunks = (out.stdout or "")[-CONFLICT_HUNK_CHARS:]
    return "conflicted hunks:\n" + (hunks or "no conflicted hunks")


def merge(a_dir, stage, task) -> dict:
    """Squash-merge the task's job branch into `branch_work` of every repo it has a worktree in (nested first), one commit per repo with the branch's own subject.
    -> {rel: "merged" | "nothing"}. Conflict: the main checkout is reset, raises Conflict. Cannot start: raises Blocked. A `.merged` marker (mark_merged) skips the task."""
    cfg, branch, parts = _parts(a_dir, stage, task)
    want = work_branch(cfg, stage)
    if marked_merged(a_dir, stage, task):
        return {"skipped": "marked merged"}
    if holder := _driver_holder(a_dir, stage):  # fail fast: a merge under a running driver races its branch switches
        raise Blocked(f"stage {stage} driver (pid {holder}) holds {STAGE_LOCK}: merge after it stops")
    _check_merge_preconditions(a_dir, stage, parts, want)  # everything first so a refusal leaves every repo as it was
    out = {}
    for rel, r, _w in parts:
        if not _unmerged(r, branch, want):
            out[rel or "."] = "nothing"
            continue
        _save_patch(a_dir, stage, task, rel, r, branch, want)
        sq = gitx.run(r, "merge", "--squash", branch, timeout=GIT_LONG_TIMEOUT_S)
        if sq.returncode:
            hunks = _conflict_hunks(r)  # read before the reset: the reset removes the conflict markers
            git(r, "reset", "--hard", "HEAD", check=False)
            done = [k for k, v in out.items() if v != "nothing"]  # repos merged before this one: their commits stay
            kept = f"\nalready merged, kept: {', '.join(done)}" if done else ""
            raise Conflict(f"conflict merging {branch} into {want} in {r}: {(sq.stdout + sq.stderr).strip()[-300:]}\n{hunks}{kept}")
        out[rel or "."] = _commit_squash(r, branch)
    return out


def _driver_holder(a_dir, stage):
    """-> the pid of a live driver that holds the stage lock (STAGE_LOCK), else None. The lock file holds the driver's pid (cli_wave._take_lock);
    a dead pid, a missing or unreadable file is a free lock. Our own pid and our parent (the driver that runs `wt.py merge`) are not a rival."""
    try:
        pid = int(pathlib.Path(a_dir, WORK_DIR, stage, STAGE_LOCK).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid in (os.getpid(), os.getppid()):
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:  # alive, owned by another user
        pass
    return pid


def _check_merge_preconditions(a_dir, stage, parts, want):
    """Refuse on uncommitted work (job worktree or main checkout). A main checkout on another branch is switched back to `want` and logged once (`main_restored`)."""
    switch = []
    for rel, r, w in parts:
        if _dirty(w, r):
            raise Blocked(f"uncommitted changes in job worktree {w}: the dev must run wt.py commit")
        dirty = _dirty(r, r)
        if dirty:
            shown = ", ".join(ln.strip() for ln in dirty[:8]) + (f", +{len(dirty) - 8} more" if len(dirty) > 8 else "")
            raise Blocked(
                f"main checkout {r} has uncommitted changes: {shown}. Commit them, or park them with `git -C {r} stash -u` and `git -C {r} stash pop` after the merge"
            )
        cur = git(r, "branch", "--show-current", check=False)
        if cur != want:
            switch.append((rel, r, cur))
    for rel, r, cur in switch:
        co = gitx.run(r, "checkout", "-q", want, timeout=GIT_LONG_TIMEOUT_S)
        if co.returncode:
            raise Blocked(f"{r} is on `{cur}` and cannot switch to `{want}`: {(co.stdout + co.stderr).strip()[-300:]}")
        util.append_event(os.path.join(a_dir, WORK_DIR, stage, ORCH_LOG), {"ev": EV_MAIN_RESTORED, "repo": rel or ".", "from": cur, "to": want})


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


def marker_state(a_dir, stage, task) -> str:
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


def marked_merged(a_dir, stage, task) -> bool:
    """True while the task's `.merged` marker is valid (`marker_state`): it records the job branch head of every repo and counts only while each head is unchanged, so a rework commit made after a hand merge is merged and guarded again. Mark a hand merge last, right before the branch and worktree are removed."""
    return marker_state(a_dir, stage, task) == "valid"


def mark_merged(a_dir, stage, task) -> dict:
    """The task's work was merged by hand (a conflict fix): later `merge` skips it and `remove` stops guarding it, while the job branches keep the heads they have now (`marked_merged`)."""
    base = os.path.dirname(layout(a_dir, stage, task)[2])
    os.makedirs(base, exist_ok=True)
    util.write_text(os.path.join(base, MERGED_MARKER), json.dumps(_job_heads(a_dir, stage, task), sort_keys=True) + "\n")
    return {"marked": task}


def is_merged(a_dir, stage, task) -> bool:
    """True when every job branch exists and the work branch already holds its tree (merge-tree check, uncommitted changes count as not merged). A `.merged` marker alone proves nothing (it may be stale), and a missing branch is not merged. False when it cannot tell (no worktree)."""
    cfg, branch, parts = _parts(a_dir, stage, task)
    want = work_branch(cfg, stage)
    return bool(parts) and all(git(r, "rev-parse", "--verify", "--quiet", branch, check=False) and not _unmerged(r, branch, want, w) for _rel, r, w in parts)


def diff(a_dir, stage, task, limit=JOB_DIFF_MAX_CHARS) -> str:
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


def clean(a_dir, stage) -> dict:
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


def review_marker(a_dir, stage, task) -> str:
    """The review-base marker lives next to the job folder, outside every repo root, so it never shows as dirty."""
    return os.path.join(os.path.abspath(a_dir), ".wt", stage, f"{task}.review-base")


def review_begin(a_dir, stage, task) -> dict:
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


def review_reset(a_dir, stage, task) -> dict:
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


def review_pending(a_dir, stage) -> list[str]:
    """Tasks of a stage whose review marker is still there (the reviewer or the driver died before the reset)."""
    base = os.path.join(os.path.abspath(a_dir), ".wt", stage)
    return sorted(f[: -len(".review-base")] for f in os.listdir(base) if f.endswith(".review-base")) if os.path.isdir(base) else []


def list_wt(a_dir, stage) -> list[str]:
    base = os.path.join(os.path.abspath(a_dir), ".wt", stage)
    return sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))) if os.path.isdir(base) else []
