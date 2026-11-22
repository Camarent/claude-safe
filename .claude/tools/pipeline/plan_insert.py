"""`plan.py task --before N`: insert a task ahead of task-N in a stage that may already be running.

A task counts as started when orch.jsonl has a dispatch or skip event for it (done or in flight). Started tasks keep their id and wave. When no task numbered N or higher has started, the later tasks are renumbered (new = task-N); otherwise the new task takes the next free id and only the dependency order changes. N gets a `depends` edge on the new task, N and its dependents move to later waves where needed, and a started task that would have to wait is refused.

When N itself has started the new task cannot go ahead of it: it takes the next free id and the earliest wave in which no task has started (no edge on N), and the output lists the tasks that started before it.
"""

import os
import re

from . import plan_check
from . import plan_files as pf
from .util import read_text, write_text

TASK_REF = re.compile(r"\btask-(\d+)\b")
TASK_ID = re.compile(r"task-\d+")  # started_tasks also returns step ids such as `final-test`


def _reaches(by, start, goal):
    """True when GOAL is START or one of its (transitive) dependencies."""
    seen, todo = set(), [start]
    while todo:
        t = todo.pop()
        if t == goal:
            return True
        if t not in seen:
            seen.add(t)
            todo += by[t]["depends"] if t in by else []
    return False


def _renumber_map(ents, before, started):
    """-> {old id: new id} shifting every task from number BEFORE up by one, or {} when one of them has started."""
    later = [e["id"] for e in ents if int(e["id"][5:]) >= before]
    if any(t in started for t in later):
        return {}
    return {t: f"task-{int(t[5:]) + 1}" for t in later}


def _shift_waves(ents, started):
    """Move tasks to the wave after their dependencies (until stable). Refuses a started task that would have to move."""
    by = {e["id"]: e for e in ents}
    moved = True
    while moved:
        moved = False
        for e in ents:
            need = max([by[x]["wave"] for x in e["depends"]] or [0]) + 1
            if e["wave"] < need:
                if e["id"] in started:
                    raise pf.PlanError(
                        f"{e['id']} has started and would have to wait for the new task (wave {e['wave']} -> {need}): wait for it or add the task after it"
                    )
                e["wave"] = need
                moved = True


def _first_idle_wave(ents, started):
    """The earliest wave in which no task has started (the one after the last started wave, or wave 1)."""
    busy = [e["wave"] for e in ents if e["id"] in started]
    return max(busy, default=0) + 1


def _check_deps(ents, target, deps):
    by = {e["id"]: e for e in ents}
    for dep in deps:
        if dep not in by:
            raise pf.PlanError(f"depends on unknown {dep}")
        if _reaches(by, dep, target):
            raise pf.PlanError(f"cycle: {dep} depends on {target}, which would wait for the new task")


def _rename_files(d, ren):
    """Rename task files (highest number first) and rewrite the task ids inside every task file."""
    for old in sorted(ren, key=lambda t: -int(t[5:])):
        f = os.path.join(d, old + ".md")
        if os.path.exists(f):
            os.rename(f, os.path.join(d, ren[old] + ".md"))
    for name in os.listdir(d):
        if re.fullmatch(r"task-\d+\.md", name):
            f = os.path.join(d, name)
            write_text(f, TASK_REF.sub(lambda m: ren.get(m[0], m[0]), read_text(f)))


def _set_wave(d, e):
    f = os.path.join(d, e["id"] + ".md")
    if os.path.exists(f):
        write_text(f, re.sub(r"(?m)^Wave: W\d+", f"Wave: W{e['wave']}", read_text(f), count=1))


def _set_depends(d, e):
    """Keep the `Depends:` line of task-N.md equal to the TASKS.md bullet depends; the file is written only when the line changes."""
    f = os.path.join(d, e["id"] + ".md")
    if os.path.exists(f):
        txt = read_text(f)
        new = pf.set_depends_line(txt, e["depends"])
        if new != txt:
            write_text(f, new)


def _check_overlap(ents, new, paths, old_paths):
    """The new task must not share paths with another task of its (final) wave: the rule of plan check (pf.wave_conflict, so a shared `(rows)` file is fine). PATHS = the new task's `--paths` items, OLD_PATHS = {id: path_modes}."""
    new_modes = pf.path_modes(f"{pf.TASK_FIELDS['paths']}{', '.join(paths)}")
    for e in ents:
        ov = e["wave"] == new["wave"] and pf.wave_conflict(new_modes, old_paths[e["id"]])
        if ov:
            raise pf.PlanError(f"{new['id']} and {e['id']} share paths in wave {new['wave']}: {ov[0]} / {ov[1]}")


def _validate(a, ents, target, started):
    """-> the dependency ids asked for, after the checks on the target and the dependencies."""
    if target not in {e["id"] for e in ents}:
        raise pf.PlanError(f"--before: unknown task {target}")
    deps = [x if x.startswith("task-") else f"task-{x}" for x in pf.csv(a.depends)]
    _check_deps(ents, target, deps)
    return deps


def _plan(a, d, ents, before):
    """-> (new entry, renumber map, old wave per new id); changes ENTS in memory, raises on a bad request. Nothing is written."""
    target, started = f"task-{before}", pf.started_tasks(d)
    deps = _validate(a, ents, target, started)
    ren = _renumber_map(ents, before, started)
    new_id = target if ren else f"task-{max(int(e['id'][5:]) for e in ents) + 1}"
    by = {e["id"]: e for e in ents}
    wave = a.wave or (max(by[x]["wave"] for x in deps) + 1 if deps else _first_idle_wave(ents, started) if target in started else by[target]["wave"])
    old_paths = {ren.get(e["id"], e["id"]): pf.task_path_modes(d, e["id"]) for e in ents}
    old_wave = {ren.get(e["id"], e["id"]): e["wave"] for e in ents}
    for e in ents:
        e["depends"] = [ren.get(x, x) for x in e["depends"]]
        e["id"] = ren.get(e["id"], e["id"])
    if target not in started:
        by[target]["depends"] = [*by[target]["depends"], new_id]
    new = {"id": new_id, "title": a.title, "owner": a.model, "wave": wave, "review": a.model, "depends": [ren.get(x, x) for x in deps]}
    _shift_waves([*ents, new], started)
    _check_overlap(ents, new, pf.csv(a.paths), old_paths)
    return new, ren, old_wave


def insert_task(a):
    """Apply `task --before`. -> (task count, plan check problems) of the result."""
    d = pf.stage_dir(a)
    if not os.path.exists(pf.tasks_path(d)):
        raise pf.PlanError(f"{pf.tasks_path(d)} missing: run stage-init first")
    if a.id:
        raise pf.PlanError("--before picks the id itself: leave out --id")
    ents = pf.read_entries(d)
    new, ren, old_wave = _plan(a, d, ents, int(a.before))
    began = sorted((t for t in pf.started_tasks(pf.stage_dir(a)) if TASK_ID.fullmatch(t) and t != new["id"]), key=lambda t: int(t[5:]))
    if f"task-{a.before}" in began:
        print(
            f"note: task-{a.before} has started, so the new task {new['id']} runs in wave {new['wave']}, the first wave with no started task; started before it: {', '.join(began)}"
        )
    elif not ren:
        print(
            f"note: a task from task-{a.before} up has started, so nothing is renumbered; the new task is {new['id']} and task-{a.before} waits for it; started before it: {', '.join(began)}"
        )
    _rename_files(d, ren)
    write_text(os.path.join(d, new["id"] + ".md"), pf.render_task(new, a, pf.csv(a.paths)))
    for e in ents:
        if e["wave"] != old_wave[e["id"]]:
            _set_wave(d, e)
        _set_depends(d, e)  # renumbering rewrote the ids in TASKS.md depends: the file lines follow
    pf.write_tasks(d, [*ents, new])
    return plan_check.check_stage(d)
