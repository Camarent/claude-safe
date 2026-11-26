"""Open ops steps before the stage runs: the manual `ops-N` steps of TASKS.md that have no skip event and no task dependency.
`open_ops` lists the blocking ones (the driver stops once, before any dispatch, and writes them all to BLOCKED.md);
`later_ops` lists the open steps that wait for a task: the run goes on and the start banner shows them as later lead steps."""

import os

from .configuration import EV_SKIP, TASKS_FILE, tool_cmd
from .tasks import parse_tasks
from .util import read_jsonl


def _open_steps(c):
    """-> (open manual rows of the stage, all task rows): a manual row with no skip event for its id."""
    tasks = parse_tasks(os.path.join(c.w, TASKS_FILE))
    skipped = {e.get("task") for e in read_jsonl(c.log) if e["ev"] == EV_SKIP}
    return [t for t in tasks if t.get("manual") and t["id"] not in skipped], tasks


def _dependents(step_id, tasks):
    """-> the ids of the rows that list step_id in their depends."""
    return [t["id"] for t in tasks if step_id in t.get("depends", [])]


def open_ops(a, c) -> list[dict]:
    """-> [{id, text, dependents, skip}] for each open ops-N step with no dependency of its own; dependents: the ids of the tasks that wait for it."""
    rows, tasks = _open_steps(c)
    return [
        {
            "id": t["id"],
            "text": t["manual"],
            "dependents": _dependents(t["id"], tasks),
            "skip": f"{tool_cmd('orch.py')} skip --step {t['id']} (stage {a.stage})",
        }
        for t in rows
        if not t.get("depends")
    ]


def later_ops(c) -> list[dict]:
    """-> [{id, text, after}] for each open ops-N step that waits for a task (its depends); not blocking."""
    rows, _ = _open_steps(c)
    return [{"id": t["id"], "text": t["manual"], "after": t["depends"]} for t in rows if t.get("depends")]


def blocked_text(steps) -> str:
    """-> the BLOCKED.md body for the open steps: step, text, the tasks that wait for it and the skip command."""
    body = "".join(f"- {s['id']}: {s['text']}\n  dependents: {', '.join(s['dependents']) or 'none'}\n  skip: {s['skip']}\n" for s in steps)
    return "# Open manual steps: blocked\n\n" + body
