"""The Graph: decide() returns the next action (dispatch / wait / ask_main / done) from TASKS.md and orch.jsonl."""

import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import gitx, pr_mode, worktree
from .audit_phases import last_report
from .briefs import tail
from .configuration import (
    BLOCKED,
    DONE,
    EV_DISPATCH,
    EV_REPORT,
    EV_SKIP,
    FAIL,
    GIT_ENTRY,
    INDEX_TIMEOUT_S,
    MERGE_CONFLICT_MARK,
    MERGE_FIX_TRIES,
    MERGE_RED_MARK,
    MODEL_HAIKU,
    MODEL_SONNET,
    STARTED_EVENTS,
    TASKS_FILE,
    allowed,
    lead_logs,
    project,
    repo_dir,
    tool_cmd,
    work_branch,
)
from .e1 import model_for
from .phases import final_phases, merge_prompt
from .plan_files import earlier_phase_tasks_mds, is_finish_phase
from .run import Run
from .steps import history, last_verdict, next_step, ops_as_script, run_phase, step
from .tasks import depends_problem, parse_flags, parse_tasks
from .util import append_event, paths, read_jsonl, read_text, run_py, swallowed
from .worktrees import job_info, tasks_merged


def ask_all(asks) -> dict:
    """One ask_main for every blocked item of the wave, so the ladder runs once per wave, not once per task."""
    return {"action": "ask_main", "message": "\n".join(x["message"] for x in asks)}


def run_index(a) -> int:
    """Script step, no agent: refresh the code index; a failure only warns (stderr)."""
    try:
        return run_py("search/local.py", "index", "--root", a.dir, cwd=a.dir, timeout=INDEX_TIMEOUT_S).returncode
    except Exception as ex:
        print(f"warning: index refresh failed: {ex}", file=sys.stderr)
        swallowed("graph.run_index", ex)
        return 1


def _task_list_problem(a, tm, tasks, evs):
    """-> ask_main dict when TASKS.md is unreadable, over the max_tasks limit or has bad `Depends:` lines, else None."""
    if msg := depends_problem(tasks):
        return {"action": "ask_main", "message": f"TASKS.md: {msg}"}
    if not tasks and os.path.exists(tm) and re.search(r"task-\d+", Path(tm).read_text(encoding="utf-8")):
        return {"action": "ask_main", "message": "TASKS.md names tasks but none parsed (bad format?)"}
    return _limit_problem(a, tasks, evs)


def _limit_problem(a, tasks, evs):
    """-> ask_main dict when more tasks than `max_tasks` have not started (started tasks do not count: a running phase can take a fix task; a fix round's tasks come on top), else None."""
    limit = int(project(a.dir).get("max_tasks") or 0)
    if not limit:
        return None
    began = {e.get("task") for e in evs if e.get("ev") in STARTED_EVENTS}
    waiting = [t for t in tasks if t["id"] not in began]
    if len(waiting) <= limit or any(e["ev"] == EV_REPORT and str(e.get("task", "")).startswith("fix-plan") for e in evs):
        return None
    msg = f"TASKS.md has {len(waiting)} tasks not yet started, the limit is {limit} (max_tasks in pipeline.json): split the stage into phases (Plan.md) or two stages"
    return {"action": "ask_main", "message": msg}


def _wave_steps(a, evs, members, n):
    """The next_step of every task of one wave (dispatch ids are consecutive from n + 1)."""
    steps: list[dict] = []
    for t in members:
        # id for a dispatch from this task = next free number (one per dispatch already chosen in this wave)
        st = next_step(a, t, history(evs, t["id"]), f"a{n + 1 + sum(1 for x in steps if x['action'] == 'dispatch')}")
        if st:
            steps.append(st)
    return steps


def _name(a):
    """Feature name of the run, else its stage name: what `work_branch` derives the branch from."""
    return getattr(a, "feature", None) or getattr(a, "stage", "")


MERGE_FIX_HOW = "Resolve each conflicted file with Read then Edit (merge both sides in place), then `git add <file>`; never write helper scripts or run scripts by path (denied, and 27 hunks are still 27 Edits). "


def _merge_fix_prompt(a, wave, rep, parts=(), tasks=()):
    br = work_branch(project(a.dir), _name(a))
    if tasks:  # worktree mode: the script merged what it could; the Sonnet merger finishes the failed task by hand
        wt_ = tool_cmd("wt.py")
        return (
            f"CONFLICT-RESOLVE MODE (Sonnet merger): you MAY edit files to resolve conflicts and fix red tests; the mechanical-only rule of the Haiku merger does not apply. Stage {a.stage}: the script merge of wave {wave} failed (conflict or red tests): read {rep}. Fix it on `{br}` in the main checkout, "
            f"keeping both sides' intent, make the tests pass, commit. {MERGE_FIX_HOW} A conflicting task: merge its branch by hand (`git merge --squash <branch>` in each repo that conflicts, nested repo too), "
            f"commit, then run `{wt_} mark-merged --stage {a.stage} --task <task> --dir {os.path.abspath(a.dir)}`. Tasks and branches: {'; '.join(parts)}. "
            f"Do NOT merge other tasks and never run `wt.py remove` or `clean`: the script removes the worktrees once the merge is green. "
            + tail(
                a,
                "{rid}",
                allowed(DONE, BLOCKED),
                "one `RESOLVED: <path>: both|dev|target: <why>` line per resolved file, the test result, or (BLOCKED) the files you could not resolve and why; on BLOCKED abort the merge and leave the tree clean",
            )
        )
    return (
        f"CONFLICT-RESOLVE MODE (Sonnet merger): you MAY edit files to resolve conflicts and fix red tests; the mechanical-only rule of the Haiku merger does not apply. Stage {a.stage}: the merge of wave {wave} failed (conflict or red tests): read {rep}. Resolve it on `{br}` "
        f"keeping both sides' intent, make the tests pass, commit. {MERGE_FIX_HOW} "
        + tail(
            a,
            "{rid}",
            allowed(DONE, BLOCKED),
            "one `RESOLVED: <path>: both|dev|target: <why>` line per resolved file, the test result, or (BLOCKED) the files you could not resolve and why; on BLOCKED abort the merge and leave the tree clean",
        )
    )


def _has_job_branches(a):
    """True when the repo (or a git repo nested in it, e.g. a private project) holds a branch besides the work and release branches: only then a wave has anything to merge."""
    cfg = project(a.dir)
    repo = repo_dir(a.dir, cfg)
    plain = {work_branch(cfg, _name(a)), cfg["branch_release"]}
    roots = [os.path.join(repo, n) for n in worktree.nested_repos(repo)]  # one definition of "a nested repo" (worktree.nested_repos)
    if os.path.exists(os.path.join(repo, GIT_ENTRY)):
        roots.insert(0, repo)
    for root in roots:
        try:
            out = gitx.run(root, "branch", "--format=%(refname:short)").stdout
        except (OSError, subprocess.SubprocessError):
            return True  # cannot tell: let the merger look
        if set(out.split()) - plain:
            return True
    return not roots  # no git repo found: cannot tell, the merger looks


def _flag_resolved(a, wave, tag=""):
    """After a merge-fix: log each `RESOLVED:` line of its report as a `resolved` event, so the lead reviews what the Sonnet merger decided before the push."""
    log = paths(a)[1]
    evs = read_jsonl(log)
    rep = last_report(evs, f"merge-fix-w{wave}{tag}")
    logged = {e.get("line") for e in evs if e["ev"] == "resolved" and e.get("task") == f"merge-fix-w{wave}{tag}"}
    for line in read_text(rep).splitlines():
        if line.startswith("RESOLVED:") and line[:300] not in logged:  # decide() runs this again on every `orch next`
            append_event(log, {"ev": "resolved", "task": f"merge-fix-w{wave}{tag}", "line": line[:300]})


def _wave_parts(a, evs, members):
    """-> one line per task of MEMBERS for a merger prompt: its dev report and its job branch with the repos it lives in."""
    parts = []
    for t in members:
        dr = [e for e in history(evs, t["id"]) if e["ev"] == EV_REPORT and e["role"] == "dev"]
        info = job_info(a, t["id"], create=False)
        where = (
            f", branch `{info['branch']}` in "
            + ", ".join(f"{r} ({p})" for r, p in info["repos"].items())
            + ("" if project(a.dir).get("worktrees") else f"; afterwards `{tool_cmd('wt.py')} remove --stage {a.stage} --task {t['id']} --dir {a.dir}`")
            if info
            else ""
        )
        parts.append(f"{t['id']} (dev report {dr[-1]['file'] if dr else 'missing'}{where})")
    return parts


def _merge_wave(a, evs, members, wave, n):
    """Merger after a wave: -> a run_phase action, or None once the wave is merged (also when the tasks worked in place and no job branch exists)."""
    if not _has_job_branches(a):
        return None
    parts = _wave_parts(a, evs, members)
    if project(a.dir).get("worktrees"):
        return _script_merge(a, evs, members, wave, n, parts)
    r = run_phase(
        a, evs, [step(f"merge-w{wave}", "merger", MODEL_HAIKU, merge_prompt(a, f"wave {wave}", parts), {DONE, FAIL})], n
    )  # BLOCKED is not satisfied: run_phase asks the lead, the next wave never starts on an unmerged dev
    if r:
        return r
    if last_verdict(evs, f"merge-w{wave}") != FAIL:
        return None
    # one Sonnet fix (conflict or red tests) and one more merge before the lead is asked
    rep = last_report(evs, f"merge-w{wave}")
    r = run_phase(a, evs, [step(f"merge-fix-w{wave}", "merger", MODEL_SONNET, _merge_fix_prompt(a, wave, rep, parts), {DONE}, None, "merger said FAIL")], n)
    if r:
        return r
    _flag_resolved(a, wave)
    return run_phase(a, evs, [step(f"merge-w{wave}-2", "merger", MODEL_HAIKU, merge_prompt(a, f"wave {wave}", parts), {DONE})], n)


def _already_merged(a, members):
    """True when every task of the wave is merged into the work branch already (the lead did it by hand): a merge-fix would find nothing to do."""
    return tasks_merged(a, [t["id"] for t in members])


def _fix_gate_prompt(a, wave, rep):
    br = work_branch(project(a.dir), _name(a))
    return (
        f"FIX-GATE MODE: stage {a.stage}: the tasks of wave {wave} merged into `{br}` in the main checkout, but a gate is red: read {rep} (every red gate is listed). "
        f"Fix the cause on `{br}` in the main checkout with the smallest change, run the red gate commands until green, commit. "
        f"Do not merge or remove any task branch. "
        + tail(a, "{rid}", allowed(DONE, BLOCKED), "the gate commands you ran and their result, or (BLOCKED) what you could not fix and why")
    )


def _fix_gate(a, evs, members, wave, n, parts, cmd, rep, merger_ok=True, tag=""):
    """Red gate after a script merge: a dev fixes it (Haiku, then Sonnet), the script re-runs the merge and gates. The lead is asked only after both fixes failed.
    The model of each fix is e1.model_for("fix-gate"): Sonnet (E1(d)) only after an earlier fix ended BLOCKED or its re-merge FAILed.
    merger_ok=False (after a merger fix already): a conflict on the re-merge goes to the lead, not to a second merger. tag: the late-merge suffix, so step ids stay unique."""
    mine: list = []  # the events of the earlier fix-gate steps of this wave: E1(d) reads them
    for i in (1, 2):
        fid = f"fix-gate-w{wave}{tag}" + ("" if i == 1 else f"-{i}")
        model, why = model_for("fix-gate", None, mine)
        r = run_phase(a, evs, [step(fid, "dev", model, _fix_gate_prompt(a, wave, rep), {DONE, BLOCKED}, None, why or "merge gate red")], n)
        if r:
            return r
        mine += history(evs, fid)
        if last_verdict(evs, fid) != DONE:
            continue
        mid = f"merge-w{wave}{tag}-g{i}"
        r = run_phase(a, evs, [step(mid, "script", None, cmd, {DONE, FAIL}, None, "merge by script, after the gate fix")], n)
        if r:
            return r
        mine += history(evs, mid)
        if last_verdict(evs, mid) != FAIL:
            return None
        rep = last_report(evs, mid)
        if MERGE_RED_MARK not in read_text(rep) and merger_ok:  # the fix is in, but the re-merge now hits a conflict: that is the merger's job
            return _merger_path(a, evs, members, wave, n, parts, cmd, rep, tag)
        if MERGE_RED_MARK not in read_text(rep):
            return {
                "action": "ask_main",
                "message": f"merge-w{wave}{tag}: the gate fix is in but the re-merge FAILs without a red gate: read {rep}, then `orch.py retry --step merge-w{wave}{tag}`.",
            }
    return {
        "action": "ask_main",
        "message": f"merge-w{wave}{tag}: merged, but {MERGE_RED_MARK} and the fix-gate devs did not make it green: read {rep}. Fix it on the stage branch, then `orch.py retry --step merge-w{wave}{tag}`.",
    }


def _merge_cmd(a, ids):
    """The merge script command for a comma-joined list of task ids."""
    return f"{tool_cmd('merge_wave.py')} --dir {shlex.quote(os.path.abspath(a.dir))} --stage {a.stage} --tasks {ids}"


def _script_merge(a, evs, members, wave, n, parts):
    """Worktree mode: merge, test and remove by script (merge_wave.py); one Sonnet fix on FAIL, then the script again."""
    ids = ",".join(t["id"] for t in members)
    cmd = _merge_cmd(a, ids)
    r = run_phase(a, evs, [step(f"merge-w{wave}", "script", None, cmd, {DONE, FAIL}, None, "merge by script")], n)  # BLOCKED -> ask the lead (see above)
    if r:
        return r
    if last_verdict(evs, f"merge-w{wave}") != FAIL:
        return None
    rep = last_report(evs, f"merge-w{wave}")
    if MERGE_RED_MARK in read_text(rep):  # red gate, not a conflict: the merger cannot fix content or lint, a fix-gate dev can
        return _fix_gate(a, evs, members, wave, n, parts, cmd, rep)
    return _merger_path(a, evs, members, wave, n, parts, cmd, rep)


def _merger_path(a, evs, members, wave, n, parts, cmd, rep, tag=""):
    """A script merge FAILed without a red gate: skip when merged by hand, else one Sonnet merger fix, then the script again."""
    ids = ",".join(t["id"] for t in members)
    if _already_merged(a, members):  # merged by hand (wt.py mark-merged or branch content already in the work branch): skip the merger, let the script finish
        return run_phase(a, evs, [step(f"merge-w{wave}{tag}-2", "script", None, cmd, {DONE}, None, "merge by script, tasks already merged by hand")], n)
    if project(a.dir).get("merge_fix_conflict_only") and MERGE_CONFLICT_MARK not in read_text(
        rep
    ):  # opt-in: a FAIL that is no conflict has nothing for a merger to resolve
        return {
            "action": "ask_main",
            "message": f"merge-w{wave}{tag}: FAIL but no conflict in {rep}, so no merger is dispatched: read it, fix the cause, then `orch.py retry --step merge-w{wave}{tag}`.",
        }
    if int(project(a.dir).get("merge_fix_tries", MERGE_FIX_TRIES)) < 1:
        return {
            "action": "ask_main",
            "message": f"merge-w{wave}{tag}: FAIL, merge_fix_tries is 0 so no merger is dispatched: read {rep}, then `orch.py retry --step merge-w{wave}{tag}`.",
        }
    r = run_phase(
        a,
        evs,
        [step(f"merge-fix-w{wave}{tag}", "merger", MODEL_SONNET, _merge_fix_prompt(a, wave, rep, parts, ids), {DONE}, None, "script merge said FAIL")],
        n,
    )
    if r:
        return r
    _flag_resolved(a, wave, tag)
    return _after_fix_merge(a, evs, members, wave, n, parts, cmd, tag)


def _after_fix_merge(a, evs, members, wave, n, parts, cmd, tag):
    """Script merge again after a merger fix. Red tests now -> one fix-gate dev (no second merger, so no loop); conflict or other FAIL -> the lead."""
    sid = f"merge-w{wave}{tag}-2"
    r = run_phase(a, evs, [step(sid, "script", None, cmd, {DONE, FAIL}, None, "merge by script, after the fix")], n)
    if r:
        return r
    if last_verdict(evs, sid) != FAIL:
        return None
    rep = last_report(evs, sid)
    if MERGE_RED_MARK in read_text(rep):  # the merger's fix left the gate red: a fix-gate dev, as for a red first merge
        return _fix_gate(a, evs, members, wave, n, parts, cmd, rep, merger_ok=False, tag=tag)
    return {
        "action": "ask_main",
        "message": f"merge-w{wave}{tag}: FAIL after the merger fix and no red gate: read {rep}, then `orch.py retry --step merge-w{wave}{tag}`.",
    }


def _refresh_index(a, wave):
    """Wave landed on dev: refresh the index (script, failure only warns)."""
    rc = run_index(a)
    append_event(paths(a)[1], {"ev": "script", "task": f"index-w{wave}", "rc": rc})
    if rc:
        print(f"warning: index refresh after wave {wave} exited {rc}", file=sys.stderr)


def _partial_merge(a, evs, settled, wave, n):
    """Dependency mode: merge the finished tasks of a wave that still has a stuck one, so their dependents can start. One script run per set size."""
    todo = [t for t in settled if job_info(a, t["id"], create=False)]
    if not todo or not project(a.dir).get("worktrees"):
        return None
    ids = ",".join(t["id"] for t in todo)
    cmd = f"{tool_cmd('merge_wave.py')} --dir {shlex.quote(os.path.abspath(a.dir))} --stage {a.stage} --tasks {ids}"
    tag = f"-p{len(todo)}"
    r = run_phase(a, evs, [step(f"merge-w{wave}{tag}", "script", None, cmd, {DONE, FAIL}, None, "merge the finished tasks of a wave with a stuck task")], n)
    if r or last_verdict(evs, f"merge-w{wave}{tag}") != FAIL:
        return r
    rep = last_report(
        evs, f"merge-w{wave}{tag}"
    )  # same route as a full wave: a conflict gets one Sonnet merger fix and a second script merge before the lead is asked
    if MERGE_RED_MARK in read_text(rep):
        return {
            "action": "ask_main",
            "message": f"merge-w{wave}{tag}: a gate is red after the partial merge: read {rep}, fix it on the stage branch, then `orch.py retry --step merge-w{wave}{tag}`.",
        }
    return _merger_path(a, evs, todo, wave, n, _wave_parts(a, evs, todo), cmd, rep, tag)


def _unmerged_deps(a, task):
    """True when a task this one depends on still holds work the stage branch lacks (worktree mode): the dependent would start from a base without it."""
    return any(job_info(a, d, create=False) and not tasks_merged(a, [d]) for d in task.get("depends", []))


@dataclass
class WaveResult:
    """What one wave decided. ACTION: the action to return now, or None when the wave is done. Dependency mode only: OPENED = ids of tasks that stay open
    (a later wave's dependents wait on them), HELD = ids held back by an open dependency, ASKS = asks of blocked tasks, collected instead of returned."""

    action: dict | None = None
    opened: set = field(default_factory=set)
    held: set = field(default_factory=set)
    asks: list = field(default_factory=list)


def _hold(a, members, open_ids):
    """-> (members that may start, ids held): those waiting on an open task (or on unmerged work) are held."""
    held = [t for t in members if set(t.get("depends", [])) & open_ids or _unmerged_deps(a, t)]
    return [t for t in members if t not in held], {t["id"] for t in held}


def _manual_gate(a, evs, members, manual, n, open_ids):
    """A wave with manual steps: the merge and later waves wait for the person; the dev tasks of this wave go on, the lead is asked once nothing else can run."""
    rest = [t for t in members if not t.get("manual")]
    res = WaveResult()
    if open_ids is not None:  # a task that waits on an open one is held here too
        res.opened = {t["id"] for t in manual}
        rest, res.held = _hold(a, rest, open_ids | res.opened)
        res.opened |= res.held
    todo = _wave_steps(a, evs, rest, n)
    ds = [x for x in todo if x["action"] == "dispatch"]
    if ds:
        res.action = {"action": "dispatch", "dispatches": ds}
        return res
    res.action = ask_all(
        [x for x in todo if x["action"] != "dispatch"]
        + [
            {"message": f"manual step {t['id']}: {t['manual']}. Do it, then run `orch.py skip --step {t['id']}` (stage {a.stage}); the wave waits for it."}
            for t in manual
        ]
    )
    return res


def _merge_tail(a, evs, members, flags, wave, n):
    """All tasks of the wave are done: merge it, refresh the index, push in PR mode. -> an action while that is open, else None."""
    if not flags["merge"]:
        return None
    r = _merge_wave(a, evs, members, wave, n) or _late_merge(a, evs, members, n) or _unmerged_ask(a, evs, members)
    if r:  # a done task the wave merge never took (added to or retried in the wave after it) is merged before the next wave starts: a later dispatch must not branch from a base missing it
        return r
    if not any(e.get("task") == f"index-w{wave}" for e in evs) and flags["index"]:
        _refresh_index(a, wave)
    if pr_mode.pr_active(a):
        return run_phase(
            a,
            evs,
            [step(f"pr-push-w{wave}", "script", None, pr_mode.cmd(a, _name(a), "push"), {DONE}, None, "after the wave merge: push the stage branch")],
            n,
        )
    return None


def _run_wave(a, evs, tasks, flags, wave, n, open_ids=None):
    """One wave -> WaveResult (action while a task or the merge is open, None when the wave is done).
    OPEN_IDS (dependency mode, a set of open task ids; None = old rule: a stuck task stops the stage): a task that waits on an open one is held, the asks of
    blocked tasks are collected in the result instead of returned, and the wave's finished tasks are merged, so independent work goes on."""
    members = [t for t in tasks if t["wave"] == wave]
    manual = [t for t in members if t.get("manual") and not any(e["ev"] == EV_SKIP and e.get("task") == t["id"] for e in evs)]
    if manual:
        return _manual_gate(a, evs, members, manual, n, open_ids)
    members = [t for t in members if not t.get("manual")]
    res = WaveResult()
    if open_ids is not None:
        members, res.held = _hold(a, members, open_ids)
        res.opened = set(res.held)
    _wave_action(a, evs, members, flags, wave, n, open_ids, res)
    return res


def _wave_action(a, evs, members, flags, wave, n, open_ids, res):
    """Set res.action (and in dependency mode res.opened / res.asks) from the steps the wave's tasks need next."""
    steps = _wave_steps(a, evs, members, n)
    ds = [x for x in steps if x["action"] == "dispatch"]
    if ds:
        res.action = {"action": "dispatch", "dispatches": ds}
    elif steps and open_ids is not None:
        _collect_asks(a, evs, members, steps, flags, wave, n, res)
    elif steps:
        res.action = ask_all(steps)
    elif open_ids is None or members:  # in dependency mode a wave whose tasks are all held has nothing to merge
        res.action = _merge_tail(a, evs, members, flags, wave, n)


def _collect_asks(a, evs, members, steps, flags, wave, n, res):
    """Dependency mode: keep the asks in the result, mark the waiting tasks open, merge the tasks that are finished."""
    waiting = [t for t in members if next_step(a, t, history(evs, t["id"]), "x")]
    res.opened |= {t["id"] for t in waiting}
    res.asks = steps
    res.action = _partial_merge(a, evs, [t for t in members if t not in waiting], wave, n) if flags["merge"] else None


def _retro_phases(a, evs, n):
    """Last, always: the numbers (script, no model) and a Haiku cost digest for the main-session retro (a failure does not stop the stage)."""
    r = run_phase(
        a,
        evs,
        [
            step(
                "retro-numbers",
                "script",
                None,
                f"{tool_cmd('retro/retro_numbers.py')} --verdict --orch .work/{a.stage}/orch.jsonl --out .work/{a.stage}/retro/retro-numbers.md",
                {DONE, FAIL},
                None,
                "stage end: transcript numbers for the retro",
            )
        ],
        n,
    )
    if r:
        return r
    if is_finish_phase(os.path.join(paths(a)[0], TASKS_FILE)):  # the closing phase: one problems file over all phases for the lead
        r = run_phase(
            a,
            evs,
            [
                step(
                    "feature-retro",
                    "script",
                    None,
                    f"{tool_cmd('retro/feature_retro.py')} --feature-dir .work/{os.path.dirname(os.path.dirname(a.stage))} --out .work/{a.stage}/feature-problems.md"
                    + "".join(f" --lead-log {shlex.quote(p)}" for p in lead_logs(a.dir)),
                    {DONE, FAIL},
                    None,
                    "feature end: tripped retro signals of all phases in one file",
                )
            ],
            n,
        )
        if r:
            return r
    return run_phase(
        a,
        evs,
        [
            ops_as_script(
                a,
                project(a.dir),
                step(
                    "retro-digest",
                    "ops",
                    MODEL_HAIKU,
                    f"Stage {a.stage}: cost digest for the retro. Read .work/{a.stage}/orch.jsonl, .work/{a.stage}/metrics.jsonl, .work/{a.stage}/retro/retro-numbers.md (it already holds the metrics; do not run wave.py). "
                    f"Write .work/{a.stage}/retro/retro-digest.md (max 25 lines, facts only, no advice): cost per agent and model; the 5 costliest sessions with task and verdict; retries, ladder (blocked) sessions and their cost; steps that ran without producing work (BLOCKED, skipped); "
                    f"any agent that ran on a different model than its frontmatter default; the LEAD-COST lines of retro-numbers.md copied as they are (lead cost by token kind, gaps, cache rewrites)."
                    + tail(a, "{rid}", allowed(DONE, BLOCKED), "the 3 costliest lines"),
                    {DONE, BLOCKED, FAIL},  # a failed digest never blocks the stage: the retro reads what it can
                    None,
                    "stage end: cost digest for the retro",
                ),
                "digest",
            )
        ],
        n,
    )


def _done_message(tasks, flags, evs):
    clean = (
        "; "
        + " and ".join(
            w for w, on in (("tests", flags["test"]), ("audits", any(e["ev"] == EV_REPORT and str(e.get("task", "")).startswith("audit-") for e in evs))) if on
        )
        + " clean"
        if flags["test"] or flags["audit"]
        else ""
    )
    return f"all {len(tasks)} task(s) reviewed PASS" + clean


def _empty_job_removed(a, task_id, prune=True):
    """A done task with nothing left to merge (`worktree.settled_job`: empty, its content already in the stage branch, or marked merged by hand) loses its job worktree and branch here. True when removed; a refused removal keeps the task blocking. PRUNE False (a read-only caller such as `wave.py status`): nothing is removed, a settled job just counts as not blocking."""
    if not worktree.settled_job(a.dir, a.stage, task_id):
        return False
    if not prune:
        return True
    try:
        worktree.remove(a.dir, a.stage, task_id)
    except RuntimeError:
        return False
    return True


def _unmerged_done(a, evs, tasks, prune=True):
    """Worktree mode: a finished (not skipped) task whose worktree is still there was never merged (the merge script removes it). An empty one is removed (PRUNE False: left alone), not blocking. -> task ids."""
    if not project(a.dir).get("worktrees"):
        return []
    skipped = {e.get("task") for e in evs if e["ev"] == EV_SKIP}
    out = []
    for t in tasks:
        done = any(e["ev"] == EV_REPORT and e["role"] == "dev" and e["verdict"] == DONE for e in history(evs, t["id"]))
        if done and t["id"] not in skipped and job_info(a, t["id"], create=False) and not _empty_job_removed(a, t["id"], prune):
            out.append(t["id"])
    return out


def unmerged_done_ids(a, evs, tasks) -> list:
    """Done tasks whose job branch is not merged into the stage branch although their wave's merge step already ran (`wave.py status` lists them; before that merge a done task is simply waiting for it). Read-only: unlike the driver's own check it never removes an empty job's worktree."""
    wave = {t["id"]: t["wave"] for t in tasks}
    return [t for t in _unmerged_done(a, evs, tasks, prune=False) if _step_satisfied(evs, f"merge-w{wave.get(t)}")]


def _sweep_phase(a, evs, n):
    """Stage start: the Dependabot sweep script step while it is active. -> action dict or None."""
    if not pr_mode.sweep_active(a):
        return None
    sweep = step("deps-sweep", "script", None, pr_mode.sweep_cmd(a), {DONE}, None, "stage start: merge green Dependabot PRs, fast-forward the release branch")
    return run_phase(a, evs, [sweep], n)


def _run_waves(a, evs, tasks, flags, n):
    """Every wave in order. -> action dict or None; the lead hears all asks of the waves at once."""
    dep_mode = any(t.get("depends") for t in tasks)  # without `Depends:` lines the old rule: a stuck task stops the stage
    open_ids: set = set()
    held: set = set()
    asks: list = []
    for wave in sorted({t["wave"] for t in tasks}):
        res = _run_wave(a, evs, tasks, flags, wave, n, open_ids if dep_mode else None)
        if res.action:
            return res.action
        open_ids |= res.opened
        held |= res.held
        asks += res.asks
    if not asks:
        return None
    r = ask_all(asks)
    r["message"] += f"\n(held back by them, not started: {', '.join(sorted(held))})" if held else ""
    return r


def _final_tasks(tm, tasks):
    """The real tasks the tests and audits look at: manual steps are done; a finish phase adds the tasks of all phases before it."""
    tasks = [t for t in tasks if not t.get("manual")]
    if is_finish_phase(tm):
        tasks = [t for md in earlier_phase_tasks_mds(tm) for t in parse_tasks(md) if not t.get("manual")] + tasks
    return tasks


def _unmerged_ask(a, evs, tasks):
    """Done tasks whose worktree branch was never merged -> ask_main dict, else None."""
    lost = _unmerged_done(a, evs, tasks)
    if not lost:  # never merge silently: the lead must see what happened and choose merge, retry or skip
        return None
    ids = ",".join(lost)
    return ask_all(
        [
            {
                "message": f"stage {a.stage}: done task(s) {ids} still have an unmerged worktree branch, so their work is not in {work_branch(project(a.dir), _name(a))}. Look at why (orch.jsonl), then merge by hand with `{tool_cmd('merge_wave.py')} --dir {os.path.abspath(a.dir)} --stage {a.stage} --tasks {ids}`, or `orch.py skip --step <task>` to drop it."
            }
        ]
    )


def _step_satisfied(evs, sid):
    """True when step SID was skipped or its last report is DONE (the rule run_phase uses)."""
    mine = history(evs, sid)
    reps = [e for e in mine if e["ev"] == EV_REPORT]
    return any(e["ev"] == EV_SKIP for e in mine) or bool(reps and reps[-1]["verdict"] == DONE)


def _last_index(evs, pred):
    """-> the index of the last event for which PRED holds, -1 when none."""
    return next((i for i in range(len(evs) - 1, -1, -1) if pred(evs[i])), -1)


def _dev_done_index(evs, task):
    """-> the index of the task's last dev DONE report, -1 when none."""
    return _last_index(evs, lambda e: e["ev"] == EV_REPORT and e.get("task") == task and e.get("role") == "dev" and e.get("verdict") == DONE)


def _late_batches(evs, wave):
    """-> [(event index, step suffix, task ids or None)] of the late-merge batches of a wave, oldest first: the dispatches of `merge-w<N>-late` and `merge-w<N>-late<k>`. The dispatch records the tasks it covers (`tasks`); the ladder steps behind a batch carry longer ids."""
    pat = re.compile(rf"merge-w{wave}(-late\d*)")
    out = []
    for i, e in enumerate(evs):
        m = pat.fullmatch(str(e.get("task", ""))) if e["ev"] == EV_DISPATCH else None
        if m:
            out.append((i, m[1], e.get("tasks")))
    return out


def _late_ladder_done(evs, wave, tag):
    """True when the ladder of a late batch ended: its script step, the re-merge after a merger fix (-2) or after a fix-gate dev (-g<i>) is DONE (or skipped)."""
    pat = re.compile(rf"merge-w{wave}{tag}(?:-2|-g\d+)?")
    return any(_step_satisfied(evs, sid) for sid in {e["task"] for e in evs if e.get("task") and pat.fullmatch(str(e["task"]))})


def _tried(evs, batches, task):
    """True when one of the late BATCHES was dispatched for TASK after the task's last dev DONE report (a dispatch without a `tasks` list counts for every task done before it)."""
    done = _dev_done_index(evs, task)
    return any(done < i and (tasks is None or task in tasks) for i, _tag, tasks in batches)


def _late_batch(evs, wave, lost):
    """-> (step suffix, task ids) of the late merge to run for the done-but-unmerged tasks LOST of a wave, or (None, []) when every one of them was tried already (the lead's ask is next). A batch whose ladder is still running continues under its own ids; otherwise the tasks no batch has tried since their last DONE form a new batch: `-late` the first time, `-late<k>` later, so a task finished or retried after an earlier batch gets a merge step of its own."""
    batches = _late_batches(evs, wave)
    if batches and not _late_ladder_done(evs, wave, batches[-1][1]):
        mine = [t for t in lost if _tried(evs, batches[-1:], t)]
        if mine:
            return batches[-1][1], mine
    pending = [t for t in lost if not _tried(evs, batches, t)]
    k = len(batches) + 1
    return (("-late" if k == 1 else f"-late{k}"), pending) if pending else (None, [])


def _late_merge(a, evs, tasks, n):
    """Worktree mode: done tasks retried or added after the wave's merge pass still have a worktree branch. The script merges them (step merge-w{N}-late, a later set merge-w{N}-late-<ids>), with the same fix ladder as a wave merge. -> action dict or None (nothing left, merged, or the same set was merged already and is still there)."""
    lost = _unmerged_done(a, evs, tasks)
    if not lost:
        return None
    wave = max(t["wave"] for t in tasks if t["id"] in lost)
    tag, lost = _late_batch(evs, wave, lost)
    if tag is None:
        return None
    members = [t for t in tasks if t["id"] in lost]
    cmd = _merge_cmd(a, ",".join(lost))
    sid = f"merge-w{wave}{tag}"
    r = run_phase(a, evs, [step(sid, "script", None, cmd, {DONE, FAIL}, None, "merge the retried tasks by script")], n)
    for d in (r or {}).get("dispatches", []):
        if d["task"] == sid:
            d["tasks"] = list(lost)  # logged with the dispatch: which tasks this batch covers
    if r or last_verdict(evs, sid) != FAIL:
        return r
    rep = last_report(evs, sid)
    parts = _wave_parts(a, evs, members)
    if MERGE_RED_MARK in read_text(rep):  # red gate, not a conflict: a fix-gate dev
        return _fix_gate(a, evs, members, wave, n, parts, cmd, rep, tag=tag)
    return _merger_path(a, evs, members, wave, n, parts, cmd, rep, tag)


def _closing_phases(a, flags, tasks, evs, n):
    """Tests, docs, audits and the fix cycle, then the retro. -> first action dict or None."""
    for phase in final_phases(a, flags, tasks, evs):
        r = run_phase(a, evs, phase, n)
        if r:
            return r
    return _retro_phases(a, evs, n) if flags["retro"] else None


def decide(a) -> dict:
    """Waves first (dev -> reviewer per task, parallel inside a wave, merger after each wave), then tests, docs, audits and the fix cycle."""
    w, log = paths(a)
    tm = os.path.join(w, TASKS_FILE)
    tasks = parse_tasks(tm)
    evs = read_jsonl(log)
    a = Run.of(a).at(orch_events=evs, wt_memo={})  # the whole log for counts that history() resets; a per-call cache of git answers (the log is the cache key)
    problem = _task_list_problem(a, tm, tasks, evs)
    if problem:
        return problem
    flags = parse_flags(tm)
    n = sum(1 for e in evs if e["ev"] == EV_DISPATCH)
    r = _sweep_phase(a, evs, n) or _run_waves(a, evs, tasks, flags, n)
    if r:
        return r
    tasks = _final_tasks(tm, tasks)
    if not tasks:
        return {"action": "done", "message": "no tasks"}
    if flags["merge"] and not is_finish_phase(tm):
        r = _late_merge(a, evs, tasks, n) or _unmerged_ask(a, evs, tasks)  # the ask stays the last fallback
        if r:
            return r
    r = _closing_phases(a, flags, tasks, evs, n)
    return r or {"action": "done", "message": _done_message(tasks, flags, evs)}
