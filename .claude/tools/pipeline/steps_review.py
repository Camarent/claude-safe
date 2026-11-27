"""Review-outcome branching of the per-task state machine: what a reviewer report (PASS, FAIL, ESCALATE, PLAN-ERROR) dispatches next.

Moved out of steps.py; steps.py re-exports the names (`X as X`) for its callers and for the tests that patch them. This module never imports
from pipeline.steps: the dispatch helper `_disp` and the test-run limit REVIEW_EXTRA_ROUNDS stay in steps.py and are passed in as arguments
(`disp`, `extra_rounds`), so a patch on steps still reaches the branch."""

import os
import shlex

from .briefs import plan_fix_prompt
from .configuration import ARCHITECT, ESCALATE, EV_DISPATCH, EV_REPORT, EXTRA_ROUND_WHY, FAIL, MAX_ROUNDS, NIT_PASS_WHY, PASS, WORK_DIR, tool_cmd
from .steps_docfix import DOCFIX_WHY, repeated_pair
from .steps_history import _extra_rounds_ran, _failed_review_reports, _plan_error, _reviewer_reports, hint, script_ids
from .util import paths, read_text, work_path, write_text
from .verdicts import is_nit_only_fail


def _script(task, rid, why, cmd):
    return {"action": "dispatch", "id": rid, "agent": "script", "model": None, "task": task["id"], "role": "script", "why": why, "prompt": cmd}


def _extra_round(a, task, mine, rid, disp):
    """One extra dev round after the last failed review round: one rework file with the verbatim text of every failed review; the model is e1.model_for's."""
    fails = _failed_review_reports(mine)
    f = work_path(os.path.join(getattr(a, "dir", "."), WORK_DIR, a.stage), f"reviews-all-{task['id']}-{rid}.md", create=True)
    write_text(f, "".join(f"## review {i}\n{read_text(e['file'])}\n" for i, e in enumerate(fails, 1)))
    d = disp(a, task, rid, "dev", rework=f, mine=mine)
    d["why"] = "; ".join(x for x in (d.get("why"), EXTRA_ROUND_WHY) if x)  # the log keeps the reason; _extra_rounds_ran counts these dispatches
    return d


def _after_reviewer(a, task, mine, rid, s, last, disp, extra_rounds, log):
    if last["verdict"] == ESCALATE and _reviewer_reports(mine, ESCALATE) > 1:  # E4: a second reviewer ESCALATE blocks to the lead
        return {
            "action": "ask_main",
            "message": f"{task['id']}: second reviewer ESCALATE blocks to the lead: {last.get('reason', '')}; findings {last['file']}" + hint(a, task["id"]),
        }
    if last["verdict"] == ESCALATE:  # E1(c): a reviewer ESCALATE sends the task to the Sonnet reviewer (e1.model_for)
        return disp(a, task, rid, "reviewer", mine=mine)
    if (why := _plan_error(last)) is not None:
        return _plan_fix(a, task, mine, rid, last, why)
    if last["verdict"] in (FAIL, ESCALATE):
        if last["verdict"] == FAIL and (d := _docfix_dispatch(a, task, mine, rid, last)) is not None:
            return d
        if s.rounds > MAX_ROUNDS - 1:
            if last["verdict"] == FAIL and is_nit_only_fail(read_text(last["file"])):  # nits only: no defect left, PASS with the nits as notes
                return _nit_pass(a, task, mine, rid, last, log)
            if _extra_rounds_ran(mine) < extra_rounds:
                return _extra_round(a, task, mine, rid, disp)
            return {"action": "ask_main", "message": f"{task['id']}: review failed {s.rounds} rounds; findings {last['file']}" + hint(a, task["id"])}
        # first FAIL: Haiku rework; Sonnet once the failed rounds passed HAIKU_REWORK_TRIES (e1.model_for, event a)
        return disp(a, task, rid, "dev", rework=last["file"], mine=mine)
    return None


def _nit_pass(a, task, mine, rid, last, log):
    """A nit-only reviewer FAIL after the failed rounds: a PASS report event with the nits file as its notes, so the task passes (None). No extra round, no lead."""
    log(
        paths(a)[1],
        {
            "ev": EV_REPORT,
            "id": rid,
            "task": task["id"],
            "role": "reviewer",
            "verdict": PASS,
            "reason": NIT_PASS_WHY,
            "why": NIT_PASS_WHY,
            "file": last["file"],
        },
    )
    return None


def _plan_fix(a, task, mine, rid, last, why):
    """Reviewer PLAN-ERROR: one architect round on the task file (not a dev try), then the reviewer runs again; a second plan error goes to the lead."""
    if any(e["ev"] == EV_DISPATCH and e["role"] == ARCHITECT for e in mine):
        return {
            "action": "ask_main",
            "message": f"{task['id']}: plan error again after the architect fix: {why}; findings {last['file']}" + hint(a, task["id"]),
        }
    return {
        "free": True,
        "action": "dispatch",
        "id": rid,
        "agent": ARCHITECT,
        "model": None,
        "task": task["id"],
        "role": ARCHITECT,
        "prompt": plan_fix_prompt(a, task, rid, why, last["file"]),
        "why": "reviewer found a plan error in the task file",
    }


def _docfix_dispatch(a, task, mine, rid, last):
    """A reviewer FAIL that repeats one docs-only one-line finding (the same `old -> new` line in the last two FAIL reports): a script applies it, then the reviewer runs again. None otherwise; once per task."""
    if script_ids(mine, DOCFIX_WHY):
        return None
    fails = _failed_review_reports(mine)
    if len(fails) < 2 or fails[-1]["id"] != last["id"]:
        return None
    pair = repeated_pair(read_text(fails[-2]["file"]), read_text(fails[-1]["file"]))
    if pair is None:
        return None
    path, old, new = pair
    return _script(
        task, rid, DOCFIX_WHY, f"{tool_cmd('pipeline/steps_docfix.py')} --file {shlex.quote(path)} --old {shlex.quote(old)} --new {shlex.quote(new)}"
    )
