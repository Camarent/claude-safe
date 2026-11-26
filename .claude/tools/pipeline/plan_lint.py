"""plan.py check rules on shared data files, test environment reads, UX-VARS. Errors name the fix."""

import os
import re
import subprocess
import sys

from . import gitx
from .configuration import (
    REGISTRY_WORDS,
    SHARED_MIN,
    TEST_ENV_MAX_BYTES,
    TEST_ENV_MAX_FILES,
    TEST_ENV_WORDS,
    TEST_NAME_RE,
    project,
)
from .plan_files import is_line_based, tasks_path
from .tasks import parse_flags
from .util import TOOLS, read_text

GLOB_CHARS = "*?["


def is_registry(path) -> bool:
    """True when the file NAME holds a registry word as a whole token (`edited-ids.js`; not `playlist.js`) and is not a test file (`weak-list-real.test.js`)."""
    name = os.path.basename(path)
    return not re.search(TEST_NAME_RE, name) and bool(REGISTRY_WORDS & set(re.split(r"[^a-z0-9]+", name.lower())))


def is_fragmented(path) -> bool:
    """True for a directory or glob entry: the fragment-directory design, one file per task."""
    return path.endswith("/") or any(c in path for c in GLOB_CHARS)


def classify_shared(path, modes) -> str:
    """-> `error`, `warn` or `` for a file listed by SHARED_MIN or more tasks with the given modes (`rows`, `mass`, ``).
    Fragments (directory or glob) are fine. A data file where all listings are `(rows)` is fine; where all but ONE are `(rows)` that one is the owner: warning only. Other data or registry files: error."""
    if len(modes) < SHARED_MIN or is_fragmented(path):
        return ""
    line = is_line_based(path)
    if not line and not is_registry(path):
        return "warn"
    rows = sum(m == "rows" for m in modes)
    if line and rows == len(modes):
        return ""
    return "warn" if line and rows == len(modes) - 1 else "error"


def shared_file_errors(listings) -> list[str]:
    """-> `shared-file:` errors from LISTINGS {path: [(task id, mode)]}."""
    out = []
    for f, es in listings.items():
        if classify_shared(f, [m for _, m in es]) == "error":
            ids = ", ".join(t for t, _ in es)
            out.append(
                f"shared-file: {f} is listed by {ids} ({len(es)} tasks, {SHARED_MIN} or more) and is a data or registry file: tasks would overwrite each other. Fix: one fragment file per task (list a directory or glob such as `data/fragments/` instead), or one owner task that edits it as a whole while every other task lists it as `(rows)` (line-based data files only)"
            )
    return out


def _ignored(repo, rel):
    """True when git (run in REPO) reports REL as ignored; any git failure -> False."""
    try:
        return gitx.run(repo, "check-ignore", "-q", "--", rel).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


LITERAL_RE = re.compile(
    r"""["'`]((?:\.\./|\./)?[\w@-]+(?:\.[\w@-]+)+|(?:\.\./|\./)?[\w.@-]+(?:/[\w.@-]+)+/?)["'`]"""
)  # a quoted name with an extension, or with a slash


def _read_capped(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(TEST_ENV_MAX_BYTES)
    except OSError:
        return ""


def _ignored_literals(repo, test_file):
    """-> the string-literal paths of TEST_FILE (read up to TEST_ENV_MAX_BYTES) that exist under REPO and are git-ignored there."""
    root, hits = os.path.realpath(repo), []
    for lit in dict.fromkeys(LITERAL_RE.findall(_read_capped(test_file))):
        for base in (os.path.dirname(test_file), repo):
            full = os.path.realpath(os.path.join(base, lit))
            if not full.startswith(root + os.sep) or not os.path.exists(full):
                continue
            if _ignored(repo, os.path.relpath(full, root)):
                hits.append(lit)
            break
    return hits


def _test_files(repo, cmd):
    """-> the existing files named in command text CMD (resolved against REPO), at most TEST_ENV_MAX_FILES."""
    out = []
    for tok in cmd.replace("`", " ").split():
        full = os.path.join(repo, tok.strip("\"'"))
        if re.search(r"\.\w+$", tok) and os.path.isfile(full):
            out.append(full)
    return out[:TEST_ENV_MAX_FILES]


def test_env_errors(d, tid, repo) -> list[str]:
    """-> `test-env:` errors for one task: a Test:/Done command that needs live Chrome and a test file it names that reads a git-ignored path (absent in a clean worktree)."""
    text = read_text(os.path.join(d, tid + ".md"))
    cmds = re.findall(r"(?im)^(?:Test|Done):\s*(.*)$", text)
    if not any(w in c.lower() for c in cmds for w in TEST_ENV_WORDS):
        return []
    tm = re.search(r"(?m)^Test:\s*(.*)$", text)
    out = []
    for f in _test_files(repo, tm[1] if tm else ""):
        hits = _ignored_literals(repo, f)
        if hits:
            out.append(
                f"test-env: {tid}: the live-browser test {os.path.relpath(f, repo)} reads `{hits[0]}`, which git ignores (absent in a clean worktree): use a synthetic fixture file the task creates instead"
            )
    return out


def ux_vars_errors(d, repo) -> list[str]:
    """-> `ux-vars:` error when the stage has flag ui, the AUDIT.md ```ux block has `<name>` placeholders and TASKS.md has no `UX-VARS:` line naming each."""
    if not parse_flags(tasks_path(d))["ui"]:
        return []
    sys.path.insert(0, TOOLS)
    import ux_run

    audit = os.path.join(repo, str(project(repo).get("audit_file") or "AUDIT.md"))
    names = set(re.findall(r"<(\w+)>", "\n".join(ux_run.block(read_text(audit)).values())))
    missing = sorted(names - set(ux_run.variables(read_text(tasks_path(d)), [])))
    if not missing:
        return []
    return [
        f"ux-vars: the stage has flag ui and the ```ux block of AUDIT.md has placeholders {', '.join('<' + n + '>' for n in missing)} with no value: add a `UX-VARS: {' '.join(n + '=<value>' for n in missing)}` line to TASKS.md"
    ]
