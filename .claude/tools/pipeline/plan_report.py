"""`cs-plan check` rules for the task header `Type:` and `Inputs:`, and the validator rule of `Type: report` tasks."""

import os
import re
import shutil

from .configuration import TOOLS_DIR
from .plan_files import TASK_TYPES, paths_in, task_inputs, task_outputs, task_type
from .util import read_text

EXISTENCE_RE = re.compile(r"^\s*(?:test\s+-[sfeSFE]\b|\[\s+-[sfeSFE]\b|ls\b)")  # a bare file-exists check, not a validator
AFTER_RE = re.compile(r"^after:task-\d+:")  # output of an earlier task of the stage: not there yet, not checked


def _tool_found(name, repo):
    return bool(shutil.which(name)) or any(os.path.exists(os.path.join(b, name)) for b in (repo, os.path.join(repo, "scripts"), TOOLS_DIR))


def _stage_listed(d, ents):
    """-> the paths the stage's tasks list under Predicted paths or Outputs: (a path the plan makes, so not checked here)."""
    listed = set()
    for e in ents or []:
        f = os.path.join(d, e["id"] + ".md")
        if os.path.exists(f):
            text = read_text(f)
            listed.update(paths_in(text) + task_outputs(text))
    return listed


def _names_path(item, text) -> bool:
    """-> True when ITEM occurs in TEXT as a whole path (not as the prefix of `out/data.csv.bak` or `x/out/data.csv`)."""
    return re.search(r"(?<![\w/.-])" + re.escape(item) + r"(?![\w/-]|\.\w)", text) is not None


def _manual_excuse(item, tid, ents):
    """-> "" when an ops-N manual row of ENTS names ITEM and TID depends on that row, else the problem text."""
    me: dict = next((x for x in ents if x["id"] == tid), {})
    rows = [x for x in ents if x["id"].startswith("ops-") and _names_path(item, x.get("manual", ""))]
    if not rows:
        return f"{tid}: input {item} does not exist and no ops-N manual row of the stage names it (add a manual row naming it, and list its id in Depends:)"
    if not any(x["id"] in me.get("depends", []) for x in rows):
        return f"{tid}: input {item} is named by {rows[0]['id']} but {tid} does not list {rows[0]['id']} in Depends:"
    return ""


def check_type_inputs(d, tid, repo, workspace, ents=None) -> list[str]:
    """Problems for one task file: an unknown `Type:` value, and each `Inputs:` entry that does not exist (path relative to the workspace or absolute; `tool:<name>` on PATH or a repo script). ENTS (the stage entries, optional) lets a missing path that no task of the stage lists pass when an ops-N manual row names it and this task depends on that row."""
    text = read_text(os.path.join(d, tid + ".md"))
    probs = []
    kind = task_type(text)
    if kind not in TASK_TYPES:
        probs.append(f"{tid}: Type: {kind!r} is not one of {', '.join(TASK_TYPES)}")
    listed = _stage_listed(d, ents) if ents is not None else set()
    for item in task_inputs(text):
        if AFTER_RE.match(item):
            continue
        if item.startswith("tool:"):
            if not _tool_found(item[5:], repo):
                probs.append(f"{tid}: input {item} not found (not on PATH, not a script of the repo)")
        elif not os.path.exists(os.path.join(workspace, item)) and item not in listed:
            why = _manual_excuse(item, tid, ents) if ents is not None else f"{tid}: input {item} does not exist (relative paths are taken from {workspace})"
            if why:
                probs.append(why)
    return probs


def check_report_validator(d, tid) -> list[str]:
    """Problems for one `Type: report` task file: every `Test:` command is a bare existence check."""
    text = read_text(os.path.join(d, tid + ".md"))
    if task_type(text) != "report":
        return []
    cmds = [c for m in re.finditer(r"(?m)^Test:\s*(.*)$", text) for c in re.split(r"&&|;", m[1]) if c.strip()]
    if cmds and all(EXISTENCE_RE.match(c) for c in cmds):
        return [f"{tid}: report task needs a validator script in Test: (row counts, required columns, totals add up), not just file existence"]
    return []
