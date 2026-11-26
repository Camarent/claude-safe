"""E1 dispatch gate: decides when a dev or reviewer runs on Sonnet, from the task's own events.

E1 events (`mine` = the task's events, the same list the task state reads):
  a  dev: the gate stayed red after the Haiku tries (red test/gate reports, or failed review rounds)
  b  dev: the last dev report is ESCALATE, or an earlier E1 dev dispatch keeps the task on Sonnet
  c  reviewer: a reviewer ESCALATE report exists; later reviewers stay on Sonnet
  d  fix-gate dev (role `fix-gate`, `mine` = the earlier fix-gate steps of the wave): an earlier fix dev ended BLOCKED or its re-merge FAILed
No event means Haiku. This module imports nothing from steps: steps imports it.
"""

import re

from .configuration import (
    BLOCKED,
    ESCALATE,
    EV_DISPATCH,
    EV_REPORT,
    FAIL,
    HAIKU_DEV_TRIES,
    HAIKU_REWORK_TRIES,
    MODEL_HAIKU,
    MODEL_SONNET,
    PLAN_ERROR,
)
from .util import read_text

E1_WHY = {
    "a": "E1(a): dev gate stayed red after the Haiku tries; Sonnet dev",
    "b": "E1(b): dev escalated; Sonnet dev",
    "c": "E1(c): reviewer escalated; Sonnet reviewer",
    "d": "E1(d): fix-gate fix failed (BLOCKED or red re-merge); Sonnet fix",
}
PLAN_ERROR_RE = re.compile(rf"^\s*{PLAN_ERROR}:\s*(.*)$", re.MULTILINE)  # reviewer report line: the task file is wrong, not the dev


def _script_ids(mine, word):
    """Ids of this task's script dispatches whose `why` starts with `word` (test run, task gates)."""
    return {e["id"] for e in mine if e["ev"] == EV_DISPATCH and e.get("role") == "script" and (e.get("why") or "").startswith(word)}


def _sonnet_ids(mine, role):
    return {e["id"] for e in mine if e["ev"] == EV_DISPATCH and e["role"] == role and e["model"] == MODEL_SONNET}


def _is_plan_error(report):
    """True when reviewer FAIL report REPORT carries a `PLAN-ERROR:` line (the task file is wrong, no dev round)."""
    return bool(PLAN_ERROR_RE.search(read_text(report.get("file") or "")))


def _failed_review_rounds(mine):
    """Reviewer FAILs that are no PLAN-ERROR, plus ESCALATEs of Sonnet reviewers (nobody stronger is left)."""
    sonnet = _sonnet_ids(mine, "reviewer")
    return sum(
        1
        for e in mine
        if e["ev"] == EV_REPORT
        and e["role"] == "reviewer"
        and ((e["verdict"] == FAIL and not _is_plan_error(e)) or (e["verdict"] == ESCALATE and e["id"] in sonnet))
    )


def _red_gate_reports(mine):
    """Red FAIL reports of the test run and the task gates."""
    ids = _script_ids(mine, "test run") | _script_ids(mine, "task gates")
    return sum(1 for e in mine if e["ev"] == EV_REPORT and e["verdict"] == FAIL and e["id"] in ids)


def _dev_event(mine):
    if _red_gate_reports(mine) >= HAIKU_DEV_TRIES or _failed_review_rounds(mine) > HAIKU_REWORK_TRIES:
        return "a"
    dev_reports = [e for e in mine if e["ev"] == EV_REPORT and e["role"] == "dev"]
    last_escalates = bool(dev_reports) and dev_reports[-1]["verdict"] == ESCALATE
    sticky = any(e["ev"] == EV_DISPATCH and e["role"] == "dev" and e["model"] == MODEL_SONNET and (e.get("why") or "").startswith("E1(") for e in mine)
    return "b" if last_escalates or sticky else None


def _reviewer_event(mine):
    escalated = any(e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] == ESCALATE for e in mine)
    return "c" if escalated else None


def _fix_gate_event(mine):
    """E1(d): an earlier fix-gate dev of this wave ended BLOCKED, or its re-merge script step FAILed (the red gate is still there)."""
    return (
        "d"
        if any(e["ev"] == EV_REPORT and ((e["role"] == "dev" and e["verdict"] == BLOCKED) or (e["role"] == "script" and e["verdict"] == FAIL)) for e in mine)
        else None
    )


def e1_event(role, mine) -> str | None:
    """-> 'a', 'b', 'c', 'd' or None: the E1 event that holds for ROLE ('dev', 'reviewer' or 'fix-gate') in the task's events MINE."""
    if role == "dev":
        return _dev_event(mine)
    if role == "reviewer":
        return _reviewer_event(mine)
    if role == "fix-gate":
        return _fix_gate_event(mine)
    return None


def model_for(role, task, mine) -> tuple[str, str | None]:
    """-> (model, why): Sonnet with an `E1(...)` why when an E1 event holds for ROLE, else (Haiku, None).

    TASK is not read: the owner_sonnet and review_sonnet flags no longer pick a model. For role `fix-gate` TASK is None.
    """
    event = e1_event(role, mine)
    if event is None:
        return MODEL_HAIKU, None
    return MODEL_SONNET, E1_WHY[event]
