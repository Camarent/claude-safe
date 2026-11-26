"""TASKS.md parsing: the task table / bullets with the wave Graph, and the `flags:` line."""

import os
import re
from pathlib import Path
from typing import Any

from .plan_files import is_mid_phase
from .util import read_text


def _task_row(line):
    """-> the task dict of one TASKS.md line (table row or bullet), or None."""
    c = [x.strip() for x in line.strip().strip("|").split("|")]
    if len(c) >= 5 and re.fullmatch(r"task-\d+", c[0]):
        return {
            "id": c[0],
            "owner_sonnet": False,
            "review_sonnet": False,
        }  # models are chosen by the driver (Haiku first); a plan cell saying sonnet is ignored
    m = re.match(
        r"\s*[-*]\s*(?:\[[ xX]\]\s*)?(ops-\d+)\b\s*manual:\s*(.*)", line, re.I
    )  # a manual step: a person does it, then `orch.py skip --step ops-N`; the box is optional (old files)
    if m:
        return {"id": m.group(1), "manual": m.group(2).strip(), "owner_sonnet": False, "review_sonnet": False}
    b = re.match(r"\s*[-*]\s*(?:\[[ xX]\]\s*)?(task-\d+)\b(.*)", line)  # bullet form (architect.md), '-' or em-dash separators; the box is optional (old files)
    if not b:
        return None
    return {
        "id": b.group(1),
        "owner_sonnet": False,
        "review_sonnet": False,
    }


def _graph_order(rows, tasks_md):
    """{task id: None} in Graph order; sets the wave of each task the Graph names."""
    order: dict = {}
    for m in re.finditer(r"wave\s*(\d+):\s*([^|\n]+)", Path(tasks_md).read_text(encoding="utf-8")):
        for t in re.findall(r"(?:task|ops)-\d+", m.group(2).split("(")[0]):
            if t in rows and t not in order:
                rows[t]["wave"] = int(m.group(1))
                order[t] = None
    return order


def _add_depends(row, line):
    """Adds the ids of a `Depends:` line to the row's depends, no duplicates."""
    dep = re.search(r"\bDepends\s*:\s*(.*)", line, re.I)
    if dep:
        row.setdefault("depends", [])
        row["depends"] += [t for t in re.findall(r"(?:task|ops)-\d+", dep.group(1)) if t not in row["depends"]]


def parse_tasks(tasks_md) -> list[dict[str, Any]]:
    """-> ordered list of {id, owner_sonnet, review_sonnet} from the TASKS.md table (wave order if a Graph line exists)."""
    if not os.path.exists(tasks_md):
        return []  # brief-only stage: the architect writes TASKS.md later
    rows, cur = {}, None
    with open(tasks_md, encoding="utf-8") as fh:
        for line in fh:
            row = _task_row(line)
            if row:
                rows[row["id"]] = cur = row
            if cur:  # on the task's own line or on a line after it, until the next task
                _add_depends(cur, line)
    for t, row in rows.items():  # the task file next to TASKS.md may also say `Depends:`; its ids join the TASKS.md ones
        tf = os.path.join(os.path.dirname(tasks_md), t + ".md")
        if os.path.exists(tf):
            for line in read_text(tf).splitlines():
                _add_depends(row, line)
    order = _graph_order(rows, tasks_md)
    top = max((rows[t]["wave"] for t in order), default=0)
    for t in rows:  # not in the Graph: one wave each, after the planned ones (sequential, as before)
        if t not in order:
            top += 1
            rows[t]["wave"] = top
            order[t] = None
    return [rows[t] for t in order]


def depends_problem(tasks) -> str | None:
    """-> a message when the `Depends:` lines are unusable (unknown task, itself, a cycle, or a Graph wave that is not after its dependencies), else None."""
    by = {t["id"]: t for t in tasks}
    for t in tasks:
        for d in t.get("depends", []):
            if d == t["id"]:
                return f"{t['id']} depends on itself"
            if d not in by:
                return f"{t['id']} depends on {d}, which is not in TASKS.md"
            if by[d]["wave"] >= t["wave"]:
                return f"{t['id']} (wave {t['wave']}) depends on {d} (wave {by[d]['wave']}): a task must come in a later wave than its dependencies"
    return None


def parse_flags(tasks_md) -> dict[str, Any]:
    """`flags: ui docs no-merge no-test no-audit no-publish` anywhere in TASKS.md. Default: merge, test, audit, release on; ui, docs off."""
    f: dict[str, Any] = {
        "ui": False,
        "docs": False,
        "index": True,
        "live": True,
        "push": True,
        "merge": True,
        "test": True,
        "audit": True,
        "retro": True,
        "clean": True,
        "release": True,
        "publish": True,
        "effort": None,
    }
    txt = read_text(tasks_md)
    toks = [t for m in re.finditer(r"^\s*flags\s*:(.*)$", txt, re.I | re.M) for t in m.group(1).lower().split()]
    why = re.search(r"^\s*high-because\s*:\s*(\S.*)$", txt, re.I | re.M)
    if re.search(r"^\s*UI\s*:\s*yes\b", txt, re.I | re.M):
        toks.append("ui")  # `UI: yes` in the TASKS.md header or any task entry = same as flag ui (web-tester + ux audit)
    for tok in toks:
        if tok in ("ui", "docs", "index"):
            f[tok] = True
        elif tok == "audit-low":
            f["effort"] = "low"
        elif tok == "audit-high" and why:  # high only with a one-line reason (ORCHESTRATOR.md); without it the default stays
            f["effort"] = "high"
            f["why"] = why.group(1).strip()
        elif tok.startswith("no-") and tok[3:] in f:
            f[tok[3:]] = False
    if is_mid_phase(
        tasks_md
    ):  # a phase that is not the last of its plan only merges into the work branch: the master merge, push, publish, index, live check and cleanup run once, in the last phase
        f.update(push=False, index=False, live=False, clean=False, release=False)
    return f
