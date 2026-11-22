"""Gate-run record: which command passed on which working tree. `tree_hash` names the tree (tracked + untracked, ignored
excluded) through a temp index, so the real index is never touched. `record` appends one JSON line per gate run to
<git common dir>/gate-runs.jsonl; `find_passed` looks up a green run of the same command on the same tree.
Nothing here raises on a git or file error: a failure means "no record" (None)."""

import json
import os
import shutil
import tempfile
import time
import uuid

from .gitx import try_run

RECORD_FILE = "gate-runs.jsonl"
QRUN_NAMES = ("cs-qrun", "qrun.py")  # launcher names stripped from the front of argv: the same gate run either way
END_OF_OPTIONS = "--"
INDEX_NAME = "index"
PASSED_RC = 0


def _git(cwd, *args, env=None):
    """git -C cwd args -> stdout (str) on exit 0, else None; a timeout, a missing git or a failed call is None."""
    r = try_run(cwd, *args, env=env)
    return r.stdout.strip() if r and r.returncode == 0 else None


def tree_hash(cwd):
    """Tree sha of the working tree at cwd (tracked + untracked, ignored excluded) -> str, or None (not a repo, any git error)."""
    tmp = tempfile.mkdtemp(prefix="gate-tree-")
    try:
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, INDEX_NAME)}
        if _git(cwd, "add", "-A", env=env) is None:
            return None
        return _git(cwd, "write-tree", env=env) or None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_key(argv):
    """argv of a gate command -> the list that identifies it: a leading cs-qrun/qrun.py and a leading `--` are dropped."""
    args = list(argv)
    if args and os.path.basename(args[0]) in QRUN_NAMES:
        args = args[1:]
    if args and args[0] == END_OF_OPTIONS:
        args = args[1:]
    return args


def _log_path(cwd):
    """<git common dir>/gate-runs.jsonl (shared by every worktree of the repo) -> path, or None outside a repo."""
    common = _git(cwd, "rev-parse", "--git-common-dir")
    return os.path.join(cwd, common, RECORD_FILE) if common else None


def _head(cwd):
    return _git(cwd, "rev-parse", "HEAD")


_TREE_NOW = object()  # record(tree=...) default: hash the tree at the call


def record(cwd, argv, rc, now=None, tree=_TREE_NOW):
    """Append {run, cmd, tree, base, rc, ts} for this gate run; writes nothing when there is no tree or no log path.
    tree: the tree hash taken before the run (a test may write files during it); default: taken now."""
    if tree is _TREE_NOW:
        tree = tree_hash(cwd)
    path = _log_path(cwd)
    if tree is None or path is None:
        return
    entry = {"run": uuid.uuid4().hex, "cmd": cmd_key(argv), "tree": tree, "base": _head(cwd), "rc": rc, "ts": time.time() if now is None else now}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        return


def _entries(path):
    """Parsed lines of the record log (bad lines skipped); [] when the log does not exist."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def find_passed(cwd, argv, tree=None, base=None):
    """Latest record with rc 0 for the same command on the same tree (and base, when given) -> dict, else None."""
    try:
        tree = tree_hash(cwd) if tree is None else tree
        path = _log_path(cwd)
        if tree is None or path is None:
            return None
        key = cmd_key(argv)
        for rec in reversed(_entries(path)):
            if rec.get("rc") == PASSED_RC and rec.get("cmd") == key and rec.get("tree") == tree and (base is None or rec.get("base") == base):
                return rec
    except (OSError, ValueError, TypeError):
        return None
    return None
