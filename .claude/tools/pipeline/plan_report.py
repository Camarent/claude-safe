"""`cs-plan check` rules for the task header `Type:` and `Inputs:`, and the validator rule of `Type: report` tasks."""

import os
import re
import shutil

from .configuration import TOOLS_DIR
from .plan_files import TASK_TYPES, task_inputs, task_type
from .util import read_text

EXISTENCE_RE = re.compile(r"^\s*(?:test\s+-[sfeSFE]\b|\[\s+-[sfeSFE]\b|ls\b)")  # a bare file-exists check, not a validator
AFTER_RE = re.compile(r"^after:task-\d+:")  # output of an earlier task of the stage: not there yet, not checked


def _tool_found(name, repo):
    return bool(shutil.which(name)) or any(os.path.exists(os.path.join(b, name)) for b in (repo, os.path.join(repo, "scripts"), TOOLS_DIR))


def check_type_inputs(d, tid, repo, workspace):
    """Problems for one task file: an unknown `Type:` value, and each `Inputs:` entry that does not exist (path relative to the workspace or absolute; `tool:<name>` on PATH or a repo script)."""
    text = read_text(os.path.join(d, tid + ".md"))
    probs = []
    kind = task_type(text)
    if kind not in TASK_TYPES:
        probs.append(f"{tid}: Type: {kind!r} is not one of {', '.join(TASK_TYPES)}")
    for item in task_inputs(text):
        if AFTER_RE.match(item):
            continue
        if item.startswith("tool:"):
            if not _tool_found(item[5:], repo):
                probs.append(f"{tid}: input {item} not found (not on PATH, not a script of the repo)")
        elif not os.path.exists(os.path.join(workspace, item)):
            probs.append(f"{tid}: input {item} does not exist (relative paths are taken from {workspace})")
    return probs


def check_report_validator(d, tid):
    """Problems for one `Type: report` task file: every `Test:` command is a bare existence check."""
    text = read_text(os.path.join(d, tid + ".md"))
    if task_type(text) != "report":
        return []
    cmds = [c for m in re.finditer(r"(?m)^Test:\s*(.*)$", text) for c in re.split(r"&&|;", m[1]) if c.strip()]
    if cmds and all(EXISTENCE_RE.match(c) for c in cmds):
        return [f"{tid}: report task needs a validator script in Test: (row counts, required columns, totals add up), not just file existence"]
    return []
