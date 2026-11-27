"""The output-path rule for code tasks: a Predicted path must be a tracked repo file. Scratch output under the work folder and git-ignored files are never committed, so they are lost (`outputs:`)."""

import os
import re

from . import gitx, task_text
from .configuration import (
    IGNORED_DONE_EXT_RE,
    IGNORED_DONE_FIELDS,
    IGNORED_DONE_PREFIX,
    IGNORED_DONE_TRIM,
    IGNORED_DONE_URL,
    OUTPUT_PATH_PREFIX,
    WORK_DIR,
)
from .configuration.planning import OUTPUTS_FIELD
from .plan_check_task import RELATIVE_WORK_RE, _in_work_root
from .plan_files import paths_in
from .util import read_text


def _under_work(d, p) -> bool:
    """True when Predicted path P is under the work folder: a relative `.work/` path or an absolute path under the main-tree `.work` dir."""
    return p == WORK_DIR or p.startswith(WORK_DIR + "/") or _in_work_root(d, p)


def _done_work_paths(text) -> list[str]:
    """The relative `.work/` paths named on the `Done:` lines of a task file's text (the same scratch rule as Predicted paths)."""
    return [p for line in task_text.field(text, "Done") for p in RELATIVE_WORK_RE.findall(line)]


def _outputs_in(text) -> list[str]:
    """The `Outputs:` line of a task file's text as paths (comma-split, backticks dropped; counts such as `3` skipped), [] when absent."""
    m = re.search(rf"(?im)^{re.escape(OUTPUTS_FIELD)}:[ \t]*(.*)$", text)
    items = (m[1] if m else "").split(",")
    return [p for p in (i.strip().strip("` ") for i in items) if p and not p.isdigit()]


def _field_tokens(text) -> list[str]:
    """The path-like tokens (a slash or a file extension, not a URL) on the Done, Goal and Test lines of a task file's text, punctuation trimmed, first-seen order."""
    out = []
    for name in IGNORED_DONE_FIELDS:
        for line in task_text.field(text, name):
            for raw in line.split():
                tok = raw.strip(IGNORED_DONE_TRIM)
                if tok and IGNORED_DONE_URL not in tok and ("/" in tok or re.search(IGNORED_DONE_EXT_RE, tok)):
                    out.append(tok)
    return list(dict.fromkeys(out))


def check_output_paths(d, tid, repo) -> list[str]:
    """`outputs:` errors: a Predicted, Outputs or Done `.work/` path of task TID is under the work folder or git-ignored. A code task writes its output to a tracked file or the task report.
    `ignored-done:` errors: a path-like token on the Done, Goal or Test line that git ignores (one check-ignore call per path)."""
    text = read_text(os.path.join(d, tid + ".md"))
    paths = list(dict.fromkeys(paths_in(text) + _outputs_in(text) + _done_work_paths(text)))
    under = [p for p in paths if _under_work(d, p)]
    bad = set(under) | set(gitx.ignored(repo, [p for p in paths if p not in under]))
    problems = [
        f"{OUTPUT_PATH_PREFIX} {tid}: output `{p}` is under the work folder or git-ignored, so it is never committed: write outputs to a tracked file or the task report"
        for p in paths
        if p in bad
    ]
    extra = [t for t in _field_tokens(text) if t not in paths]
    problems += [
        f"{IGNORED_DONE_PREFIX} {tid}: path `{p}` on a Done, Goal or Test line is git-ignored, so it is never committed: name a tracked file"
        for p in gitx.ignored(repo, extra)
    ]
    return problems
