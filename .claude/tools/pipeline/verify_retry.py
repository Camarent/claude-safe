"""retry --verify: run the task's Test lines and Done gates on its existing job commit. Green records the step DONE with a report; red records nothing. Never starts a dev."""

import re

from .configuration import DONE, EV_REPORT
from .util import append_event, jprint, paths, read_jsonl, work_path, write_text

WHY_MAX = 300


def verify_step(a, log, checks):
    """checks(a) -> (note, gate count) when the Test lines and gates are green, else None. -> True when green and DONE is recorded."""
    ok = checks(a)
    if not ok:
        jprint({"verified": False, "step": a.step, "log": log, "why": "Test lines or gates red: nothing recorded, no dev started"})
        return False
    note, n = ok
    evs = read_jsonl(log)
    nums = [int(m.group(1)) for x in evs if (m := re.fullmatch(r"retry-(\d+)", str(x.get("id", ""))))]
    rid = f"retry-{max(nums, default=0) + 1}"
    f = work_path(paths(a)[0], f"report-{rid}.md", create=True)
    write_text(f, f"VERDICT: {DONE}\nretry --verify: {note}; {n} gate command(s) green.\n")
    append_event(
        log,
        {"ev": EV_REPORT, "id": rid, "task": a.step, "role": "dev", "verdict": DONE, "reason": "retry --verify green", "why": note[:WHY_MAX], "file": f},
    )
    jprint({"recorded": "done", "step": a.step, "log": log, "why": "Test lines and gates green on the job commit: no dev started"})
    return True
