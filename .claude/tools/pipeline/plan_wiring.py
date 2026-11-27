"""The wiring rule: a new module under a source dir of the `wiring_dirs` setting (pipeline.json) must be mounted, or it is dead code. A task whose Predicted paths add such a file needs a tracked mount point in the same task (a listed tracked file outside that dir), or a dependent task that carries a `Mounts: <task>` header naming it (`wiring:`, an error). A task with a `Mounts:` header must prove the mount: its Done names an entry proof (`reach:`, a warning). No `wiring_dirs` setting: no check."""

import os
import re
import subprocess

from . import gitx
from .configuration import (
    MOUNTS_FIELD,
    REACH_PREFIX,
    REACH_PROOF_RE,
    WIRING_DIRS_KEY,
    WIRING_PREFIX,
    project,
)
from .plan_check_task import _repo_root, _workspace
from .plan_files import csv, task_paths
from .task_text import field, lines
from .util import read_text

PROOF_RE = re.compile(REACH_PROOF_RE)


def _text(d, tid) -> str:
    return read_text(os.path.join(d, tid + ".md"))


def _tracked(repo) -> set[str] | None:
    """-> the tracked paths of REPO; None when REPO is no git work tree (no check then)."""
    try:
        r = gitx.run(repo, "ls-files", "-z")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode:
        return None
    return set(r.stdout.split("\0"))


def _mounts(text) -> list[str]:
    """-> the task ids named by the `Mounts:` header lines of TEXT."""
    return [tid for value in field(text, MOUNTS_FIELD) for tid in csv(value)]


def _wiring_dir(path, dirs) -> str | None:
    """-> the wiring dir that PATH lies under, None when it lies under none."""
    for w in dirs:
        if path.startswith(w + "/"):
            return w
    return None


def _mount_missing(tid, w, paths, tracked, ents, texts) -> bool:
    """-> True when task TID, a new module under wiring dir W, has no tracked mount point outside W in its task and no dependent task names TID in a `Mounts:` header."""
    outside = [q for q in paths if q in tracked and _wiring_dir(q, [w]) is None]
    dependents = [f["id"] for f in ents if tid in f["depends"]]
    mounted = any(tid in _mounts(texts[f]) for f in dependents)
    return not outside and not mounted


def _new_module_problems(d, ents, dirs, tracked, texts) -> list[str]:
    """`wiring:` errors for the new modules of ENTS under a wiring dir with no mount."""
    probs = []
    for e in ents:
        tid = e["id"]
        paths = task_paths(d, tid)
        for p in paths:
            w = _wiring_dir(p, dirs)
            if w is None or p in tracked:
                continue
            if _mount_missing(tid, w, paths, tracked, ents, texts):
                probs.append(
                    f"{WIRING_PREFIX} {tid}: {p} is a new module under wiring dir {w} with no mount: list a tracked mount point of the task, or add a dependent task with `{MOUNTS_FIELD}: {tid}`"
                )
    return probs


def _reach_problems(ents, texts) -> list[str]:
    """`reach:` warnings for the tasks with a `Mounts:` header whose Done names no entry proof."""
    probs = []
    for e in ents:
        tid = e["id"]
        text = texts[tid]
        if not _mounts(text):
            continue
        done = " ".join(value for _, value in lines(text, "Done"))
        if not PROOF_RE.search(done):
            probs.append(
                f"{REACH_PREFIX} {tid}: its `{MOUNTS_FIELD}:` header needs a Done line naming an entry proof (entry, bundle, built, cli, route or e2e), not only a harness, mock or fake"
            )
    return probs


def check_wiring(d, ents) -> list[str]:
    """`wiring:` errors for the new modules of ENTS under a wiring dir with no mount, and `reach:` warnings for the tasks with a `Mounts:` header whose Done names no entry proof."""
    dirs = [w.rstrip("/") for w in (project(_workspace(d)).get(WIRING_DIRS_KEY) or [])]
    if not dirs:
        return []
    tracked = _tracked(_repo_root(d))
    if tracked is None:
        return []
    texts = {e["id"]: _text(d, e["id"]) for e in ents}
    return _new_module_problems(d, ents, dirs, tracked, texts) + _reach_problems(ents, texts)
