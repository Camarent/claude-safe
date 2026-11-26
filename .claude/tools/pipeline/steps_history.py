"""Event-history readers of a task: its events since the last retry, failed reports, review rounds and the per-task state (pure functions over the event list)."""

import re

from .configuration import DONE, ESCALATE, EV_DISPATCH, EV_REPORT, EV_RETRY, EXTRA_ROUND_WHY, FAIL, MODEL_SONNET, PLAN_ERROR
from .entry import name_of_stage
from .util import read_text


def history(evs: list[dict], tid: str) -> list[dict]:
    """Events of one task/step since its last `retry` (the owner's reset)."""
    mine = [e for e in evs if e.get("task") == tid]
    for i in range(len(mine) - 1, -1, -1):
        if mine[i]["ev"] == EV_RETRY:
            return _after_retry(mine, i)
    return mine


def _after_retry(mine, i):
    """The events of a task that survive its retry marker `mine[i]` (the owner's reset, or a driver re-run)."""
    marker = mine[i]
    if marker.get("from") == "script":  # a lost script step: only its own dispatch goes, the task's events stay
        return [e for e in mine[:i] if e.get("id") != marker.get("id")] + mine[i + 1 :]
    if marker.get("from") == "review":  # keep the dev events, drop the reviewer's
        return [e for e in mine[:i] if e["ev"] != EV_RETRY and e.get("role") != "reviewer"] + mine[i + 1 :]
    if marker.get("auto"):  # an unclear dev report: the review rounds it was asked to fix survive
        rounds = [e for e in mine[:i] if e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] in (FAIL, ESCALATE)]
        return rounds + mine[i + 1 :]
    return mine[i + 1 :]


def hint(a, tid: str) -> str:
    return f"; fix the cause, then `orch.py retry {name_of_stage(a.stage)} --step {tid}` (or `skip`)"


def script_ids(mine: list[dict], word: str) -> set[str]:
    """Ids of this task's script dispatches whose `why` starts with `word` (pre-review, test run)."""
    return {e["id"] for e in mine if e["ev"] == EV_DISPATCH and e.get("role") == "script" and (e.get("why") or "").startswith(word)}


def failing_reports(mine: list[dict]) -> list[dict]:
    """Every dev ESCALATE, reviewer FAIL/ESCALATE and red test-run FAIL report of the task, oldest first."""
    test_ids = script_ids(mine, "test run")
    return [
        e
        for e in mine
        if e["ev"] == EV_REPORT
        and (
            (e["role"] == "dev" and e["verdict"] == ESCALATE)
            or (e["role"] == "reviewer" and e["verdict"] in (FAIL, ESCALATE))
            or (e["id"] in test_ids and e["verdict"] == FAIL)
        )
    ]


def failed_event(mine: list[dict]) -> dict | None:
    """The latest of them: what the escalation gate judges."""
    return next(iter(reversed(failing_reports(mine))), None)


def _reviewer_reports(mine, verdict):
    return sum(1 for e in mine if e["ev"] == EV_REPORT and e["role"] == "reviewer" and e["verdict"] == verdict)


PLAN_ERROR_RE = re.compile(rf"^\s*{PLAN_ERROR}:\s*(.*)$", re.MULTILINE)  # reviewer report line: the task file is wrong, not the dev


def _plan_error(e):
    """-> the reviewer's `PLAN-ERROR:` text when report event E is a reviewer FAIL that carries one, else None."""
    if e["ev"] != EV_REPORT or e.get("role") != "reviewer" or e["verdict"] != FAIL:
        return None
    m = PLAN_ERROR_RE.search(read_text(e["file"]))
    return (m.group(1)[:300] or "no detail given") if m else None


def _failed_review_reports(mine):
    """Failed review reports, oldest first: every reviewer FAIL (a PLAN-ERROR is no dev round), plus an ESCALATE from a Sonnet reviewer (nobody stronger is left, so it counts as a FAIL)."""
    sonnet_ids = {e["id"] for e in mine if e["ev"] == EV_DISPATCH and e["role"] == "reviewer" and e["model"] == MODEL_SONNET}
    return [
        e
        for e in mine
        if e["ev"] == EV_REPORT
        and e["role"] == "reviewer"
        and ((e["verdict"] == FAIL and _plan_error(e) is None) or (e["verdict"] == ESCALATE and e["id"] in sonnet_ids))
    ]


def _failed_rounds(mine):
    return len(_failed_review_reports(mine))


def _extra_rounds_ran(mine):
    """Extra dev rounds the task already got (dispatches with the EXTRA_ROUND_WHY reason)."""
    return sum(1 for e in mine if e["ev"] == EV_DISPATCH and EXTRA_ROUND_WHY in (e.get("why") or ""))


def _reports(mine, role, verdict, ids=None):
    """How many report events of ROLE (any role when None) with VERDICT; only those whose id is in IDS when given."""
    return sum(1 for e in mine if _is_report(e, role, verdict, ids))


def _is_report(e, role, verdict, ids):
    return e["ev"] == EV_REPORT and (role is None or e["role"] == role) and e["verdict"] == verdict and (ids is None or e["id"] in ids)


class _TaskState:
    """What next_step derives once from a task's events: review rounds, escalations, pre-review script ids, whether a Sonnet dev ran (the model itself is e1.model_for)."""

    def __init__(self, a, task, mine):
        self.rounds = _failed_rounds(mine)
        self.dev_esc = _reports(mine, "dev", ESCALATE)
        self.test_ids = script_ids(mine, "test run")
        self.qual_ids = script_ids(mine, "quality run")
        self.gate_ids = script_ids(mine, "task gates")
        self.test_fails = _reports(mine, None, FAIL, self.test_ids)
        self.dev_done = _reports(mine, "dev", DONE)
        self.sonnet_dev = sonnet_dev_dispatched(mine)


def sonnet_dev_dispatched(mine: list[dict]) -> bool:
    return any(e["ev"] == EV_DISPATCH and e["role"] == "dev" and e["model"] == MODEL_SONNET for e in mine)
