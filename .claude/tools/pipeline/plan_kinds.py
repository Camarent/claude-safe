"""The per-kind task checks: each `Type:` keeps its own output rule. A code task must write to tracked files (plan_outputs); a report task keeps the `.work/` rule (plan_check_task.check_work_paths)."""

import os

from .plan_check_task import check_work_paths
from .plan_files import task_type
from .plan_outputs import check_output_paths
from .util import read_text


def check_kinds(d, e, ents, repo) -> list[str]:
    """Problems of task entry E by its `Type:`: `code` -> check_output_paths; any other type -> check_work_paths. ENTS is the stage's entries (for the later per-kind checks)."""
    tid = e["id"]
    if task_type(read_text(os.path.join(d, tid + ".md"))) == "code":
        return check_output_paths(d, tid, repo)
    return check_work_paths(d, tid)
