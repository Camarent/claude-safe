"""The output-path rule for code tasks: a Predicted path must be a tracked repo file. Scratch output under the work folder and git-ignored files are never committed, so they are lost (`outputs:`)."""

import os
import re
import subprocess

from . import gitx
from .configuration import OUTPUT_PATH_PREFIX, WORK_DIR
from .configuration.planning import OUTPUTS_FIELD
from .plan_check_task import RELATIVE_WORK_RE, _in_work_root
from .plan_files import paths_in
from .util import read_text

DONE_RE = re.compile(r"(?im)^Done:\s*(.*)$")


def _under_work(d, p) -> bool:
    """True when Predicted path P is under the work folder: a relative `.work/` path or an absolute path under the main-tree `.work` dir."""
    return p == WORK_DIR or p.startswith(WORK_DIR + "/") or _in_work_root(d, p)


def _ignored(repo, paths) -> list[str]:
    """-> the PATHS git ignores in REPO (`git check-ignore`; tracked files are never reported). One call per path: a path git rejects (outside REPO) hides no other path."""
    ignored = []
    for p in paths:
        try:
            r = gitx.run(repo, "check-ignore", "--", p)
        except (OSError, subprocess.TimeoutExpired):
            continue
        # exit 0: the path is ignored; exit 1: not ignored; anything else (outside REPO, not a work tree): not reported.
        if r.returncode == 0:
            ignored.append(p)
    return ignored


def _done_work_paths(text) -> list[str]:
    """The relative `.work/` paths named on the `Done:` lines of a task file's text (the same scratch rule as Predicted paths)."""
    return [p for line in DONE_RE.findall(text) for p in RELATIVE_WORK_RE.findall(line)]


def _outputs_in(text) -> list[str]:
    """The `Outputs:` line of a task file's text as paths (comma-split, backticks dropped; counts such as `3` skipped), [] when absent."""
    m = re.search(rf"(?im)^{re.escape(OUTPUTS_FIELD)}:[ \t]*(.*)$", text)
    items = (m[1] if m else "").split(",")
    return [p for p in (i.strip().strip("` ") for i in items) if p and not p.isdigit()]


def check_output_paths(d, tid, repo) -> list[str]:
    """`outputs:` errors: a Predicted, Outputs or Done `.work/` path of task TID is under the work folder or git-ignored. A code task writes its output to a tracked file or the task report."""
    text = read_text(os.path.join(d, tid + ".md"))
    paths = list(dict.fromkeys(paths_in(text) + _outputs_in(text) + _done_work_paths(text)))
    under = [p for p in paths if _under_work(d, p)]
    bad = set(under) | set(_ignored(repo, [p for p in paths if p not in under]))
    return [
        f"{OUTPUT_PATH_PREFIX} {tid}: output `{p}` is under the work folder or git-ignored, so it is never committed: write outputs to a tracked file or the task report"
        for p in paths
        if p in bad
    ]
