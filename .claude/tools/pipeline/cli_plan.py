"""Plan writer for architects: writes Plan.md / TASKS.md / task-N.md in the exact format orch.py and wave.py parse, and validates them.

    cs-plan plan-init --feature F --goal TEXT             Plan.md skeleton + phases/
    cs-plan phase --feature F --name N --goal G --done-when CMD [--depends A,B] --paths p1,p2 [--tasks N] [--after P]   append phase (or insert after phase P and renumber the later, unstarted ones), create phases/NN-name (paths must not overlap other phases)
    cs-plan phase-done --feature F --name N               mark a phase done
    cs-plan next-phase --feature F                        JSON of the first open phase whose depends are done
    cs-plan stage-init --stage S --goal G [--flags "ui docs"] [--pr-title "<type>(<area>): <what>"]   TASKS.md skeleton
    cs-plan task --stage S --id N --title T --paths p1,p2 --test CMD --done TEXT [--wave W] [--depends N,M]
                 [--pattern file:line] [--affected "tests|none"] [--turns 25]   task-N.md + TASKS.md entry + Graph (same-wave tasks must not share paths)
    cs-plan edit-task --stage S --id N --depends M[,K]   add dependencies (no cycle; the task moves to a later wave when needed); it is held until they are merged
    cs-plan task --stage S --before N --title T --paths p1,p2 --test CMD --done TEXT [--depends N,M]   insert a task ahead of task-N, also while waves run (see ORCHESTRATOR.md)
    cs-plan edit-task --stage S --id N --field F --value V [--field F2 --value V2]   replace fields of task-N.md (title paths pattern test done turns); TASKS.md kept in sync
    cs-plan check --stage S | --feature F                 `OK n tasks` or one problem per line (exit 2)
    cs-plan dryrun --stage S                              run each task's `Dry run: <cmd>  # expect > N changes` in a temp worktree; exit 2 when a tool is a no-op
    cs-plan testrun --stage S                             run each task's `Test:` line once in a temp worktree of HEAD; exit 2 when no test matches or the command cannot run (a failing test is accepted)
    cs-plan run --feature F --prompt-file P [--model sonnet|opus] [--dry-run]   one architect session (default Sonnet, ESCALATE -> Opus; --model opus starts on Opus when known ahead), check, one rerun
Every command takes --dir (default .). --stage S = folder .work/S or .work/F/phases/NN-name. Exit 2 on invalid input.
"""

import argparse
import os
import sys

from . import plan_check, plan_dryrun, plan_insert, plan_testrun, pr_mode
from . import plan_files as pf
from .branches import dirty_block, red_base_block
from .configuration import BLOCKED_FILE, EXIT_BLOCKED, MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET
from .plan_run import run
from .util import default_dir, jprint, read_text, write_text


def cmd_plan_init(a):
    pf.init_plan(a)
    return 0


def cmd_phase(a):
    phases, paths = pf.read_phases(a), pf.csv(a.paths)
    if not os.path.exists(pf.plan_path(a)):
        raise pf.PlanError("Plan.md missing: run plan-init first")
    if pf.phase_ref(phases, a.name):
        raise pf.PlanError(f"phase {a.name} exists")
    for dep in pf.csv(a.depends):
        if not pf.phase_ref(phases, dep):
            raise pf.PlanError(f"depends on unknown phase {dep}")
    deps = pf.csv(a.depends) or ([pf.phase_ref(phases, a.after)["dir"]] if a.after and pf.phase_ref(phases, a.after) else [])
    probe = {"dir": a.name, "depends": deps}  # the new phase, so ordering sees its depends chain
    for p in phases:
        ov = pf.overlap(paths, p["paths"])
        if ov and not pf.phases_ordered([*phases, probe], a.name, p["dir"]):
            raise pf.PlanError(f"paths overlap with phase {p['dir']}: {ov[0]} / {ov[1]}")
    nn = max([int(p["dir"][:2]) for p in phases] or [0]) + 1
    new = {
        "dir": a.name if a.after else f"{nn:02d}-{a.name}",
        "goal": a.goal,
        "done_when": a.done_when,
        "depends": deps,
        "paths": paths,
        "status": "todo",
        "tasks": a.tasks,
    }
    pf.write_phases(a, pf.insert_phase(a, phases, new, a.after) if a.after else [*phases, new])
    os.makedirs(os.path.join(pf.feature_dir(a), "phases", new["dir"]), exist_ok=True)
    jprint({"phase": new["dir"]})
    return 0


def cmd_phase_done(a):
    return mark_phase_done(a, a.name)


def mark_phase_done(a, name):
    phases = pf.read_phases(a)
    p = pf.phase_ref(phases, name)
    if not p:
        raise pf.PlanError(f"unknown phase {name}")
    p["status"] = "done"
    pf.write_phases(a, phases)
    return 0


def cmd_next_phase(a):
    phases = pf.read_phases(a)
    p = pf.next_open_phase(phases)
    jprint(
        {**p, "folder": os.path.join(pf.feature_dir(a), "phases", p["dir"])} if p else {"phase": None, "all_done": all(x["status"] == "done" for x in phases)}
    )
    return 0


def cmd_stage_init(a):
    d = pf.stage_dir(a)
    os.makedirs(d, exist_ok=True)
    pf.init_stage(d, a.stage, a.goal, a.flags, a.pr_title if pr_mode.has_origin(a) else "")  # no origin = no PR = no title
    return 0


def _insert(a):
    n, probs = plan_insert.insert_task(a)
    print("\n".join(probs) if probs else f"OK {n} tasks")
    return 2 if plan_check.errors_of(probs) else 0


def cmd_task(a):
    if a.before:
        return _insert(a)
    if not a.id:
        raise pf.PlanError("task needs --id (or --before N)")
    return _add_task(a)


def _known_deps(a, ents):
    """--depends as task ids; an unknown one is a PlanError."""
    deps = [x if x.startswith("task-") else f"task-{x}" for x in pf.csv(a.depends)]
    known = {e["id"] for e in ents}
    for dep in deps:
        if dep not in known:
            raise pf.PlanError(f"depends on unknown {dep}")
    return deps


def _add_task(a):
    d, paths = pf.stage_dir(a), pf.csv(a.paths)
    if not os.path.exists(pf.tasks_path(d)):
        raise pf.PlanError(f"{pf.tasks_path(d)} missing: run stage-init first")
    tid = f"task-{int(a.id)}"
    if tid in pf.started_tasks(d):
        raise pf.PlanError(f"{tid} already started (orch.jsonl has a dispatch or skip for it): pick a free id")
    ents = [e for e in pf.read_entries(d) if e["id"] != tid]
    deps = _known_deps(a, ents)
    by_id = {e["id"]: e for e in ents}
    wave = a.wave or max([by_id[x]["wave"] for x in deps] or [0]) + 1
    new_modes = pf.path_modes(f"{pf.TASK_FIELDS['paths']}{', '.join(paths)}")
    for e in ents:
        ov = e["wave"] == wave and pf.wave_conflict(new_modes, pf.task_path_modes(d, e["id"]))  # the rule of plan check: a shared `(rows)` file is fine
        if ov:
            raise pf.PlanError(f"{tid} and {e['id']} share paths in wave {wave}: {ov[0]} / {ov[1]}")
    e = {"id": tid, "title": a.title, "owner": a.model, "wave": wave, "review": a.model, "depends": deps}
    write_text(os.path.join(d, tid + ".md"), pf.render_task(e, a, paths))
    pf.write_tasks(d, [*ents, e])
    return 0


def _check_new_paths(d, ents, e, tid, v):
    """A paths edit must not overlap another task of the same wave."""
    new_modes = pf.path_modes(f"{pf.TASK_FIELDS['paths']}{v}")
    for o in ents:
        ov = o["id"] != tid and e and o["wave"] == e["wave"] and pf.wave_conflict(new_modes, pf.task_path_modes(d, o["id"]))
        if ov:
            raise pf.PlanError(f"{tid} and {o['id']} share paths in wave {e['wave']}: {ov[0]} / {ov[1]}")


def _add_depends(d, ents, e, tid, spec):
    """edit-task --depends: add the named tasks to E's dependencies. Each must exist, not be the task itself and not depend on it (no cycle). A dependency in the same or a later wave moves the task (and the tasks that depend on it) to the wave after it, like `task --before`; a started task cannot move. -> [(task id, old wave, new wave)] of the tasks that moved."""
    if e is None:
        raise pf.PlanError(f"{tid} is not in TASKS.md")
    by = {x["id"]: x for x in ents}
    old = {x["id"]: x["wave"] for x in ents}
    for dep in (x if x.startswith("task-") else f"task-{x}" for x in pf.csv(spec)):
        if dep not in by or dep == tid:
            raise pf.PlanError(f"depends on unknown {dep}" if dep != tid else f"{tid} cannot depend on itself")
        if plan_insert._reaches(by, dep, tid):
            raise pf.PlanError(f"cycle: {dep} depends on {tid}")
        if dep not in e["depends"]:
            e["depends"] = [*e["depends"], dep]
    plan_insert._shift_waves(ents, pf.started_tasks(d))
    return [(x["id"], old[x["id"]], x["wave"]) for x in ents if x["wave"] != old[x["id"]]]


def _depends_report(d, ents, e, moved, had_deps):
    """-> the lines `edit-task --depends` prints: the dependencies now, the tasks moved to a later wave, the plan problems of moved tasks, and the notes a lead needs (dependency mode, a started task)."""
    out = [f"{e['id']} now depends on {', '.join(e['depends']) or 'nothing'}"]
    out += [f"{t} moved from wave {w0} to wave {w1}" for t, w0, w1 in moved]
    moved_ids = tuple(t for t, _w0, _w1 in moved)
    out += [p for p in plan_check.check_stage(d)[1] if moved_ids and p.startswith(moved_ids)]
    if not had_deps:
        out.append(
            "note: no task had a dependency before, so the stage now runs in dependency mode (a stuck task no longer stops the stage; a task whose dependency is not merged is held)"
        )
    if e["id"] in pf.started_tasks(d):
        out.append(f"note: {e['id']} has started and keeps running; its next retry is held until the dependencies are merged")
    return out


def _apply_fields(d, ents, e, tid, pairs, txt):
    """edit-task --field/--value: -> the task file text with every field set (an unknown field or a paths clash is a PlanError)."""
    for k, v in pairs:
        if k not in pf.TASK_FIELDS:
            raise pf.PlanError(f"unknown field {k}: use {', '.join(pf.TASK_FIELDS)}")
        val = v
        if k == "paths":
            val = ", ".join(pf.csv(v))
            _check_new_paths(d, ents, e, tid, val)
        txt = pf.set_task_field(txt, tid, k, val)
        if k == "title" and e:
            e["title"] = val
    return txt


def _after_depends(d, ents, e, moved, had_deps, asked):
    """After an edit-task: rewrite the `Wave:` line of each moved task file, the `Depends:` line of the edited task, and, when --depends was given, print the report."""
    ids = {t for t, _w0, _w1 in moved}
    for x in (x for x in ents if x["id"] in ids):
        plan_insert._set_wave(d, x)
    if e:
        plan_insert._set_depends(d, e)
    if asked:
        print("\n".join(_depends_report(d, ents, e, moved, had_deps)))


def cmd_edit_task(a):
    d = pf.stage_dir(a)
    tid = f"task-{int(a.id)}"
    f = os.path.join(d, tid + ".md")
    if not os.path.exists(f):
        raise pf.PlanError(f"unknown task {tid} in {d}")
    if a.field and len(a.field) != len(a.value) or a.depends is None and not a.field:
        raise pf.PlanError("edit-task needs matching --field/--value pairs or --depends")
    ents = pf.read_entries(d)
    e = next((x for x in ents if x["id"] == tid), None)
    had_deps = any(x["depends"] for x in ents)
    moved = _add_depends(d, ents, e, tid, a.depends) if a.depends else []
    if e and a.depends == "":  # --depends "" clears the dependencies of the task (TASKS.md and its Depends line)
        e["depends"] = []
    write_text(f, _apply_fields(d, ents, e, tid, zip(a.field, a.value, strict=False), read_text(f)))
    if e:
        pf.write_tasks(d, ents)
    _after_depends(d, ents, e, moved, had_deps, a.depends is not None)
    return 0


def cmd_check(a):
    if bool(a.stage) == bool(a.feature):
        raise pf.PlanError("check needs exactly one of --stage / --feature")
    d = pf.stage_dir(a) if a.stage else pf.feature_dir(a)
    n, probs = plan_check.check_stage(d) if a.stage else plan_check.check_feature(a, d)
    probs = [*probs, *plan_check.request_problems(d), *plan_check.ci_drift_warnings(d)]
    print("\n".join(probs) if probs else f"OK {n} tasks")
    return 2 if plan_check.errors_of(probs) else 0


def cmd_dryrun(a):
    d = pf.stage_dir(a)
    n, probs = plan_dryrun.dryrun_stage(d, plan_check.repo_of(d))
    print("\n".join(probs) if probs else f"OK {n} dry runs")
    return 2 if probs else 0


def cmd_testrun(a):
    d = pf.stage_dir(a)
    n, probs = plan_testrun.testrun_stage(d, plan_check.repo_of(d))
    print("\n".join(probs) if probs else f"OK {n} test lines")
    return 2 if probs else 0


def _blocked(a, msg):
    """Print MSG, write it to the feature's BLOCKED.md and -> the configured blocked exit code."""
    print(msg)
    write_text(os.path.join(pf.feature_dir(a), BLOCKED_FILE), msg + "\n")
    return EXIT_BLOCKED


def cmd_run(a):
    _preflight_model()
    if msg := dirty_block(a):
        return _blocked(a, msg)
    if msg := red_base_block(a):
        return _blocked(a, msg)
    return run(a)


def _preflight_model():
    from search.strata import preflight

    preflight()


def build():
    ap = argparse.ArgumentParser(prog="cs-plan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(cmd, fn, **args):
        p = sub.add_parser(cmd)
        p.add_argument("--dir", default=default_dir())
        for k, v in args.items():
            p.add_argument("--" + k.replace("_", "-"), **({"required": True} if v is True else {"default": v}))
        p.set_defaults(fn=fn)

    add("plan-init", cmd_plan_init, feature=True, goal=True)
    add("phase", cmd_phase, feature=True, name=True, goal=True, done_when=True, depends="", paths=True, tasks=0, after="")
    for act in sub.choices["phase"]._actions:
        if act.dest == "tasks":
            act.type = int
    add("phase-done", cmd_phase_done, feature=True, name=True)
    add("next-phase", cmd_next_phase, feature=True)
    add("stage-init", cmd_stage_init, stage=True, goal=True, flags="", pr_title="")
    add(
        "task",
        cmd_task,
        stage=True,
        id="",
        before="",
        title=True,
        paths=True,
        test=True,
        done=True,
        model=MODEL_HAIKU,
        wave=None,
        depends="",
        do_not_touch="",
        pattern="",
        affected="",
        turns=25,
    )
    for act in sub.choices["task"]._actions:
        if act.dest == "wave":
            act.type = int
        if act.dest == "model":
            act.choices = [MODEL_HAIKU, MODEL_SONNET]
    add("edit-task", cmd_edit_task, stage=True, id=True)
    ep = sub.choices["edit-task"]
    ep.add_argument("--field", action="append", default=[])
    ep.add_argument("--value", action="append", default=[])
    ep.add_argument(
        "--depends",
        default=None,  # None: not given; "": clear the dependencies
        help="add these tasks (N,M) to the task's dependencies (the task moves to the wave after them when needed): it is held until they are merged; an empty value clears them",
    )
    add("check", cmd_check, stage="", feature="")
    add("dryrun", cmd_dryrun, stage=True)
    add("testrun", cmd_testrun, stage=True)
    add("run", cmd_run, feature=True, prompt_file=True)
    sub.choices["run"].add_argument("--dry-run", action="store_true")
    sub.choices["run"].add_argument("--model", choices=[MODEL_SONNET, MODEL_OPUS], default=MODEL_SONNET)
    sub.choices["run"].add_argument("--allow-dirty", action="store_true")
    return ap


def main(argv=None):
    a = build().parse_args(argv)
    try:
        return a.fn(a)
    except pf.PlanError as e:
        print(f"plan: {e}", file=sys.stderr)
        return 2
